#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/run_backoff_retry_windows.sh --cores <list> [options]

Collect per-thread wins plus CAS_UNTIL_SUCCESS attempt/failure stats, optionally
in time windows (multiple short runs). Produces CSVs only; no plotting.

Required:
  --cores LIST           Comma-separated core list (e.g., "0,1,2,3")

Options:
  --reps N               Total repetitions per configuration (default: 10000)
  --window N             Window size in repetitions (default: 0 = single run)
  --backoff-max LIST     Comma-separated backoff max values (default: "0,64,256")
                         Use 0 for baseline (no backoff).
  --bully-thread ID      Set one thread to a different backoff (uses -A array)
  --bully-backoff N      Backoff max for bully thread (default: 0)
  --others-backoff N     Backoff max for all other threads (default: 256)
  --seed-core N          Fixed seed core for contended runs (disables rotation)
  --seed-cores LIST      Comma-separated seed cores to sweep (overrides --seed-core)
  --test-id N            Test id (default: 34 = CAS_UNTIL_SUCCESS)
  --stride N             Stride size (default: 1)
  --flush                Flush cache line before each rep (default: off)
  --ccbench PATH         Path to ccbench binary (default: ./ccbench)
  --results-dir DIR      Output directory (default: results/backoff_retry_windows)
  --dry-run              Print commands without running them
  -h, --help             Show this help

Outputs:
  <results-dir>/retries.csv
  <results-dir>/wins_windows.csv

Examples:
  scripts/run_backoff_retry_windows.sh \
    --cores "2,3,4,5" \
    --reps 20000 \
    --window 1000 \
    --seed-cores "2,3,4,5" \
    --backoff-max "0,64,256" \
    --results-dir results/backoff_retry_windows

  # Bully run: thread 0 has no backoff, others use 256
  scripts/run_backoff_retry_windows.sh \
    --cores "2,3,4,5" \
    --seed-cores "2,3,4,5" \
    --bully-thread 0 \
    --bully-backoff 0 \
    --others-backoff 256 \
    --reps 20000 \
    --window 1000 \
    --results-dir results/backoff_retry_windows_bully
USAGE
}

cores_input=""
reps=10000
window=0
backoff_max_input="0,64,256"
seed_core=""
seed_cores_input=""
test_id=34
stride=1
flush=0
ccbench="./ccbench"
results_dir="results/backoff_retry_windows"
dry_run=0
bully_thread=""
bully_backoff=0
others_backoff=256
bully_mode=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cores)
      cores_input="$2"; shift 2 ;;
    --reps)
      reps="$2"; shift 2 ;;
    --window)
      window="$2"; shift 2 ;;
    --backoff-max)
      backoff_max_input="$2"; shift 2 ;;
    --seed-core)
      seed_core="$2"; shift 2 ;;
    --seed-cores)
      seed_cores_input="$2"; shift 2 ;;
    --bully-thread)
      bully_thread="$2"; shift 2 ;;
    --bully-backoff)
      bully_backoff="$2"; shift 2 ;;
    --others-backoff)
      others_backoff="$2"; shift 2 ;;
    --test-id)
      test_id="$2"; shift 2 ;;
    --stride)
      stride="$2"; shift 2 ;;
    --flush)
      flush=1; shift ;;
    --ccbench)
      ccbench="$2"; shift 2 ;;
    --results-dir)
      results_dir="$2"; shift 2 ;;
    --dry-run)
      dry_run=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1 ;;
  esac
done

if [[ -z "$cores_input" ]]; then
  echo "Missing --cores" >&2
  usage >&2
  exit 1
fi

cores_input="${cores_input//[\[\] ]/}"
IFS=',' read -r -a cores <<< "$cores_input"
if [[ ${#cores[@]} -lt 2 ]]; then
  echo "Need at least two cores for contention." >&2
  exit 1
fi

seed_cores=()
if [[ -n "$seed_cores_input" ]]; then
  seed_cores_input="${seed_cores_input//[\[\] ]/}"
  IFS=',' read -r -a seed_cores <<< "$seed_cores_input"
elif [[ -n "$seed_core" ]]; then
  seed_cores=("$seed_core")
else
  seed_cores=("${cores[@]}")
fi

if [[ ${#seed_cores[@]} -lt 1 ]]; then
  echo "Seed core list must not be empty." >&2
  exit 1
fi

if [[ "$reps" -lt 1 ]]; then
  echo "--reps must be >= 1" >&2
  exit 1
fi

if [[ "$window" -lt 0 ]]; then
  echo "--window must be >= 0" >&2
  exit 1
fi

backoff_max_input="${backoff_max_input//[\[\] ]/}"
IFS=',' read -r -a backoff_levels <<< "$backoff_max_input"
if [[ ${#backoff_levels[@]} -lt 1 ]]; then
  echo "--backoff-max must include at least one value." >&2
  exit 1
fi

if [[ ! -x "$ccbench" ]]; then
  if [[ "$dry_run" -eq 1 ]]; then
    echo "ccbench binary not found at $ccbench; dry-run mode, skipping build." >&2
  else
    echo "ccbench binary not found at $ccbench, running make..." >&2
    make
  fi
fi

mkdir -p "$results_dir/logs"

core_array="[$(IFS=','; echo "${cores[*]}")]"
num_threads=${#cores[@]}

if [[ -n "$bully_thread" ]]; then
  bully_mode=1
  if [[ "$bully_thread" -lt 0 || "$bully_thread" -ge "$num_threads" ]]; then
    echo "--bully-thread must be in range [0, ${num_threads}-1]." >&2
    exit 1
  fi
  if [[ "$bully_backoff" -lt 0 || "$others_backoff" -lt 0 ]]; then
    echo "Bully backoff values must be >= 0." >&2
    exit 1
  fi
  backoff_levels=("$others_backoff")
fi

retries_csv="${results_dir}/retries.csv"
wins_csv="${results_dir}/wins_windows.csv"

printf "run_id,window_id,backoff_max,seed_core,threads,window_reps,bully_thread,bully_backoff,others_backoff,thread_id,core,attempts,successes,failures,failure_rate,wins\n" > "$retries_csv"
printf "run_id,window_id,backoff_max,seed_core,threads,window_reps,bully_thread,bully_backoff,others_backoff,thread_id,core,wins\n" > "$wins_csv"

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

windows=1
if [[ "$window" -gt 0 ]]; then
  windows=$(( (reps + window - 1) / window ))
fi

run_id=0
for seed in "${seed_cores[@]}"; do
  for backoff_max in "${backoff_levels[@]}"; do
    if [[ "$backoff_max" -lt 0 ]]; then
      echo "--backoff-max values must be >= 0" >&2
      exit 1
    fi
    for ((window_id=1; window_id<=windows; window_id++)); do
    if [[ "$window" -gt 0 ]]; then
      window_reps=$((reps - (window * (window_id - 1))))
      if [[ "$window_reps" -gt "$window" ]]; then
        window_reps="$window"
      fi
    else
      window_reps="$reps"
    fi

    run_id=$((run_id + 1))
    log_file="${results_dir}/logs/run_${run_id}_b${backoff_max}_w${window_id}.log"

    cmd=("$ccbench" -x "$core_array" -t "[$test_id]" -r "$window_reps" -b "$seed" -s "$stride" -F)
    if [[ -n "$bully_thread" ]]; then
      backoff_array=""
      for ((i=0; i<num_threads; i++)); do
        value="$others_backoff"
        if [[ "$i" -eq "$bully_thread" ]]; then
          value="$bully_backoff"
        fi
        if [[ -n "$backoff_array" ]]; then
          backoff_array+=",${value}"
        else
          backoff_array="${value}"
        fi
      done
      cmd+=(-A "[${backoff_array}]")
    elif [[ "$backoff_max" -gt 0 ]]; then
      cmd+=(-B -M "$backoff_max")
    fi
    if [[ "$flush" -eq 1 ]]; then
      cmd+=(-f)
    fi

    if [[ "$dry_run" -eq 1 ]]; then
      run_cmd "${cmd[@]}"
      continue
    fi

    run_cmd "${cmd[@]}" | tee "$log_file"

    declare -A wins_by_thread=()
    declare -A core_by_thread=()
    while read -r tid core wins; do
      wins_by_thread["$tid"]="$wins"
      core_by_thread["$tid"]="$core"
    done < <(awk '
      match($0, /on thread ([0-9]+) \(thread ID ([0-9]+)\): ([0-9]+) wins/, m) {
        print m[2], m[1], m[3]; next
      }
      match($0, /thread ID ([0-9]+) \(core ([0-9]+)\): ([0-9]+) wins/, m) {
        print m[1], m[2], m[3];
      }
    ' "$log_file")

    declare -A attempts_by_thread=()
    declare -A successes_by_thread=()
    declare -A failures_by_thread=()
    declare -A failrate_by_thread=()
    while read -r tid core att succ fail rate; do
      attempts_by_thread["$tid"]="$att"
      successes_by_thread["$tid"]="$succ"
      failures_by_thread["$tid"]="$fail"
      failrate_by_thread["$tid"]="$rate"
      if [[ -z "${core_by_thread[$tid]:-}" ]]; then
        core_by_thread["$tid"]="$core"
      fi
    done < <(awk '
      /^Atomic failure stats \(CAS_UNTIL_SUCCESS\):/ {in_block=1; next}
      /^Atomic failure stats \(/ {in_block=0}
      in_block && match($0, /thread ID ([0-9]+) \(core ([0-9]+)\): attempts ([0-9]+), successes ([0-9]+), failures ([0-9]+), failure rate ([0-9.]+)/, m) {
        print m[1], m[2], m[3], m[4], m[5], m[6];
      }
    ' "$log_file")

    for ((tid=0; tid<num_threads; tid++)); do
      core="${core_by_thread[$tid]:-${cores[$tid]}}"
      wins="${wins_by_thread[$tid]:-0}"
      att="${attempts_by_thread[$tid]:-0}"
      succ="${successes_by_thread[$tid]:-0}"
      fail="${failures_by_thread[$tid]:-0}"
      rate="${failrate_by_thread[$tid]:-0.0}"
      printf "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n" \
        "$run_id" "$window_id" "$backoff_max" "$seed" "$num_threads" "$window_reps" \
        "${bully_thread:-}" "$bully_backoff" "$others_backoff" \
        "$tid" "$core" "$att" "$succ" "$fail" "$rate" "$wins" \
        >> "$retries_csv"
      printf "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n" \
        "$run_id" "$window_id" "$backoff_max" "$seed" "$num_threads" "$window_reps" \
        "${bully_thread:-}" "$bully_backoff" "$others_backoff" \
        "$tid" "$core" "$wins" \
        >> "$wins_csv"
    done
done
  done
done

if [[ "$dry_run" -eq 0 ]]; then
  printf '\nCompleted. CSV outputs:\n'
  printf '  %s\n' "$retries_csv"
  printf '  %s\n' "$wins_csv"
fi
