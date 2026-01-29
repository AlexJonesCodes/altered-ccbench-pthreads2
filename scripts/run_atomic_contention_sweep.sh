#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/run_atomic_contention_sweep.sh [options]

Run CAS_UNTIL_SUCCESS vs FAI vs TAS across contention levels using a fixed core list.

Options:
  --cores LIST         Comma-separated core list to use (required, e.g., "0,2,4,6,8")
  --thread-counts LIST Comma-separated thread counts (default: "2,4,6,8,10")
  --reps N             Repetitions per run (default: 10000)
  --seed-core N        Seed core for contended runs (default: first --cores entry)
  --rotate-seed        Rotate the seed core across the --cores list per run
  --ccbench PATH       Path to ccbench binary (default: ./ccbench)
  --output-dir DIR     Output directory for logs/CSVs (default: results/atomic_contention_sweep)
  --dry-run            Print commands without running them
  -h, --help           Show this help

Example:
  scripts/run_atomic_contention_sweep.sh --cores "0,2,4,6,8,10" --rotate-seed
USAGE
}

cores_list=""
thread_counts="2,4,6,8,10"
reps=10000
seed_core=""
ccbench=./ccbench
output_dir=results/atomic_contention_sweep
dry_run=0
rotate_seed=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cores)
      cores_list="$2"; shift 2 ;;
    --thread-counts)
      thread_counts="$2"; shift 2 ;;
    --reps)
      reps="$2"; shift 2 ;;
    --seed-core)
      seed_core="$2"; shift 2 ;;
    --rotate-seed)
      rotate_seed=1; shift ;;
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

IFS=',' read -r -a core_array <<<"$cores_list"
IFS=',' read -r -a thread_list <<<"$thread_counts"

if [[ ${#core_array[@]} -lt 1 ]]; then
  echo "--cores must include at least one core." >&2
  exit 1
fi

if [[ -z "$seed_core" && "$rotate_seed" -eq 0 ]]; then
  seed_core="${core_array[0]}"
fi

if [[ -n "$seed_core" && "$seed_core" -lt 0 ]]; then
  echo "Seed core must be >= 0." >&2
  exit 1
fi

seed_found=0
seed_index=0
if [[ -n "$seed_core" ]]; then
  for idx in "${!core_array[@]}"; do
    if [[ "${core_array[$idx]}" -eq "$seed_core" ]]; then
      seed_index="$idx"
      seed_found=1
      break
    fi
  done
fi

if [[ "$rotate_seed" -eq 1 && "$seed_found" -ne 1 && -n "$seed_core" ]]; then
  echo "Seed core ($seed_core) must be in --cores list when using --rotate-seed." >&2
  exit 1
fi

for count in "${thread_list[@]}"; do
  if [[ "$count" -lt 1 ]]; then
    echo "Thread counts must be >= 1" >&2
    exit 1
  fi
  max_workers=${#core_array[@]}
  if [[ "$rotate_seed" -eq 1 || -n "$seed_core" ]]; then
    if [[ "$seed_found" == "1" || "$rotate_seed" -eq 1 ]]; then
      max_workers=$((max_workers - 1))
    fi
  fi
  if [[ "$count" -gt "$max_workers" ]]; then
    echo "Thread count $count exceeds available worker cores ($max_workers)." >&2
    exit 1
  fi
 done

mkdir -p "$output_dir/logs"

runs_csv="$output_dir/runs.csv"
threads_csv="$output_dir/threads.csv"

printf "run_id,atomic,seed_core,threads,tests,cores,reps,mean_avg,jain_fairness,success_rate\n" >"$runs_csv"
printf "run_id,atomic,thread_id,core,avg,min,max,std_dev,abs_dev,wins,success_rate\n" >"$threads_csv"

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

make_core_slice() {
  local count="$1"
  local seed="$2"
  local list=""
  local used=0
  for core in "${core_array[@]}"; do
    if [[ -n "$seed" && "$core" -eq "$seed" ]]; then
      continue
    fi
    if [[ -n "$list" ]]; then
      list+=","${core}
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

ensure_seed_not_in_cores() {
  local seed="$1"
  local cores="$2"
  if [[ -z "$seed" ]]; then
    return 0
  fi
  local normalized
  normalized="${cores//[\[\]]/}"
  if [[ ",${normalized}," == *",${seed},"* ]]; then
    echo "Seed core ($seed) must not be in worker cores: ${cores}" >&2
    exit 1
  fi
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

  awk -v run_id="$run_label" \
      -v atomic="$atomic" \
      -v seed="$seed" \
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
        if (val == "") val = 0
        wins_sum += val
        sum += val
        sum_sq += val * val
      }

      fairness = 0
      if (count > 0 && sum_sq > 0) {
        fairness = (sum * sum) / (count * sum_sq)
      }

      success_rate = 0
      if (count > 0 && reps > 0) {
        success_rate = wins_sum / (count * reps)
      }

      printf "%s,%s,%s,%s,\"%s\",\"%s\",%s,%.3f,%.6f,%.6f\n", \
        run_id, atomic, seed, threads, tests, cores, reps, mean_avg + 0, fairness, success_rate \
        >> runs_csv

      for (t in thread_seen) {
        if (t == "") continue
        wins = wins_by_thread[t]
        if (wins == "") wins = 0
        sr = 0
        if (reps > 0) sr = wins / reps
        printf "%s,%s,%s,%s,%.3f,%.3f,%.3f,%.3f,%.3f,%s,%.6f\n", \
          run_id, atomic, t, core_by_thread[t], \
          avg_by_thread[t] + 0, min_by_thread[t] + 0, max_by_thread[t] + 0, \
          std_by_thread[t] + 0, abs_by_thread[t] + 0, wins, sr \
          >> threads_csv
      }
    }
  ' "$log_file"
}

run_id=0
for threads in "${thread_list[@]}"; do
  declare -A tests_by_atomic=(
    [CAS_UNTIL_SUCCESS]="$cas_id"
    [FAI]="$fai_id"
    [TAS]="$tas_id"
  )

  for atomic in CAS_UNTIL_SUCCESS FAI TAS; do
    test_id="${tests_by_atomic[$atomic]}"
    tests_list=$(make_list "$threads" "$test_id")
    run_id=$((run_id + 1))

    if [[ "$rotate_seed" -eq 1 ]]; then
      seed_core="${core_array[$seed_index]}"
      seed_index=$(((seed_index + 1) % ${#core_array[@]}))
    fi

    cores=$(make_core_slice "$threads" "$seed_core")
    ensure_seed_not_in_cores "$seed_core" "$cores"

    if [[ "$dry_run" -eq 0 ]]; then
      printf '\n[run %d] atomic=%s seed_core=%s threads=%s tests=%s cores=%s\n' \
        "$run_id" "$atomic" "$seed_core" "$threads" "$tests_list" "$cores"
    fi

    log_file="$output_dir/logs/run_${run_id}_${atomic}_t${threads}.log"
    cmd=("$ccbench" -r "$reps" -t "$tests_list" -x "$cores" -b "$seed_core")
    if [[ "$dry_run" -eq 1 ]]; then
      run_cmd "${cmd[@]}"
      continue
    fi
    run_cmd "${cmd[@]}" | tee "$log_file"
    parse_stats "$log_file" "$run_id" "$atomic" "$seed_core" "$threads" "$tests_list" "$cores" "$reps"
  done
 done

if [[ "$dry_run" -eq 0 ]]; then
  printf '\nCompleted. Summary CSVs:\n'
  printf '  Runs   : %s\n' "$runs_csv"
  printf '  Threads: %s\n' "$threads_csv"
fi
