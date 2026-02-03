#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/run_atomic_full_sweep.sh --cores LIST [options]

Run CAS_UNTIL_SUCCESS, FAI, and TAS across thread counts. For each thread
count, the first N cores from --cores define the worker/seed pool for that run.
Collect per-run latency, fairness, and optional failure stats in concise CSVs.

Required:
  --cores LIST           Comma-separated core list (e.g., "0,1,2,3")

Options:
  --thread-counts LIST   Comma-separated thread counts (default: "2,4,6,8")
  --max-threads N        Maximum threads when --thread-counts omitted
  --reps N               Repetitions per run (default: 10000)
  --rotate-seed          Rotate the seed core across the selected core prefix
  --seed-core N          Fixed seed core (must be inside the selected prefix)
  --fail-stats           Capture per-thread attempts/successes/failures
  --ccbench PATH         Path to ccbench binary (default: ./ccbench)
  --output-dir DIR       Output directory (default: results/atomic_full_sweep)
  --dry-run              Print commands without running them
  -h, --help             Show this help

Outputs:
  <output-dir>/runs.csv          Top-level per-run summary
  <output-dir>/threads.csv       Per-thread latency/wins
  <output-dir>/failure_stats.csv Optional per-thread failure stats

Example:
  scripts/run_atomic_full_sweep.sh --cores "0,1,2,3,4,5,6,7,8,9" \
    --thread-counts "2,4,8" --rotate-seed --fail-stats
USAGE
}

cores_list=""
thread_counts="2,4,6,8"
max_threads=""
reps=10000
rotate_seed=0
seed_core=""
fail_stats=0
ccbench=./ccbench
output_dir=results/atomic_full_sweep
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cores)
      cores_list="$2"; shift 2 ;;
    --thread-counts)
      thread_counts="$2"; shift 2 ;;
    --max-threads)
      max_threads="$2"; shift 2 ;;
    --reps)
      reps="$2"; shift 2 ;;
    --rotate-seed)
      rotate_seed=1; shift ;;
    --seed-core)
      seed_core="$2"; shift 2 ;;
    --fail-stats)
      fail_stats=1; shift ;;
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

if [[ -z "$cores_list" ]]; then
  echo "--cores is required." >&2
  usage
  exit 1
fi

if [[ ! -x "$ccbench" ]]; then
  echo "ccbench binary not found or not executable: $ccbench" >&2
  exit 1
fi

if [[ ! -f include/ccbench.h ]]; then
  echo "include/ccbench.h not found; run from repo root." >&2
  exit 1
fi

get_test_id() {
  local name="$1"
  awk -v name="$name" '
    /const char\* moesi_type_des\[/ {in_arr=1; next}
    in_arr && /\};/ {exit}
    in_arr && /"/ {
      gsub(/^[[:space:]]*"|",[[:space:]]*$/, "", $0)
      if ($0 == name) { print idx; exit }
      idx++
    }
  ' include/ccbench.h
}

cas_id=$(get_test_id "CAS_UNTIL_SUCCESS")
fai_id=$(get_test_id "FAI")
tas_id=$(get_test_id "TAS")

if [[ -z "$cas_id" || -z "$fai_id" || -z "$tas_id" ]]; then
  echo "Failed to resolve test IDs from include/ccbench.h" >&2
  exit 1
fi

IFS=',' read -r -a core_array <<<"${cores_list//[\[\] ]/}"
if [[ ${#core_array[@]} -lt 2 ]]; then
  echo "--cores must include at least two cores." >&2
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
fail_csv="$output_dir/failure_stats.csv"

printf "run_id,atomic,seed_core,threads,tests,cores,reps,mean_avg,jain_fairness,attempts_total,successes_total,failures_total,failure_rate_total\n" >"$runs_csv"
printf "run_id,atomic,seed_core,thread_id,core,avg,min,max,std_dev,abs_dev,wins\n" >"$threads_csv"
if [[ "$fail_stats" -eq 1 ]]; then
  printf "run_id,atomic,thread_id,core,attempts,successes,failures,failure_rate\n" >"$fail_csv"
fi

make_list() {
  local count="$1"
  local value="$2"
  local list=""
  for ((i=0; i<count; i++)); do
    if [[ -n "$list" ]]; then
      list+=",${value}"
    else
      list=${value}
    fi
  done
  printf '[%s]' "$list"
}

build_cores_list() {
  local count="$1"
  local list=""
  local used=0
  for core in "${core_array[@]}"; do
    if [[ -n "$list" ]]; then
      list+=",${core}"
    else
      list=${core}
    fi
    used=$((used + 1))
    if [[ "$used" -ge "$count" ]]; then
      break
    fi
  done
  printf '[%s]' "$list"
}

seed_in_selected() {
  local seed="$1"
  local cores="$2"
  local normalized
  normalized="${cores//[\[\]]/}"
  if [[ ",${normalized}," == *",${seed},"* ]]; then
    return 0
  fi
  return 1
}

run_cmd() {
  local -a cmd=(${1+"$@"})
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
  local atomic="$3"
  local seed="$4"
  local threads="$5"
  local tests="$6"
  local cores="$7"
  local reps="$8"
  local want_fail_stats="$9"

  awk -v run_id="$run_label" \
      -v atomic="$atomic" \
      -v seed="$seed" \
      -v threads="$threads" \
      -v tests="$tests" \
      -v cores="$cores" \
      -v reps="$reps" \
      -v runs_csv="$runs_csv" \
      -v threads_csv="$threads_csv" \
      -v fail_csv="$fail_csv" \
      -v want_fail_stats="$want_fail_stats" '
    BEGIN {
      fail_label = ""
      target_label = atomic
      attempts_total = 0
      successes_total = 0
      failures_total = 0
      failure_rate_total = ""
    }
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
    /wins/ {
      if (match($0, /thread ID[[:space:]]+([0-9]+)\):[[:space:]]+([0-9]+)[[:space:]]+wins/, m) ||
          match($0, /thread ID[[:space:]]+([0-9]+):[[:space:]]+([0-9]+)[[:space:]]+wins/, m)) {
        thread = m[1]
        wins = m[2]
        if (thread != "") {
          wins_by_thread[thread] = wins
          wins_seen[thread] = 1
        }
      }
    }
    /Atomic failure stats/ {
      if (match($0, /Atomic failure stats \(([A-Z_]+)\):/, m)) {
        fail_label = m[1]
      }
    }
    /thread ID/ && /attempts/ && want_fail_stats == 1 {
      if (fail_label == target_label &&
          match($0, /thread ID[[:space:]]+([0-9]+)[^)]*\(core[[:space:]]+([0-9]+)\):[[:space:]]+attempts[[:space:]]+([0-9]+),[[:space:]]+successes[[:space:]]+([0-9]+),[[:space:]]+failures[[:space:]]+([0-9]+),[[:space:]]+failure rate[[:space:]]+([0-9.]+)/, m)) {
        f_thread = m[1]
        f_core = m[2]
        f_attempts = m[3]
        f_successes = m[4]
        f_failures = m[5]
        f_rate = m[6]
        printf "%s,%s,%s,%s,%s,%s,%s,%s\n", \
          run_id, atomic, f_thread, f_core, f_attempts, f_successes, f_failures, f_rate \
          >> fail_csv
        attempts_total += f_attempts
        successes_total += f_successes
        failures_total += f_failures
      }
    }
    /totals: attempts/ && want_fail_stats == 1 {
      if (fail_label == target_label &&
          match($0, /totals: attempts[[:space:]]+([0-9]+),[[:space:]]+successes[[:space:]]+([0-9]+),[[:space:]]+failures[[:space:]]+([0-9]+),[[:space:]]+failure rate[[:space:]]+([0-9.]+)/, m)) {
        attempts_total = m[1]
        successes_total = m[2]
        failures_total = m[3]
        failure_rate_total = m[4]
      }
    }
    END {
      sum = 0
      sum_sq = 0
      wins_sum = 0
      count = 0
      if (length(wins_seen) > 0) {
        for (t in wins_seen) {
          if (t == "") continue
          count++
          val = wins_by_thread[t]
          if (val == "") val = 0
          wins_sum += val
          sum += val
          sum_sq += val * val
        }
      } else {
        for (t in thread_seen) {
          if (t == "") continue
          count++
          val = wins_by_thread[t]
          if (val == "") val = 0
          wins_sum += val
          sum += val
          sum_sq += val * val
        }
      }

      fairness = 0
      if (count > 0 && sum_sq > 0) {
        fairness = (sum * sum) / (count * sum_sq)
      }

      if (want_fail_stats == 1 && failure_rate_total == "" && attempts_total > 0) {
        failure_rate_total = failures_total / attempts_total
      }

      if (want_fail_stats == 1 && attempts_total == 0 && successes_total == 0 && failures_total == 0) {
        attempts_total = ""
        successes_total = ""
        failures_total = ""
        failure_rate_total = ""
      }

      printf "%s,%s,%s,%s,\"%s\",\"%s\",%s,%.3f,%.6f,%s,%s,%s,%s\n", \
        run_id, atomic, seed, threads, tests, cores, reps, mean_avg + 0, fairness, \
        attempts_total, successes_total, failures_total, failure_rate_total \
        >> runs_csv

      for (t in thread_seen) {
        if (t == "") continue
        wins = wins_by_thread[t]
        if (wins == "") wins = 0
        printf "%s,%s,%s,%s,%s,%.3f,%.3f,%.3f,%.3f,%.3f,%s\n", \
          run_id, atomic, seed, t, core_by_thread[t], \
          avg_by_thread[t] + 0, min_by_thread[t] + 0, max_by_thread[t] + 0, \
          std_by_thread[t] + 0, abs_by_thread[t] + 0, wins \
          >> threads_csv
      }
    }
  ' "$log_file"
}

declare -A tests_by_atomic=(
  [CAS_UNTIL_SUCCESS]="$cas_id"
  [FAI]="$fai_id"
  [TAS]="$tas_id"
)

run_id=0
for threads in "${thread_list[@]}"; do
  if [[ "$threads" -lt 1 || "$threads" -gt "${#core_array[@]}" ]]; then
    echo "Thread counts must be between 1 and ${#core_array[@]}" >&2
    exit 1
  fi

  cores=$(build_cores_list "$threads")
  if [[ -z "$cores" ]]; then
    echo "Unable to build cores list for threads=$threads" >&2
    exit 1
  fi

  seed_from_list=()
  if [[ -n "$seed_core" ]]; then
    if ! seed_in_selected "$seed_core" "$cores"; then
      echo "Seed core $seed_core is not in selected cores: $cores" >&2
      exit 1
    fi
    seed_from_list=("$seed_core")
  elif [[ "$rotate_seed" -eq 1 ]]; then
    IFS=',' read -r -a seed_from_list <<<"${cores//[\[\]]/}"
  else
    IFS=',' read -r -a seed_from_list <<<"${cores//[\[\]]/}"
    seed_from_list=("${seed_from_list[0]}")
  fi

  for atomic in CAS_UNTIL_SUCCESS FAI TAS; do
    test_id="${tests_by_atomic[$atomic]}"
    tests_list=$(make_list "$threads" "$test_id")

    for seed in "${seed_from_list[@]}"; do
      run_id=$((run_id + 1))
      log_file="$output_dir/logs/run_${run_id}_${atomic}_t${threads}_seed${seed}.log"
      cmd=("$ccbench" -r "$reps" -t "$tests_list" -x "$cores" -b "$seed")
      if [[ "$fail_stats" -eq 1 ]]; then
        cmd+=(-F)
      fi
      if [[ "$dry_run" -eq 1 ]]; then
        run_cmd "${cmd[@]}"
        continue
      fi
      run_cmd "${cmd[@]}" | tee "$log_file"
      parse_stats "$log_file" "$run_id" "$atomic" "$seed" \
        "$threads" "$tests_list" "$cores" "$reps" "$fail_stats"
    done
  done
done

if [[ "$dry_run" -eq 0 ]]; then
  printf '\nCompleted. CSV outputs:\n'
  printf '  %s\n' "$runs_csv"
  printf '  %s\n' "$threads_csv"
  if [[ "$fail_stats" -eq 1 ]]; then
    printf '  %s\n' "$fail_csv"
  fi
fi
