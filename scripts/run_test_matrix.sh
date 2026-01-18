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

if [[ -z "$max_threads" ]]; then
  if command -v nproc >/dev/null 2>&1; then
    max_threads=$(nproc --all)
  else
    max_threads=1
  fi
fi

if [[ "$max_threads" -lt 1 ]]; then
  echo "--max-threads must be >= 1" >&2
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

run_cmd() {
  local cmd="$1"
  if [[ "$dry_run" -eq 1 ]]; then
    echo "$cmd"
  else
    echo "Running: $cmd"
    eval "$cmd"
  fi
}

for ((threads=1; threads<=max_threads; threads++)); do
  cores=$(make_cores "$threads")
  for ((test_id=0; test_id<test_count; test_id++)); do
    tests=$(make_list "$threads" "$test_id")
    cmd="$ccbench -r $reps -t \"$tests\" -x \"$cores\""
    if [[ "$seed_core" -ge 0 ]]; then
      cmd+=" -b $seed_core"
    fi
    run_cmd "$cmd"
  done
done
