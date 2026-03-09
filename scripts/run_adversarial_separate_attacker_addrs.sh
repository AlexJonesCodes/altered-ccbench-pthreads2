#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/run_adversarial_separate_attacker_addrs.sh [options]

Adversarial fairness/slowdown test where attacker traffic is compared across:
  1) victim_baseline          : victim alone
  2) victim_plus_shared       : one attacker process per attacker core, all sharing one cache line
  3) victim_plus_separate     : one attacker process per attacker core, each using a different cache line

This directly answers: "do attackers touching separate addresses still induce unfairness/slowdown?"

The run can take a long time with large --attacker-reps; phase-start messages are printed
so long-running phases do not look like a hang.

Options:
  --victim-cores LIST             Comma-separated victim cores (required)
  --attacker-cores LIST           Comma-separated attacker cores (required)
  --victim-test NAME|ID           Victim primitive (default: CAS)
  --attacker-test NAME|ID         Attacker primitive (default: FAI)
  --victim-reps N                 Victim repetitions (default: 20000)
  --attacker-reps N               Attacker repetitions (default: 200000000)
  --seed-core N                   Victim seed core (default: first victim core)
  --attacker-seed-core N          (compat option; ignored, per-core seed is used)
  --victim-stride N               Victim stride (default: 1)
  --attacker-stride N             Attacker stride (default: 1)
  --fixed-victim-addr SPEC        static|0xHEX|none (default: static)
  --shared-attacker-addr HEX      Shared attacker line (default: 0x700000100000)
  --separate-attacker-base HEX    Base address for separate attackers (default: 0x700000300000)
  --separate-attacker-step HEX    Address step per attacker proc (default: 0x1000)
  --output-dir DIR                Output dir (default: results/adversarial_separate_attacker_addrs)
  --victim-fallback-addr HEX      Victim fallback address if static segfaults
                                  (default: 0x700000200000)
  --fail-stats                    Enable fail stats; auto-disabled if probe segfaults
  --perf-counters                 Collect perf stat counters per phase (victim process)
  --perf-events LIST              Comma-separated perf events for perf stat
                                  (default: cycles,instructions,cache-references,cache-misses)
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
victim_fallback_addr="0x700000200000"
fail_stats=0
fail_stats_effective=0
fail_stats_auto_disabled=0
victim_addr_auto_fallback=0
victim_fixed_disabled=0
perf_counters=0
perf_events="cycles,instructions,cache-references,cache-misses"
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
    --victim-fallback-addr) victim_fallback_addr="$2"; shift 2 ;;
    --fail-stats) fail_stats=1; shift ;;
    --perf-counters) perf_counters=1; shift ;;
    --perf-events) perf_events="$2"; shift 2 ;;
    --ccbench) ccbench="$2"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

[[ -n "$victim_cores" && -n "$attacker_cores" ]] || { echo "--victim-cores and --attacker-cores are required." >&2; exit 1; }
[[ -x "$ccbench" ]] || { echo "ccbench not found/executable: $ccbench" >&2; exit 1; }
[[ -f include/ccbench.h ]] || { echo "include/ccbench.h not found; run from repo root." >&2; exit 1; }
if [[ "$perf_counters" -eq 1 ]] && ! command -v perf &>/dev/null; then
  echo "ERROR: --perf-counters requested but 'perf' is not installed." >&2
  exit 1
fi

as_array() { local csv="$1"; local -n ref="$2"; IFS=',' read -r -a ref <<<"$csv"; }
make_list() { local n="$1" v="$2" out=""; for ((i=0;i<n;i++)); do [[ -n "$out" ]] && out+=",$v" || out="$v"; done; printf '[%s]' "$out"; }
hex_to_dec() { printf '%d' "$(( $1 ))"; }
dec_to_hex() { printf '0x%x' "$1"; }

is_crash_exit_code() { local rc="$1"; [[ "$rc" -eq 134 || "$rc" -eq 139 ]]; }
log_info() { echo "INFO: $*" >&2; }

run_cmd_quiet() {
  local log_file="$1"; shift
  python - "$log_file" "$@" <<'PYQ'
import subprocess, sys
log = sys.argv[1]
cmd = sys.argv[2:]
with open(log, 'w', encoding='utf-8', errors='replace') as f:
    rc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT).returncode
if rc < 0:
    rc = 128 + (-rc)
sys.exit(rc)
PYQ
}

build_victim_cmd() {
  local reps="$1"
  local -a cmd=("$ccbench" -r "$reps" -t "$victim_tests" -x "$victim_core_list" -b "$seed_core" -s "$victim_stride")
  [[ "$fixed_victim_addr" != "none" ]] && cmd+=(-Z "$fixed_victim_addr")
  [[ "$fail_stats_effective" -eq 1 ]] && cmd+=(-f)
  printf '%s\n' "${cmd[@]}"
}

run_probe_logged() {
  local probe_log="$1"; shift
  local -a cmd=("$@")
  set +e
  run_cmd_quiet "$probe_log" "${cmd[@]}"
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
    mapfile -t probe_cmd < <(build_victim_cmd "1")
    local safe_addr="${fixed_victim_addr//[^a-zA-Z0-9]/_}"
    local attempt_log="$output_dir/logs/preflight_victim_probe_attempt${attempt}_addr_${safe_addr}_failstats_${fail_stats_effective}.log"
    local rc
    if run_probe_logged "$attempt_log" "${probe_cmd[@]}"; then rc=0; else rc=$?; fi
    cp "$attempt_log" "$preflight_log"
    if [[ "$rc" -eq 0 ]]; then
      log_info "victim preflight succeeded (attempt=$attempt, fixed-victim-addr=$fixed_victim_addr, fail-stats=$fail_stats_effective)"
      return 0
    fi

    if is_crash_exit_code "$rc" && [[ "$fail_stats_effective" -eq 1 ]]; then
      echo "WARNING: victim preflight crashed with --fail-stats; auto-disabling --fail-stats." >&2
      fail_stats_effective=0
      fail_stats_auto_disabled=1
      ((attempt++))
      continue
    fi
    if is_crash_exit_code "$rc" && [[ "$fixed_victim_addr" == "static" ]]; then
      echo "WARNING: victim preflight crashed with static victim address. Falling back to $victim_fallback_addr" >&2
      fixed_victim_addr="$victim_fallback_addr"
      victim_addr_auto_fallback=1
      ((attempt++))
      continue
    fi
    if is_crash_exit_code "$rc" && [[ "$fixed_victim_addr" != "none" ]]; then
      echo "WARNING: victim preflight still crashes with fixed victim address. Retrying with --fixed-victim-addr none" >&2
      fixed_victim_addr="none"
      victim_fixed_disabled=1
      ((attempt++))
      continue
    fi

    echo "ERROR: victim preflight failed with rc=$rc. See $preflight_log" >&2
    return "$rc"
  done
}

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
fail_stats_effective="$fail_stats"
adaptive_victim_preflight

meta_file="$output_dir/run_meta.txt"
cat > "$meta_file" <<META
fixed_victim_addr_effective=$fixed_victim_addr
fail_stats_requested=$fail_stats
fail_stats_effective=$fail_stats_effective
fail_stats_auto_disabled=$fail_stats_auto_disabled
victim_addr_auto_fallback=$victim_addr_auto_fallback
victim_fixed_disabled=$victim_fixed_disabled
attacker_launch_mode=per_core_processes_for_shared_and_separate
perf_counters=$perf_counters
perf_events=$perf_events
META

echo "phase,mean_avg,jain_fairness,success_rate,latency_ratio_vs_baseline,latency_delta_pct_vs_baseline,effect_vs_baseline,notes" > "$summary_csv"
log_info "effective victim config: fixed-victim-addr=$fixed_victim_addr, fail-stats=$fail_stats_effective"
log_info "starting experiment (victim_reps=$victim_reps, attacker_reps=$attacker_reps, victim_threads=$victim_count, attacker_threads=$attacker_count)"

run_cmd_logged() {
  local log_file="$1"; shift
  local perf_file="${1:-}"
  if [[ $# -gt 0 ]]; then
    shift
  fi
  local -a cmd=("$@")
  if [[ "$dry_run" -eq 1 ]]; then
    printf '[dry-run] %q ' "${cmd[@]}" >&2; echo >&2
    : > "$log_file"
    [[ -n "$perf_file" ]] && : > "$perf_file"
    return 0
  fi
  if [[ "$perf_counters" -eq 1 && -n "$perf_file" ]]; then
    run_cmd_quiet "$log_file" perf stat -x, -o "$perf_file" -e "$perf_events" -- "${cmd[@]}"
    return $?
  fi
  run_cmd_quiet "$log_file" "${cmd[@]}"
}

run_baseline() {
  log_info "phase start: victim_baseline"
  local log="$output_dir/logs/victim_baseline.log"
  local perf_log="$output_dir/logs/perf_victim_baseline.csv"
  local -a cmd
  mapfile -t cmd < <(build_victim_cmd "$victim_reps")
  run_cmd_logged "$log" "$perf_log" "${cmd[@]}"
  log_info "phase done: victim_baseline (log=$log)"
  extract_stats "$log"
}

run_with_shared_attackers() {
  local mode="$1"
  log_info "phase start: victim_plus_${mode}"
  local v_log="$output_dir/logs/victim_plus_${mode}.log"
  local v_perf_log="$output_dir/logs/perf_victim_plus_${mode}.csv"
  local fifo="$output_dir/.start_fifo_${mode}"
  rm -f "$fifo"; mkfifo "$fifo"

  local -a pids=()
  local -a victim_cmd
  mapfile -t victim_cmd < <(build_victim_cmd "$victim_reps")

  if [[ "$dry_run" -eq 1 ]]; then
    for core in "${attacker_core_arr[@]}"; do
      printf '[dry-run] shared attacker core=%s addr=%s\n' "$core" "$shared_attacker_addr" >&2
    done
    printf '[dry-run] shared victim:   %q ' "${victim_cmd[@]}" >&2; echo >&2
    : > "$v_log"; rm -f "$fifo"
    extract_stats "$v_log"; return
  fi

  for core in "${attacker_core_arr[@]}"; do
    local a_log="$output_dir/logs/attacker_${mode}_core${core}.log"
    ( read -r _ < "$fifo"; run_cmd_quiet "$a_log" "$ccbench" -r "$attacker_reps" -t "[$attacker_test_id]" -x "[$core]" -b "$core" -s "$attacker_stride" -Z "$shared_attacker_addr" ) &
    pids+=("$!")
  done

  sleep 0.1
  ( read -r _ < "$fifo"; run_cmd_logged "$v_log" "$v_perf_log" "${victim_cmd[@]}" ) &
  local victim_pid=$!
  sleep 0.1

  local signals=$((attacker_count + 1))
  for ((i=0; i<signals; i++)); do printf 'go\n'; done > "$fifo"

  local victim_rc=0
  if ! wait "$victim_pid"; then victim_rc=$?; fi
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
  rm -f "$fifo"
  if [[ "$victim_rc" -ne 0 ]]; then
    echo "WARNING: victim_plus_${mode} failed with rc=$victim_rc; see $v_log" >&2
    printf "NA,NA,NA"
    return 0
  fi
  log_info "phase done: victim_plus_${mode} (log=$v_log)"
  extract_stats "$v_log"
}

run_with_separate_attackers() {
  log_info "phase start: victim_plus_separate"
  local v_log="$output_dir/logs/victim_plus_separate.log"
  local v_perf_log="$output_dir/logs/perf_victim_plus_separate.csv"
  local fifo="$output_dir/.start_fifo_separate"
  rm -f "$fifo"; mkfifo "$fifo"

  local -a pids=()
  local base_dec step_dec
  base_dec=$(hex_to_dec "$separate_attacker_base")
  step_dec=$(hex_to_dec "$separate_attacker_step")

  local -a victim_cmd
  mapfile -t victim_cmd < <(build_victim_cmd "$victim_reps")

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
    ( read -r _ < "$fifo"; run_cmd_quiet "$a_log" "$ccbench" -r "$attacker_reps" -t "[$attacker_test_id]" -x "[$core]" -b "$core" -s "$attacker_stride" -Z "$addr" ) &
    pids+=("$!")
  done

  sleep 0.1
  ( read -r _ < "$fifo"; run_cmd_logged "$v_log" "$v_perf_log" "${victim_cmd[@]}" ) &
  local victim_pid=$!
  sleep 0.1

  local signals=$((attacker_count + 1))
  for ((i=0; i<signals; i++)); do printf 'go\n'; done > "$fifo"

  local victim_rc=0
  if ! wait "$victim_pid"; then victim_rc=$?; fi
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
  rm -f "$fifo"
  if [[ "$victim_rc" -ne 0 ]]; then
    echo "WARNING: victim_plus_separate failed with rc=$victim_rc; see $v_log" >&2
    printf "NA,NA,NA"
    return 0
  fi
  log_info "phase done: victim_plus_separate (log=$v_log)"
  extract_stats "$v_log"
}

baseline_stats=$(run_baseline)
shared_stats=$(run_with_shared_attackers shared)
separate_stats=$(run_with_separate_attackers)

IFS=',' read -r base_mean base_fair base_succ <<<"$baseline_stats"
IFS=',' read -r shared_mean shared_fair shared_succ <<<"$shared_stats"
IFS=',' read -r sep_mean sep_fair sep_succ <<<"$separate_stats"

calc_latency_ratio() {
  local base="$1" now="$2"
  if [[ "$base" == "NA" || "$now" == "NA" ]]; then printf 'NA'; return; fi
  awk -v b="$base" -v n="$now" 'BEGIN { if (b==0) {print "NA"} else {printf "%.4f", n/b} }'
}

calc_latency_delta_pct() {
  local base="$1" now="$2"
  if [[ "$base" == "NA" || "$now" == "NA" ]]; then printf 'NA'; return; fi
  awk -v b="$base" -v n="$now" 'BEGIN { if (b==0) {print "NA"} else {printf "%.2f", ((n-b)/b)*100.0} }'
}

effect_label() {
  local ratio="$1"
  if [[ "$ratio" == "NA" ]]; then printf 'unknown'; return; fi
  awk -v r="$ratio" 'BEGIN {
    if (r > 1.0001) print "slower";
    else if (r < 0.9999) print "faster";
    else print "neutral";
  }'
}

shared_ratio=$(calc_latency_ratio "$base_mean" "$shared_mean")
sep_ratio=$(calc_latency_ratio "$base_mean" "$sep_mean")
shared_delta_pct=$(calc_latency_delta_pct "$base_mean" "$shared_mean")
sep_delta_pct=$(calc_latency_delta_pct "$base_mean" "$sep_mean")
shared_effect=$(effect_label "$shared_ratio")
sep_effect=$(effect_label "$sep_ratio")

echo "victim_baseline,$base_mean,$base_fair,$base_succ,1.0000,0.00,baseline,no_attackers" >> "$summary_csv"
shared_note="attackers_share_one_line"
sep_note="each_attacker_has_distinct_line"
[[ "$shared_mean" == "NA" ]] && shared_note+="_victim_failed"
[[ "$sep_mean" == "NA" ]] && sep_note+="_victim_failed"
echo "victim_plus_shared,$shared_mean,$shared_fair,$shared_succ,$shared_ratio,$shared_delta_pct,$shared_effect,$shared_note" >> "$summary_csv"
echo "victim_plus_separate,$sep_mean,$sep_fair,$sep_succ,$sep_ratio,$sep_delta_pct,$sep_effect,$sep_note" >> "$summary_csv"

report_file="$output_dir/diagnostic_summary.txt"

sep_vs_shared_ratio="NA"
sep_vs_shared_delta_pct="NA"
if [[ "$shared_mean" != "NA" && "$sep_mean" != "NA" ]]; then
  sep_vs_shared_ratio=$(awk -v s="$shared_mean" -v p="$sep_mean" 'BEGIN { if (s==0) print "NA"; else printf "%.4f", p/s }')
  sep_vs_shared_delta_pct=$(awk -v s="$shared_mean" -v p="$sep_mean" 'BEGIN { if (s==0) print "NA"; else printf "%.2f", ((p-s)/s)*100.0 }')
fi

fair_drop_shared="NA"
fair_drop_separate="NA"
if [[ "$base_fair" != "NA" && "$shared_fair" != "NA" ]]; then
  fair_drop_shared=$(awk -v b="$base_fair" -v s="$shared_fair" 'BEGIN { printf "%.4f", s - b }')
fi
if [[ "$base_fair" != "NA" && "$sep_fair" != "NA" ]]; then
  fair_drop_separate=$(awk -v b="$base_fair" -v s="$sep_fair" 'BEGIN { printf "%.4f", s - b }')
fi

interference_diagnosis="unknown"
if [[ "$shared_ratio" != "NA" && "$sep_ratio" != "NA" ]]; then
  interference_diagnosis=$(awk -v sr="$shared_ratio" -v pr="$sep_ratio" 'BEGIN {
    both_slow  = (sr > 1.05 && pr > 1.05)
    shared_only = (sr > 1.05 && pr <= 1.05)
    neither    = (sr <= 1.05 && pr <= 1.05)

    if (shared_only)
      print "coherence_hotspot"
    else if (both_slow && pr >= sr * 0.90)
      print "broad_interconnect"
    else if (both_slow)
      print "mixed"
    else if (neither)
      print "no_significant_interference"
    else
      print "inconclusive"
  }')
fi

fairness_diagnosis="unknown"
if [[ "$base_fair" != "NA" && "$shared_fair" != "NA" && "$sep_fair" != "NA" ]]; then
  fairness_diagnosis=$(awk -v bf="$base_fair" -v sf="$shared_fair" -v pf="$sep_fair" 'BEGIN {
    sd = bf - sf; pd = bf - pf
    if (sd > 0.05 && pd <= 0.02)
      print "shared_contention_unfair"
    else if (sd > 0.05 && pd > 0.05)
      print "both_unfair"
    else if (sd <= 0.02 && pd <= 0.02)
      print "fairness_preserved"
    else
      print "marginal"
  }')
fi

fmt_val() {
  local v="$1" w="${2:-12}"
  if [[ "$v" == "NA" ]]; then printf "%${w}s" "N/A"; else printf "%${w}s" "$v"; fi
}

generate_summary() {
cat <<'BANNER'
==========================================================================
          ADVERSARIAL SEPARATE-ADDRESS DIAGNOSTIC SUMMARY
==========================================================================
BANNER

printf "\n"
printf "  Experiment Configuration\n"
printf "  %-30s : %s\n" "Victim cores" "$victim_cores"
printf "  %-30s : %s\n" "Attacker cores" "$attacker_cores"
printf "  %-30s : %s\n" "Victim test" "$victim_test (id=$victim_test_id)"
printf "  %-30s : %s\n" "Attacker test" "$attacker_test (id=$attacker_test_id)"
printf "  %-30s : %s\n" "Victim reps" "$victim_reps"
printf "  %-30s : %s\n" "Attacker reps" "$attacker_reps"
printf "  %-30s : %s\n" "Seed core" "$seed_core"
printf "  %-30s : %s\n" "Fixed victim addr" "$fixed_victim_addr"
printf "  %-30s : %s\n" "Shared attacker addr" "$shared_attacker_addr"
printf "  %-30s : %s ...\n" "Separate attacker base" "$separate_attacker_base (+$separate_attacker_step)"
if [[ "$victim_addr_auto_fallback" -eq 1 ]]; then
  printf "  %-30s : %s\n" "NOTE" "victim addr fell back to $victim_fallback_addr"
fi
if [[ "$fail_stats_auto_disabled" -eq 1 ]]; then
  printf "  %-30s : %s\n" "NOTE" "fail-stats auto-disabled (crash)"
fi

cat <<'HDR'

--------------------------------------------------------------------------
  Phase Comparison                  Baseline      Shared     Separate
--------------------------------------------------------------------------
HDR

printf "  %-30s :%s  %s  %s\n" \
  "Mean latency (cycles)" "$(fmt_val "$base_mean")" "$(fmt_val "$shared_mean")" "$(fmt_val "$sep_mean")"
printf "  %-30s :%s  %s  %s\n" \
  "Jain fairness index" "$(fmt_val "$base_fair")" "$(fmt_val "$shared_fair")" "$(fmt_val "$sep_fair")"
printf "  %-30s :%s  %s  %s\n" \
  "Success rate (%)" "$(fmt_val "$base_succ")" "$(fmt_val "$shared_succ")" "$(fmt_val "$sep_succ")"

cat <<'HDR2'

--------------------------------------------------------------------------
  Latency Impact (vs Baseline)             Shared     Separate
--------------------------------------------------------------------------
HDR2

printf "  %-30s :         %s  %s\n" \
  "Ratio (>1 = slower)" "$(fmt_val "$shared_ratio")" "$(fmt_val "$sep_ratio")"
printf "  %-30s :         %s%%  %s%%\n" \
  "Delta" "$(fmt_val "$shared_delta_pct")" "$(fmt_val "$sep_delta_pct")"
printf "  %-30s :         %s  %s\n" \
  "Effect" "$(fmt_val "$shared_effect")" "$(fmt_val "$sep_effect")"

cat <<'HDR3'

--------------------------------------------------------------------------
  Cross-Phase Comparison             Separate vs Shared
--------------------------------------------------------------------------
HDR3

printf "  %-30s :         %s\n" "Latency ratio" "$(fmt_val "$sep_vs_shared_ratio")"
printf "  %-30s :         %s%%\n" "Delta" "$(fmt_val "$sep_vs_shared_delta_pct")"

cat <<'HDR4'

--------------------------------------------------------------------------
  Fairness Change (vs Baseline)        Shared     Separate
--------------------------------------------------------------------------
HDR4

printf "  %-30s :         %s  %s\n" \
  "Jain delta (neg = worse)" "$(fmt_val "$fair_drop_shared")" "$(fmt_val "$fair_drop_separate")"

cat <<'HDR5'

--------------------------------------------------------------------------
  Diagnosis
--------------------------------------------------------------------------
HDR5

printf "  %-30s : %s\n" "Interference pattern" "$interference_diagnosis"
printf "  %-30s : %s\n" "Fairness pattern" "$fairness_diagnosis"

printf "\n  Interpretation:\n"
case "$interference_diagnosis" in
  coherence_hotspot)
    printf "    Shared attackers slow the victim significantly, but separate attackers\n"
    printf "    do not. Interference is driven by cache-line coherence hotspot contention.\n"
    ;;
  broad_interconnect)
    printf "    Both shared and separate attackers slow the victim similarly.\n"
    printf "    Interference is from broad interconnect/memory-subsystem pressure,\n"
    printf "    not just single-line coherence.\n"
    ;;
  mixed)
    printf "    Both layouts cause slowdown, but shared is notably worse.\n"
    printf "    There is a coherence-hotspot component on top of general\n"
    printf "    interconnect interference.\n"
    ;;
  no_significant_interference)
    printf "    Neither layout causes meaningful slowdown (< 5%% overhead).\n"
    printf "    Attacker traffic at this intensity does not measurably affect the victim.\n"
    ;;
  *)
    printf "    Results are inconclusive or one/more phases failed.\n"
    printf "    Consider re-running with different parameters.\n"
    ;;
esac

printf "\n"
case "$fairness_diagnosis" in
  shared_contention_unfair)
    printf "    Fairness degrades under shared contention but is preserved when\n"
    printf "    attackers use separate lines. Unfairness is contention-driven.\n"
    ;;
  both_unfair)
    printf "    Fairness degrades in both configurations. Interference at this\n"
    printf "    intensity disrupts fair scheduling regardless of address layout.\n"
    ;;
  fairness_preserved)
    printf "    Fairness remains stable across all configurations.\n"
    ;;
  marginal)
    printf "    Fairness changes are small and may not be significant.\n"
    ;;
  *)
    printf "    Fairness analysis inconclusive (missing data).\n"
    ;;
esac

cat <<'FOOTER'

==========================================================================
  Files
==========================================================================
FOOTER

printf "  Summary CSV     : %s\n" "$summary_csv"
printf "  Run metadata    : %s\n" "$meta_file"
printf "  Diagnostic report : %s\n" "$report_file"
printf "  Logs            : %s/logs/\n" "$output_dir"

cat <<'FOOTER2'

==========================================================================
FOOTER2
}

generate_summary | tee "$report_file"
