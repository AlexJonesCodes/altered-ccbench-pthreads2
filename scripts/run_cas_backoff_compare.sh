#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/run_cas_backoff_compare.sh [options]

Compare CAS_UNTIL_SUCCESS with and without backoff, dumping per-thread wins.

Options:
  --thread-counts LIST  Comma-separated thread counts (e.g., "1,2,4,8")
  --max-threads N       Maximum threads when --thread-counts omitted (default: all)
  --reps N              Repetitions per run (default: 10000)
  --seed-core N         Seed core for contended runs (default: 0, -1 disables)
  --backoff-max N       Backoff max pause iterations (default: 1024)
  --ccbench PATH        Path to ccbench binary (default: ./ccbench)
  --output-dir DIR      Output directory for logs/CSVs (default: results/cas_backoff)
  --dry-run             Print commands without running them
  -h, --help            Show this help
USAGE
}

thread_counts=""
max_threads=""
reps=10000
seed_core=0
backoff_max=1024
ccbench=./ccbench
output_dir=results/cas_backoff
dry_run=0

total_cores=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --thread-counts)
      thread_counts="$2"; shift 2 ;;
    --max-threads)
      max_threads="$2"; shift 2 ;;
    --reps)
      reps="$2"; shift 2 ;;
    --seed-core)
      seed_core="$2"; shift 2 ;;
    --backoff-max)
      backoff_max="$2"; shift 2 ;;
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

if [[ "$seed_core" -lt 0 ]]; then
  echo "CAS_UNTIL_SUCCESS requires a seed core; overriding --seed-core to 0." >&2
  seed_core=0
fi

if [[ "$seed_core" -ge "$total_cores" ]]; then
  echo "--seed-core ($seed_core) must be less than total cores ($total_cores)." >&2
  exit 1
fi

if [[ ! -x "$ccbench" ]]; then
  echo "ccbench binary not found or not executable: $ccbench" >&2
  exit 1
fi

mkdir -p "$output_dir/logs"

IFS=',' read -r -a thread_list <<<"$thread_counts"

progress_csv="$output_dir/progress.csv"
printf "run_id,mode,threads,reps,backoff_max,thread_id,core,wins\n" >"$progress_csv"

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

parse_wins() {
  local log_file="$1"
  local run_label="$2"
  local mode="$3"
  local threads="$4"
  local reps="$5"
  local backoff="$6"

  awk -v run_id="$run_label" \
      -v mode="$mode" \
      -v threads="$threads" \
      -v reps="$reps" \
      -v backoff="$backoff" \
      -v out_csv="$progress_csv" '
    /wins$/ {
      if (match($0, /thread[[:space:]]+([0-9]+)[^0-9]+thread ID[[:space:]]+([0-9]+)[^0-9]+([0-9]+)[[:space:]]+wins$/, m)) {
        core = m[1]
        thread = m[2]
        wins = m[3]
        printf "%s,%s,%s,%s,%s,%s,%s,%s\n", \
          run_id, mode, threads, reps, backoff, thread, core, wins \
          >> out_csv
      }
    }
  ' "$log_file"
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

run_id=0
for threads in "${thread_list[@]}"; do
  if [[ "$threads" -lt 1 ]]; then
    echo "Thread counts must be >= 1" >&2
    exit 1
  fi
  cores=$(make_cores "$threads")
  tests_list=$(make_list "$threads" 34)

  for mode in baseline backoff; do
    run_id=$((run_id + 1))
    log_file="$output_dir/logs/run_${run_id}_t${threads}_${mode}.log"
    cmd=("$ccbench" -r "$reps" -t "$tests_list" -x "$cores")
    if [[ "$seed_core" -ge 0 ]]; then
      cmd+=(-b "$seed_core")
    fi
    if [[ "$mode" == "backoff" ]]; then
      cmd+=(--backoff --backoff-max "$backoff_max")
    fi
    if [[ "$dry_run" -eq 1 ]]; then
      run_cmd "${cmd[@]}"
      continue
    fi
    run_cmd "${cmd[@]}" | tee "$log_file"
    parse_wins "$log_file" "$run_id" "$mode" "$threads" "$reps" "$backoff_max"
  done
done

if [[ "$dry_run" -eq 0 ]]; then
  printf '\nCompleted. CSV:\n'
  printf '  %s\n' "$progress_csv"
fi
