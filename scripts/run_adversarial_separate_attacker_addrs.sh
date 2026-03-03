#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/run_adversarial_separate_attacker_addrs.sh [options]

Adversarial fairness/slowdown test where attacker traffic is compared across:
  1) victim_baseline          : victim alone
  2) victim_plus_shared       : one attacker process, all attacker threads share one cache line
  3) victim_plus_separate     : one attacker process per attacker core, each using a different cache line

This directly answers: "do attackers touching separate addresses still induce unfairness/slowdown?"

Options:
  --victim-cores LIST             Comma-separated victim cores (required)
  --attacker-cores LIST           Comma-separated attacker cores (required)
  --victim-test NAME|ID           Victim primitive (default: CAS)
  --attacker-test NAME|ID         Attacker primitive (default: FAI)
  --victim-reps N                 Victim repetitions (default: 20000)
  --attacker-reps N               Attacker repetitions (default: 200000000)
  --seed-core N                   Victim seed core (default: first victim core)
  --attacker-seed-core N          Shared-attacker seed core (default: first attacker core)
  --victim-stride N               Victim stride (default: 1)
  --attacker-stride N             Attacker stride (default: 1)
  --fixed-victim-addr SPEC        static|0xHEX|none (default: static)
  --shared-attacker-addr HEX      Shared attacker line (default: 0x700000100000)
  --separate-attacker-base HEX    Base address for separate attackers (default: 0x700000300000)
  --separate-attacker-step HEX    Address step per attacker proc (default: 0x1000)
  --output-dir DIR                Output dir (default: results/adversarial_separate_attacker_addrs)
  --ccbench PATH                  Path to ccbench (default: ./ccbench)
  --dry-run                       Print planned commands only
  -h, --help                      Show help
USAGE
}

victim_cores=""
attacker_cores=""
victim_test="CAS"
attacker_test="FAI"
victim_reps=20000
attacker_reps=200000000
seed_core=""
attacker_seed_core=""
victim_stride=1
attacker_stride=1
fixed_victim_addr="static"
shared_attacker_addr="0x700000100000"
separate_attacker_base="0x700000300000"
separate_attacker_step="0x1000"
output_dir="results/adversarial_separate_attacker_addrs"
ccbench="./ccbench"
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --victim-cores) victim_cores="$2"; shift 2 ;;
    --attacker-cores) attacker_cores="$2"; shift 2 ;;
    --victim-test) victim_test="$2"; shift 2 ;;
    --attacker-test) attacker_test="$2"; shift 2 ;;
    --victim-reps) victim_reps="$2"; shift 2 ;;
    --attacker-reps) attacker_reps="$2"; shift 2 ;;
    --seed-core) seed_core="$2"; shift 2 ;;
    --attacker-seed-core) attacker_seed_core="$2"; shift 2 ;;
    --victim-stride) victim_stride="$2"; shift 2 ;;
    --attacker-stride) attacker_stride="$2"; shift 2 ;;
    --fixed-victim-addr) fixed_victim_addr="$2"; shift 2 ;;
    --shared-attacker-addr) shared_attacker_addr="$2"; shift 2 ;;
    --separate-attacker-base) separate_attacker_base="$2"; shift 2 ;;
    --separate-attacker-step) separate_attacker_step="$2"; shift 2 ;;
    --output-dir) output_dir="$2"; shift 2 ;;
    --ccbench) ccbench="$2"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

[[ -n "$victim_cores" && -n "$attacker_cores" ]] || { echo "--victim-cores and --attacker-cores are required." >&2; exit 1; }
[[ -x "$ccbench" ]] || { echo "ccbench not found/executable: $ccbench" >&2; exit 1; }
[[ -f include/ccbench.h ]] || { echo "include/ccbench.h not found; run from repo root." >&2; exit 1; }

as_array() { local csv="$1"; local -n ref="$2"; IFS=',' read -r -a ref <<<"$csv"; }
make_list() { local n="$1" v="$2" out=""; for ((i=0;i<n;i++)); do [[ -n "$out" ]] && out+=",$v" || out="$v"; done; printf '[%s]' "$out"; }
hex_to_dec() { printf '%d' "$(( $1 ))"; }
dec_to_hex() { printf '0x%x' "$1"; }

resolve_test_id() {
  local spec="$1"
  if [[ "$spec" =~ ^[0-9]+$ ]]; then printf '%s\n' "$spec"; return 0; fi
  python - "$spec" <<'PY2'
import re, sys
name=sys.argv[1]
idx=0
in_arr=False
for raw in open('include/ccbench.h', encoding='utf-8'):
    if 'const char* moesi_type_des[' in raw:
        in_arr=True
        continue
    if in_arr and '};' in raw:
        break
    if in_arr and '"' in raw:
        m=re.search(r'"([^"]+)"', raw)
        if not m:
            continue
        if m.group(1)==name:
            print(idx)
            sys.exit(0)
        idx += 1
sys.exit(1)
PY2
}

extract_stats() {
  local log_file="$1"
  awk '
    /Summary : mean avg/ { if (match($0, /mean avg[[:space:]]*([0-9.]+)/, m)) mean=m[1] }
    /Jain fairness/ { if (match($0, /Jain fairness[^0-9]*([0-9.]+)/, m)) fair=m[1] }
    /success rate/ { if (match($0, /success rate[^0-9]*([0-9.]+)/, m)) succ=m[1] }
    /Winner==argmin\(B4[[:space:]]*->[[:space:]]*success\)/ { if (match($0, /\(([0-9.]+)%\)/, m)) succ=m[1] }
    END {
      if (mean == "") mean = "NA"
      if (fair == "") fair = "NA"
      if (succ == "") succ = "NA"
      if (fair != "NA") fair = sprintf("%.4f", fair)
      printf "%s,%s,%s", mean, fair, succ
    }
  ' "$log_file"
}

as_array "$victim_cores" victim_core_arr
as_array "$attacker_cores" attacker_core_arr
for c in "${victim_core_arr[@]}" "${attacker_core_arr[@]}"; do [[ "$c" =~ ^[0-9]+$ ]] || { echo "Non-integer core id: $c" >&2; exit 1; }; done

victim_count=${#victim_core_arr[@]}
attacker_count=${#attacker_core_arr[@]}
[[ "$victim_count" -gt 0 && "$attacker_count" -gt 0 ]] || { echo "empty core list" >&2; exit 1; }

victim_test_id=$(resolve_test_id "$victim_test") || { echo "Unknown victim test: $victim_test" >&2; exit 1; }
attacker_test_id=$(resolve_test_id "$attacker_test") || { echo "Unknown attacker test: $attacker_test" >&2; exit 1; }

[[ "$victim_reps" =~ ^[0-9]+$ ]] || { echo "--victim-reps must be integer" >&2; exit 1; }
[[ "$attacker_reps" =~ ^[0-9]+$ ]] || { echo "--attacker-reps must be integer" >&2; exit 1; }

[[ -z "$seed_core" ]] && seed_core="${victim_core_arr[0]}"
[[ -z "$attacker_seed_core" ]] && attacker_seed_core="${attacker_core_arr[0]}"

victim_tests=$(make_list "$victim_count" "$victim_test_id")
victim_core_list="[$victim_cores]"
attacker_tests_shared=$(make_list "$attacker_count" "$attacker_test_id")
attacker_core_list="[$attacker_cores]"

mkdir -p "$output_dir/logs"
summary_csv="$output_dir/summary.csv"
echo "phase,mean_avg,jain_fairness,success_rate,slowdown_vs_baseline,notes" > "$summary_csv"

run_cmd_logged() {
  local log_file="$1"; shift
  local -a cmd=("$@")
  if [[ "$dry_run" -eq 1 ]]; then
    printf '[dry-run] %q ' "${cmd[@]}" >&2; echo >&2
    : > "$log_file"
    return 0
  fi
  "${cmd[@]}" > "$log_file" 2>&1
}

run_baseline() {
  local log="$output_dir/logs/victim_baseline.log"
  local -a cmd=("$ccbench" -r "$victim_reps" -t "$victim_tests" -x "$victim_core_list" -b "$seed_core" -s "$victim_stride")
  [[ "$fixed_victim_addr" != "none" ]] && cmd+=(-Z "$fixed_victim_addr")
  run_cmd_logged "$log" "${cmd[@]}"
  extract_stats "$log"
}

run_with_shared_attackers() {
  local mode="$1"
  local v_log="$output_dir/logs/victim_plus_${mode}.log"
  local a_log="$output_dir/logs/attacker_${mode}.log"
  local fifo="$output_dir/.start_fifo_${mode}"
  rm -f "$fifo"; mkfifo "$fifo"

  local -a attacker_cmd=("$ccbench" -r "$attacker_reps" -t "$attacker_tests_shared" -x "$attacker_core_list" -b "$attacker_seed_core" -s "$attacker_stride" -Z "$shared_attacker_addr")
  local -a victim_cmd=("$ccbench" -r "$victim_reps" -t "$victim_tests" -x "$victim_core_list" -b "$seed_core" -s "$victim_stride")
  [[ "$fixed_victim_addr" != "none" ]] && victim_cmd+=(-Z "$fixed_victim_addr")

  if [[ "$dry_run" -eq 1 ]]; then
    printf '[dry-run] shared attacker: %q ' "${attacker_cmd[@]}" >&2; echo >&2
    printf '[dry-run] shared victim:   %q ' "${victim_cmd[@]}" >&2; echo >&2
    : > "$a_log"; : > "$v_log"; rm -f "$fifo"
    extract_stats "$v_log"; return
  fi

  ( read -r _ < "$fifo"; "${attacker_cmd[@]}" > "$a_log" 2>&1 ) &
  local attacker_pid=$!
  sleep 0.1
  ( read -r _ < "$fifo"; "${victim_cmd[@]}" > "$v_log" 2>&1 ) &
  local victim_pid=$!
  sleep 0.1
  printf 'go\ngo\n' > "$fifo"

  wait "$victim_pid"
  kill "$attacker_pid" 2>/dev/null || true
  wait "$attacker_pid" 2>/dev/null || true
  rm -f "$fifo"
  extract_stats "$v_log"
}

run_with_separate_attackers() {
  local v_log="$output_dir/logs/victim_plus_separate.log"
  local fifo="$output_dir/.start_fifo_separate"
  rm -f "$fifo"; mkfifo "$fifo"

  local -a pids=()
  local base_dec step_dec
  base_dec=$(hex_to_dec "$separate_attacker_base")
  step_dec=$(hex_to_dec "$separate_attacker_step")

  local -a victim_cmd=("$ccbench" -r "$victim_reps" -t "$victim_tests" -x "$victim_core_list" -b "$seed_core" -s "$victim_stride")
  [[ "$fixed_victim_addr" != "none" ]] && victim_cmd+=(-Z "$fixed_victim_addr")

  if [[ "$dry_run" -eq 1 ]]; then
    for i in "${!attacker_core_arr[@]}"; do
      addr=$(dec_to_hex "$((base_dec + i * step_dec))")
      printf '[dry-run] separate attacker core=%s addr=%s\n' "${attacker_core_arr[$i]}" "$addr" >&2
    done
    printf '[dry-run] separate victim: %q ' "${victim_cmd[@]}" >&2; echo >&2
    : > "$v_log"; rm -f "$fifo"
    extract_stats "$v_log"; return
  fi

  for i in "${!attacker_core_arr[@]}"; do
    core="${attacker_core_arr[$i]}"
    addr=$(dec_to_hex "$((base_dec + i * step_dec))")
    a_log="$output_dir/logs/attacker_separate_core${core}.log"
    ( read -r _ < "$fifo"; "$ccbench" -r "$attacker_reps" -t "[$attacker_test_id]" -x "[$core]" -b "$core" -s "$attacker_stride" -Z "$addr" > "$a_log" 2>&1 ) &
    pids+=("$!")
  done

  sleep 0.1
  ( read -r _ < "$fifo"; "${victim_cmd[@]}" > "$v_log" 2>&1 ) &
  local victim_pid=$!
  sleep 0.1

  local signals=$((attacker_count + 1))
  for ((i=0; i<signals; i++)); do printf 'go\n'; done > "$fifo"

  wait "$victim_pid"
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
  rm -f "$fifo"
  extract_stats "$v_log"
}

baseline_stats=$(run_baseline)
shared_stats=$(run_with_shared_attackers shared)
separate_stats=$(run_with_separate_attackers)

IFS=',' read -r base_mean base_fair base_succ <<<"$baseline_stats"
IFS=',' read -r shared_mean shared_fair shared_succ <<<"$shared_stats"
IFS=',' read -r sep_mean sep_fair sep_succ <<<"$separate_stats"

calc_slowdown() {
  local base="$1" now="$2"
  if [[ "$base" == "NA" || "$now" == "NA" ]]; then printf 'NA'; return; fi
  awk -v b="$base" -v n="$now" 'BEGIN { if (b==0) {print "NA"} else {printf "%.4f", n/b} }'
}

shared_slow=$(calc_slowdown "$base_mean" "$shared_mean")
sep_slow=$(calc_slowdown "$base_mean" "$sep_mean")

echo "victim_baseline,$base_mean,$base_fair,$base_succ,1.0000,no_attackers" >> "$summary_csv"
echo "victim_plus_shared,$shared_mean,$shared_fair,$shared_succ,$shared_slow,attackers_share_one_line" >> "$summary_csv"
echo "victim_plus_separate,$sep_mean,$sep_fair,$sep_succ,$sep_slow,each_attacker_has_distinct_line" >> "$summary_csv"

cat <<REPORT
Wrote: $summary_csv

Interpretation guide:
  - Compare slowdown_vs_baseline for shared vs separate attacker layouts.
  - If victim_plus_separate still slows down or lowers fairness, interference is not only same-line contention.
  - If separate is much better than shared, unfairness is likely coherence-hotspot driven.
REPORT
