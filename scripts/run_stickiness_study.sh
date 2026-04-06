#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# run_stickiness_study.sh — Master data-collection + analysis for thread
# contention stickiness / temporal-locality research.
#
# Collects per-repetition winner sequences from ccbench while rotating the
# seed core, then runs comprehensive stickiness analysis at multiple window
# scales.
#
# Workflow:
#   1. For each (op × core_set × thread_count × seed_core): run ccbench
#   2. Concatenate all winner sequences into one CSV
#   3. Run analyze_stickiness.py with multi-scale windowed analysis
###############################################################################

usage() {
  cat <<'HELP'
Usage: scripts/run_stickiness_study.sh [options]

Collect winner sequences and run stickiness analysis.

Required:
  --core-sets LIST        Semicolon-separated core arrays (e.g. "[0,2,4,6];[1,3,5,7]")

Options:
  --ops LIST              Comma-separated operations (default: CAS_UNTIL_SUCCESS,FAI,TAS)
  --thread-counts LIST    Comma-separated thread counts (default: all from 2..len(set))
  --reps N                Repetitions per run (default: 100000)
  --seed CORE             Fixed seed core (default: rotate across all prefix cores)
  --no-rotate             Use fixed --seed only, do not rotate
  --window-sizes LIST     Comma-separated window sizes for analysis (default: 50,200,1000)
  --trials N              Monte Carlo permutation trials (default: 1000)
  --ccbench PATH          Path to ccbench binary (default: ./ccbench)
  --output-dir DIR        Output directory (default: results/stickiness_study)
  --collect-only          Only collect data, skip analysis
  --analyze-only          Only run analysis on existing data in --output-dir
  --dry-run               Print ccbench commands without executing
  -h, --help              Show this help

Outputs (in --output-dir):
  collect/
    runs.csv              One row per run configuration
    winner_sequence.csv   All winner sequences concatenated (input to analysis)
    logs/                 Raw ccbench output per run
    sequences/            Raw per-run winner sequence CSVs
  analysis/
    stickiness_group_summary.csv      Per-group overall metrics
    stickiness_window_detail.csv      Per-window metrics at each scale
    stickiness_thread_summary.csv     Per-thread metrics
    stickiness_regime_summary.csv     Regime/change-point detail

Example:
  scripts/run_stickiness_study.sh \
    --core-sets "[0,2,4,6,8];[1,3,5,7,9]" \
    --ops "CAS_UNTIL_SUCCESS,FAI,TAS" \
    --thread-counts "3,5" \
    --reps 200000 \
    --window-sizes "50,200,1000,5000" \
    --trials 1000
HELP
}

# ── Defaults ─────────────────────────────────────────────────────────────────
ops_raw="CAS_UNTIL_SUCCESS,FAI,TAS"
core_sets_raw=""
thread_counts_raw=""
reps=100000
rotate_seed=1
fixed_seed=""
window_sizes="50,200,1000"
trials=1000
ccbench=./ccbench
output_dir=results/stickiness_study
collect_only=0
analyze_only=0
dry_run=0

# ── Parse args ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ops)            ops_raw="$2";           shift 2 ;;
    --core-sets)      core_sets_raw="$2";     shift 2 ;;
    --thread-counts)  thread_counts_raw="$2"; shift 2 ;;
    --reps)           reps="$2";              shift 2 ;;
    --seed)           fixed_seed="$2";        shift 2 ;;
    --no-rotate)      rotate_seed=0;          shift ;;
    --window-sizes)   window_sizes="$2";      shift 2 ;;
    --trials)         trials="$2";            shift 2 ;;
    --ccbench)        ccbench="$2";           shift 2 ;;
    --output-dir)     output_dir="$2";        shift 2 ;;
    --collect-only)   collect_only=1;         shift ;;
    --analyze-only)   analyze_only=1;         shift ;;
    --dry-run)        dry_run=1;              shift ;;
    -h|--help)        usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$core_sets_raw" && "$analyze_only" -eq 0 ]]; then
  echo "Error: --core-sets is required for data collection." >&2
  usage
  exit 1
fi

# ── Helpers ──────────────────────────────────────────────────────────────────
normalize_op() {
  local raw="${1^^}"
  case "$raw" in
    CAS|12)              echo "CAS:12" ;;
    FAI|13)              echo "FAI:13" ;;
    TAS|14)              echo "TAS:14" ;;
    SWAP|15)             echo "SWAP:15" ;;
    CAS_UNTIL_SUCCESS|34) echo "CAS_UNTIL_SUCCESS:34" ;;
    *) echo "Unsupported operation: $1" >&2; return 1 ;;
  esac
}

parse_cores() {
  echo "$1" | tr -d '[]' | tr ',' '\n'
}

make_bracket_list() {
  local IFS=','
  printf '[%s]' "$*"
}

# ── Collection phase ────────────────────────────────────────────────────────
do_collect() {
  if [[ ! -x "$ccbench" ]]; then
    if [[ "$dry_run" -eq 1 ]]; then
      echo "WARN: ccbench not found at $ccbench (dry-run, continuing)" >&2
    else
      echo "ccbench not found at $ccbench, running make..." >&2
      make
    fi
  fi

  # Verify --winner-seq support
  if [[ "$dry_run" -eq 0 && -x "$ccbench" ]]; then
    if ! "$ccbench" --help 2>/dev/null | grep -q "winner-seq"; then
      echo "Error: ccbench does not support --winner-seq; rebuild with winner-seq support." >&2
      exit 1
    fi
  fi

  # Parse operations
  IFS=',' read -r -a op_tokens <<<"$ops_raw"
  op_specs=()
  for tok in "${op_tokens[@]}"; do
    tok_trim="$(echo "$tok" | xargs)"
    [[ -z "$tok_trim" ]] && continue
    spec=$(normalize_op "$tok_trim") || exit 1
    op_specs+=("$spec")
  done
  if [[ ${#op_specs[@]} -eq 0 ]]; then
    echo "No valid operations." >&2; exit 1
  fi

  local collect_dir="$output_dir/collect"
  mkdir -p "$collect_dir/logs" "$collect_dir/sequences"

  local runs_csv="$collect_dir/runs.csv"
  local all_seq_csv="$collect_dir/winner_sequence.csv"

  echo "run_id,op,op_id,core_set_id,thread_count,seed_core,cores,reps,log_file,sequence_file" > "$runs_csv"
  echo "run_id,op,op_id,core_set_id,thread_count,seed_core,rep,seq_idx,winner_thread_id,winner_core,group,role" > "$all_seq_csv"

  # Parse core sets
  IFS=';' read -r -a core_set_tokens <<<"$core_sets_raw"
  local run_id=0 core_set_id=0

  for set_tok in "${core_set_tokens[@]}"; do
    set_tok="$(echo "$set_tok" | xargs)"
    [[ -z "$set_tok" ]] && continue
    core_set_id=$((core_set_id + 1))

    mapfile -t set_cores < <(parse_cores "$set_tok")
    if [[ ${#set_cores[@]} -lt 2 ]]; then
      echo "Core set must have >= 2 cores: $set_tok" >&2; exit 1
    fi

    # Determine thread counts
    local -a thread_counts=()
    if [[ -n "$thread_counts_raw" ]]; then
      IFS=',' read -r -a t_tokens <<<"$thread_counts_raw"
      for t in "${t_tokens[@]}"; do
        t="$(echo "$t" | xargs)"
        [[ -z "$t" ]] && continue
        if [[ "$t" -lt 2 || "$t" -gt ${#set_cores[@]} ]]; then
          echo "Thread count $t out of range for core set size ${#set_cores[@]}" >&2; exit 1
        fi
        thread_counts+=("$t")
      done
    else
      for ((t=2; t<=${#set_cores[@]}; t++)); do
        thread_counts+=("$t")
      done
    fi

    for op_spec in "${op_specs[@]}"; do
      local op_name="${op_spec%%:*}"
      local op_id="${op_spec##*:}"

      for tcount in "${thread_counts[@]}"; do
        # Build prefix core list
        local -a prefix_cores=()
        for ((i=0; i<tcount; i++)); do
          prefix_cores+=("${set_cores[$i]}")
        done

        local cores_arg
        cores_arg=$(make_bracket_list "${prefix_cores[@]}")

        # Build tests list (same op for all threads)
        local tests_parts=()
        for ((i=0; i<tcount; i++)); do
          tests_parts+=("$op_id")
        done
        local tests_arg
        tests_arg=$(make_bracket_list "${tests_parts[@]}")

        # Determine seed cores to rotate through
        local -a seed_list=()
        if [[ $rotate_seed -eq 1 ]]; then
          seed_list=("${prefix_cores[@]}")
        elif [[ -n "$fixed_seed" ]]; then
          seed_list=("$fixed_seed")
        else
          seed_list=("${prefix_cores[0]}")
        fi

        for seed_core in "${seed_list[@]}"; do
          run_id=$((run_id + 1))
          local log_file="$collect_dir/logs/run_${run_id}_${op_name}_set${core_set_id}_t${tcount}_seed${seed_core}.log"
          local seq_file="$collect_dir/sequences/run_${run_id}_winner_sequence.csv"

          local -a cmd=("$ccbench" -r "$reps" -t "$tests_arg" -x "$cores_arg" -b "$seed_core" --winner-seq "$seq_file")

          if [[ $dry_run -eq 1 ]]; then
            printf 'DRY RUN: '
            printf '%q ' "${cmd[@]}"
            printf '\n'
            continue
          fi

          printf '\n══════════════════════════════════════════════════════════════\n'
          printf '[run %d] op=%-20s threads=%s  seed=%s  cores=%s\n' \
            "$run_id" "$op_name" "$tcount" "$seed_core" "$cores_arg"
          printf '══════════════════════════════════════════════════════════════\n'
          "${cmd[@]}" 2>&1 | tee "$log_file"

          if [[ ! -s "$seq_file" ]]; then
            echo "ERROR: Missing winner sequence file: $seq_file" >&2
            exit 1
          fi

          echo "$run_id,$op_name,$op_id,$core_set_id,$tcount,$seed_core,$cores_arg,$reps,$log_file,$seq_file" >> "$runs_csv"

          # Normalize and append to unified CSV
          awk -F',' -v rid="$run_id" -v op="$op_name" -v oid="$op_id" \
              -v sid="$core_set_id" -v tc="$tcount" -v sc="$seed_core" '
            BEGIN { OFS=","; idx=0; bad=0 }
            NR==1 { next }
            {
              if (NF < 5) { bad++; next }
              rep=$1; wtid=$2; wcore=$3; grp=$4; role=$5
              gsub(/^[[:space:]]+|[[:space:]]+$/, "", rep)
              gsub(/^[[:space:]]+|[[:space:]]+$/, "", wtid)
              gsub(/^[[:space:]]+|[[:space:]]+$/, "", wcore)
              gsub(/^[[:space:]]+|[[:space:]]+$/, "", grp)
              gsub(/^[[:space:]]+|[[:space:]]+$/, "", role)
              if (rep == "") { rep = idx }
              print rid, op, oid, sid, tc, sc, rep, idx, wtid, wcore, grp, role
              idx++
            }
            END {
              if (bad > 0)
                printf "WARN: run %s dropped %d malformed rows from %s\n", rid, bad, FILENAME > "/dev/stderr"
            }
          ' "$seq_file" >> "$all_seq_csv"
        done
      done
    done
  done

  if [[ $dry_run -eq 0 ]]; then
    echo
    echo "Collection complete: $run_id runs"
    echo "  Runs CSV:     $runs_csv"
    echo "  Sequence CSV: $all_seq_csv"
  fi
}

# ── Analysis phase ──────────────────────────────────────────────────────────
do_analyze() {
  local seq_csv="$output_dir/collect/winner_sequence.csv"
  if [[ ! -f "$seq_csv" ]]; then
    echo "Error: No winner_sequence.csv found at $seq_csv" >&2
    echo "Run collection first (without --analyze-only)." >&2
    exit 1
  fi

  local analysis_dir="$output_dir/analysis"
  mkdir -p "$analysis_dir"

  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local analyzer="$script_dir/analyze_stickiness.py"

  if [[ ! -f "$analyzer" ]]; then
    echo "Error: analyze_stickiness.py not found at $analyzer" >&2
    exit 1
  fi

  echo
  echo "Running stickiness analysis..."
  echo "  Input:        $seq_csv"
  echo "  Output dir:   $analysis_dir"
  echo "  Window sizes: $window_sizes"
  echo "  MC trials:    $trials"
  echo

  python3 "$analyzer" \
    "$seq_csv" \
    --out-prefix "$analysis_dir/stickiness" \
    --window-sizes "$window_sizes" \
    --trials "$trials"

  echo
  echo "Analysis complete. Results in: $analysis_dir/"
}

# ── Main ────────────────────────────────────────────────────────────────────
if [[ "$analyze_only" -eq 0 ]]; then
  do_collect
fi

if [[ "$collect_only" -eq 0 && "$dry_run" -eq 0 ]]; then
  do_analyze
fi
