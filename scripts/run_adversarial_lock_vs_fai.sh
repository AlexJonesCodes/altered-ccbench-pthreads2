#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/run_adversarial_lock_vs_fai.sh [options]

Adversarial coherence experiment:
  Victim group runs an atomic RMW primitive (CAS by default).
  Attacker group runs a different atomic RMW primitive (FAI by default) on a different shared line.

Phases per attacker intensity:
  1) victim_baseline
  2) victim_plus_attacker_rmw      (synchronized start; attacker single long run)
  3) victim_plus_attacker_control  (control attacker, default LOAD_FROM_L1)

Options:
  --victim-cores LIST             Comma-separated victim cores (required)
  --attacker-cores LIST           Comma-separated attacker cores (required)
  --victim-test NAME|ID           Victim atomic primitive (default: CAS)
  --attacker-test NAME|ID         Attacker primitive (default: FAI)
  --control-test NAME|ID          Control attacker primitive (default: LOAD_FROM_L1)
  --attacker-thread-sweep LIST    Attacker thread counts sweep, e.g. "1,2,4,8"
                                  (default: use full attacker core count only)
  --victim-reps N                 Victim repetitions per run (default: 20000)
  --max-auto-victim-reps N        Auto-cap victim reps for CAS_UNTIL_SUCCESS (id=34)
                                  when reps are very large (default: 5000)
  --no-auto-victim-reps-cap       Disable auto-cap behavior
  --attacker-reps N               Attacker repetitions per run (default: 200000000)
  --seed-core N                   Seed core for victim run (default: first victim core)
  --attacker-seed-core N          Seed core for attacker/control run (default: first attacker core)
  --victim-backoff-max N          Victim backoff max (used only if victim is CAS_UNTIL_SUCCESS, default: 1024)
  --attacker-backoff-max N        Attacker backoff max (used only if attacker is CAS_UNTIL_SUCCESS, default: 1)
  --victim-stride N               Victim stride (default: 1)
  --attacker-stride N             Attacker stride (default: 1)
  --fixed-victim-addr SPEC        Victim fixed line: static|0xHEX|none (default: static)
  --fixed-attacker-addr HEX       Attacker fixed line address (default: 0x700000100000)
  --victim-fallback-addr HEX      Victim fallback fixed line address used if static preflight segfaults
                                  (default: 0x700000200000)
  --fail-stats                    Enable per-thread atomic failure stats
  --enforce-no-smt-siblings       Fail if victim/attacker cores share SMT sibling sets
  --ccbench PATH                  Path to ccbench binary (default: ./ccbench)
  --output-dir DIR                Output directory (default: results/adversarial_lock_vs_fai)
  --results-csv PATH              Flat results CSV path (default: results/adversarial_lock_vs_fai_results.csv)
  --dry-run                       Print commands without running
  -h, --help                      Show this help

Example:
  scripts/run_adversarial_lock_vs_fai.sh \
    --victim-cores "0,2,4,6" \
    --attacker-cores "8,10,12,14" \
    --attacker-thread-sweep "1,2,4" \
    --victim-test CAS \
    --attacker-test FAI \
    --control-test LOAD_FROM_L1
USAGE
}

victim_cores=""
attacker_cores=""
victim_test="CAS"
attacker_test="FAI"
control_test="LOAD_FROM_L1"
attacker_thread_sweep=""
victim_reps=20000
attacker_reps=200000000
max_auto_victim_reps=5000
auto_cap_victim_reps=1
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
output_dir="results/adversarial_lock_vs_fai"
results_csv="results/adversarial_lock_vs_fai_results.csv"
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --victim-cores) victim_cores="$2"; shift 2 ;;
    --attacker-cores) attacker_cores="$2"; shift 2 ;;
    --victim-test) victim_test="$2"; shift 2 ;;
    --attacker-test) attacker_test="$2"; shift 2 ;;
    --control-test) control_test="$2"; shift 2 ;;
    --attacker-thread-sweep) attacker_thread_sweep="$2"; shift 2 ;;
    --victim-reps) victim_reps="$2"; shift 2 ;;
    --attacker-reps) attacker_reps="$2"; shift 2 ;;
    --max-auto-victim-reps) max_auto_victim_reps="$2"; shift 2 ;;
    --no-auto-victim-reps-cap) auto_cap_victim_reps=0; shift ;;
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
    --results-csv) results_csv="$2"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$victim_cores" || -z "$attacker_cores" ]]; then
  echo "--victim-cores and --attacker-cores are required." >&2
  usage
  exit 1
fi

if [[ ! -x "$ccbench" ]]; then
  echo "ccbench binary not found or not executable: $ccbench" >&2
  exit 1
fi

if [[ ! -f include/ccbench.h ]]; then
  echo "include/ccbench.h not found; run from repo root." >&2
  exit 1
fi

mkdir -p "$output_dir/logs"
mkdir -p "$(dirname "$results_csv")"

resolve_test_id() {
  local spec="$1"
  if [[ "$spec" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$spec"
    return 0
  fi
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

make_list() {
  local count="$1"
  local value="$2"
  local out=""
  for ((i=0; i<count; i++)); do
    if [[ -n "$out" ]]; then out+=","$value; else out="$value"; fi
  done
  printf '[%s]' "$out"
}

slice_cores() {
  local count="$1"
  shift
  local -a src=("$@")
  local out=""
  local i
  for ((i=0; i<count; i++)); do
    if [[ -n "$out" ]]; then out+=","${src[$i]}; else out="${src[$i]}"; fi
  done
  printf '[%s]' "$out"
}

as_array() {
  local csv="$1"
  local -n ref="$2"
  IFS=',' read -r -a ref <<<"$csv"
}

contains_value() {
  local needle="$1"; shift
  local x
  for x in "$@"; do [[ "$x" == "$needle" ]] && return 0; done
  return 1
}

get_sibling_list() {
  local cpu="$1"
  local p="/sys/devices/system/cpu/cpu${cpu}/topology/thread_siblings_list"
  if [[ -f "$p" ]]; then
    cat "$p"
  else
    printf '%s' "$cpu"
  fi
}

as_array "$victim_cores" victim_core_arr
as_array "$attacker_cores" attacker_core_arr

if [[ "${#victim_core_arr[@]}" -lt 1 || "${#attacker_core_arr[@]}" -lt 1 ]]; then
  echo "Core lists must not be empty." >&2
  exit 1
fi

for c in "${victim_core_arr[@]}" "${attacker_core_arr[@]}"; do
  [[ "$c" =~ ^[0-9]+$ ]] || { echo "Core IDs must be integers: $c" >&2; exit 1; }
done

for c in "${victim_core_arr[@]}"; do
  contains_value "$c" "${attacker_core_arr[@]}" && {
    echo "Victim and attacker core sets must be disjoint; overlapping core: $c" >&2
    exit 1
  }
done

# SMT sibling overlap check (warn or fail).
smt_overlap=0
for v in "${victim_core_arr[@]}"; do
  v_sib=$(get_sibling_list "$v")
  for a in "${attacker_core_arr[@]}"; do
    a_sib=$(get_sibling_list "$a")
    if [[ "$v_sib" == "$a_sib" ]]; then
      echo "SMT sibling overlap detected: victim core $v and attacker core $a share siblings set $v_sib" >&2
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
control_test_id=$(resolve_test_id "$control_test") || { echo "Unknown --control-test: $control_test" >&2; exit 1; }

[[ "$victim_reps" =~ ^[0-9]+$ ]] || { echo "--victim-reps must be an integer" >&2; exit 1; }
[[ "$max_auto_victim_reps" =~ ^[0-9]+$ ]] || { echo "--max-auto-victim-reps must be an integer" >&2; exit 1; }

if [[ "$auto_cap_victim_reps" -eq 1 && "$victim_test_id" == "34" && "$victim_reps" -gt "$max_auto_victim_reps" ]]; then
  echo "INFO: --victim-test $victim_test (id=34) can run for a long time with high reps; auto-capping victim reps from $victim_reps to $max_auto_victim_reps. Use --no-auto-victim-reps-cap to disable." >&2
  victim_reps="$max_auto_victim_reps"
fi

if [[ -z "$seed_core" ]]; then seed_core="${victim_core_arr[0]}"; fi
if [[ -z "$attacker_seed_core" ]]; then attacker_seed_core="${attacker_core_arr[0]}"; fi

victim_count="${#victim_core_arr[@]}"
max_attacker_count="${#attacker_core_arr[@]}"
victim_tests=$(make_list "$victim_count" "$victim_test_id")
victim_core_list="[$victim_cores]"

if [[ -z "$attacker_thread_sweep" ]]; then
  attacker_thread_sweep="$max_attacker_count"
fi
as_array "$attacker_thread_sweep" attacker_count_arr
for n in "${attacker_count_arr[@]}"; do
  [[ "$n" =~ ^[0-9]+$ ]] || { echo "--attacker-thread-sweep values must be integers: $n" >&2; exit 1; }
  (( n >= 1 && n <= max_attacker_count )) || {
    echo "attacker thread count $n out of range [1, $max_attacker_count]" >&2
    exit 1
  }
done

summary_csv="$output_dir/summary.csv"
printf '%s\n' 'phase,attacker_threads,attacker_mode,victim_test,attacker_test,mean_avg,jain_fairness,success_rate,log_path' > "$summary_csv"
printf '%s\n' 'phase,attacker_threads,attacker_mode,victim_test,attacker_test,mean_avg,jain_fairness,success_rate,log_path' > "$results_csv"

common_extra=()
[[ "$fail_stats" -eq 1 ]] && common_extra+=(--fail-stats)
fail_stats_effective="$fail_stats"

strip_fail_stats_flag() {
  local arr_name="$1"
  local -n arr_ref="$arr_name"
  local -a filtered=()
  local tok
  for tok in "${arr_ref[@]}"; do
    [[ "$tok" == "--fail-stats" ]] && continue
    filtered+=("$tok")
  done
  arr_ref=("${filtered[@]}")
}

run_probe_logged() {
  local probe_log="$1"
  shift
  local -a cmd=("$@")
  set +e
  "${cmd[@]}" >"$probe_log" 2>&1
  local rc=$?
  set -e
  return "$rc"
}

is_crash_exit_code() {
  local rc="$1"
  [[ "$rc" -eq 134 || "$rc" -eq 139 ]]
}

adaptive_victim_preflight() {
  if [[ "$dry_run" -eq 1 ]]; then
    return 0
  fi

  local preflight_log="$output_dir/logs/preflight_victim_probe.log"
  local attempt=1
  while true; do
    local -a probe_cmd
    mapfile -t probe_cmd < <(build_cmd "1" "$victim_tests" "$victim_core_list" "$seed_core" "$victim_stride" "$fixed_victim_addr" "$victim_backoff_max")

    local safe_addr="${fixed_victim_addr//[^a-zA-Z0-9]/_}"
    local attempt_log="$output_dir/logs/preflight_victim_probe_attempt${attempt}_addr_${safe_addr}_failstats_${fail_stats_effective}.log"

    echo "=== Preflight: victim probe (attempt=$attempt, addr=$fixed_victim_addr, fail-stats=$fail_stats_effective) ==="
    local rc
    if run_probe_logged "$attempt_log" "${probe_cmd[@]}"; then
      rc=0
    else
      rc=$?
    fi

    cp "$attempt_log" "$preflight_log"

    if [[ "$rc" -eq 0 ]]; then
      return 0
    fi

    if is_crash_exit_code "$rc" && [[ "$fail_stats_effective" -eq 1 ]]; then
      echo "WARNING: preflight crashed with --fail-stats (exit $rc). Auto-disabling --fail-stats for this run." >&2
      fail_stats_effective=0
      fail_stats_auto_disabled=1
      common_extra=()
      ((attempt++))
      continue
    fi

    if is_crash_exit_code "$rc" && [[ "$fixed_victim_addr" == "static" ]]; then
      echo "WARNING: preflight still crashes with victim static line. Falling back to --fixed-victim-addr $victim_fallback_addr." >&2
      fixed_victim_addr="$victim_fallback_addr"
      victim_addr_auto_fallback=1
      ((attempt++))
      continue
    fi

    if is_crash_exit_code "$rc" && [[ "$fixed_victim_addr" != "none" ]]; then
      echo "WARNING: preflight still crashes with fixed victim address ($fixed_victim_addr). Retrying with victim fixed-address mode disabled." >&2
      fixed_victim_addr="none"
      victim_fixed_disabled=1
      ((attempt++))
      continue
    fi

    echo "ERROR: victim preflight failed with exit code $rc. See $preflight_log" >&2
    return "$rc"
  done
}

build_cmd() {
  local reps="$1" tests="$2" cores="$3" seed="$4" stride="$5" fixed_addr="$6"
  local -a cmd=("$ccbench" -r "$reps" -t "$tests" -x "$cores" -b "$seed" -s "$stride")
  if [[ "$fixed_addr" != "none" ]]; then
    cmd+=(-Z "$fixed_addr")
  fi
  local op_id
  op_id=$(echo "$tests" | sed -E 's/^\[([0-9]+).*/\1/')
  if [[ "$op_id" == "34" ]]; then
    local bmax="$7"
    cmd+=(-B -M "$bmax")
  fi
  cmd+=("${common_extra[@]}")
  printf '%s\n' "${cmd[@]}"
}

extract_run_stats() {
  local log_file="$1"
  awk '
    /Summary : mean avg/ {
      if (match($0, /mean avg[[:space:]]*([0-9.]+)/, m)) mean=m[1]
    }
    /Jain fairness/ {
      if (match($0, /Jain fairness[^0-9]*([0-9.]+)/, m)) fair=m[1]
    }
    /success rate/ {
      if (match($0, /success rate[^0-9]*([0-9.]+)/, m)) succ=m[1]
    }
    /Winner==argmin\(B4[[:space:]]*->[[:space:]]*success\)/ {
      if (match($0, /\(([0-9.]+)%\)/, m)) succ=m[1]
    }
    /First-success winners per thread/ { in_winners=1; next }
    in_winners && /^[[:space:]]*Group[[:space:]]+/ {
      if (match($0, /: *([0-9]+) wins/, m)) {
        w = m[1] + 0
        sum += w
        sumsq += (w * w)
        cnt += 1
      }
      next
    }
    in_winners && !/^[[:space:]]*Group[[:space:]]+/ {
      in_winners=0
    }
    END {
      if (fair == "" && cnt > 0 && sumsq > 0) {
        fair = (sum * sum) / (cnt * sumsq)
      }
      if (mean == "") mean = "NA"
      if (fair == "") fair = "NA"
      if (succ == "") succ = "NA"
      if (fair != "NA") fair = sprintf("%.4f", fair)
      printf "%s,%s,%s", mean, fair, succ
    }
  ' "$log_file"
}

run_logged() {
  local log_file="$1"
  shift
  local -a cmd=("$@")
  printf 'Running: ' && printf '%q ' "${cmd[@]}" && printf '\n'
  if [[ "$dry_run" -eq 1 ]]; then
    return 0
  fi
  set +e
  "${cmd[@]}" | tee "$log_file"
  local rc=$?
  set -e
  if is_crash_exit_code "$rc"; then
    echo "ERROR: ccbench crashed (exit $rc)." >&2
    echo "Hint: if using static victim line, set --fixed-victim-addr to a 0xHEX mapping (or tune --victim-fallback-addr)." >&2
  fi
  return "$rc"
}

run_with_synchronized_attacker() {
  local victim_log="$1"
  local attacker_log="$2"
  local victim_var="$3"
  local attacker_var="$4"
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
    if [[ -n "${attacker_pid:-}" ]]; then
      kill "$attacker_pid" >/dev/null 2>&1 || true
      wait "$attacker_pid" >/dev/null 2>&1 || true
    fi
    if [[ -n "${victim_pid:-}" ]]; then
      wait "$victim_pid" >/dev/null 2>&1 || true
    fi
  }
  trap cleanup_sync EXIT INT TERM

  (
    read -r _ < "$gate_attacker"
    "${attacker_cmd_ref[@]}" >>"$attacker_log" 2>&1
  ) &
  attacker_pid=$!

  (
    read -r _ < "$gate_victim"
    "${victim_cmd_ref[@]}"
  ) | tee "$victim_log" &
  victim_pid=$!

  # release both waiters
  echo go > "$gate_attacker"
  echo go > "$gate_victim"

  wait "$victim_pid"
  kill "$attacker_pid" >/dev/null 2>&1 || true
  wait "$attacker_pid" >/dev/null 2>&1 || true

  trap - EXIT INT TERM
  rm -f "$gate_attacker" "$gate_victim"
}

baseline_log="$output_dir/logs/victim_baseline.log"
adaptive_victim_preflight
mapfile -t victim_base_cmd < <(build_cmd "$victim_reps" "$victim_tests" "$victim_core_list" "$seed_core" "$victim_stride" "$fixed_victim_addr" "$victim_backoff_max")

cat >"$output_dir/run_meta.txt" <<META
Victim cores:             $victim_core_list
Attacker cores (max):     [$attacker_cores]
Victim test:              $victim_test (id=$victim_test_id)
Attacker test (RMW):      $attacker_test (id=$attacker_test_id)
Attacker control test:    $control_test (id=$control_test_id)
Experiment mode:          atomic-vs-atomic
Attacker thread sweep:    $attacker_thread_sweep
Victim fixed line:        $fixed_victim_addr
Victim fallback addr:     $victim_fallback_addr
Victim addr auto-fallback:$victim_addr_auto_fallback
Victim fixed disabled:    $victim_fixed_disabled
Attacker fixed line:      $fixed_attacker_addr
Victim seed core:         $seed_core
Attacker seed core:       $attacker_seed_core
Victim reps:              $victim_reps
Victim reps auto-cap:     $auto_cap_victim_reps
Victim reps auto-cap max: $max_auto_victim_reps
Attacker reps:            $attacker_reps
SMT overlap detected:     $smt_overlap
Fail stats requested:     $fail_stats
Fail stats effective:     $fail_stats_effective
Fail stats auto-disabled: $fail_stats_auto_disabled
Flat results CSV:         $results_csv
META

echo "=== Phase: victim_baseline ==="
run_logged "$baseline_log" "${victim_base_cmd[@]}"

if [[ "$dry_run" -eq 0 ]]; then
  IFS=',' read -r mean fair succ <<<"$(extract_run_stats "$baseline_log")"
  printf 'victim_baseline,0,none,%s,none,%s,%s,%s,%s\n' "$victim_test" "$mean" "$fair" "$succ" "$baseline_log" | tee -a "$summary_csv" >> "$results_csv"
fi

for a_threads in "${attacker_count_arr[@]}"; do
  attacker_core_list=$(slice_cores "$a_threads" "${attacker_core_arr[@]}")
  attacker_tests=$(make_list "$a_threads" "$attacker_test_id")
  control_tests=$(make_list "$a_threads" "$control_test_id")

  rmw_victim_log="$output_dir/logs/victim_with_attacker_rmw_t${a_threads}.log"
  rmw_attacker_log="$output_dir/logs/attacker_rmw_t${a_threads}.log"
  ctrl_victim_log="$output_dir/logs/victim_with_attacker_control_t${a_threads}.log"
  ctrl_attacker_log="$output_dir/logs/attacker_control_t${a_threads}.log"

  mapfile -t rmw_cmd < <(build_cmd "$attacker_reps" "$attacker_tests" "$attacker_core_list" "$attacker_seed_core" "$attacker_stride" "$fixed_attacker_addr" "$attacker_backoff_max")
  mapfile -t ctrl_cmd < <(build_cmd "$attacker_reps" "$control_tests" "$attacker_core_list" "$attacker_seed_core" "$attacker_stride" "$fixed_attacker_addr" "$attacker_backoff_max")
  if [[ "$fail_stats_auto_disabled" -eq 1 ]]; then
    strip_fail_stats_flag rmw_cmd
    strip_fail_stats_flag ctrl_cmd
  fi

  echo
  echo "=== Phase: victim_plus_attacker_rmw (threads=$a_threads) ==="
  run_with_synchronized_attacker "$rmw_victim_log" "$rmw_attacker_log" victim_base_cmd rmw_cmd
  if [[ "$dry_run" -eq 0 ]]; then
    IFS=',' read -r mean fair succ <<<"$(extract_run_stats "$rmw_victim_log")"
    printf 'victim_plus_attacker_rmw,%s,rmw,%s,%s,%s,%s,%s,%s\n' "$a_threads" "$victim_test" "$attacker_test" "$mean" "$fair" "$succ" "$rmw_victim_log" | tee -a "$summary_csv" >> "$results_csv"
  fi

  echo
  echo "=== Phase: victim_plus_attacker_control (threads=$a_threads) ==="
  run_with_synchronized_attacker "$ctrl_victim_log" "$ctrl_attacker_log" victim_base_cmd ctrl_cmd
  if [[ "$dry_run" -eq 0 ]]; then
    IFS=',' read -r mean fair succ <<<"$(extract_run_stats "$ctrl_victim_log")"
    printf 'victim_plus_attacker_control,%s,control,%s,%s,%s,%s,%s,%s\n' "$a_threads" "$victim_test" "$control_test" "$mean" "$fair" "$succ" "$ctrl_victim_log" | tee -a "$summary_csv" >> "$results_csv"
  fi
done

echo
echo "Done. Artifacts:"
echo "  $output_dir/run_meta.txt"
echo "  $summary_csv"
echo "  $results_csv"
echo "  $output_dir/logs/"
