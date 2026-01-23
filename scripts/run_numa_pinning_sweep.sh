#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/run_numa_pinning_sweep.sh [options]

Run a NUMA/pinning sweep and dump per-thread wins to CSV.

Scenarios:
  - single_socket_local: threads on socket 0 (even CPUs), memory on socket 0
  - split_socket_seed0: threads split across sockets, memory on socket 0
  - split_socket_interleaved: threads split across sockets, interleaved memory (if numactl)

Options:
  --threads N           Thread count (default: all cores)
  --reps N              Repetitions per run (default: 10000)
  --seed-core N         Seed core for contended runs (default: 0, -1 disables)
  --ccbench PATH        Path to ccbench binary (default: ./ccbench)
  --output-dir DIR      Output directory for logs/CSVs (default: results/numa_sweep)
  --dry-run             Print commands without running them
  -h, --help            Show this help
USAGE
}

threads=""
reps=10000
seed_core=0
ccbench=./ccbench
output_dir=results/numa_sweep
dry_run=0

total_cores=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --threads)
      threads="$2"; shift 2 ;;
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

if [[ -z "$threads" ]]; then
  threads="$total_cores"
fi

if [[ "$threads" -lt 1 ]]; then
  echo "--threads must be >= 1" >&2
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

mkdir -p "$output_dir/logs"

progress_csv="$output_dir/progress.csv"
printf "run_id,scenario,mem_policy,threads,reps,thread_id,core,wins\n" >"$progress_csv"

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

make_even_cores() {
  local count="$1"
  local list=""
  local used=0
  for ((core=0; core<total_cores && used<count; core++)); do
    if (( core % 2 == 0 )); then
      if [[ -n "$list" ]]; then
        list+=","${core}
      else
        list=${core}
      fi
      used=$((used + 1))
    fi
  done
  if [[ "$used" -lt "$count" ]]; then
    echo "Not enough even-numbered cores to allocate $count workers." >&2
    exit 1
  fi
  printf '[%s]' "$list"
}

make_split_cores() {
  local count="$1"
  local list=""
  local used=0
  local even=0
  local odd=1
  while [[ "$used" -lt "$count" ]]; do
    local core
    if (( used % 2 == 0 )); then
      core="$even"
      even=$((even + 2))
    else
      core="$odd"
      odd=$((odd + 2))
    fi
    if [[ "$core" -ge "$total_cores" ]]; then
      echo "Not enough cores to allocate $count workers." >&2
      exit 1
    fi
    if [[ -n "$list" ]]; then
      list+=","${core}
    else
      list=${core}
    fi
    used=$((used + 1))
  done
  printf '[%s]' "$list"
}

parse_wins() {
  local log_file="$1"
  local run_label="$2"
  local scenario="$3"
  local mem_policy="$4"
  local threads="$5"
  local reps="$6"

  awk -v run_id="$run_label" \
      -v scenario="$scenario" \
      -v mem_policy="$mem_policy" \
      -v threads="$threads" \
      -v reps="$reps" \
      -v out_csv="$progress_csv" '
    {
      sub(/\r$/, "");
    }
    /wins/ {
      if (match($0, /thread[[:space:]]+([0-9]+)[^0-9]+thread ID[[:space:]]+([0-9]+):[[:space:]]+([0-9]+)[[:space:]]+wins/, m)) {
        core = m[1]
        thread = m[2]
        wins = m[3]
        printf "%s,%s,%s,%s,%s,%s,%s,%s\n", \
          run_id, scenario, mem_policy, threads, reps, thread, core, wins \
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

tests_list=$(make_list "$threads" 33)
numactl_bin=""
if command -v numactl >/dev/null 2>&1; then
  numactl_bin="numactl"
fi

run_id=0

scenario="single_socket_local"
cores=$(make_even_cores "$threads")
run_id=$((run_id + 1))
log_file="$output_dir/logs/run_${run_id}_${scenario}.log"
cmd=("$ccbench" -r "$reps" -t "$tests_list" -x "$cores")
if [[ "$seed_core" -ge 0 ]]; then
  cmd+=(-b "$seed_core")
fi
if [[ "$dry_run" -eq 1 ]]; then
  run_cmd "${cmd[@]}"
else
  run_cmd "${cmd[@]}" | tee "$log_file"
  parse_wins "$log_file" "$run_id" "$scenario" "local_socket0" "$threads" "$reps"
fi

scenario="split_socket_seed0"
cores=$(make_split_cores "$threads")
run_id=$((run_id + 1))
log_file="$output_dir/logs/run_${run_id}_${scenario}.log"
cmd=("$ccbench" -r "$reps" -t "$tests_list" -x "$cores")
if [[ "$seed_core" -ge 0 ]]; then
  cmd+=(-b "$seed_core")
fi
if [[ "$dry_run" -eq 1 ]]; then
  run_cmd "${cmd[@]}"
else
  run_cmd "${cmd[@]}" | tee "$log_file"
  parse_wins "$log_file" "$run_id" "$scenario" "remote_socket0" "$threads" "$reps"
fi

scenario="split_socket_interleaved"
cores=$(make_split_cores "$threads")
run_id=$((run_id + 1))
log_file="$output_dir/logs/run_${run_id}_${scenario}.log"
cmd=("$ccbench" -r "$reps" -t "$tests_list" -x "$cores" -n)
if [[ "$seed_core" -ge 0 ]]; then
  cmd+=(-b "$seed_core")
fi
if [[ "$dry_run" -eq 1 ]]; then
  if [[ -n "$numactl_bin" ]]; then
    run_cmd "$numactl_bin" --interleave=all "${cmd[@]}"
  else
    run_cmd "${cmd[@]}"
  fi
else
  if [[ -n "$numactl_bin" ]]; then
    run_cmd "$numactl_bin" --interleave=all "${cmd[@]}" | tee "$log_file"
  else
    run_cmd "${cmd[@]}" | tee "$log_file"
  fi
  parse_wins "$log_file" "$run_id" "$scenario" "interleave_all" "$threads" "$reps"
fi

if [[ "$dry_run" -eq 0 ]]; then
  printf '\nCompleted. CSV:\n'
  printf '  %s\n' "$progress_csv"
fi
