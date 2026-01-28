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
  --results-dir <path>   Results directory (default: results/retry_dominance)
  --ccbench <path>       Path to ccbench binary (default: ./ccbench)
  -h, --help             Show this help

Notes:
  - Rotates the seed (pinned) core across the provided list.
  - Rotates backoff levels across threads so each thread experiences each level.
  - Uses CAS_UNTIL_SUCCESS (test 34).
EOF
}

cores_input=""
reps=10000
levels=3
level_max=1024
level_values_input=""
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
per_run_csv="${results_dir}/per_run_correlations.csv"
echo "level,max_backoff,avg_wins_per_thread,avg_win_share,avg_retries_per_success,avg_success_prob" > "$summary_csv"
echo "run_id,seed_core,rotation,thread_id,core,backoff_max,wins,attempts,failures,successes,retries_per_success,success_prob" > "$runs_csv"
echo "run_id,seed_core,rotation,duration_s,throughput_reps_per_s,spearman_backoff_wins,spearman_retries_wins,spearman_backoff_wins_excl_seed,spearman_retries_wins_excl_seed" > "$per_run_csv"

num_threads=${#cores[@]}
num_levels=${#level_values[@]}

for seed_core in "${cores[@]}"; do
  for ((rotation=0; rotation<num_threads; rotation++)); do
    declare -a backoff_assignment=()
    for ((i=0; i<num_threads; i++)); do
      level_idx=$(( (i + rotation) % num_levels ))
      backoff_assignment+=("${level_values[$level_idx]}")
    done

    backoff_array="[$(IFS=','; echo "${backoff_assignment[*]}")]"
    log_file="${results_dir}/run_seed${seed_core}_rot${rotation}.log"

    start_ns=$(date +%s%N)
    "$ccbench" -x "$core_array" -t "$test_id" -r "$reps" -b "$seed_core" -A "$backoff_array" > "$log_file"
    end_ns=$(date +%s%N)
    duration_s=$(awk -v s="$start_ns" -v e="$end_ns" 'BEGIN { printf "%.6f", (e - s) / 1000000000 }')
    throughput=$(awk -v r="$reps" -v d="$duration_s" 'BEGIN { if (d > 0) printf "%.3f", r / d; else print "0.000" }')

    declare -A thread_wins=()
    while read -r tid wins; do
      thread_wins["$tid"]="$wins"
    done < <(awk 'match($0, /thread ID ([0-9]+).*: ([0-9]+) wins/, m) { print m[1], m[2]; }' "$log_file")

    declare -A thread_attempts=()
    declare -A thread_failures=()
    declare -A thread_successes=()
    declare -A thread_retry_ps=()
    declare -A thread_success_prob=()
    while read -r tid attempts failures successes retries success_prob; do
      thread_attempts["$tid"]="$attempts"
      thread_failures["$tid"]="$failures"
      thread_successes["$tid"]="$successes"
      thread_retry_ps["$tid"]="$retries"
      thread_success_prob["$tid"]="$success_prob"
    done < <(awk 'match($0, /thread ID ([0-9]+).*attempts ([0-9]+), failures ([0-9]+), successes ([0-9]+), retries_per_success ([0-9.]+), success_prob ([0-9.]+)/, m) { print m[1], m[2], m[3], m[4], m[5], m[6]; }' "$log_file")

    for ((i=0; i<num_threads; i++)); do
      tid="$i"
      wins="${thread_wins[$tid]:-0}"
      level="${backoff_assignment[$i]}"
      attempts="${thread_attempts[$tid]:-0}"
      failures="${thread_failures[$tid]:-0}"
      successes="${thread_successes[$tid]:-0}"
      retries_ps="${thread_retry_ps[$tid]:-0}"
      success_prob="${thread_success_prob[$tid]:-0}"
      level_win_sum["$level"]=$((level_win_sum["$level"] + wins))
      level_count["$level"]=$((level_count["$level"] + 1))
      echo "${run_id},${seed_core},${rotation},${tid},${cores[$i]},${level},${wins},${attempts},${failures},${successes},${retries_ps},${success_prob}" >> "$runs_csv"
    done

    python - "$runs_csv" "$per_run_csv" "$run_id" "$seed_core" "$rotation" "$duration_s" "$throughput" <<'PY'
import csv
import math
import sys

def rankdata(values):
  indexed = list(enumerate(values))
  indexed.sort(key=lambda x: x[1])
  ranks = [0.0] * len(values)
  i = 0
  while i < len(indexed):
    j = i
    while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
      j += 1
    avg_rank = (i + j + 2) / 2.0
    for k in range(i, j + 1):
      ranks[indexed[k][0]] = avg_rank
    i = j + 1
  return ranks

def spearman(x, y):
  if len(x) < 2:
    return ""
  rx = rankdata(x)
  ry = rankdata(y)
  mean_rx = sum(rx) / len(rx)
  mean_ry = sum(ry) / len(ry)
  num = sum((a - mean_rx) * (b - mean_ry) for a, b in zip(rx, ry))
  den_x = math.sqrt(sum((a - mean_rx) ** 2 for a in rx))
  den_y = math.sqrt(sum((b - mean_ry) ** 2 for b in ry))
  if den_x == 0 or den_y == 0:
    return ""
  return num / (den_x * den_y)

runs_csv, per_run_csv, run_id, seed_core, rotation, duration_s, throughput = sys.argv[1:]
rows = []
with open(runs_csv, newline="") as f:
  reader = csv.DictReader(f)
  for row in reader:
    if row["run_id"] == run_id:
      rows.append(row)

def to_float(v):
  try:
    return float(v)
  except ValueError:
    return 0.0

backoff = [to_float(r["backoff_max"]) for r in rows]
wins = [to_float(r["wins"]) for r in rows]
retries = [to_float(r["retries_per_success"]) for r in rows]

spearman_backoff_wins = spearman(backoff, wins)
spearman_retries_wins = spearman(retries, wins)

rows_excl = [r for r in rows if r["core"] != seed_core]
backoff_excl = [to_float(r["backoff_max"]) for r in rows_excl]
wins_excl = [to_float(r["wins"]) for r in rows_excl]
retries_excl = [to_float(r["retries_per_success"]) for r in rows_excl]
spearman_backoff_wins_excl = spearman(backoff_excl, wins_excl)
spearman_retries_wins_excl = spearman(retries_excl, wins_excl)

with open(per_run_csv, "a", newline="") as f:
  writer = csv.writer(f)
  writer.writerow([
    run_id,
    seed_core,
    rotation,
    duration_s,
    throughput,
    "" if spearman_backoff_wins == "" else f"{spearman_backoff_wins:.6f}",
    "" if spearman_retries_wins == "" else f"{spearman_retries_wins:.6f}",
    "" if spearman_backoff_wins_excl == "" else f"{spearman_backoff_wins_excl:.6f}",
    "" if spearman_retries_wins_excl == "" else f"{spearman_retries_wins_excl:.6f}",
  ])
PY

    run_id=$((run_id + 1))
  done
done

for lvl in "${level_values[@]}"; do
  total_wins="${level_win_sum[$lvl]}"
  total_count="${level_count[$lvl]}"
  if [[ "$total_count" -gt 0 ]]; then
    avg_wins=$(awk -v w="$total_wins" -v c="$total_count" 'BEGIN { printf "%.2f", w / c }')
    avg_share=$(awk -v a="$avg_wins" -v r="$reps" 'BEGIN { printf "%.6f", a / r }')
    avg_retries=$(awk -v l="$lvl" -v file="$runs_csv" 'BEGIN { sum=0; n=0 } $0 ~ /^[0-9]/ { split($0, f, ","); if (f[6]==l) { sum+=f[11]; n++ } } END { if (n>0) printf "%.3f", sum/n; else printf "0.000" }' "$runs_csv")
    avg_success_prob=$(awk -v l="$lvl" -v file="$runs_csv" 'BEGIN { sum=0; n=0 } $0 ~ /^[0-9]/ { split($0, f, ","); if (f[6]==l) { sum+=f[12]; n++ } } END { if (n>0) printf "%.6f", sum/n; else printf "0.000000" }' "$runs_csv")
  else
    avg_wins="0.00"
    avg_share="0.000000"
    avg_retries="0.000"
    avg_success_prob="0.000000"
  fi
  echo "${lvl},${lvl},${avg_wins},${avg_share},${avg_retries},${avg_success_prob}" >> "$summary_csv"
done

echo "Done. Results in ${results_dir}"
echo "Summary by backoff level:"
column -t -s, "$summary_csv"
