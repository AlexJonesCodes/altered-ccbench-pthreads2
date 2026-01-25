#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/run_interference_sweep.sh [options]

Run structured interference experiments with per-thread stats, baselines,
and fairness metrics.

Options:
  --tests LIST            Comma-separated test IDs (e.g., "12,13,14")
  --thread-counts LIST    Comma-separated thread counts (e.g., "2,4,8")
  --topology NAME=CORES   Named topology with a core list (e.g., same_ccx=[0,1,2,3])
                          May be provided multiple times.
  --reps N                Repetitions per run (default: 10000)
  --seed-core N           Seed core for contended runs (default: 0, -1 disables)
  --ccbench PATH          Path to ccbench binary (default: ./ccbench)
  --output-dir DIR        Output directory for logs/CSVs (default: results)
  --dry-run               Print commands without running them
  -h, --help              Show this help

Examples:
  scripts/run_interference_sweep.sh \
    --tests "12,13,14" \
    --thread-counts "2,4" \
    --topology same_ccx="[0,1,2,3]" \
    --topology cross_socket="[0,16,1,17]"
EOF
}

tests=""
thread_counts=""
reps=10000
seed_core=0
ccbench=./ccbench
output_dir=results
dry_run=0
declare -a topology_names=()
declare -a topology_cores=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tests)
      tests="$2"; shift 2 ;;
    --thread-counts)
      thread_counts="$2"; shift 2 ;;
    --topology)
      if [[ "$2" != *=* ]]; then
        echo "--topology expects NAME=CORES" >&2
        exit 1
      fi
      topology_names+=("${2%%=*}")
      topology_cores+=("${2#*=}")
      shift 2 ;;
    --reps)
      reps="$2"; shift 2 ;;
    --seed-core)
      seed_core="$2"; shift 2 ;;
    --ccbench)
      ccbench="$2"; shift 2 ;;
    --output-dir)
      output_dir="$2"; shift 2 ;;
    --dry-run)
      dry_run=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage; exit 1 ;;
  esac
done

if [[ -z "$tests" || -z "$thread_counts" || "${#topology_names[@]}" -eq 0 ]]; then
  echo "--tests, --thread-counts, and at least one --topology are required." >&2
  usage
  exit 1
fi

if [[ ! -x "$ccbench" ]]; then
  echo "ccbench binary not found or not executable: $ccbench" >&2
  exit 1
fi

mkdir -p "$output_dir/logs"

IFS=',' read -r -a test_ids <<<"$tests"
IFS=',' read -r -a thread_list <<<"$thread_counts"

requires_seed=0
for tid in "${test_ids[@]}"; do
  if [[ "$tid" == "34" ]]; then
    requires_seed=1
    break
  fi
done

if [[ "$requires_seed" -eq 1 && "$seed_core" -lt 0 ]]; then
  echo "CAS_UNTIL_SUCCESS requires a seed core; overriding --seed-core to 0." >&2
  seed_core=0
fi

run_id=0
runs_csv="$output_dir/runs.csv"
threads_csv="$output_dir/threads.csv"

printf "run_id,phase,topology,threads,tests,cores,reps,mean_avg,jain_fairness,success_rate\n" >"$runs_csv"
printf "run_id,phase,thread_id,core,avg,min,max,std_dev,abs_dev,wins,success_rate\n" >"$threads_csv"

print_key_stats() {
  cat <<'EOF'
Key ccbench stats:
  - "Summary : mean avg ..." (overall mean latency)
  - "Core number ... avg ... std dev ..." (per-thread stats)
  - "wins" lines (contention winners per thread)
EOF
}

join_tests() {
  local count="$1"
  local op_a="$2"
  local op_b="$3"
  local list=""
  local split=$(( (count + 1) / 2 ))
  for ((i=0; i<count; i++)); do
    local val="$op_b"
    if [[ "$i" -lt "$split" ]]; then
      val="$op_a"
    fi
    if [[ -n "$list" ]]; then
      list+=","${val}
    else
      list=${val}
    fi
  done
  printf '[%s]' "$list"
}

pick_cores() {
  local count="$1"
  local cores="$2"
  local parsed
  parsed=$(echo "$cores" | tr -d '[]' | tr ',' ' ')
  read -r -a core_array <<<"$parsed"
  if [[ "${#core_array[@]}" -lt "$count" ]]; then
    echo "Topology has ${#core_array[@]} cores, need $count: $cores" >&2
    exit 1
  fi
  local list=""
  for ((i=0; i<count; i++)); do
    if [[ -n "$list" ]]; then
      list+=","${core_array[$i]}
    else
      list=${core_array[$i]}
    fi
  done
  printf '[%s]' "$list"
}

run_cmd() {
  local -a cmd=("$@")
  if [[ "$dry_run" -eq 1 ]]; then
    printf '%q ' "${cmd[@]}"
    printf '\n'
    return 0
  fi
  printf 'Running: '
  printf '%q ' "${cmd[@]}"
  printf '\n'
  print_key_stats
  "${cmd[@]}"
}

parse_stats() {
  local log_file="$1"
  local run_label="$2"
  local phase="$3"
  local topology="$4"
  local threads="$5"
  local tests="$6"
  local cores="$7"
  local reps="$8"

  awk -v run_id="$run_label" \
      -v phase="$phase" \
      -v topology="$topology" \
      -v threads="$threads" \
      -v tests="$tests" \
      -v cores="$cores" \
      -v reps="$reps" \
      -v runs_csv="$runs_csv" \
      -v threads_csv="$threads_csv" '
    /Summary : mean avg/ {
      if (match($0, /mean avg[[:space:]]*([0-9.]+)/, m)) {
        mean_avg = m[1]
      }
    }
    /Core number/ {
      if (match($0, /Core number[[:space:]]+([0-9]+)[^0-9]+thread:[[:space:]]+([0-9]+).*avg[[:space:]]+([0-9.]+)[^0-9]+min[[:space:]]+([0-9.]+)[^0-9]+max[[:space:]]+([0-9.]+)[^0-9]+std dev:[[:space:]]+([0-9.]+)[^0-9]+abs dev:[[:space:]]+([0-9.]+)/, m)) {
        core = m[1]
        thread = m[2]
        avg = m[3]
        min = m[4]
        max = m[5]
        std = m[6]
        abs = m[7]
        if (thread != "") {
          avg_by_thread[thread] = avg
          core_by_thread[thread] = core
          min_by_thread[thread] = min
          max_by_thread[thread] = max
          std_by_thread[thread] = std
          abs_by_thread[thread] = abs
          thread_seen[thread] = 1
        }
      }
    }
    /wins$/ {
      if (match($0, /thread ID[[:space:]]+([0-9]+):[[:space:]]+([0-9]+)[[:space:]]+wins$/, m)) {
        thread = m[1]
        wins = m[2]
        if (thread != "") {
          wins_by_thread[thread] = wins
        }
      }
    }
    END {
      sum = 0
      sum_sq = 0
      wins_sum = 0
      count = 0
      for (t in thread_seen) {
        if (t == "") continue
        count++
        val = wins_by_thread[t]
        if (val == "" || val == 0) {
          if (avg_by_thread[t] > 0) {
            val = 1 / avg_by_thread[t]
          } else {
            val = 0
          }
        } else {
          wins_sum += val
        }
        sum += val
        sum_sq += val * val
      }

      fairness = 0
      if (count > 0 && sum_sq > 0) {
        fairness = (sum * sum) / (count * sum_sq)
      }

      success_rate = 0
      if (wins_sum > 0 && count > 0) {
        success_rate = wins_sum / (count * reps)
      }

      printf "%s,%s,%s,%s,\"%s\",\"%s\",%s,%.3f,%.6f,%.6f\n", \
        run_id, phase, topology, threads, tests, cores, reps, mean_avg + 0, fairness, success_rate \
        >> runs_csv

      for (t in thread_seen) {
        if (t == "") continue
        wins = wins_by_thread[t]
        if (wins == "") wins = 0
        sr = 0
        if (reps > 0) sr = wins / reps
        printf "%s,%s,%s,%s,%.3f,%.3f,%.3f,%.3f,%.3f,%s,%.6f\n", \
          run_id, phase, t, core_by_thread[t], \
          avg_by_thread[t] + 0, min_by_thread[t] + 0, max_by_thread[t] + 0, \
          std_by_thread[t] + 0, abs_by_thread[t] + 0, wins, sr \
          >> threads_csv
      }
    }
  ' "$log_file"
}

for idx in "${!topology_names[@]}"; do
  topology="${topology_names[$idx]}"
  topology_core_list="${topology_cores[$idx]}"
  for threads in "${thread_list[@]}"; do
    cores=$(pick_cores "$threads" "$topology_core_list")
    for op_a in "${test_ids[@]}"; do
      for op_b in "${test_ids[@]}"; do
        run_id=$((run_id + 1))
        tests_list=$(join_tests "$threads" "$op_a" "$op_b")

        if [[ "$dry_run" -eq 0 ]]; then
          printf '\n[run %d] topology=%s threads=%s tests=%s cores=%s\n' \
            "$run_id" "$topology" "$threads" "$tests_list" "$cores"
        fi

        if [[ "$dry_run" -eq 0 ]]; then
          IFS=',' read -r -a core_array <<<"$(echo "$cores" | tr -d '[]')"
          IFS=',' read -r -a test_array <<<"$(echo "$tests_list" | tr -d '[]')"
          for i in "${!core_array[@]}"; do
            core="${core_array[$i]}"
            op="${test_array[$i]}"
            baseline_log="$output_dir/logs/baseline_run_${run_id}_core_${core}_op_${op}.log"
            cmd=("$ccbench" -r "$reps" -t "[$op]" -x "[$core]" -b "$core")
            run_cmd "${cmd[@]}" | tee "$baseline_log"
            parse_stats "$baseline_log" "${run_id}_b${i}" "baseline" "$topology" 1 "[$op]" "[$core]" "$reps"
          done
        fi

        log_file="$output_dir/logs/run_${run_id}_t${threads}_a${op_a}_b${op_b}.log"
        cmd=("$ccbench" -r "$reps" -t "$tests_list" -x "$cores")
        if [[ "$seed_core" -ge 0 ]]; then
          cmd+=(-b "$seed_core")
        fi
        if [[ "$dry_run" -eq 1 ]]; then
          run_cmd "${cmd[@]}"
          continue
        fi
        run_cmd "${cmd[@]}" | tee "$log_file"
        parse_stats "$log_file" "$run_id" "contended" "$topology" "$threads" "$tests_list" "$cores" "$reps"
      done
    done
  done
done

if [[ "$dry_run" -eq 0 ]]; then
  printf '\nCompleted. Summary CSVs:\n'
  printf '  Runs   : %s\n' "$runs_csv"
  printf '  Threads: %s\n' "$threads_csv"
fi
