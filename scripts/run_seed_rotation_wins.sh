#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'HELP'
Usage: scripts/run_seed_rotation_wins.sh [options]

Rotate the seed (pinned) thread across cores and record per-thread wins for
CAS_UNTIL_SUCCESS (test ID 34) under contention.

Options:
  --op NAME              Atomic op name: CAS_UNTIL_SUCCESS (default: CAS_UNTIL_SUCCESS)
  --cores LIST           Core list (e.g., "[0,1,2,3,4]") (required)
  --threads N            Use only the first N cores from --cores (optional)
  --reps N               Repetitions per run (default: 10000)
  --ccbench PATH         Path to ccbench binary (default: ./ccbench)
  --output-dir DIR       Output directory for logs/report (default: results)
  --dry-run              Print commands without running them
  -h, --help             Show this help

Example:
  scripts/run_seed_rotation_wins.sh \
    --op CAS_UNTIL_SUCCESS \
    --cores "[0,1,2,3,16,17]" \
    --threads 4 \
    --reps 20000 \
    --output-dir results_seed_rotation
HELP
}

op="CAS_UNTIL_SUCCESS"
cores=""
threads=""
reps=10000
ccbench=./ccbench
output_dir=results
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --op)
      op="$2"; shift 2 ;;
    --cores)
      cores="$2"; shift 2 ;;
    --threads)
      threads="$2"; shift 2 ;;
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

if [[ -z "$op" || -z "$cores" ]]; then
  echo "--op and --cores are required." >&2
  usage
  exit 1
fi

if [[ ! -x "$ccbench" ]]; then
  echo "ccbench binary not found or not executable: $ccbench" >&2
  exit 1
fi

case "${op^^}" in
  CAS_UNTIL_SUCCESS|34)
    test_id=34 ;;
  *)
    echo "Unsupported --op '${op}'. Use CAS_UNTIL_SUCCESS." >&2
    exit 1 ;;
esac

mkdir -p "$output_dir/logs"

parse_cores() {
  local raw="$1"
  local parsed
  parsed=$(echo "$raw" | tr -d '[]' | tr ',' ' ')
  read -r -a core_array <<<"$parsed"
  if [[ -n "$threads" ]]; then
    if [[ "$threads" -lt 1 || "$threads" -gt "${#core_array[@]}" ]]; then
      echo "--threads must be between 1 and ${#core_array[@]}" >&2
      exit 1
    fi
    core_array=("${core_array[@]:0:$threads}")
  fi
  printf '%s\n' "${core_array[@]}"
}

build_tests_list() {
  local count="$1"
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

format_wins_line() {
  local log_file="$1"
  local skip_thread="$2"
  local thread_count="$3"

  awk -v skip="$skip_thread" -v count="$thread_count" '
    /wins$/ {
      if (match($0, /thread[[:space:]]+([0-9]+)[^0-9]+thread ID[[:space:]]+([0-9]+)[^0-9]+([0-9]+)[[:space:]]+wins$/, m)) {
        wins[m[2]] = m[3]
      } else if (match($0, /thread ID[[:space:]]+([0-9]+):[[:space:]]+([0-9]+)[[:space:]]+wins$/, m)) {
        wins[m[1]] = m[2]
      }
    }
    END {
      first = 1
      for (i = 0; i < count; i++) {
        if (i == skip) continue
        val = wins[i]
        if (val == "") val = 0
        label = "t" (i + 1) ": " val
        if (first) {
          printf "%s", label
          first = 0
        } else {
          printf " %s", label
        }
      }
      printf "\n"
    }
  ' "$log_file"
}

mapfile -t core_array < <(parse_cores "$cores")
thread_count=${#core_array[@]}
if [[ "$thread_count" -lt 1 ]]; then
  echo "No cores provided." >&2
  exit 1
fi

cores_list=$(build_cores_list "${core_array[@]}")
tests_list=$(build_tests_list "$thread_count")

report_file="$output_dir/seed_rotation_wins.txt"
if [[ "$dry_run" -eq 0 ]]; then
  : >"$report_file"
fi

for ((i=0; i<thread_count; i++)); do
  seed_core="${core_array[$i]}"
  log_file="$output_dir/logs/seed_t$((i + 1))_core_${seed_core}_op_${test_id}.log"
  cmd=("$ccbench" -r "$reps" -t "$tests_list" -x "$cores_list" -b "$seed_core")
  if [[ "$dry_run" -eq 1 ]]; then
    run_cmd "${cmd[@]}"
    continue
  fi
  run_cmd "${cmd[@]}" | tee "$log_file" >/dev/null

  {
    printf 'pinned thread: t%d (core %s)\n' "$((i + 1))" "$seed_core"
    printf 'wins per thread:\n'
    format_wins_line "$log_file" "$i" "$thread_count"
    printf '\n'
  } >>"$report_file"

done

if [[ "$dry_run" -eq 0 ]]; then
  printf 'Completed. Report written to: %s\n' "$report_file"
fi
