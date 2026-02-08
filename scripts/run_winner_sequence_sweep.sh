#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'HELP'
Usage: scripts/run_winner_sequence_sweep.sh [options]

Run ccbench and collect per-repetition winner sequence CSVs while rotating the
seed core across selected core sets and thread counts.

Options:
  --ops LIST              Operation names/ids (default: CAS_UNTIL_SUCCESS)
                          e.g. "CAS,FAI,TAS,SWAP" or "12,13,14,15,34"
  --core-sets LIST        Semicolon-separated core arrays (required)
                          e.g. "[0,2,4,6,8];[1,3,5,7]"
  --thread-counts LIST    Comma-separated thread counts to take prefixes of each core set
                          (default: all sizes from 2..len(core-set))
  --reps N                Repetitions per run (default: 100000)
  --rotate-seed           Rotate seed across selected prefix cores (default: on)
  --no-rotate-seed        Use fixed --seed only
  --seed CORE             Fixed seed core when --no-rotate-seed is set (default: first core)
  --ccbench PATH          Path to ccbench binary (default: ./ccbench)
  --output-dir DIR        Output directory (default: results/winner_sequence_sweep)
  --dry-run               Print commands without running
  -h, --help              Show this help

Outputs:
  runs.csv                One row per run configuration
  winner_sequence.csv     Concatenated normalized sequence rows for all runs (adds seq_idx)
  logs/*.log              Raw ccbench logs per run
  sequences/*.csv         Raw per-run winner sequence CSVs from ccbench

Example:
  scripts/run_winner_sequence_sweep.sh \
    --ops "CAS,FAI,TAS,SWAP,CAS_UNTIL_SUCCESS" \
    --core-sets "[0,2,4,6,8];[1,3,5,7,9]" \
    --thread-counts "3,5" \
    --reps 200000 --rotate-seed
HELP
}

ops_raw="CAS_UNTIL_SUCCESS"
core_sets_raw=""
thread_counts_raw=""
reps=100000
rotate_seed=1
seed=""
ccbench=./ccbench
output_dir=results/winner_sequence_sweep
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ops) ops_raw="$2"; shift 2 ;;
    --core-sets) core_sets_raw="$2"; shift 2 ;;
    --thread-counts) thread_counts_raw="$2"; shift 2 ;;
    --reps) reps="$2"; shift 2 ;;
    --rotate-seed) rotate_seed=1; shift ;;
    --no-rotate-seed) rotate_seed=0; shift ;;
    --seed) seed="$2"; shift 2 ;;
    --ccbench) ccbench="$2"; shift 2 ;;
    --output-dir) output_dir="$2"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$core_sets_raw" ]]; then
  echo "--core-sets is required." >&2
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
    CAS|12) echo "CAS:12" ;;
    FAI|13) echo "FAI:13" ;;
    TAS|14) echo "TAS:14" ;;
    SWAP|15) echo "SWAP:15" ;;
    CAS_UNTIL_SUCCESS|34) echo "CAS_UNTIL_SUCCESS:34" ;;
    *) return 1 ;;
  esac
}

parse_cores() {
  local raw="$1"
  local parsed
  parsed=$(echo "$raw" | tr -d '[]' | tr ',' ' ')
  read -r -a arr <<<"$parsed"
  printf '%s\n' "${arr[@]}"
}

prefix_core_list() {
  local n="$1"; shift
  local -a arr=("$@")
  local out=()
  for ((i=0; i<n; i++)); do
    out+=("${arr[$i]}")
  done
  local joined
  joined="$(IFS=,; echo "${out[*]}")"
  printf '[%s]' "$joined"
}

make_tests_list() {
  local n="$1"
  local op_id="$2"
  local out=""
  for ((i=0; i<n; i++)); do
    if [[ -n "$out" ]]; then out+=","; fi
    out+="$op_id"
  done
  printf '[%s]' "$out"
}

mkdir -p "$output_dir" "$output_dir/logs" "$output_dir/sequences"
runs_csv="$output_dir/runs.csv"
all_seq_csv="$output_dir/winner_sequence.csv"
echo "run_id,op,op_id,core_set_id,thread_count,seed_core,cores,reps,log_file,sequence_file" > "$runs_csv"
echo "run_id,op,op_id,core_set_id,thread_count,seed_core,rep,seq_idx,winner_thread_id,winner_core,group,role" > "$all_seq_csv"

IFS=',' read -r -a op_tokens <<<"$ops_raw"
op_specs=()
for tok in "${op_tokens[@]}"; do
  tok_trim="$(echo "$tok" | xargs)"
  [[ -z "$tok_trim" ]] && continue
  if ! spec=$(normalize_op "$tok_trim"); then
    echo "Unsupported op in --ops: $tok_trim" >&2
    exit 1
  fi
  op_specs+=("$spec")
done
if [[ ${#op_specs[@]} -eq 0 ]]; then
  echo "No valid operations in --ops." >&2
  exit 1
fi

IFS=';' read -r -a core_set_tokens <<<"$core_sets_raw"
run_id=0
core_set_id=0

for set_tok in "${core_set_tokens[@]}"; do
  set_tok="$(echo "$set_tok" | xargs)"
  [[ -z "$set_tok" ]] && continue
  core_set_id=$((core_set_id + 1))

  mapfile -t set_cores < <(parse_cores "$set_tok")
  if [[ ${#set_cores[@]} -lt 2 ]]; then
    echo "Core set must have at least 2 cores: $set_tok" >&2
    exit 1
  fi

  thread_counts=()
  if [[ -n "$thread_counts_raw" ]]; then
    IFS=',' read -r -a t_tokens <<<"$thread_counts_raw"
    for t in "${t_tokens[@]}"; do
      t="$(echo "$t" | xargs)"
      [[ -z "$t" ]] && continue
      if [[ "$t" -lt 2 || "$t" -gt ${#set_cores[@]} ]]; then
        echo "Thread count $t out of range for core set size ${#set_cores[@]}" >&2
        exit 1
      fi
      thread_counts+=("$t")
    done
  else
    for ((t=2; t<=${#set_cores[@]}; t++)); do
      thread_counts+=("$t")
    done
  fi

  for op_spec in "${op_specs[@]}"; do
    op_name="${op_spec%%:*}"
    op_id="${op_spec##*:}"

    for tcount in "${thread_counts[@]}"; do
      mapfile -t prefix_cores < <(printf '%s\n' "${set_cores[@]}" | head -n "$tcount")
      cores_arg=$(prefix_core_list "$tcount" "${prefix_cores[@]}")
      tests_arg=$(make_tests_list "$tcount" "$op_id")

      seed_list=()
      if [[ $rotate_seed -eq 1 ]]; then
        seed_list=("${prefix_cores[@]}")
      else
        if [[ -n "$seed" ]]; then
          seed_list=("$seed")
        else
          seed_list=("${prefix_cores[0]}")
        fi
      fi

      for seed_core in "${seed_list[@]}"; do
        run_id=$((run_id + 1))
        log_file="$output_dir/logs/run_${run_id}_op_${op_name}_set${core_set_id}_t${tcount}_seed${seed_core}.log"
        seq_file="$output_dir/sequences/run_${run_id}_winner_sequence.csv"
        cmd=("$ccbench" -r "$reps" -t "$tests_arg" -x "$cores_arg" -b "$seed_core" --winner-seq "$seq_file")

        if [[ $dry_run -eq 1 ]]; then
          printf 'DRY RUN:'
          printf ' %q' "${cmd[@]}"
          printf '\n'
          continue
        fi

        printf '\n[run %d] op=%s threads=%s seed=%s cores=%s\n' "$run_id" "$op_name" "$tcount" "$seed_core" "$cores_arg"
        printf 'Running:' | tee "$log_file"
        printf ' %q' "${cmd[@]}" | tee -a "$log_file"
        printf '\n' | tee -a "$log_file"
        "${cmd[@]}" 2>&1 | tee -a "$log_file"

        if [[ ! -s "$seq_file" ]]; then
          echo "Missing winner sequence file: $seq_file" >&2
          exit 1
        fi

        echo "$run_id,$op_name,$op_id,$core_set_id,$tcount,$seed_core,$cores_arg,$reps,$log_file,$seq_file" >> "$runs_csv"

        awk -F',' -v run_id="$run_id" -v op="$op_name" -v op_id="$op_id" -v set_id="$core_set_id" -v tcount="$tcount" -v seed="$seed_core" '
          BEGIN { OFS=","; seq_idx=0; bad=0 }
          NR==1 { next }
          {
            if (NF < 5) { bad++; next }
            rep=$1; wtid=$2; wcore=$3; grp=$4; role=$5
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", rep)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", wtid)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", wcore)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", grp)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", role)
            if (rep == "") { rep = seq_idx }
            print run_id, op, op_id, set_id, tcount, seed, rep, seq_idx, wtid, wcore, grp, role
            seq_idx++
          }
          END {
            if (bad > 0) {
              printf "WARN run_id=%s dropped %d malformed winner-seq rows from %s\n", run_id, bad, FILENAME > "/dev/stderr"
            }
          }
        ' "$seq_file" >> "$all_seq_csv"
      done
    done
  done
done

if [[ $dry_run -eq 0 ]]; then
  echo
  echo "Done."
  echo "  Runs CSV:      $runs_csv"
  echo "  Sequence CSV:  $all_seq_csv"
fi
