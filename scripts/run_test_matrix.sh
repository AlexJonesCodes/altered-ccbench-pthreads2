#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/run_test_matrix.sh [options]

Run a matrix of ccbench tests across thread counts and test IDs.

Options:
  --max-threads N   Maximum worker threads to use (default: all available)
  --reps N          Repetitions per run (default: 10000)
  --seed-core N     Seed core for priming (default: 0, skipped from workers)
  --ccbench PATH    Path to ccbench binary (default: ./ccbench)
  --dry-run         Print commands without running them
  -h, --help        Show this help

Examples:
  scripts/run_test_matrix.sh --max-threads 8 --reps 5000
  scripts/run_test_matrix.sh --seed-core -1
EOF
}

max_threads=""
reps=10000
seed_core=0
ccbench=./ccbench
dry_run=0
total_cores=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-threads)
      max_threads="$2"; shift 2 ;;
    --reps)
      reps="$2"; shift 2 ;;
    --seed-core)
      seed_core="$2"; shift 2 ;;
    --ccbench)
      ccbench="$2"; shift 2 ;;
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

if [[ -z "$max_threads" ]]; then
  max_threads="$total_cores"
fi

if [[ "$max_threads" -lt 1 ]]; then
  echo "--max-threads must be >= 1" >&2
  exit 1
fi

if [[ "$seed_core" -ge 0 && "$seed_core" -ge "$total_cores" ]]; then
  echo "--seed-core ($seed_core) must be less than total cores ($total_cores)." >&2
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

test_count=$(awk '
  /const char\* moesi_type_des\[/ {in_arr=1; next}
  in_arr && /\};/ {print count; exit}
  in_arr && /"/ {count++}
' include/ccbench.h)

if [[ -z "$test_count" || "$test_count" -lt 1 ]]; then
  echo "Failed to detect test IDs from include/ccbench.h" >&2
  exit 1
fi

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
  local start=0
  local list=""
  local core=0

  while [[ "$start" -lt "$count" ]]; do
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
    start=$((start + 1))
  done
  printf '[%s]' "$list"
}

print_key_stats() {
  cat <<'EOF'
Key stats to look for in ccbench output:
  - "Summary : mean avg ..." (overall latency summary)
  - "Common-start latency (B4 -> success)..." (contention timing)
  - "wins" lines (which thread/role won each repetition)
EOF
}

run_cmd() {
  local -a cmd=("$@")
  local start_ns end_ns duration_ns duration_ms status
  if [[ "$dry_run" -eq 1 ]]; then
    printf '%q ' "${cmd[@]}"
    printf '\n'
    LAST_DURATION_NS=0
    return 0
  fi
  printf 'Running: '
  printf '%q ' "${cmd[@]}"
  printf '\n'
  print_key_stats
  start_ns=$(date +%s%N)
  set +e
  "${cmd[@]}"
  status=$?
  set -e
  end_ns=$(date +%s%N)
  duration_ns=$((end_ns - start_ns))
  duration_ms=$((duration_ns / 1000000))
  printf 'Run duration: %s ms\n' "$duration_ms"
  LAST_DURATION_NS="$duration_ns"
  return "$status"
}

available_workers=$total_cores
if [[ "$seed_core" -ge 0 ]]; then
  available_workers=$((total_cores - 1))
fi

if [[ "$available_workers" -lt 1 ]]; then
  echo "No worker cores available after reserving seed core $seed_core." >&2
  exit 1
fi

if [[ "$max_threads" -gt "$available_workers" ]]; then
  echo "Adjusting --max-threads from $max_threads to $available_workers (available worker cores)." >&2
  max_threads="$available_workers"
fi

total_runs=$((max_threads * test_count))
run_index=0
success_runs=0
failed_runs=0
total_duration_ns=0
min_duration_ns=0
max_duration_ns=0
LAST_DURATION_NS=0

printf 'Planned runs: %d (tests=%d, threads=1..%d)\n' "$total_runs" "$test_count" "$max_threads"
printf 'Total cores: %d | Worker cores: %d | Seed core: %s\n' \
  "$total_cores" "$available_workers" "$seed_core"

for ((threads=1; threads<=max_threads; threads++)); do
  cores=$(make_cores "$threads")
  for ((test_id=0; test_id<test_count; test_id++)); do
    run_index=$((run_index + 1))
    remaining=$((total_runs - run_index))
    printf '\n[%d/%d] threads=%d test_id=%d remaining=%d\n' \
      "$run_index" "$total_runs" "$threads" "$test_id" "$remaining"
    tests=$(make_list "$threads" "$test_id")
    cmd=("$ccbench" -r "$reps" -t "$tests" -x "$cores")
    if [[ "$seed_core" -ge 0 ]]; then
      cmd+=(-b "$seed_core")
    fi
    if run_cmd "${cmd[@]}"; then
      success_runs=$((success_runs + 1))
    else
      failed_runs=$((failed_runs + 1))
    fi
    total_duration_ns=$((total_duration_ns + LAST_DURATION_NS))
    if [[ "$min_duration_ns" -eq 0 || "$LAST_DURATION_NS" -lt "$min_duration_ns" ]]; then
      min_duration_ns="$LAST_DURATION_NS"
    fi
    if [[ "$LAST_DURATION_NS" -gt "$max_duration_ns" ]]; then
      max_duration_ns="$LAST_DURATION_NS"
    fi
  done
done

if [[ "$dry_run" -eq 0 ]]; then
  avg_duration_ns=$((total_duration_ns / total_runs))
  printf '\n===== Overall Summary =====\n'
  printf 'Total runs           : %d\n' "$total_runs"
  printf 'Successful runs      : %d\n' "$success_runs"
  printf 'Failed runs          : %d\n' "$failed_runs"
  if [[ "$total_runs" -gt 0 ]]; then
    success_rate=$((success_runs * 100 / total_runs))
    printf 'Success rate         : %d%%\n' "$success_rate"
  fi
  printf 'Threads tested       : 1..%d\n' "$max_threads"
  printf 'Tests per thread set : %d\n' "$test_count"
  printf 'Total elapsed (ms)   : %d\n' "$((total_duration_ns / 1000000))"
  printf 'Avg run (ms)         : %d\n' "$((avg_duration_ns / 1000000))"
  printf 'Min run (ms)         : %d\n' "$((min_duration_ns / 1000000))"
  printf 'Max run (ms)         : %d\n' "$((max_duration_ns / 1000000))"
fi
