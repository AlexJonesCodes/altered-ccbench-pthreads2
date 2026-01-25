#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/run_cas_latency_fairness.sh [options]

Sweep thread counts for test ID 34 (CAS until success) and record
latency + fairness metrics in CSVs.

Options:
  --thread-counts LIST  Comma-separated thread counts (e.g., "1,2,4,8")
  --max-threads N       Maximum threads when --thread-counts omitted (default: all)
  --test-id N           Test ID to run (default: 34)
  --reps N              Repetitions per run (default: 10000)
  --seed-core N         Seed core for contended runs (default: 0, -1 disables)
  --ccbench PATH        Path to ccbench binary (default: ./ccbench)
  --output-dir DIR      Output directory for logs/CSVs (default: results/cas_sweep)
  --dry-run             Print commands without running them
  -h, --help            Show this help

Examples:
  scripts/run_cas_latency_fairness.sh --thread-counts "1,2,4,8"
  scripts/run_cas_latency_fairness.sh --max-threads 16 --reps 5000
USAGE
}

thread_counts=""
max_threads=""
test_id=34
reps=10000
seed_core=0
ccbench=./ccbench
output_dir=results/cas_sweep
dry_run=0

total_cores=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --thread-counts)
      thread_counts="$2"; shift 2 ;;
    --max-threads)
      max_threads="$2"; shift 2 ;;
    --test-id)
      test_id="$2"; shift 2 ;;
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

if command -v nproc >/dev/null 2>&1; then
  total_cores=$(nproc --all)
else
  total_cores=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)
fi

if [[ -z "$thread_counts" ]]; then
  if [[ -z "$max_threads" ]]; then
    max_threads="$total_cores"
  fi
  if [[ "$max_threads" -lt 1 ]]; then
    echo "--max-threads must be >= 1" >&2
    exit 1
  fi
  thread_counts=$(seq 1 "$max_threads" | paste -sd, -)
fi

if [[ "$test_id" == "34" && "$seed_core" -lt 0 ]]; then
  echo "CAS_UNTIL_SUCCESS requires a seed core; overriding --seed-core to 0." >&2
  seed_core=0
fi

if [[ "$seed_core" -ge 0 && "$seed_core" -ge "$total_cores" ]]; then
  echo "--seed-core ($seed_core) must be less than total cores ($total_cores)." >&2
  exit 1
fi

if [[ ! -x "$ccbench" ]]; then
  echo "ccbench binary not found or not executable: $ccbench" >&2
  exit 1
fi

mkdir -p "$output_dir/logs"

IFS=',' read -r -a thread_list <<<"$thread_counts"

runs_csv="$output_dir/runs.csv"
threads_csv="$output_dir/threads.csv"

printf "run_id,threads,tests,cores,reps,mean_avg,jain_fairness,success_rate\n" >"$runs_csv"
printf "run_id,thread_id,core,avg,min,max,std_dev,abs_dev,wins,success_rate\n" >"$threads_csv"

print_key_stats() {
  cat <<'NOTES'
Key ccbench stats:
  - "Summary : mean avg ..." (overall mean latency)
  - "Core number ... avg ... std dev ..." (per-thread stats)
  - "wins" lines (contention winners per thread)
NOTES
}

make_list() {
  local count="$1"
  local value="$2"
  local list=""
  for ((i=0; i<count; i++)); do
    if [[ -n "$list" ]]; then
      list+=","${value}
    else
      list=${value}
    fi
  done
  printf '[%s]' "$list"
}

make_cores() {
  local count="$1"
  local list=""
  local core=0
  local used=0

  while [[ "$used" -lt "$count" ]]; do
    if [[ "$core" -ge "$total_cores" ]]; then
      echo "Not enough cores available to allocate $count workers." >&2
      exit 1
    fi
    if [[ "$seed_core" -ge 0 && "$core" -eq "$seed_core" ]]; then
      core=$((core + 1))
      continue
    fi
    if [[ -n "$list" ]]; then
      list+=","${core}
    else
      list=${core}
    fi
    core=$((core + 1))
    used=$((used + 1))
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
  local threads="$3"
  local tests="$4"
  local cores="$5"
  local reps="$6"

  awk -v run_id="$run_label" \
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

      printf "%s,%s,\"%s\",\"%s\",%s,%.3f,%.6f,%.6f\n", \
        run_id, threads, tests, cores, reps, mean_avg + 0, fairness, success_rate \
        >> runs_csv

      for (t in thread_seen) {
        if (t == "") continue
        wins = wins_by_thread[t]
        if (wins == "") wins = 0
        sr = 0
        if (reps > 0) sr = wins / reps
        printf "%s,%s,%s,%.3f,%.3f,%.3f,%.3f,%.3f,%s,%.6f\n", \
          run_id, t, core_by_thread[t], \
          avg_by_thread[t] + 0, min_by_thread[t] + 0, max_by_thread[t] + 0, \
          std_by_thread[t] + 0, abs_by_thread[t] + 0, wins, sr \
          >> threads_csv
      }
    }
  ' "$log_file"
}

run_id=0
for threads in "${thread_list[@]}"; do
  if [[ "$threads" -lt 1 ]]; then
    echo "Thread counts must be >= 1" >&2
    exit 1
  fi
  cores=$(make_cores "$threads")
  tests_list=$(make_list "$threads" "$test_id")
  run_id=$((run_id + 1))

  if [[ "$dry_run" -eq 0 ]]; then
    printf '\n[run %d] threads=%s tests=%s cores=%s\n' \
      "$run_id" "$threads" "$tests_list" "$cores"
  fi

  log_file="$output_dir/logs/run_${run_id}_t${threads}_test_${test_id}.log"
  cmd=("$ccbench" -r "$reps" -t "$tests_list" -x "$cores")
  if [[ "$seed_core" -ge 0 ]]; then
    cmd+=(-b "$seed_core")
  fi
  if [[ "$dry_run" -eq 1 ]]; then
    run_cmd "${cmd[@]}"
    continue
  fi
  run_cmd "${cmd[@]}" | tee "$log_file"
  parse_stats "$log_file" "$run_id" "$threads" "$tests_list" "$cores" "$reps"
done

if [[ "$dry_run" -eq 0 ]]; then
  printf '\nCompleted. Summary CSVs:\n'
  printf '  Runs   : %s\n' "$runs_csv"
  printf '  Threads: %s\n' "$threads_csv"
fi
