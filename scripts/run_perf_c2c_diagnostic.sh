#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/run_perf_c2c_diagnostic.sh [options]

Run a single (baseline, shared-attacker, separate-attacker) cycle under
perf c2c to identify which cache lines cause HITM (cross-core invalidation)
events.

WARNING: perf c2c adds significant overhead (5-30%). Do NOT use this for
         latency measurements — use it only as a diagnostic to understand
         *which* cache lines are contested. Run your timed experiments
         separately with the normal scripts.

Output:
  <output-dir>/c2c_baseline.data        perf c2c raw data
  <output-dir>/c2c_baseline_report.txt  human-readable HITM report
  <output-dir>/c2c_shared.data          ...
  <output-dir>/c2c_shared_report.txt
  <output-dir>/c2c_separate.data
  <output-dir>/c2c_separate_report.txt

Options:
  --victim-cores LIST             Comma-separated victim cores (required)
  --attacker-cores LIST           Comma-separated attacker cores (required)
  --victim-test NAME|ID           Victim primitive (default: CAS)
  --attacker-test NAME|ID         Attacker primitive (default: FAI)
  --victim-reps N                 Victim repetitions (default: 5000)
  --attacker-reps N               Attacker repetitions (default: 50000000)
  --seed-core N                   Victim seed core (default: first victim core)
  --victim-stride N               Victim stride (default: 1)
  --attacker-stride N             Attacker stride (default: 1)
  --fixed-victim-addr SPEC        static|0xHEX|none (default: static)
  --shared-attacker-addr HEX      Shared attacker line (default: 0x700000100000)
  --separate-attacker-base HEX    Base for separate attackers (default: 0x700000300000)
  --separate-attacker-step HEX    Step per attacker (default: 0x1000)
  --ldlat N                       Load latency threshold in cycles (default: 30)
  --output-dir DIR                Output directory (default: results/perf_c2c_diagnostic)
  --ccbench PATH                  Path to ccbench (default: ./ccbench)
  --phases LIST                   Comma-separated phases to run (default: baseline,shared,separate)
  --dry-run                       Print planned commands only
  -h, --help                      Show help
USAGE
}

victim_cores=""
attacker_cores=""
victim_test="CAS"
attacker_test="FAI"
victim_reps=5000
attacker_reps=50000000
seed_core=""
victim_stride=1
attacker_stride=1
fixed_victim_addr="static"
shared_attacker_addr="0x700000100000"
separate_attacker_base="0x700000300000"
separate_attacker_step="0x1000"
ldlat=30
output_dir="results/perf_c2c_diagnostic"
ccbench="./ccbench"
phases="baseline,shared,separate"
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
    --victim-stride) victim_stride="$2"; shift 2 ;;
    --attacker-stride) attacker_stride="$2"; shift 2 ;;
    --fixed-victim-addr) fixed_victim_addr="$2"; shift 2 ;;
    --shared-attacker-addr) shared_attacker_addr="$2"; shift 2 ;;
    --separate-attacker-base) separate_attacker_base="$2"; shift 2 ;;
    --separate-attacker-step) separate_attacker_step="$2"; shift 2 ;;
    --ldlat) ldlat="$2"; shift 2 ;;
    --output-dir) output_dir="$2"; shift 2 ;;
    --ccbench) ccbench="$2"; shift 2 ;;
    --phases) phases="$2"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

[[ -n "$victim_cores" && -n "$attacker_cores" ]] || { echo "--victim-cores and --attacker-cores are required." >&2; exit 1; }
[[ -x "$ccbench" ]] || { echo "ccbench not found/executable: $ccbench" >&2; exit 1; }
[[ -f include/ccbench.h ]] || { echo "include/ccbench.h not found; run from repo root." >&2; exit 1; }

# Check perf c2c availability
if ! command -v perf &>/dev/null; then
  echo "ERROR: 'perf' not found in PATH." >&2; exit 1
fi
if ! perf c2c record -e ldlat-loads -- true 2>/dev/null; then
  # Try without ldlat-loads (older kernels)
  if ! perf c2c record -- true 2>/dev/null; then
    echo "ERROR: perf c2c not available on this system. Requires perf with c2c support and kernel >=4.2." >&2
    exit 1
  fi
  c2c_record_args=()
  echo "WARNING: ldlat-loads not supported; using default perf c2c events." >&2
else
  c2c_record_args=(-e ldlat-loads -e ldlat-stores)
fi
rm -f perf.data  # clean up probe artifacts

as_array() { local csv="$1"; local -n ref="$2"; IFS=',' read -r -a ref <<<"$csv"; }
hex_to_dec() { printf '%d' "$(( $1 ))"; }
dec_to_hex() { printf '0x%x' "$1"; }
make_list() { local n="$1" v="$2" out=""; for ((i=0;i<n;i++)); do [[ -n "$out" ]] && out+=",$v" || out="$v"; done; printf '[%s]' "$out"; }
log_info() { echo "INFO: $*" >&2; }

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

as_array "$victim_cores" victim_core_arr
as_array "$attacker_cores" attacker_core_arr

victim_count=${#victim_core_arr[@]}
attacker_count=${#attacker_core_arr[@]}

victim_test_id=$(resolve_test_id "$victim_test") || { echo "Unknown victim test: $victim_test" >&2; exit 1; }
attacker_test_id=$(resolve_test_id "$attacker_test") || { echo "Unknown attacker test: $attacker_test" >&2; exit 1; }

[[ -z "$seed_core" ]] && seed_core="${victim_core_arr[0]}"

victim_tests=$(make_list "$victim_count" "$victim_test_id")
victim_core_list="[$victim_cores]"

# Build all-cores CSV for perf c2c -C filtering
all_cores_csv="$victim_cores,$attacker_cores"

mkdir -p "$output_dir"

build_victim_cmd() {
  local reps="$1"
  local -a cmd=("$ccbench" -r "$reps" -t "$victim_tests" -x "$victim_core_list" -b "$seed_core" -s "$victim_stride")
  [[ "$fixed_victim_addr" != "none" ]] && cmd+=(-Z "$fixed_victim_addr")
  printf '%s\n' "${cmd[@]}"
}

# --- Phase runners ---

run_c2c_baseline() {
  log_info "perf c2c phase: baseline (victim only)"
  local data_file="$output_dir/c2c_baseline.data"
  local report_file="$output_dir/c2c_baseline_report.txt"

  local -a victim_cmd
  mapfile -t victim_cmd < <(build_victim_cmd "$victim_reps")

  if [[ "$dry_run" -eq 1 ]]; then
    printf '[dry-run] perf c2c record -o %s -- %q ' "$data_file" "${victim_cmd[@]}" >&2; echo >&2
    return
  fi

  perf c2c record "${c2c_record_args[@]}" --ldlat="$ldlat" \
    -o "$data_file" -- "${victim_cmd[@]}" >/dev/null 2>&1 || true

  perf c2c report -i "$data_file" --stdio > "$report_file" 2>&1 || true
  log_info "perf c2c baseline done: $report_file"
}

run_c2c_with_attackers() {
  local mode="$1"  # "shared" or "separate"
  log_info "perf c2c phase: $mode"
  local data_file="$output_dir/c2c_${mode}.data"
  local report_file="$output_dir/c2c_${mode}_report.txt"

  local -a victim_cmd
  mapfile -t victim_cmd < <(build_victim_cmd "$victim_reps")

  local base_dec step_dec
  base_dec=$(hex_to_dec "$separate_attacker_base")
  step_dec=$(hex_to_dec "$separate_attacker_step")

  # Build a wrapper script that launches attackers + victim together,
  # so perf c2c can record all of them as children.
  local wrapper="$output_dir/.c2c_wrapper_${mode}.sh"
  {
    echo '#!/usr/bin/env bash'
    # Launch attackers in background
    for i in "${!attacker_core_arr[@]}"; do
      core="${attacker_core_arr[$i]}"
      if [[ "$mode" == "shared" ]]; then
        addr="$shared_attacker_addr"
      else
        addr=$(dec_to_hex "$((base_dec + i * step_dec))")
      fi
      printf '%q -r %q -t "[%s]" -x "[%s]" -b %q -s %q -Z %q >/dev/null 2>&1 &\n' \
        "$ccbench" "$attacker_reps" "$attacker_test_id" "$core" "$core" "$attacker_stride" "$addr"
    done
    echo 'ATTACKER_PIDS=$(jobs -p)'
    echo 'sleep 0.1'
    # Run victim in foreground
    printf '%q' "${victim_cmd[0]}"
    for arg in "${victim_cmd[@]:1}"; do
      printf ' %q' "$arg"
    done
    printf ' >/dev/null 2>&1\n'
    echo 'VICTIM_RC=$?'
    # Kill attackers
    echo 'for p in $ATTACKER_PIDS; do kill "$p" 2>/dev/null || true; done'
    echo 'wait 2>/dev/null || true'
    echo 'exit $VICTIM_RC'
  } > "$wrapper"
  chmod +x "$wrapper"

  if [[ "$dry_run" -eq 1 ]]; then
    printf '[dry-run] perf c2c record -o %s -- bash %s\n' "$data_file" "$wrapper" >&2
    return
  fi

  perf c2c record "${c2c_record_args[@]}" --ldlat="$ldlat" \
    -o "$data_file" -- bash "$wrapper" >/dev/null 2>&1 || true

  perf c2c report -i "$data_file" --stdio > "$report_file" 2>&1 || true
  rm -f "$wrapper"
  log_info "perf c2c $mode done: $report_file"
}

# --- Run requested phases ---

IFS=',' read -r -a phase_arr <<<"$phases"
for phase in "${phase_arr[@]}"; do
  case "$phase" in
    baseline)  run_c2c_baseline ;;
    shared)    run_c2c_with_attackers shared ;;
    separate)  run_c2c_with_attackers separate ;;
    *) echo "WARNING: unknown phase '$phase', skipping" >&2 ;;
  esac
done

cat <<REPORT

perf c2c diagnostic complete.
Output directory: $output_dir

Files:
REPORT

for phase in "${phase_arr[@]}"; do
  report_file="$output_dir/c2c_${phase}_report.txt"
  if [[ -f "$report_file" ]]; then
    echo "  $report_file"
  fi
done

cat <<'GUIDE'

How to interpret:
  1. Look at the "Shared Data Cache Line Table" in each report.
  2. The "Hitm" column shows cross-core invalidation hits (the expensive ones).
  3. Compare HITM counts between baseline, shared, and separate reports.
  4. High HITM on the victim's cache line address in the "shared" report
     but not in "separate" confirms coherence-hotspot contention.
  5. The "Snoop" column shows snoop filter activity — high snoop counts
     on a line indicate it's bouncing between cores.

For deeper analysis:
  perf c2c report -i <data_file> --stdio --full-symbols
GUIDE
