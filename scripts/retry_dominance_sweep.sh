#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/retry_dominance_sweep.sh --cores <list> [options]

Required:
  --cores <list>         Comma-separated core list (e.g., 0,1,2,3)

Options:
  --reps <int>           Repetitions per run (default: 10000)
  --levels <int>         Number of backoff levels to generate (default: 3)
  --level-max <int>      Max backoff for generated levels (default: 1024)
  --level-values <list>  Comma-separated backoff max values (overrides --levels)
  --seed-cores <list>    Comma-separated seed-core list (excluded from workers)
  --results-dir <path>   Results directory (default: results/retry_dominance)
  --ccbench <path>       Path to ccbench binary (default: ./ccbench)
  -h, --help             Show this help

Notes:
  - Rotates the seed (pinned) core across the provided list, but excludes it from workers.
  - Rotates backoff levels across threads so each thread experiences each level.
  - Uses CAS_UNTIL_SUCCESS (test 34).
EOF
}

cores_input=""
reps=10000
levels=3
level_max=1024
level_values_input=""
seed_cores_input=""
results_dir="results/retry_dominance"
ccbench="./ccbench"
test_id=34

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
    --level-values)
      level_values_input="$2"
      shift 2
      ;;
    --seed-cores)
      seed_cores_input="$2"
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

declare -a seed_cores
if [[ -n "$seed_cores_input" ]]; then
  seed_cores_input="${seed_cores_input//[\[\] ]/}"
  IFS=',' read -r -a seed_cores <<< "$seed_cores_input"
else
  seed_cores=("${cores[@]}")
fi

if [[ ${#seed_cores[@]} -lt 1 ]]; then
  echo "Need at least one seed core." >&2
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
  for ((i=0; i<levels; i++)); do
    val=$((1 << i))
    if [[ "$val" -gt "$level_max" ]]; then
      val="$level_max"
    fi
    level_values+=("$val")
  done
fi

mkdir -p "$results_dir"

if [[ ! -x "$ccbench" ]]; then
  echo "ccbench binary not found at $ccbench, running make..." >&2
  make
fi

run_id=0

declare -A level_win_sum
declare -A level_count

for lvl in "${level_values[@]}"; do
  level_win_sum["$lvl"]=0
  level_count["$lvl"]=0
done

summary_csv="${results_dir}/summary_by_level.csv"
runs_csv="${results_dir}/runs.csv"
run_summary_csv="${results_dir}/run_summary.csv"
echo "level,max_backoff,avg_wins_per_thread,avg_win_share" > "$summary_csv"
echo "run_id,seed_core,rotation,thread_id,core,backoff_max,wins,attempts,failures,successes,retries_per_success,duration_s" > "$runs_csv"
echo "run_id,seed_core,rotation,num_threads,duration_s,spearman_backoff_wins,spearman_retries_per_success_wins,total_wins,wins_per_s" > "$run_summary_csv"

num_levels=${#level_values[@]}

for seed_core in "${seed_cores[@]}"; do
  worker_cores=()
  for core in "${cores[@]}"; do
    if [[ "$core" != "$seed_core" ]]; then
      worker_cores+=("$core")
    fi
  done
  if [[ ${#worker_cores[@]} -lt 1 ]]; then
    echo "Seed core ${seed_core} leaves no worker cores." >&2
    exit 1
  fi
  num_threads=${#worker_cores[@]}
  core_array="[$(IFS=','; echo "${worker_cores[*]}")]"

  for ((rotation=0; rotation<num_threads; rotation++)); do
    declare -a backoff_assignment=()
    for ((i=0; i<num_threads; i++)); do
      level_idx=$(( (i + rotation) % num_levels ))
      backoff_assignment+=("${level_values[$level_idx]}")
    done

    backoff_array="[$(IFS=','; echo "${backoff_assignment[*]}")]"
    log_file="${results_dir}/run_seed${seed_core}_rot${rotation}.log"

    start_ns=$(date +%s%N)
    "$ccbench" -x "$core_array" -t "[$test_id]" -r "$reps" -b "$seed_core" -A "$backoff_array" > "$log_file"
    end_ns=$(date +%s%N)
    duration_ns=$((end_ns - start_ns))
    duration_s=$(awk -v ns="$duration_ns" 'BEGIN { printf "%.6f", ns / 1000000000 }')

    declare -A thread_wins=()
    while read -r tid wins; do
      thread_wins["$tid"]="$wins"
    done < <(awk 'match($0, /thread ID ([0-9]+).*: ([0-9]+) wins/, m) { print m[1], m[2]; }' "$log_file")

    declare -A thread_attempts=()
    declare -A thread_failures=()
    declare -A thread_successes=()
    while read -r tid attempts failures successes; do
      thread_attempts["$tid"]="$attempts"
      thread_failures["$tid"]="$failures"
      thread_successes["$tid"]="$successes"
    done < <(awk 'match($0, /thread ID ([0-9]+).*attempts ([0-9]+) failures ([0-9]+) successes ([0-9]+)/, m) { print m[1], m[2], m[3], m[4]; }' "$log_file")

    wins_total=0
    temp_data=$(mktemp)
    for ((i=0; i<num_threads; i++)); do
      tid="$i"
      wins="${thread_wins[$tid]:-0}"
      attempts="${thread_attempts[$tid]:-0}"
      failures="${thread_failures[$tid]:-0}"
      successes="${thread_successes[$tid]:-0}"
      retries_per_success="NA"
      if [[ "$successes" -gt 0 ]]; then
        retries_per_success=$(awk -v f="$failures" -v s="$successes" 'BEGIN { printf "%.6f", f / s }')
      fi
      level="${backoff_assignment[$i]}"
      level_win_sum["$level"]=$((level_win_sum["$level"] + wins))
      level_count["$level"]=$((level_count["$level"] + 1))
      wins_total=$((wins_total + wins))
      echo "${level},${wins},${retries_per_success}" >> "$temp_data"
      echo "${run_id},${seed_core},${rotation},${tid},${worker_cores[$i]},${level},${wins},${attempts},${failures},${successes},${retries_per_success},${duration_s}" >> "$runs_csv"
    done

    spearman_backoff_wins="NA"
    spearman_retries_wins="NA"
    if [[ -s "$temp_data" ]]; then
      read -r spearman_backoff_wins spearman_retries_wins < <(
        python3 - "$temp_data" <<'PY'
import csv
import math
import sys

path = sys.argv[1]
rows = []
with open(path, newline="") as f:
    for row in csv.reader(f):
        if len(row) < 3:
            continue
        backoff, wins, retries = row[0], row[1], row[2]
        if not backoff or not wins:
            continue
        rows.append((float(backoff), float(wins), None if retries == "NA" else float(retries)))

def rankdata(values):
    sorted_vals = sorted((v, i) for i, v in enumerate(values))
    ranks = [0.0] * len(values)
    i = 0
    while i < len(sorted_vals):
        j = i
        while j + 1 < len(sorted_vals) and sorted_vals[j + 1][0] == sorted_vals[i][0]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[sorted_vals[k][1]] = avg_rank
        i = j + 1
    return ranks

def spearman(xs, ys):
    if len(xs) < 2:
        return None
    rx = rankdata(xs)
    ry = rankdata(ys)
    mx = sum(rx) / len(rx)
    my = sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    denx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    deny = math.sqrt(sum((b - my) ** 2 for b in ry))
    if denx == 0 or deny == 0:
        return None
    return num / (denx * deny)

backoff = [r[0] for r in rows]
wins = [r[1] for r in rows]
retries = [r[2] for r in rows if r[2] is not None]
retries_wins = [r[1] for r in rows if r[2] is not None]

sbw = spearman(backoff, wins)
srw = spearman(retries, retries_wins) if retries else None

def out(v):
    return "NA" if v is None else f"{v:.6f}"

print(out(sbw), out(srw))
PY
      )
    fi
    rm -f "$temp_data"

    wins_per_s="NA"
    if awk -v d="$duration_s" 'BEGIN { exit !(d > 0) }'; then
      wins_per_s=$(awk -v w="$wins_total" -v d="$duration_s" 'BEGIN { printf "%.6f", w / d }')
    fi
    echo "${run_id},${seed_core},${rotation},${num_threads},${duration_s},${spearman_backoff_wins},${spearman_retries_wins},${wins_total},${wins_per_s}" >> "$run_summary_csv"

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
