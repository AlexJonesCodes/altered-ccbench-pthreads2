#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'HELP'
Usage: scripts/run_seed_rotation_wins.sh [options]

Rotate the seed (pinned) thread across cores and record per-thread wins for
CAS/FAI/TAS/CAS_UNTIL_SUCCESS under contention, plus fairness stats.

Options:
  --op NAME              Atomic op name: CAS, FAI, TAS, CAS_UNTIL_SUCCESS, ALL (default: ALL)
                         ALL runs FAI, TAS, and CAS_UNTIL_SUCCESS in sequence.
  --cores LIST           Core list (e.g., "[0,1,2,3,4]") (required)
  --threads N            Use only the first N cores from --cores (optional)
  --threads-list LIST    Comma/space list of thread counts (e.g., "2,4,6,8")
  --reps N               Repetitions per run (default: 10000)
  --ccbench PATH         Path to ccbench binary (default: ./ccbench)
  --output-dir DIR       Output directory for logs/report (default: results)
                         Reports: seed_rotation_wins_<OP>.txt and .csv
  --dry-run              Print commands without running them
  -h, --help             Show this help

Example:
  scripts/run_seed_rotation_wins.sh \
    --op ALL \
    --cores "[0,1,2,3,16,17]" \
    --threads-list "2,4,6,8" \
    --reps 20000 \
    --output-dir results_seed_rotation
HELP
}

op="ALL"
cores=""
threads=""
threads_list=""
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
    --threads-list)
      threads_list="$2"; shift 2 ;;
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

if [[ "${op^^}" == "ALL" ]]; then
  ops=("FAI" "TAS" "CAS_UNTIL_SUCCESS")
else
  ops=("$op")
fi

mkdir -p "$output_dir/logs"

parse_cores() {
  local raw="$1"
  local parsed
  parsed=$(echo "$raw" | tr -d '[]' | tr ',' ' ')
  read -r -a core_array <<<"$parsed"
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

format_fairness_line() {
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
      sum_all = 0
      sum_sq_all = 0
      sum_workers = 0
      sum_sq_workers = 0
      count_all = 0
      count_workers = 0

      for (i = 0; i < count; i++) {
        val = wins[i]
        if (val == "") val = 0
        sum_all += val
        sum_sq_all += val * val
        count_all++
        if (i != skip) {
          sum_workers += val
          sum_sq_workers += val * val
          count_workers++
        }
      }

      fairness_all = 0
      if (count_all > 0 && sum_sq_all > 0) {
        fairness_all = (sum_all * sum_all) / (count_all * sum_sq_all)
      }
      fairness_workers = 0
      if (count_workers > 0 && sum_sq_workers > 0) {
        fairness_workers = (sum_workers * sum_workers) / (count_workers * sum_sq_workers)
      }

      printf "fairness (Jain) all threads: %.6f\n", fairness_all
      printf "fairness (Jain) workers only: %.6f\n", fairness_workers
    }
  ' "$log_file"
}

append_thread_csv() {
  local log_file="$1"
  local op_label="$2"
  local seed_core="$3"
  local csv_file="$4"

  awk -v op="$op_label" -v seed="$seed_core" -v csv_file="$csv_file" '
    /Core number/ {
      if (match($0, /Core number[[:space:]]+([0-9]+)[^0-9]+thread:[[:space:]]+([0-9]+).*avg[[:space:]]+([0-9.]+)/, m)) {
        core = m[2]
        avg = m[3]
        avg_by_core[core] = avg
      }
    }
    /wins$/ {
      if (match($0, /thread[[:space:]]+([0-9]+)[^0-9]+thread ID[[:space:]]+([0-9]+)[^0-9]+([0-9]+)[[:space:]]+wins$/, m)) {
        core = m[1]
        thread = m[2]
        wins[thread] = m[3]
        core_by_thread[thread] = core
        thread_seen[thread] = 1
        if (thread > max_thread) max_thread = thread
      } else if (match($0, /thread ID[[:space:]]+([0-9]+):[[:space:]]+([0-9]+)[[:space:]]+wins$/, m)) {
        thread = m[1]
        wins[thread] = m[2]
        thread_seen[thread] = 1
        if (thread > max_thread) max_thread = thread
      }
    }
    END {
      for (i = 0; i <= max_thread; i++) {
        if (!thread_seen[i]) continue
        core = core_by_thread[i]
        avg = avg_by_core[core]
        win = wins[i]
        if (core == "") core = 0
        if (avg == "") avg = 0
        if (win == "") win = 0
        printf "%s,%s,%s,%s,%.3f,%s\n", op, seed, i, core, avg + 0, win \
          >> csv_file
      }
    }
  ' "$log_file"
}

append_summary_csv() {
  local log_file="$1"
  local op_label="$2"
  local seed_core="$3"
  local thread_count="$4"
  local csv_file="$5"

  awk -v op="$op_label" -v seed="$seed_core" -v threads="$thread_count" -v csv_file="$csv_file" '
    /Core number/ {
      if (match($0, /Core number[[:space:]]+([0-9]+)[^0-9]+thread:[[:space:]]+([0-9]+).*avg[[:space:]]+([0-9.]+)/, m)) {
        core = m[2]
        avg = m[3]
        avg_by_core[core] = avg
      }
    }
    /wins$/ {
      if (match($0, /thread[[:space:]]+([0-9]+)[^0-9]+thread ID[[:space:]]+([0-9]+)[^0-9]+([0-9]+)[[:space:]]+wins$/, m)) {
        core = m[1]
        thread = m[2]
        wins[thread] = m[3]
        core_by_thread[thread] = core
        thread_seen[thread] = 1
        if (thread > max_thread) max_thread = thread
      } else if (match($0, /thread ID[[:space:]]+([0-9]+):[[:space:]]+([0-9]+)[[:space:]]+wins$/, m)) {
        thread = m[1]
        wins[thread] = m[2]
        thread_seen[thread] = 1
        if (thread > max_thread) max_thread = thread
      }
    }
    END {
      for (i = 0; i <= max_thread; i++) {
        if (!thread_seen[i]) continue
        core = core_by_thread[i]
        avg = avg_by_core[core]
        win = wins[i]
        if (core == "") core = 0
        if (avg == "") avg = 0
        if (win == "") win = 0
        printf "%s,%s,%s,%s,%s,%.3f,%s\n", op, threads, seed, i, core, avg + 0, win \
          >> csv_file
      }
    }
  ' "$log_file"
}

mapfile -t core_array < <(parse_cores "$cores")
total_cores=${#core_array[@]}
if [[ "$total_cores" -lt 1 ]]; then
  echo "No cores provided." >&2
  exit 1
fi

if [[ -n "$threads_list" ]]; then
  parsed_threads=$(echo "$threads_list" | tr -d '[]' | tr ',' ' ')
  read -r -a thread_counts <<<"$parsed_threads"
elif [[ -n "$threads" ]]; then
  thread_counts=("$threads")
else
  thread_counts=("$total_cores")
fi

for count in "${thread_counts[@]}"; do
  if [[ "$count" -lt 1 || "$count" -gt "$total_cores" ]]; then
    echo "Thread count ${count} must be between 1 and ${total_cores}." >&2
    exit 1
  fi
done

summary_csv="$output_dir/seed_rotation_wins_summary.csv"
if [[ "$dry_run" -eq 0 ]]; then
  printf "op,threads,seed_core,thread_id,core,avg_latency,wins\n" >"$summary_csv"
fi

for op_name in "${ops[@]}"; do
  op_entry=$(normalize_op "$op_name") || {
    echo "Unsupported --op '${op_name}'. Use CAS, FAI, TAS, CAS_UNTIL_SUCCESS, or ALL." >&2
    exit 1
  }
  op_label="${op_entry%%:*}"
  test_id="${op_entry##*:}"
  for thread_count in "${thread_counts[@]}"; do
    tests_list=$(build_tests_list "$thread_count")
    core_subset=("${core_array[@]:0:$thread_count}")
    cores_list=$(build_cores_list "${core_subset[@]}")

    report_suffix=""
    if [[ "${#thread_counts[@]}" -gt 1 ]]; then
      report_suffix="_t${thread_count}"
    fi
    report_file="$output_dir/seed_rotation_wins_${op_label}${report_suffix}.txt"
    csv_file="$output_dir/seed_rotation_wins_${op_label}${report_suffix}.csv"
    if [[ "$dry_run" -eq 0 ]]; then
      : >"$report_file"
      printf "op,seed_core,thread_id,core,avg_latency,wins\n" >"$csv_file"
    fi

    for ((i=0; i<thread_count; i++)); do
      seed_core="${core_subset[$i]}"
      log_file="$output_dir/logs/threads_${thread_count}_seed_t$((i + 1))_core_${seed_core}_op_${test_id}.log"
      cmd=("$ccbench" -r "$reps" -t "$tests_list" -x "$cores_list" -b "$seed_core")
      if [[ "$dry_run" -eq 1 ]]; then
        run_cmd "${cmd[@]}"
        continue
      fi
      run_cmd "${cmd[@]}" | tee "$log_file" >/dev/null

      {
        printf 'operation: %s (test %s)\n' "$op_label" "$test_id"
        printf 'thread count: %s\n' "$thread_count"
        printf 'pinned thread: t%d (core %s)\n' "$((i + 1))" "$seed_core"
        printf 'wins per thread:\n'
        format_wins_line "$log_file" "$i" "$thread_count"
        format_fairness_line "$log_file" "$i" "$thread_count"
        printf '\n'
      } >>"$report_file"
      append_thread_csv "$log_file" "$op_label" "$seed_core" "$csv_file"
      append_summary_csv "$log_file" "$op_label" "$seed_core" "$thread_count" "$summary_csv"

    done
  done
done

if [[ "$dry_run" -eq 0 ]]; then
  printf 'Completed. Reports written to:\n'
  for op_name in "${ops[@]}"; do
    op_entry=$(normalize_op "$op_name") || continue
    op_label="${op_entry%%:*}"
    if [[ "${#thread_counts[@]}" -gt 1 ]]; then
      for thread_count in "${thread_counts[@]}"; do
        printf '  %s\n' "$output_dir/seed_rotation_wins_${op_label}_t${thread_count}.txt"
        printf '  %s\n' "$output_dir/seed_rotation_wins_${op_label}_t${thread_count}.csv"
      done
    else
      printf '  %s\n' "$output_dir/seed_rotation_wins_${op_label}.txt"
      printf '  %s\n' "$output_dir/seed_rotation_wins_${op_label}.csv"
    fi
  done
  printf '  %s\n' "$summary_csv"
fi
