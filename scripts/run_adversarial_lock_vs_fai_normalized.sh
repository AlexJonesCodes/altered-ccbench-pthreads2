#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/run_adversarial_lock_vs_fai_normalized.sh [options]

Normalized adversarial coherence experiment with paired randomized crossover.

Design per replicate:
  1) victim_baseline
  2) for each attacker thread count and each control test, run {rmw,control} in randomized order
  3) compute paired deltas (rmw-control) and baseline-normalized deltas
  4) aggregate median/IQR/sign-consistency across replicates

Options:
  --victim-cores LIST             Comma-separated victim cores (required)
  --attacker-cores LIST           Comma-separated attacker cores (required)
  --victim-test NAME|ID           Victim primitive (default: CAS)
  --attacker-test NAME|ID         Attacker primitive (default: FAI)
  --control-tests LIST            Comma-separated controls (default: NOP,LOAD_FROM_L1,LOAD_FROM_MEM_SIZE,PAUSE)
  --control-test NAME|ID          Single control primitive (compat; overrides --control-tests)
  --attacker-thread-sweep LIST    Attacker thread counts (default: full attacker core count)
  --victim-reps N                 Victim reps per measured run (default: 20000)
  --attacker-reps N               Attacker reps per measured run (default: 200000000)
  --replicates N                  Number of replicate blocks (default: 5)
  --random-seed N                 Seed for randomized phase order (default: 12345)
  --warmup-reps N                 Victim warmup reps before each measured phase (default: 5000)
  --inter-phase-sleep-ms N        Sleep between phases in ms (default: 200)
  --seed-core N                   Victim seed core (default: first victim core)
  --attacker-seed-core N          Attacker seed core (default: first attacker core)
  --victim-backoff-max N          Victim CAS_UNTIL_SUCCESS backoff max (default: 1024)
  --attacker-backoff-max N        Attacker CAS_UNTIL_SUCCESS backoff max (default: 1)
  --victim-stride N               Victim stride (default: 1)
  --attacker-stride N             Attacker stride (default: 1)
  --fixed-victim-addr SPEC        static|0xHEX|none (default: static)
  --fixed-attacker-addr HEX       Attacker fixed line (default: 0x700000100000)
  --victim-fallback-addr HEX      Victim fallback fixed line (default: 0x700000200000)
  --fail-stats                    Enable fail stats
  --enforce-no-smt-siblings       Fail on SMT sibling overlap
  --ccbench PATH                  Path to ccbench (default: ./ccbench)
  --output-dir DIR                Output directory (default: results/adversarial_lock_vs_fai_normalized)
  --dry-run                       Print commands only
  -h, --help                      Show help
USAGE
}

victim_cores=""
attacker_cores=""
victim_test="CAS"
attacker_test="FAI"
control_tests_csv="NOP,LOAD_FROM_L1,LOAD_FROM_MEM_SIZE,PAUSE"
control_test=""
attacker_thread_sweep=""
victim_reps=20000
attacker_reps=200000000
replicates=5
random_seed=12345
warmup_reps=5000
inter_phase_sleep_ms=200
seed_core=""
attacker_seed_core=""
victim_backoff_max=1024
attacker_backoff_max=1
victim_stride=1
attacker_stride=1
fixed_victim_addr="static"
fixed_attacker_addr="0x700000100000"
victim_fallback_addr="0x700000200000"
fail_stats=0
fail_stats_effective=0
fail_stats_auto_disabled=0
victim_addr_auto_fallback=0
victim_fixed_disabled=0
enforce_no_smt=0
ccbench=./ccbench
output_dir="results/adversarial_lock_vs_fai_normalized"
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --victim-cores) victim_cores="$2"; shift 2 ;;
    --attacker-cores) attacker_cores="$2"; shift 2 ;;
    --victim-test) victim_test="$2"; shift 2 ;;
    --attacker-test) attacker_test="$2"; shift 2 ;;
    --control-tests) control_tests_csv="$2"; shift 2 ;;
    --control-test) control_test="$2"; shift 2 ;;
    --attacker-thread-sweep) attacker_thread_sweep="$2"; shift 2 ;;
    --victim-reps) victim_reps="$2"; shift 2 ;;
    --attacker-reps) attacker_reps="$2"; shift 2 ;;
    --replicates) replicates="$2"; shift 2 ;;
    --random-seed) random_seed="$2"; shift 2 ;;
    --warmup-reps) warmup_reps="$2"; shift 2 ;;
    --inter-phase-sleep-ms) inter_phase_sleep_ms="$2"; shift 2 ;;
    --seed-core) seed_core="$2"; shift 2 ;;
    --attacker-seed-core) attacker_seed_core="$2"; shift 2 ;;
    --victim-backoff-max) victim_backoff_max="$2"; shift 2 ;;
    --attacker-backoff-max) attacker_backoff_max="$2"; shift 2 ;;
    --victim-stride) victim_stride="$2"; shift 2 ;;
    --attacker-stride) attacker_stride="$2"; shift 2 ;;
    --fixed-victim-addr) fixed_victim_addr="$2"; shift 2 ;;
    --fixed-attacker-addr) fixed_attacker_addr="$2"; shift 2 ;;
    --victim-fallback-addr) victim_fallback_addr="$2"; shift 2 ;;
    --fail-stats) fail_stats=1; shift ;;
    --enforce-no-smt-siblings) enforce_no_smt=1; shift ;;
    --ccbench) ccbench="$2"; shift 2 ;;
    --output-dir) output_dir="$2"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -n "$control_test" ]]; then
  control_tests_csv="$control_test"
fi

[[ -n "$victim_cores" && -n "$attacker_cores" ]] || { echo "--victim-cores and --attacker-cores are required." >&2; exit 1; }
[[ -x "$ccbench" ]] || { echo "ccbench binary not found or not executable: $ccbench" >&2; exit 1; }
[[ -f include/ccbench.h ]] || { echo "include/ccbench.h not found; run from repo root." >&2; exit 1; }

for n in "$victim_reps" "$attacker_reps" "$replicates" "$random_seed" "$warmup_reps" "$inter_phase_sleep_ms"; do
  [[ "$n" =~ ^[0-9]+$ ]] || { echo "Numeric option expected integer, got: $n" >&2; exit 1; }
done

mkdir -p "$output_dir/logs"

resolve_test_id() {
  local spec="$1"
  if [[ "$spec" =~ ^[0-9]+$ ]]; then printf '%s\n' "$spec"; return 0; fi
  awk -v name="$spec" '
    /const char\* moesi_type_des\[/ {in_arr=1; next}
    in_arr && /\};/ {exit}
    in_arr && /"/ {
      line=$0
      gsub(/^[[:space:]]*"|",?[[:space:]]*$/, "", line)
      if (line == name) { print idx; found=1; exit }
      idx++
    }
    END { if (!found) exit 1 }
  ' include/ccbench.h
}

as_array() { local csv="$1"; local -n ref="$2"; IFS=',' read -r -a ref <<<"$csv"; }
contains_value() { local needle="$1"; shift; local x; for x in "$@"; do [[ "$x" == "$needle" ]] && return 0; done; return 1; }
get_sibling_list() { local cpu="$1"; local p="/sys/devices/system/cpu/cpu${cpu}/topology/thread_siblings_list"; [[ -f "$p" ]] && cat "$p" || printf '%s' "$cpu"; }

make_list() {
  local count="$1" value="$2" out=""
  for ((i=0; i<count; i++)); do
    [[ -n "$out" ]] && out+=",$value" || out="$value"
  done
  printf '[%s]' "$out"
}

slice_cores() {
  local count="$1"; shift
  local -a src=("$@")
  local out=""
  for ((i=0; i<count; i++)); do
    [[ -n "$out" ]] && out+=",${src[$i]}" || out="${src[$i]}"
  done
  printf '[%s]' "$out"
}

is_crash_exit_code() { local rc="$1"; [[ "$rc" -eq 134 || "$rc" -eq 139 ]]; }

build_cmd() {
  local reps="$1" tests="$2" cores="$3" seed="$4" stride="$5" fixed_addr="$6"
  local -a cmd=("$ccbench" -r "$reps" -t "$tests" -x "$cores" -b "$seed" -s "$stride")
  [[ "$fixed_addr" != "none" ]] && cmd+=(-Z "$fixed_addr")
  local op_id
  op_id=$(echo "$tests" | sed -E 's/^\[([0-9]+).*/\1/')
  if [[ "$op_id" == "34" ]]; then
    local bmax="$7"
    cmd+=(-B -M "$bmax")
  fi
  cmd+=("${common_extra[@]}")
  printf '%s\n' "${cmd[@]}"
}

run_probe_logged() {
  local probe_log="$1"; shift
  local -a cmd=("$@")
  set +e
  "${cmd[@]}" >"$probe_log" 2>&1
  local rc=$?
  set -e
  return "$rc"
}

adaptive_victim_preflight() {
  [[ "$dry_run" -eq 1 ]] && return 0
  local preflight_log="$output_dir/logs/preflight_victim_probe.log"
  local attempt=1
  while true; do
    local -a probe_cmd
    mapfile -t probe_cmd < <(build_cmd "1" "$victim_tests" "$victim_core_list" "$seed_core" "$victim_stride" "$fixed_victim_addr" "$victim_backoff_max")
    local safe_addr="${fixed_victim_addr//[^a-zA-Z0-9]/_}"
    local attempt_log="$output_dir/logs/preflight_victim_probe_attempt${attempt}_addr_${safe_addr}_failstats_${fail_stats_effective}.log"
    echo "=== Preflight: victim probe (attempt=$attempt, addr=$fixed_victim_addr, fail-stats=$fail_stats_effective) ==="
    local rc
    if run_probe_logged "$attempt_log" "${probe_cmd[@]}"; then rc=0; else rc=$?; fi
    cp "$attempt_log" "$preflight_log"
    [[ "$rc" -eq 0 ]] && return 0

    if is_crash_exit_code "$rc" && [[ "$fail_stats_effective" -eq 1 ]]; then
      echo "WARNING: preflight crashed with --fail-stats (exit $rc). Auto-disabling --fail-stats." >&2
      fail_stats_effective=0
      fail_stats_auto_disabled=1
      common_extra=()
      ((attempt++))
      continue
    fi
    if is_crash_exit_code "$rc" && [[ "$fixed_victim_addr" == "static" ]]; then
      echo "WARNING: preflight crashed with static victim line. Falling back to $victim_fallback_addr." >&2
      fixed_victim_addr="$victim_fallback_addr"
      victim_addr_auto_fallback=1
      ((attempt++))
      continue
    fi
    if is_crash_exit_code "$rc" && [[ "$fixed_victim_addr" != "none" ]]; then
      echo "WARNING: preflight still crashes with fixed victim address. Retrying with --fixed-victim-addr none." >&2
      fixed_victim_addr="none"
      victim_fixed_disabled=1
      ((attempt++))
      continue
    fi

    echo "ERROR: victim preflight failed with exit code $rc. See $preflight_log" >&2
    return "$rc"
  done
}

extract_run_stats() {
  local log_file="$1"
  awk '
    /Summary : mean avg/ { if (match($0, /mean avg[[:space:]]*([0-9.]+)/, m)) mean=m[1] }
    /Jain fairness/ { if (match($0, /Jain fairness[^0-9]*([0-9.]+)/, m)) fair=m[1] }
    /success rate/ { if (match($0, /success rate[^0-9]*([0-9.]+)/, m)) succ=m[1] }
    /Winner==argmin\(B4[[:space:]]*->[[:space:]]*success\)/ { if (match($0, /\(([0-9.]+)%\)/, m)) succ=m[1] }
    /First-success winners per thread/ { in_winners=1; next }
    in_winners && /^[[:space:]]*Group[[:space:]]+/ {
      if (match($0, /: *([0-9]+) wins/, m)) {
        w = m[1] + 0; sum += w; sumsq += (w * w); cnt += 1
      }
      next
    }
    in_winners && !/^[[:space:]]*Group[[:space:]]+/ { in_winners=0 }
    END {
      if (fair == "" && cnt > 0 && sumsq > 0) fair = (sum * sum) / (cnt * sumsq)
      if (mean == "") mean = "NA"
      if (fair == "") fair = "NA"
      if (succ == "") succ = "NA"
      if (fair != "NA") fair = sprintf("%.4f", fair)
      printf "%s,%s,%s", mean, fair, succ
    }
  ' "$log_file"
}

run_logged() {
  local log_file="$1"; shift
  local -a cmd=("$@")
  printf 'Running: ' && printf '%q ' "${cmd[@]}" && printf '\n'
  [[ "$dry_run" -eq 1 ]] && return 0
  set +e
  "${cmd[@]}" | tee "$log_file"
  local rc=$?
  set -e
  if is_crash_exit_code "$rc"; then
    echo "ERROR: ccbench crashed (exit $rc)." >&2
  fi
  return "$rc"
}

run_with_synchronized_attacker() {
  local victim_log="$1" attacker_log="$2" victim_var="$3" attacker_var="$4"
  local -n victim_cmd_ref="$victim_var"
  local -n attacker_cmd_ref="$attacker_var"

  if [[ "$dry_run" -eq 1 ]]; then
    echo "(synchronized start) victim+attacker"
    printf 'ATTACKER: ' && printf '%q ' "${attacker_cmd_ref[@]}" && printf '\n'
    printf 'VICTIM:   ' && printf '%q ' "${victim_cmd_ref[@]}" && printf '\n'
    return 0
  fi

  local gate_attacker="$output_dir/.start_gate_attacker_$$.fifo"
  local gate_victim="$output_dir/.start_gate_victim_$$.fifo"
  mkfifo "$gate_attacker" "$gate_victim"

  cleanup_sync() {
    rm -f "$gate_attacker" "$gate_victim"
    if [[ -n "${attacker_pid:-}" ]]; then kill "$attacker_pid" >/dev/null 2>&1 || true; wait "$attacker_pid" >/dev/null 2>&1 || true; fi
    if [[ -n "${victim_pid:-}" ]]; then wait "$victim_pid" >/dev/null 2>&1 || true; fi
  }
  trap cleanup_sync EXIT INT TERM

  ( read -r _ < "$gate_attacker"; "${attacker_cmd_ref[@]}" >>"$attacker_log" 2>&1 ) & attacker_pid=$!
  ( read -r _ < "$gate_victim"; "${victim_cmd_ref[@]}" ) | tee "$victim_log" & victim_pid=$!

  echo go > "$gate_attacker"
  echo go > "$gate_victim"

  wait "$victim_pid"
  kill "$attacker_pid" >/dev/null 2>&1 || true
  wait "$attacker_pid" >/dev/null 2>&1 || true

  trap - EXIT INT TERM
  rm -f "$gate_attacker" "$gate_victim"
}

sleep_ms() {
  local ms="$1"
  [[ "$ms" -le 0 ]] && return 0
  python - "$ms" <<'PY'
import sys,time
ms=int(sys.argv[1])
time.sleep(ms/1000.0)
PY
}

as_array "$victim_cores" victim_core_arr
as_array "$attacker_cores" attacker_core_arr
[[ "${#victim_core_arr[@]}" -gt 0 && "${#attacker_core_arr[@]}" -gt 0 ]] || { echo "Core lists must not be empty." >&2; exit 1; }
for c in "${victim_core_arr[@]}" "${attacker_core_arr[@]}"; do [[ "$c" =~ ^[0-9]+$ ]] || { echo "Core IDs must be integers: $c" >&2; exit 1; }; done
for c in "${victim_core_arr[@]}"; do contains_value "$c" "${attacker_core_arr[@]}" && { echo "Victim and attacker core sets must be disjoint: $c" >&2; exit 1; }; done

smt_overlap=0
for v in "${victim_core_arr[@]}"; do
  v_sib=$(get_sibling_list "$v")
  for a in "${attacker_core_arr[@]}"; do
    a_sib=$(get_sibling_list "$a")
    if [[ "$v_sib" == "$a_sib" ]]; then
      echo "SMT sibling overlap detected: victim core $v attacker core $a set $v_sib" >&2
      smt_overlap=1
    fi
  done
done
if [[ "$smt_overlap" -eq 1 && "$enforce_no_smt" -eq 1 ]]; then
  echo "Failing due to --enforce-no-smt-siblings." >&2
  exit 1
fi

victim_test_id=$(resolve_test_id "$victim_test") || { echo "Unknown --victim-test: $victim_test" >&2; exit 1; }
attacker_test_id=$(resolve_test_id "$attacker_test") || { echo "Unknown --attacker-test: $attacker_test" >&2; exit 1; }

as_array "$control_tests_csv" control_test_name_arr
[[ "${#control_test_name_arr[@]}" -gt 0 ]] || { echo "--control-tests must not be empty" >&2; exit 1; }
control_test_ids=()
control_test_name_norm=()
for ct in "${control_test_name_arr[@]}"; do
  ct="${ct//[[:space:]]/}"
  [[ -n "$ct" ]] || continue
  ct_id=$(resolve_test_id "$ct") || { echo "Unknown control test in --control-tests: $ct" >&2; exit 1; }
  control_test_ids+=("$ct_id")
  control_test_name_norm+=("$ct")
done
control_test_name_arr=("${control_test_name_norm[@]}")
[[ "${#control_test_name_arr[@]}" -gt 0 ]] || { echo "No valid control tests in --control-tests" >&2; exit 1; }

[[ -z "$seed_core" ]] && seed_core="${victim_core_arr[0]}"
[[ -z "$attacker_seed_core" ]] && attacker_seed_core="${attacker_core_arr[0]}"

victim_count="${#victim_core_arr[@]}"
max_attacker_count="${#attacker_core_arr[@]}"
victim_tests=$(make_list "$victim_count" "$victim_test_id")
victim_core_list="[$victim_cores]"

[[ -z "$attacker_thread_sweep" ]] && attacker_thread_sweep="$max_attacker_count"
as_array "$attacker_thread_sweep" attacker_count_arr
for n in "${attacker_count_arr[@]}"; do
  [[ "$n" =~ ^[0-9]+$ ]] || { echo "--attacker-thread-sweep values must be integers: $n" >&2; exit 1; }
  (( n >= 1 && n <= max_attacker_count )) || { echo "attacker thread count $n out of range [1,$max_attacker_count]" >&2; exit 1; }
done

raw_csv="$output_dir/raw_results.csv"
pairs_csv="$output_dir/replicate_pairs.csv"
summary_csv="$output_dir/summary_normalized.csv"
legacy_summary_csv="$output_dir/summary_normalised.csv"
printf '%s\n' 'replicate,phase,attacker_threads,attacker_mode,victim_test,attacker_test,order_idx,mean_avg,jain_fairness,success_rate,log_path' > "$raw_csv"
printf '%s\n' 'replicate,attacker_threads,control_test,baseline_mean,rmw_mean,control_mean,delta,delta_pct,norm_delta' > "$pairs_csv"

common_extra=()
[[ "$fail_stats" -eq 1 ]] && common_extra+=(--fail-stats)
fail_stats_effective="$fail_stats"

adaptive_victim_preflight
RANDOM=$random_seed

cat > "$output_dir/run_meta.txt" <<META
Experiment mode:          adversarial-normalized-crossover
Victim cores:             $victim_core_list
Attacker cores (max):     [$attacker_cores]
Victim test:              $victim_test (id=$victim_test_id)
Attacker test (RMW):      $attacker_test (id=$attacker_test_id)
Attacker control tests:   $control_tests_csv
Replicates:               $replicates
Random seed:              $random_seed
Warmup reps:              $warmup_reps
Inter-phase sleep ms:     $inter_phase_sleep_ms
Attacker thread sweep:    $attacker_thread_sweep
Victim fixed line:        $fixed_victim_addr
Victim fallback addr:     $victim_fallback_addr
Victim addr auto-fallback:$victim_addr_auto_fallback
Victim fixed disabled:    $victim_fixed_disabled
Attacker fixed line:      $fixed_attacker_addr
Victim seed core:         $seed_core
Attacker seed core:       $attacker_seed_core
Victim reps:              $victim_reps
Attacker reps:            $attacker_reps
SMT overlap detected:     $smt_overlap
Fail stats requested:     $fail_stats
Fail stats effective:     $fail_stats_effective
Fail stats auto-disabled: $fail_stats_auto_disabled
Raw results CSV:          $raw_csv
Pairs CSV:                $pairs_csv
Summary CSV:              $summary_csv
Summary CSV (legacy):     $legacy_summary_csv
META

for ((rep=1; rep<=replicates; rep++)); do
  echo
  echo "=== Replicate $rep/$replicates ==="
  baseline_log="$output_dir/logs/rep${rep}_victim_baseline.log"
  mapfile -t victim_base_cmd < <(build_cmd "$victim_reps" "$victim_tests" "$victim_core_list" "$seed_core" "$victim_stride" "$fixed_victim_addr" "$victim_backoff_max")

  if [[ "$warmup_reps" -gt 0 ]]; then
    warm_log="$output_dir/logs/rep${rep}_warmup_baseline.log"
    mapfile -t warm_cmd < <(build_cmd "$warmup_reps" "$victim_tests" "$victim_core_list" "$seed_core" "$victim_stride" "$fixed_victim_addr" "$victim_backoff_max")
    run_logged "$warm_log" "${warm_cmd[@]}" >/dev/null
    sleep_ms "$inter_phase_sleep_ms"
  fi

  echo "=== Phase: victim_baseline (replicate=$rep) ==="
  run_logged "$baseline_log" "${victim_base_cmd[@]}"
  if [[ "$dry_run" -eq 0 ]]; then
    IFS=',' read -r base_mean base_fair base_succ <<<"$(extract_run_stats "$baseline_log")"
  else
    base_mean="NA"; base_fair="NA"; base_succ="NA"
  fi
  printf 'rep%02d,victim_baseline,0,none,%s,none,0,%s,%s,%s,%s\n' "$rep" "$victim_test" "$base_mean" "$base_fair" "$base_succ" "$baseline_log" >> "$raw_csv"

  for a_threads in "${attacker_count_arr[@]}"; do
    attacker_core_list=$(slice_cores "$a_threads" "${attacker_core_arr[@]}")
    attacker_tests=$(make_list "$a_threads" "$attacker_test_id")
    mapfile -t rmw_cmd < <(build_cmd "$attacker_reps" "$attacker_tests" "$attacker_core_list" "$attacker_seed_core" "$attacker_stride" "$fixed_attacker_addr" "$attacker_backoff_max")

    for ci in "${!control_test_name_arr[@]}"; do
      control_name="${control_test_name_arr[$ci]}"
      control_id="${control_test_ids[$ci]}"
      control_tests=$(make_list "$a_threads" "$control_id")
      mapfile -t ctrl_cmd < <(build_cmd "$attacker_reps" "$control_tests" "$attacker_core_list" "$attacker_seed_core" "$attacker_stride" "$fixed_attacker_addr" "$attacker_backoff_max")

      if (( RANDOM % 2 == 0 )); then order=(rmw control); else order=(control rmw); fi

      unset mean_map
      declare -A mean_map

      idx=0
      for mode in "${order[@]}"; do
        ((idx+=1))
        if [[ "$mode" == "rmw" ]]; then
          victim_log="$output_dir/logs/rep${rep}_victim_with_attacker_rmw_t${a_threads}_vs_${control_name}.log"
          attacker_log="$output_dir/logs/rep${rep}_attacker_rmw_t${a_threads}_vs_${control_name}.log"
          attacker_name="$attacker_test"
          cmd_name=rmw_cmd
        else
          victim_log="$output_dir/logs/rep${rep}_victim_with_attacker_control_${control_name}_t${a_threads}.log"
          attacker_log="$output_dir/logs/rep${rep}_attacker_control_${control_name}_t${a_threads}.log"
          attacker_name="$control_name"
          cmd_name=ctrl_cmd
        fi

        if [[ "$warmup_reps" -gt 0 ]]; then
          warm_log="$output_dir/logs/rep${rep}_warmup_${mode}_${control_name}_t${a_threads}.log"
          mapfile -t warm_cmd < <(build_cmd "$warmup_reps" "$victim_tests" "$victim_core_list" "$seed_core" "$victim_stride" "$fixed_victim_addr" "$victim_backoff_max")
          run_logged "$warm_log" "${warm_cmd[@]}" >/dev/null
        fi

        echo "=== Phase: victim_plus_attacker_${mode} (replicate=$rep threads=$a_threads control=$control_name order=$idx/${#order[@]}) ==="
        run_with_synchronized_attacker "$victim_log" "$attacker_log" victim_base_cmd "$cmd_name"
        if [[ "$dry_run" -eq 0 ]]; then
          IFS=',' read -r mean fair succ <<<"$(extract_run_stats "$victim_log")"
        else
          mean="NA"; fair="NA"; succ="NA"
        fi
        mean_map["$mode"]="$mean"
        printf 'rep%02d,victim_plus_attacker_%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' "$rep" "$mode" "$a_threads" "$mode" "$victim_test" "$attacker_name" "$idx" "$mean" "$fair" "$succ" "$victim_log" >> "$raw_csv"
        sleep_ms "$inter_phase_sleep_ms"
      done

      rmw_mean="${mean_map[rmw]:-NA}"
      ctrl_mean="${mean_map[control]:-NA}"
      if [[ "$rmw_mean" != "NA" && "$ctrl_mean" != "NA" && "$base_mean" != "NA" ]]; then
        read -r delta delta_pct norm_delta < <(python - "$rmw_mean" "$ctrl_mean" "$base_mean" <<'PY'
import sys
rmw=float(sys.argv[1]); ctl=float(sys.argv[2]); base=float(sys.argv[3])
d=rmw-ctl
pct=(d/ctl*100.0) if ctl else float('nan')
nd=(d/base) if base else float('nan')
print(f"{d:.6f} {pct:.6f} {nd:.6f}")
PY
)
        printf 'rep%02d,%s,%s,%s,%s,%s,%s,%s,%s\n' "$rep" "$a_threads" "$control_name" "$base_mean" "$rmw_mean" "$ctrl_mean" "$delta" "$delta_pct" "$norm_delta" >> "$pairs_csv"
      fi
    done
  done
done

python - "$pairs_csv" "$summary_csv" <<'PY'
import csv, sys, math
from collections import defaultdict

pairs_csv, out_csv = sys.argv[1], sys.argv[2]
vals = defaultdict(lambda: {"delta": [], "norm_delta": [], "delta_pct": []})
with open(pairs_csv, newline='') as f:
    r = csv.DictReader(f)
    for row in r:
        key = (int(row["attacker_threads"]), row["control_test"])
        vals[key]["delta"].append(float(row["delta"]))
        vals[key]["norm_delta"].append(float(row["norm_delta"]))
        vals[key]["delta_pct"].append(float(row["delta_pct"]))

def quantile(arr, q):
    arr = sorted(arr)
    if not arr:
        return float('nan')
    if len(arr) == 1:
        return arr[0]
    i = (len(arr)-1) * q
    lo = int(math.floor(i))
    hi = int(math.ceil(i))
    frac = i - lo
    return arr[lo] * (1-frac) + arr[hi] * frac

with open(out_csv, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow([
        "attacker_threads",
        "control_test",
        "n",
        "median_delta",
        "q1_delta",
        "q3_delta",
        "median_delta_pct",
        "median_norm_delta",
        "sign_consistency_pos"
    ])
    for key in sorted(vals):
        t, ctl = key
        d = vals[key]["delta"]
        dp = vals[key]["delta_pct"]
        nd = vals[key]["norm_delta"]
        n = len(d)
        pos = sum(1 for x in d if x > 0) / n if n else float('nan')
        w.writerow([
            t,
            ctl,
            n,
            f"{quantile(d,0.5):.6f}",
            f"{quantile(d,0.25):.6f}",
            f"{quantile(d,0.75):.6f}",
            f"{quantile(dp,0.5):.6f}",
            f"{quantile(nd,0.5):.6f}",
            f"{pos:.6f}",
        ])
PY

cp "$summary_csv" "$legacy_summary_csv"

if [[ "$dry_run" -eq 0 ]]; then
  raw_rows=$(wc -l < "$raw_csv")
  if [[ "$raw_rows" -le 1 ]]; then
    echo "WARNING: $raw_csv has no measured phase rows (only header). Check preflight logs under $output_dir/logs/." >&2
  fi
fi

echo
echo "Done. Artifacts:"
echo "  $output_dir/run_meta.txt"
echo "  $raw_csv"
echo "  $pairs_csv"
echo "  $summary_csv"
echo "  $legacy_summary_csv"
echo "  $output_dir/logs/"
