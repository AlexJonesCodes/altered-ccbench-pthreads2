#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'HELP'
Usage: scripts/run_fixed_winner_bias_rotation.sh [options]

Run a contended atomic op for a fixed time and measure fixed-winner (handoff)
bias while rotating the pinned seed core across the provided cores.

Options:
  --op NAME              Atomic op name: CAS, FAI, TAS, CAS_UNTIL_SUCCESS (default: CAS)
  --cores LIST           Core list (e.g., "[0,1,2,3]") (required)
  --reps N               Repetitions per run (default: 100000)
  --ccbench PATH         Path to ccbench binary (default: ./ccbench)
  --output-dir DIR       Output directory (default: results/fixed_winner_bias)
  --dry-run              Print commands without running them
  -h, --help             Show this help

Outputs (per seed):
  <output-dir>/seed_<core>/winner_sequence.csv
  <output-dir>/seed_<core>/fixed_winner_bias_summary.txt
  <output-dir>/seed_<core>/fixed_winner_bias_per_thread.csv

Outputs (all seeds):
  <output-dir>/fixed_winner_bias_by_seed.csv

Example:
  scripts/run_fixed_winner_bias_rotation.sh \
    --op CAS_UNTIL_SUCCESS \
    --cores "[0,1,2,3,4,5,6,7]" \
    --reps 200000 \
    --output-dir results/fixed_winner_bias
HELP
}

op="CAS"
cores=""
reps=100000
ccbench=./ccbench
output_dir=results/fixed_winner_bias
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --op)
      op="$2"; shift 2 ;;
    --cores)
      cores="$2"; shift 2 ;;
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

if [[ -z "$cores" ]]; then
  echo "--cores is required." >&2
  usage
  exit 1
fi

if [[ ! -x "$ccbench" ]]; then
  echo "ccbench binary not found or not executable: $ccbench" >&2
  exit 1
fi

if ! "$ccbench" --help 2>/dev/null | grep -q "winner-seq"; then
  echo "ccbench does not support --winner-seq; rebuild ccbench with winner-seq support." >&2
  exit 1
fi

normalize_op() {
  local raw="${1^^}"
  case "$raw" in
    CAS|12)
      echo "CAS:12" ;;
    FAI|13)
      echo "FAI:13" ;;
    TAS|14)
      echo "TAS:14" ;;
    CAS_UNTIL_SUCCESS|34)
      echo "CAS_UNTIL_SUCCESS:34" ;;
    *)
      return 1 ;;
  esac
}

if ! op_desc=$(normalize_op "$op"); then
  echo "Unknown --op value: $op" >&2
  exit 1
fi

op_name=${op_desc%%:*}
op_id=${op_desc##*:}

parse_cores() {
  local raw="$1"
  local parsed
  parsed=$(echo "$raw" | tr -d '[]' | tr ',' ' ')
  read -r -a core_array <<<"$parsed"
  printf '%s\n' "${core_array[@]}"
}

mapfile -t core_array < <(parse_cores "$cores")
if [[ ${#core_array[@]} -eq 0 ]]; then
  echo "No cores parsed from --cores." >&2
  exit 1
fi

validate_cores() {
  local raw_list
  raw_list=$(cat /sys/devices/system/cpu/online 2>/dev/null || true)
  if [[ -z "$raw_list" ]]; then
    return 0
  fi

  declare -A online_map=()
  local range start end i
  IFS=',' read -r -a ranges <<<"$raw_list"
  for range in "${ranges[@]}"; do
    if [[ "$range" == *-* ]]; then
      start=${range%-*}
      end=${range#*-}
      for ((i=start; i<=end; i++)); do
        online_map["$i"]=1
      done
    else
      online_map["$range"]=1
    fi
  done

  local invalid=()
  local core
  for core in "${core_array[@]}"; do
    if [[ ! "$core" =~ ^[0-9]+$ ]]; then
      invalid+=("$core")
      continue
    fi
    if [[ -z "${online_map[$core]:-}" ]]; then
      invalid+=("$core")
    fi
  done

  if [[ ${#invalid[@]} -gt 0 ]]; then
    printf 'Requested cores not online: %s\n' "${invalid[*]}" >&2
    printf 'Online cores: %s\n' "$raw_list" >&2
    exit 1
  fi
}

validate_cores

thread_count=${#core_array[@]}
core_list="$(IFS=,; echo "${core_array[*]}")"

mkdir -p "$output_dir"
seed_summary_csv="$output_dir/fixed_winner_bias_by_seed.csv"

run_one_seed() {
  local seed_core="$1"
  local seed_tag="seed_${seed_core}"
  local seed_dir="$output_dir/$seed_tag"
  local seed_log="$seed_dir/ccbench_${op_name}_reps${reps}.log"
  local seed_winner_csv="$seed_dir/winner_sequence.csv"
  local seed_summary_txt="$seed_dir/fixed_winner_bias_summary.txt"
  local seed_per_thread_csv="$seed_dir/fixed_winner_bias_per_thread.csv"

  mkdir -p "$seed_dir"

  cmd_base=(
    "$ccbench"
    -t "[$op_id]"
    -r "$reps"
    -c "$thread_count"
    -x "[$core_list]"
    -b "$seed_core"
  )
  cmd_with_seq=(
    "${cmd_base[@]}"
    --winner-seq "$seed_winner_csv"
  )

  if [[ $dry_run -eq 1 ]]; then
    printf 'DRY RUN: %q ' "${cmd_with_seq[@]}"
    printf '\n'
    return 0
  fi

  printf 'Running:' | tee "$seed_log"
  printf ' %q' "${cmd_with_seq[@]}" | tee -a "$seed_log"
  printf '\n' | tee -a "$seed_log"
  if ! "${cmd_with_seq[@]}" 2>&1 | tee -a "$seed_log"; then
    echo "ccbench failed; see $seed_log for details." >&2
    if [[ -s "$seed_log" ]]; then
      echo "Last 50 lines of $seed_log:" >&2
      tail -n 50 "$seed_log" >&2 || true
    fi
    fallback_log="$seed_dir/ccbench_${op_name}_reps${reps}_no_winner_seq.log"
    echo "Retrying without --winner-seq to isolate failure..." | tee -a "$seed_log" >&2
    if "${cmd_base[@]}" 2>&1 | tee -a "$fallback_log"; then
      echo "ccbench succeeded without --winner-seq. Winner-seq output may be the failure point." >&2
      echo "Fallback log written to $fallback_log" >&2
    else
      echo "ccbench also failed without --winner-seq; failure likely unrelated to winner sequence output." >&2
      if [[ -s "$fallback_log" ]]; then
        echo "Last 50 lines of $fallback_log:" >&2
        tail -n 50 "$fallback_log" >&2 || true
      fi
    fi
    exit 1
  fi

  if [[ ! -s "$seed_winner_csv" ]]; then
    echo "Winner sequence CSV missing or empty: $seed_winner_csv" >&2
    exit 1
  fi

  awk -F',' \
    -v summary_file="$seed_summary_txt" \
    -v per_thread_csv="$seed_per_thread_csv" \
    -v seed_core="$seed_core" \
    '
    NR==1 {next}
    {
      winner=$2
      if (winner == "" || winner == "-1") next
      total++
      counts[winner]++
      if (prev == "") {
        prev = winner
        run_len = 1
        next
      }
      if (winner == prev) {
        run_len++
      } else {
        run_sum += run_len
        run_count++
        if (run_len > max_run[prev]) max_run[prev] = run_len
        prev = winner
        run_len = 1
      }
    }
    END {
      if (total == 0) {
        print "No winners recorded." > summary_file
        exit 1
      }
      if (prev != "") {
        run_sum += run_len
        run_count++
        if (run_len > max_run[prev]) max_run[prev] = run_len
      }
      mean_run = (run_count > 0) ? run_sum / run_count : 0
      top_thread = ""
      top_count = 0
      for (t in counts) {
        if (counts[t] > top_count) {
          top_count = counts[t]
          top_thread = t
        }
      }
      top_share = (total > 0) ? (100.0 * top_count / total) : 0
      printf "total_ops,%d\nrun_count,%d\nmean_run_length,%.2f\n", total, run_count, mean_run > summary_file
      printf "top_thread,%s\n", top_thread >> summary_file
      printf "top_thread_ops,%d\n", top_count >> summary_file
      printf "top_thread_share,%.2f%%\n", top_share >> summary_file
      print "thread_id,total_wins,win_share,max_run_length" > per_thread_csv
      for (t in counts) {
        share = (100.0 * counts[t] / total)
        maxlen = (t in max_run) ? max_run[t] : counts[t]
        printf "%s,%d,%.2f%%,%d\n", t, counts[t], share, maxlen >> per_thread_csv
      }
      printf "%d,%d,%.2f,%d,%d,%.2f\n", seed_core, total, mean_run, top_thread, top_count, top_share
    }
  ' "$seed_winner_csv"
}

echo "seed_core,total_ops,mean_run_length,top_thread,top_thread_ops,top_thread_share" > "$seed_summary_csv"
for seed_core in "${core_array[@]}"; do
  run_one_seed "$seed_core" >> "$seed_summary_csv"
done

if [[ $dry_run -eq 0 ]]; then
  echo "Seed summary written to $seed_summary_csv"
fi
