#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/retry_dominance_sweep.sh --cores <list> [options]

Required:
  --cores <list>         Comma-separated core list (e.g., 0,1,2,3)

Options:
  --reps <int>           Repetitions per run (default: 10000)
  --levels <int>         Number of backoff levels to generate (default: 4)
  --level-base <int>     Base multiplier for generated levels (default: 8)
  --level-max <int>      Max backoff for generated levels (default: 4096)
  --level-values <list>  Comma-separated backoff max values (overrides --levels)
  --results-dir <path>   Results directory (default: results/retry_dominance)
  --ccbench <path>       Path to ccbench binary (default: ./ccbench)
  --stride <int>         Stride size (default: 1)
  --flush                Flush cache line before each rep (default: off)
  --seed-core <int>      Fixed seed core (disables seed rotation)
  --test-id <int>        Test id to run (default: 34)
  -h, --help             Show this help

Notes:
  - Rotates the seed (pinned) core across the provided list (unless --seed-core is set).
  - Rotates backoff levels across threads so each thread experiences each level.
  - Uses CAS_UNTIL_SUCCESS (test 34) by default.
EOF
}

cores_input=""
reps=10000
levels=4
level_base=8
level_max=4096
level_values_input=""
results_dir="results/retry_dominance"
ccbench="./ccbench"
test_id=34
stride=1
flush=0
seed_core_override=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cores)
      cores_input="$2"
      shift 2
      ;;
    --reps)
      reps="$2"
      shift 2
      ;;
    --levels)
      levels="$2"
      shift 2
      ;;
    --level-max)
      level_max="$2"
      shift 2
      ;;
    --level-base)
      level_base="$2"
      shift 2
      ;;
    --level-values)
      level_values_input="$2"
      shift 2
      ;;
    --results-dir)
      results_dir="$2"
      shift 2
      ;;
    --ccbench)
      ccbench="$2"
      shift 2
      ;;
    --test-id)
      test_id="$2"
      shift 2
      ;;
    --stride)
      stride="$2"
      shift 2
      ;;
    --flush)
      flush=1
      shift
      ;;
    --seed-core)
      seed_core_override="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
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

declare -a level_values
if [[ -n "$level_values_input" ]]; then
  level_values_input="${level_values_input//[\[\] ]/}"
  IFS=',' read -r -a level_values <<< "$level_values_input"
else
  if [[ "$levels" -lt 1 ]]; then
    echo "--levels must be >= 1" >&2
    exit 1
  fi
  if [[ "$level_base" -lt 2 ]]; then
    echo "--level-base must be >= 2" >&2
    exit 1
  fi
  val=1
  for ((i=0; i<levels; i++)); do
    if [[ "$val" -gt "$level_max" ]]; then
      val="$level_max"
    fi
    level_values+=("$val")
    val=$((val * level_base))
  done
fi

mkdir -p "$results_dir"

if [[ ! -x "$ccbench" ]]; then
  echo "ccbench binary not found at $ccbench, running make..." >&2
  make
fi

core_array="[$(IFS=','; echo "${cores[*]}")]"
run_id=0

declare -A level_win_sum
declare -A level_count

for lvl in "${level_values[@]}"; do
  level_win_sum["$lvl"]=0
  level_count["$lvl"]=0
done

summary_csv="${results_dir}/summary_by_level.csv"
runs_csv="${results_dir}/runs.csv"
echo "level,max_backoff,avg_wins_per_thread,avg_win_share" > "$summary_csv"
echo "run_id,seed_core,rotation,thread_id,core,backoff_max,wins" > "$runs_csv"

num_threads=${#cores[@]}
num_levels=${#level_values[@]}

seed_cores=()
if [[ -n "$seed_core_override" ]]; then
  seed_cores=("$seed_core_override")
else
  seed_cores=("${cores[@]}")
fi

for seed_core in "${seed_cores[@]}"; do
  for ((rotation=0; rotation<num_threads; rotation++)); do
    declare -a backoff_assignment=()
    for ((i=0; i<num_threads; i++)); do
      level_idx=$(( (i + rotation) % num_levels ))
      backoff_assignment+=("${level_values[$level_idx]}")
    done

    backoff_array="[$(IFS=','; echo "${backoff_assignment[*]}")]"
    log_file="${results_dir}/run_seed${seed_core}_rot${rotation}.log"

    cmd=("$ccbench" -x "$core_array" -t "[$test_id]" -r "$reps" -b "$seed_core" -A "$backoff_array" -s "$stride")
    if [[ "$flush" -eq 1 ]]; then
      cmd+=(-f)
    fi
    "${cmd[@]}" > "$log_file"

    declare -A thread_wins=()
    while read -r tid wins; do
      thread_wins["$tid"]="$wins"
    done < <(awk 'match($0, /thread ID ([0-9]+).*: ([0-9]+) wins/, m) { print m[1], m[2]; }' "$log_file")

    for ((i=0; i<num_threads; i++)); do
      tid="$i"
      wins="${thread_wins[$tid]:-0}"
      level="${backoff_assignment[$i]}"
      level_win_sum["$level"]=$((level_win_sum["$level"] + wins))
      level_count["$level"]=$((level_count["$level"] + 1))
      echo "${run_id},${seed_core},${rotation},${tid},${cores[$i]},${level},${wins}" >> "$runs_csv"
    done

    run_id=$((run_id + 1))
  done
done

for lvl in "${level_values[@]}"; do
  total_wins="${level_win_sum[$lvl]}"
  total_count="${level_count[$lvl]}"
  if [[ "$total_count" -gt 0 ]]; then
    avg_wins=$(awk -v w="$total_wins" -v c="$total_count" 'BEGIN { printf "%.2f", w / c }')
    avg_share=$(awk -v a="$avg_wins" -v r="$reps" 'BEGIN { printf "%.6f", a / r }')
  else
    avg_wins="0.00"
    avg_share="0.000000"
  fi
  echo "${lvl},${lvl},${avg_wins},${avg_share}" >> "$summary_csv"
done

echo "Done. Results in ${results_dir}"
echo "Summary by backoff level:"
column -t -s, "$summary_csv"
