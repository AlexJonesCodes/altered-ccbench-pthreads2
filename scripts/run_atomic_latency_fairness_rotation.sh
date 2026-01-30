#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/run_atomic_latency_fairness_rotation.sh [options]

Run CAS_UNTIL_SUCCESS, FAI, and TAS across thread counts while rotating the
pinned (seed) thread across all participating threads. Collect latency and
fairness metrics in CSVs.

Options:
  --cores LIST           Core list (e.g., "[0,1,2,3,4]") (required)
  --thread-counts LIST   Comma-separated thread counts (e.g., "1,2,4,8")
  --max-threads N        Maximum threads when --thread-counts omitted
  --reps N               Repetitions per run (default: 10000)
  --ccbench PATH         Path to ccbench binary (default: ./ccbench)
  --output-dir DIR       Output directory for logs/CSVs (default: results/atomic_fairness_rotation)
  --dry-run              Print commands without running them
  -h, --help             Show this help

Examples:
  scripts/run_atomic_latency_fairness_rotation.sh \
    --cores "[0,1,2,3]" \
    --thread-counts "2,4"
USAGE
}

cores=""
thread_counts=""
max_threads=""
reps=10000
ccbench=./ccbench
output_dir=results/atomic_fairness_rotation
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cores)
      cores="$2"; shift 2 ;;
    --thread-counts)
      thread_counts="$2"; shift 2 ;;
    --max-threads)
      max_threads="$2"; shift 2 ;;
    --reps)
      reps="$2"; shift 2 ;;
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

if [[ -z "$cores" ]]; then
  echo "--cores is required." >&2
  usage
  exit 1
fi

if [[ ! -x "$ccbench" ]]; then
  echo "ccbench binary not found or not executable: $ccbench" >&2
  exit 1
fi

parse_cores() {
  local raw="$1"
  local parsed
  parsed=$(echo "$raw" | tr -d '[]' | tr ',' ' ')
  read -r -a core_array <<<"$parsed"
  printf '%s\n' "${core_array[@]}"
}

make_tests_list() {
  local count="$1"
  local test_id="$2"
  local list=""
  for ((i=0; i<count; i++)); do
    if [[ -n "$list" ]]; then
      list+=","$test_id
    else
      list=$test_id
    fi
  done
  printf '[%s]' "$list"
}

build_cores_list() {
  local -a core_array=("$@")
  local list=""
  for core in "${core_array[@]}"; do
    if [[ -n "$list" ]]; then
      list+=","$core
    else
      list=$core
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
  "${cmd[@]}"
}

parse_stats() {
  local log_file="$1"
  local run_label="$2"
  local op_name="$3"
  local seed_core="$4"
  local threads="$5"
  local tests="$6"
  local cores="$7"
  local reps="$8"

  awk -v run_id="$run_label" \
      -v op="$op_name" \
      -v seed="$seed_core" \
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
        run_id, op, seed, threads, tests, cores, reps, mean_avg + 0, fairness, success_rate \
        >> runs_csv

      for (t in thread_seen) {
        if (t == "") continue
        wins = wins_by_thread[t]
        if (wins == "") wins = 0
        sr = 0
        if (reps > 0) sr = wins / reps
        printf "%s,%s,%s,%s,%s,%.3f,%.3f,%.3f,%.3f,%.3f,%s,%.6f\n", \
          run_id, op, seed, t, core_by_thread[t], \
          avg_by_thread[t] + 0, min_by_thread[t] + 0, max_by_thread[t] + 0, \
          std_by_thread[t] + 0, abs_by_thread[t] + 0, wins, sr \
          >> threads_csv
      }
    }
  ' "$log_file"
}

mapfile -t core_array < <(parse_cores "$cores")
if [[ "${#core_array[@]}" -lt 1 ]]; then
  echo "No cores provided." >&2
  exit 1
fi

if [[ -z "$thread_counts" ]]; then
  if [[ -z "$max_threads" ]]; then
    max_threads="${#core_array[@]}"
  fi
  if [[ "$max_threads" -lt 1 || "$max_threads" -gt "${#core_array[@]}" ]]; then
    echo "--max-threads must be between 1 and ${#core_array[@]}" >&2
    exit 1
  fi
  thread_counts=$(seq 1 "$max_threads" | paste -sd, -)
fi

IFS=',' read -r -a thread_list <<<"$thread_counts"

mkdir -p "$output_dir/logs"

runs_csv="$output_dir/runs.csv"
threads_csv="$output_dir/threads.csv"

if [[ "$dry_run" -eq 0 ]]; then
  printf "run_id,op,seed_core,threads,tests,cores,reps,mean_avg,jain_fairness,success_rate\n" >"$runs_csv"
  printf "run_id,op,seed_core,thread_id,core,avg,min,max,std_dev,abs_dev,wins,success_rate\n" >"$threads_csv"
fi

ops=("CAS_UNTIL_SUCCESS:34" "FAI:13" "TAS:14")
run_id=0

for threads in "${thread_list[@]}"; do
  if [[ "$threads" -lt 1 || "$threads" -gt "${#core_array[@]}" ]]; then
    echo "Thread counts must be between 1 and ${#core_array[@]}" >&2
    exit 1
  fi
  selected_cores=("${core_array[@]:0:$threads}")
  cores_list=$(build_cores_list "${selected_cores[@]}")

  for op_entry in "${ops[@]}"; do
    op_name="${op_entry%%:*}"
    test_id="${op_entry##*:}"
    tests_list=$(make_tests_list "$threads" "$test_id")

    for ((i=0; i<threads; i++)); do
      seed_core="${selected_cores[$i]}"
      run_id=$((run_id + 1))

      if [[ "$dry_run" -eq 0 ]]; then
        printf '\n[run %d] op=%s threads=%s seed_core=%s tests=%s cores=%s\n' \
          "$run_id" "$op_name" "$threads" "$seed_core" "$tests_list" "$cores_list"
      fi

      log_file="$output_dir/logs/run_${run_id}_op_${op_name}_t${threads}_seed_${seed_core}.log"
      cmd=("$ccbench" -r "$reps" -t "$tests_list" -x "$cores_list" -b "$seed_core")
      if [[ "$dry_run" -eq 1 ]]; then
        run_cmd "${cmd[@]}"
        continue
      fi
      run_cmd "${cmd[@]}" | tee "$log_file"
      parse_stats "$log_file" "$run_id" "$op_name" "$seed_core" \
        "$threads" "$tests_list" "$cores_list" "$reps"
    done
  done
done

if [[ "$dry_run" -eq 0 ]]; then
  printf '\nCompleted. Summary CSVs:\n'
  printf '  Runs   : %s\n' "$runs_csv"
  printf '  Threads: %s\n' "$threads_csv"
fi
