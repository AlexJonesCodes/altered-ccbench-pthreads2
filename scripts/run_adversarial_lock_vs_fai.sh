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
  --attacker-reps N               Attacker repetitions per run (default: 200000000)
  --seed-core N                   Seed core for victim run (default: first victim core)
  --attacker-seed-core N          Seed core for attacker/control run (default: first attacker core)
  --victim-backoff-max N          Victim backoff max (used only if victim is CAS_UNTIL_SUCCESS, default: 1024)
  --attacker-backoff-max N        Attacker backoff max (used only if attacker is CAS_UNTIL_SUCCESS, default: 1)
  --victim-stride N               Victim stride (default: 1)
  --attacker-stride N             Attacker stride (default: 1)
  --fixed-victim-addr SPEC        Victim fixed line: static|0xHEX (default: static)
  --fixed-attacker-addr HEX       Attacker fixed line address (default: 0x700000100000)
  --fail-stats                    Enable per-thread atomic failure stats
  --enforce-no-smt-siblings       Fail if victim/attacker cores share SMT sibling sets
  --ccbench PATH                  Path to ccbench binary (default: ./ccbench)
  --output-dir DIR                Output directory (default: results/adversarial_lock_vs_fai)
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
seed_core=""
attacker_seed_core=""
victim_backoff_max=1024
attacker_backoff_max=1
victim_stride=1
attacker_stride=1
fixed_victim_addr="static"
fixed_attacker_addr="0x700000100000"
fail_stats=0
enforce_no_smt=0
ccbench=./ccbench
output_dir="results/adversarial_lock_vs_fai"
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
    --seed-core) seed_core="$2"; shift 2 ;;
    --attacker-seed-core) attacker_seed_core="$2"; shift 2 ;;
    --victim-backoff-max) victim_backoff_max="$2"; shift 2 ;;
    --attacker-backoff-max) attacker_backoff_max="$2"; shift 2 ;;
    --victim-stride) victim_stride="$2"; shift 2 ;;
    --attacker-stride) attacker_stride="$2"; shift 2 ;;
    --fixed-victim-addr) fixed_victim_addr="$2"; shift 2 ;;
    --fixed-attacker-addr) fixed_attacker_addr="$2"; shift 2 ;;
    --fail-stats) fail_stats=1; shift ;;
    --enforce-no-smt-siblings) enforce_no_smt=1; shift ;;
    --ccbench) ccbench="$2"; shift 2 ;;
    --output-dir) output_dir="$2"; shift 2 ;;
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
printf 'phase,attacker_threads,attacker_mode,victim_test,attacker_test,mean_avg,jain_fairness,success_rate,log_path\n' > "$summary_csv"

common_extra=()
[[ "$fail_stats" -eq 1 ]] && common_extra+=(--fail-stats)

build_cmd() {
  local reps="$1" tests="$2" cores="$3" seed="$4" stride="$5" fixed_addr="$6"
  local -a cmd=("$ccbench" -r "$reps" -t "$tests" -x "$cores" -b "$seed" -s "$stride" -Z "$fixed_addr")
  local op_id
  op_id=$(echo "$tests" | sed -E 's/^\[([0-9]+).*/\1/')
  if [[ "$op_id" == "34" ]]; then
    local bmax="$7"
    cmd+=(-B -M "$bmax")
  fi
  cmd+=("${common_extra[@]}")
  printf '%q ' "${cmd[@]}"
}

extract_run_stats() {
  local log_file="$1"
  awk '
    /Summary : mean avg/ { if (match($0, /mean avg[[:space:]]*([0-9.]+)/, m)) mean=m[1] }
    /Jain fairness/ { if (match($0, /Jain fairness[^0-9]*([0-9.]+)/, m)) fair=m[1] }
    /success rate/ { if (match($0, /success rate[^0-9]*([0-9.]+)/, m)) succ=m[1] }
    END {
      if (mean == "") mean = "NA";
      if (fair == "") fair = "NA";
      if (succ == "") succ = "NA";
      printf "%s,%s,%s", mean, fair, succ;
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
  "${cmd[@]}" | tee "$log_file"
}

run_with_synchronized_attacker() {
  local victim_log="$1"
  local attacker_log="$2"
  local victim_var="$3"
  local attacker_var="$4"
  local victim_cmd="${!victim_var}"
  local attacker_cmd="${!attacker_var}"

  if [[ "$dry_run" -eq 1 ]]; then
    echo "(synchronized start) victim+attacker"
    printf 'ATTACKER: %s\n' "$attacker_cmd"
    printf 'VICTIM:   %s\n' "$victim_cmd"
    return 0
  fi

  local gate_fifo="$output_dir/.start_gate_$$.fifo"
  mkfifo "$gate_fifo"

  cleanup_sync() {
    rm -f "$gate_fifo"
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
    read -r _ < "$gate_fifo"
    eval "$attacker_cmd" >>"$attacker_log" 2>&1
  ) &
  attacker_pid=$!

  (
    read -r _ < "$gate_fifo"
    eval "$victim_cmd"
  ) | tee "$victim_log" &
  victim_pid=$!

  # release both waiters
  { echo go > "$gate_fifo"; echo go > "$gate_fifo"; } || true

  wait "$victim_pid"
  kill "$attacker_pid" >/dev/null 2>&1 || true
  wait "$attacker_pid" >/dev/null 2>&1 || true

  trap - EXIT INT TERM
  rm -f "$gate_fifo"
}

cat >"$output_dir/run_meta.txt" <<META
Victim cores:             $victim_core_list
Attacker cores (max):     [$attacker_cores]
Victim test:              $victim_test (id=$victim_test_id)
Attacker test (RMW):      $attacker_test (id=$attacker_test_id)
Attacker control test:    $control_test (id=$control_test_id)
Experiment mode:          atomic-vs-atomic
Attacker thread sweep:    $attacker_thread_sweep
Victim fixed line:        $fixed_victim_addr
Attacker fixed line:      $fixed_attacker_addr
Victim seed core:         $seed_core
Attacker seed core:       $attacker_seed_core
Victim reps:              $victim_reps
Attacker reps:            $attacker_reps
SMT overlap detected:     $smt_overlap
META

baseline_log="$output_dir/logs/victim_baseline.log"
victim_base_cmd=("$(build_cmd "$victim_reps" "$victim_tests" "$victim_core_list" "$seed_core" "$victim_stride" "$fixed_victim_addr" "$victim_backoff_max")")

echo "=== Phase: victim_baseline ==="
run_logged "$baseline_log" bash -lc "${victim_base_cmd[0]}"

if [[ "$dry_run" -eq 0 ]]; then
  IFS=',' read -r mean fair succ <<<"$(extract_run_stats "$baseline_log")"
  printf 'victim_baseline,0,none,%s,none,%s,%s,%s,%s\n' "$victim_test" "$mean" "$fair" "$succ" "$baseline_log" >> "$summary_csv"
fi

for a_threads in "${attacker_count_arr[@]}"; do
  attacker_core_list=$(slice_cores "$a_threads" "${attacker_core_arr[@]}")
  attacker_tests=$(make_list "$a_threads" "$attacker_test_id")
  control_tests=$(make_list "$a_threads" "$control_test_id")

  rmw_victim_log="$output_dir/logs/victim_with_attacker_rmw_t${a_threads}.log"
  rmw_attacker_log="$output_dir/logs/attacker_rmw_t${a_threads}.log"
  ctrl_victim_log="$output_dir/logs/victim_with_attacker_control_t${a_threads}.log"
  ctrl_attacker_log="$output_dir/logs/attacker_control_t${a_threads}.log"

  rmw_cmd=("$(build_cmd "$attacker_reps" "$attacker_tests" "$attacker_core_list" "$attacker_seed_core" "$attacker_stride" "$fixed_attacker_addr" "$attacker_backoff_max")")
  ctrl_cmd=("$(build_cmd "$attacker_reps" "$control_tests" "$attacker_core_list" "$attacker_seed_core" "$attacker_stride" "$fixed_attacker_addr" "$attacker_backoff_max")")

  echo
  echo "=== Phase: victim_plus_attacker_rmw (threads=$a_threads) ==="
  run_with_synchronized_attacker "$rmw_victim_log" "$rmw_attacker_log" victim_base_cmd rmw_cmd
  if [[ "$dry_run" -eq 0 ]]; then
    IFS=',' read -r mean fair succ <<<"$(extract_run_stats "$rmw_victim_log")"
    printf 'victim_plus_attacker_rmw,%s,rmw,%s,%s,%s,%s,%s,%s\n' "$a_threads" "$victim_test" "$attacker_test" "$mean" "$fair" "$succ" "$rmw_victim_log" >> "$summary_csv"
  fi

  echo
  echo "=== Phase: victim_plus_attacker_control (threads=$a_threads) ==="
  run_with_synchronized_attacker "$ctrl_victim_log" "$ctrl_attacker_log" victim_base_cmd ctrl_cmd
  if [[ "$dry_run" -eq 0 ]]; then
    IFS=',' read -r mean fair succ <<<"$(extract_run_stats "$ctrl_victim_log")"
    printf 'victim_plus_attacker_control,%s,control,%s,%s,%s,%s,%s,%s\n' "$a_threads" "$victim_test" "$control_test" "$mean" "$fair" "$succ" "$ctrl_victim_log" >> "$summary_csv"
  fi
done

echo
echo "Done. Artifacts:"
echo "  $output_dir/run_meta.txt"
echo "  $summary_csv"
echo "  $output_dir/logs/"
