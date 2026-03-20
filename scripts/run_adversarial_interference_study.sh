#!/usr/bin/env bash
set -euo pipefail

usage() {
cat <<'USAGE'
Usage: scripts/run_adversarial_interference_study.sh [options]

Full adversarial interference study orchestrator.

Runs three experiment stages end-to-end and then plots the combined results:

  Stage 1 — Lock-vs-FAI adversarial sweep
    Victim runs CAS (or a chosen primitive) while attacker threads run an RMW
    primitive and a control (LOAD_FROM_L1) at increasing attacker thread counts.
    Reveals how much cross-core RMW traffic slows the victim vs a benign control.

  Stage 2 — Separate-address sweep (with seed rotation + replicates)
    For each attacker intensity level, compares shared-line attackers against
    separate-line attackers across multiple seed cores and replicates.
    Reveals whether slowdown is true cache-line contention or broad interconnect
    pressure.

  Stage 3 — Perf c2c diagnostic
    Runs a shorter burst under perf c2c to capture HITM (cross-core
    invalidation) event counts for baseline, shared-attacker, and
    separate-attacker phases.  Confirms the hardware mechanism.

  Stage 4 — Plot all results
    Calls plot_adversarial_interference.py to generate the four chart families.

Core allocation:
  Victim cores and attacker cores must be disjoint.  The script auto-detects
  available cores if --auto-cores is given; otherwise you must pass explicit
  core lists.

Options:
  --victim-cores LIST        Comma-separated victim cores (required unless --auto-cores)
  --attacker-cores LIST      Comma-separated attacker cores (required unless --auto-cores)
  --auto-cores               Auto-detect: split available physical cores in half
                             (first half = victim, second half = attacker)
  --auto-cores-max N         Max cores per group when using --auto-cores (default: 8)

  --victim-test NAME|ID      Victim primitive (default: CAS)
  --attacker-test NAME|ID    Attacker primitive for RMW phase (default: FAI)
  --control-test NAME|ID     Control attacker primitive (default: LOAD_FROM_L1)
  --extra-victim-tests LIST  Additional victim tests to sweep, comma-separated
                             (default: none; e.g. "TAS,SWAP,CAS_UNTIL_SUCCESS")

  --attacker-thread-sweep LIST  Attacker thread counts (default: "1,2,4" capped to
                                available attacker cores)
  --seed-rotation LIST       Victim seed cores to rotate (default: all victim cores)
  --replicates N             Replicates per (seed, attacker_count) in stage 2
                             (default: 3)

  --victim-reps N            Victim repetitions (default: 20000)
  --attacker-reps N          Attacker repetitions (default: 200000000)
  --c2c-victim-reps N        Victim reps for perf c2c stage (default: 5000)
  --c2c-attacker-reps N      Attacker reps for perf c2c stage (default: 50000000)

  --fixed-victim-addr SPEC   static|0xHEX|none (default: static)
  --fail-stats               Enable per-thread atomic failure statistics
  --perf-counters            Collect perf stat HW counters in stage 2

  --stages LIST              Comma-separated stages to run (default: 1,2,3,4)
                             e.g. "1,2" to skip c2c and plotting
  --output-dir DIR           Top-level output directory (default: results)
  --ccbench PATH             Path to ccbench binary (default: ./ccbench)
  --plot-format FMT          Plot format: png|pdf|svg (default: png)
  --plot-dpi N               Plot DPI (default: 150)
  --dry-run                  Print planned commands without running
  -h, --help                 Show this help

Examples:
  # Auto-detect cores, run everything with defaults
  scripts/run_adversarial_interference_study.sh --auto-cores

  # Explicit cores, sweep more attacker counts
  scripts/run_adversarial_interference_study.sh \
    --victim-cores "0,2,4,6" \
    --attacker-cores "8,10,12,14" \
    --attacker-thread-sweep "1,2,4"

  # Also test TAS and SWAP as victim primitives
  scripts/run_adversarial_interference_study.sh \
    --auto-cores \
    --extra-victim-tests "TAS,SWAP"

  # Only run stages 1 and 2, skip c2c and plotting
  scripts/run_adversarial_interference_study.sh \
    --auto-cores --stages "1,2"

  # Dry run to see what would execute
  scripts/run_adversarial_interference_study.sh --auto-cores --dry-run
USAGE
}

# ── Defaults ─────────────────────────────────────────────────────────────────

victim_cores=""
attacker_cores=""
auto_cores=0
auto_cores_max=8

victim_test="CAS"
attacker_test="FAI"
control_test="LOAD_FROM_L1"
extra_victim_tests=""

attacker_thread_sweep=""
seed_rotation=""
replicates=3

victim_reps=20000
attacker_reps=200000000
c2c_victim_reps=5000
c2c_attacker_reps=50000000

fixed_victim_addr="static"
fail_stats=0
perf_counters=0

stages="1,2,3,4"
output_dir="results"
ccbench="./ccbench"
plot_format="png"
plot_dpi=150
dry_run=0

# ── Parse args ───────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    --victim-cores)          victim_cores="$2"; shift 2 ;;
    --attacker-cores)        attacker_cores="$2"; shift 2 ;;
    --auto-cores)            auto_cores=1; shift ;;
    --auto-cores-max)        auto_cores_max="$2"; shift 2 ;;
    --victim-test)           victim_test="$2"; shift 2 ;;
    --attacker-test)         attacker_test="$2"; shift 2 ;;
    --control-test)          control_test="$2"; shift 2 ;;
    --extra-victim-tests)    extra_victim_tests="$2"; shift 2 ;;
    --attacker-thread-sweep) attacker_thread_sweep="$2"; shift 2 ;;
    --seed-rotation)         seed_rotation="$2"; shift 2 ;;
    --replicates)            replicates="$2"; shift 2 ;;
    --victim-reps)           victim_reps="$2"; shift 2 ;;
    --attacker-reps)         attacker_reps="$2"; shift 2 ;;
    --c2c-victim-reps)       c2c_victim_reps="$2"; shift 2 ;;
    --c2c-attacker-reps)     c2c_attacker_reps="$2"; shift 2 ;;
    --fixed-victim-addr)     fixed_victim_addr="$2"; shift 2 ;;
    --fail-stats)            fail_stats=1; shift ;;
    --perf-counters)         perf_counters=1; shift ;;
    --stages)                stages="$2"; shift 2 ;;
    --output-dir)            output_dir="$2"; shift 2 ;;
    --ccbench)               ccbench="$2"; shift 2 ;;
    --plot-format)           plot_format="$2"; shift 2 ;;
    --plot-dpi)              plot_dpi="$2"; shift 2 ;;
    --dry-run)               dry_run=1; shift ;;
    -h|--help)               usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

# ── Helpers ──────────────────────────────────────────────────────────────────

log_info()  { printf '\n\033[1;34m[INFO]\033[0m %s\n' "$*"; }
log_stage() { printf '\n\033[1;32m════════════════════════════════════════════════════════════════\033[0m\n'; \
              printf '\033[1;32m  STAGE %s\033[0m\n' "$*"; \
              printf '\033[1;32m════════════════════════════════════════════════════════════════\033[0m\n'; }
log_warn()  { printf '\033[1;33m[WARN]\033[0m %s\n' "$*" >&2; }
log_err()   { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; }

as_array() { local csv="$1"; local -n ref="$2"; IFS=',' read -r -a ref <<<"$csv"; }

stage_enabled() {
  local s="$1"
  local -a arr
  IFS=',' read -r -a arr <<<"$stages"
  local x
  for x in "${arr[@]}"; do
    [[ "$x" == "$s" ]] && return 0
  done
  return 1
}

# ── Validate prerequisites ───────────────────────────────────────────────────

if [[ ! -x "$ccbench" ]]; then
  log_err "ccbench binary not found or not executable: $ccbench"
  log_info "Run 'make' first to build the binary."
  exit 1
fi

if [[ ! -f include/ccbench.h ]]; then
  log_err "include/ccbench.h not found — run this script from the repo root."
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for required_script in \
    "$script_dir/run_adversarial_lock_vs_fai.sh" \
    "$script_dir/run_adversarial_separate_attacker_addrs_sweep.sh" \
    "$script_dir/run_perf_c2c_diagnostic.sh" \
    "$script_dir/plot_adversarial_interference.py"; do
  if [[ ! -f "$required_script" ]]; then
    log_err "Required script not found: $required_script"
    exit 1
  fi
done

if stage_enabled 3; then
  if ! command -v perf &>/dev/null; then
    log_warn "'perf' not found — stage 3 (c2c diagnostic) will be skipped."
    # Remove stage 3 from the list
    stages=$(echo "$stages" | sed 's/3//;s/,,/,/g;s/^,//;s/,$//')
  fi
fi

if stage_enabled 4; then
  if ! python3 -c "import matplotlib" 2>/dev/null; then
    log_warn "matplotlib not installed — stage 4 (plotting) will be skipped."
    log_info "Install with: pip install matplotlib numpy"
    stages=$(echo "$stages" | sed 's/4//;s/,,/,/g;s/^,//;s/,$//')
  fi
fi

# ── Auto-detect cores ───────────────────────────────────────────────────────

detect_physical_cores() {
  # Returns a sorted, deduplicated list of physical core IDs.
  # On SMT systems, pick one logical CPU per physical core.
  local -a phys_cores=()

  if [[ -d /sys/devices/system/cpu/cpu0/topology ]]; then
    # Use kernel topology: pick the lowest-numbered CPU in each core_id group
    local -A seen_core_ids=()
    local cpu core_id
    for cpu_dir in /sys/devices/system/cpu/cpu[0-9]*/topology; do
      cpu="${cpu_dir%/topology}"
      cpu="${cpu##*/cpu}"
      core_id=$(cat "$cpu_dir/core_id" 2>/dev/null || echo "$cpu")
      pkg_id=$(cat "$cpu_dir/physical_package_id" 2>/dev/null || echo "0")
      key="${pkg_id}_${core_id}"
      if [[ -z "${seen_core_ids[$key]:-}" ]]; then
        seen_core_ids["$key"]="$cpu"
        phys_cores+=("$cpu")
      fi
    done
  else
    # Fallback: use nproc and assume no SMT
    local n
    if command -v nproc >/dev/null 2>&1; then
      n=$(nproc --all)
    else
      n=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)
    fi
    for ((i = 0; i < n; i++)); do
      phys_cores+=("$i")
    done
  fi

  # Sort numerically
  IFS=$'\n' phys_cores=($(printf '%s\n' "${phys_cores[@]}" | sort -n))
  unset IFS
  printf '%s\n' "${phys_cores[@]}"
}

if [[ "$auto_cores" -eq 1 ]]; then
  if [[ -n "$victim_cores" || -n "$attacker_cores" ]]; then
    log_warn "--auto-cores overrides --victim-cores and --attacker-cores"
  fi

  mapfile -t all_phys_cores < <(detect_physical_cores)
  total_phys=${#all_phys_cores[@]}

  if [[ "$total_phys" -lt 4 ]]; then
    log_err "Only $total_phys physical cores detected; need at least 4 (2 victim + 2 attacker)."
    exit 1
  fi

  # Cap each group
  half=$(( total_phys / 2 ))
  victim_count=$(( half > auto_cores_max ? auto_cores_max : half ))
  attacker_count=$(( (total_phys - half) > auto_cores_max ? auto_cores_max : (total_phys - half) ))

  # Build comma-separated lists
  victim_cores=""
  for ((i = 0; i < victim_count; i++)); do
    [[ -n "$victim_cores" ]] && victim_cores+=","
    victim_cores+="${all_phys_cores[$i]}"
  done

  attacker_cores=""
  for ((i = half; i < half + attacker_count; i++)); do
    [[ -n "$attacker_cores" ]] && attacker_cores+=","
    attacker_cores+="${all_phys_cores[$i]}"
  done

  log_info "Auto-detected $total_phys physical cores"
  log_info "  Victim cores ($victim_count):   $victim_cores"
  log_info "  Attacker cores ($attacker_count): $attacker_cores"
fi

# ── Validate core lists ─────────────────────────────────────────────────────

if [[ -z "$victim_cores" || -z "$attacker_cores" ]]; then
  log_err "--victim-cores and --attacker-cores are required (or use --auto-cores)."
  usage
  exit 1
fi

as_array "$victim_cores" victim_arr
as_array "$attacker_cores" attacker_arr
victim_count=${#victim_arr[@]}
attacker_count=${#attacker_arr[@]}

for c in "${victim_arr[@]}" "${attacker_arr[@]}"; do
  [[ "$c" =~ ^[0-9]+$ ]] || { log_err "Non-integer core id: $c"; exit 1; }
done

# Check disjoint
for v in "${victim_arr[@]}"; do
  for a in "${attacker_arr[@]}"; do
    [[ "$v" == "$a" ]] && { log_err "Overlapping core: $v (must be disjoint)"; exit 1; }
  done
done

# ── Compute derived defaults ─────────────────────────────────────────────────

# Attacker thread sweep: default to powers of 2 up to attacker_count
if [[ -z "$attacker_thread_sweep" ]]; then
  attacker_thread_sweep=""
  n=1
  while (( n <= attacker_count )); do
    [[ -n "$attacker_thread_sweep" ]] && attacker_thread_sweep+=","
    attacker_thread_sweep+="$n"
    (( n *= 2 ))
  done
  # Always include max if not already there
  as_array "$attacker_thread_sweep" _sweep_check
  last="${_sweep_check[-1]}"
  if [[ "$last" -ne "$attacker_count" ]]; then
    attacker_thread_sweep+=",$attacker_count"
  fi
fi

# Seed rotation: default to all victim cores
if [[ -z "$seed_rotation" ]]; then
  seed_rotation="$victim_cores"
fi

# Build the list of all victim tests to run
all_victim_tests=("$victim_test")
if [[ -n "$extra_victim_tests" ]]; then
  IFS=',' read -r -a extras <<<"$extra_victim_tests"
  all_victim_tests+=("${extras[@]}")
fi

# ── Print plan ───────────────────────────────────────────────────────────────

cat <<PLAN

┌──────────────────────────────────────────────────────────────────┐
│               ADVERSARIAL INTERFERENCE STUDY                     │
├──────────────────────────────────────────────────────────────────┤
│  Victim cores:          $victim_cores
│  Attacker cores:        $attacker_cores
│  Victim test(s):        $(IFS=,; echo "${all_victim_tests[*]}")
│  Attacker test (RMW):   $attacker_test
│  Control test:          $control_test
│  Thread sweep:          $attacker_thread_sweep
│  Seed rotation:         $seed_rotation
│  Replicates (stage 2):  $replicates
│  Victim reps:           $victim_reps
│  Attacker reps:         $attacker_reps
│  Stages:                $stages
│  Output dir:            $output_dir
│  Dry run:               $( [[ "$dry_run" -eq 1 ]] && echo "YES" || echo "no" )
└──────────────────────────────────────────────────────────────────┘

PLAN

# ── Common flags builder ─────────────────────────────────────────────────────

common_flags=()
[[ "$fail_stats" -eq 1 ]] && common_flags+=(--fail-stats)
[[ "$dry_run" -eq 1 ]]    && common_flags+=(--dry-run)

run_cmd() {
  log_info "Running: $*"
  "$@"
}

# Track per-stage success
declare -A stage_status=()

# ═════════════════════════════════════════════════════════════════════════════
#  STAGE 1 — Lock-vs-FAI adversarial sweep
# ═════════════════════════════════════════════════════════════════════════════

if stage_enabled 1; then
  log_stage "1 — Lock-vs-FAI adversarial sweep"

  for vtest in "${all_victim_tests[@]}"; do
    suffix=""
    [[ "${#all_victim_tests[@]}" -gt 1 ]] && suffix="_${vtest}"
    stage1_dir="$output_dir/adversarial_lock_vs_fai${suffix}"

    log_info "Victim test: $vtest, attacker: $attacker_test, control: $control_test"
    log_info "  Attacker thread sweep: $attacker_thread_sweep"
    log_info "  Output: $stage1_dir"

    cmd=(
      "$script_dir/run_adversarial_lock_vs_fai.sh"
      --victim-cores "$victim_cores"
      --attacker-cores "$attacker_cores"
      --victim-test "$vtest"
      --attacker-test "$attacker_test"
      --control-test "$control_test"
      --attacker-thread-sweep "$attacker_thread_sweep"
      --victim-reps "$victim_reps"
      --attacker-reps "$attacker_reps"
      --fixed-victim-addr "$fixed_victim_addr"
      --ccbench "$ccbench"
      --output-dir "$stage1_dir"
      --results-csv "$stage1_dir/results.csv"
      "${common_flags[@]}"
    )

    if run_cmd "${cmd[@]}"; then
      stage_status[1]="ok"
      log_info "Stage 1 ($vtest): DONE — $stage1_dir/summary.csv"
    else
      stage_status[1]="failed"
      log_warn "Stage 1 ($vtest): FAILED (exit $?) — continuing with next stages"
    fi
  done
else
  log_info "Stage 1 skipped"
fi


# ═════════════════════════════════════════════════════════════════════════════
#  STAGE 2 — Separate-address sweep (seed rotation + replicates)
# ═════════════════════════════════════════════════════════════════════════════

if stage_enabled 2; then
  log_stage "2 — Separate-address sweep"

  for vtest in "${all_victim_tests[@]}"; do
    suffix=""
    [[ "${#all_victim_tests[@]}" -gt 1 ]] && suffix="_${vtest}"
    stage2_dir="$output_dir/adversarial_separate_attacker_addrs_sweep${suffix}"

    log_info "Victim test: $vtest, attacker: $attacker_test"
    log_info "  Seed rotation: $seed_rotation"
    log_info "  Replicates: $replicates"
    log_info "  Attacker core sweep: $attacker_thread_sweep"
    log_info "  Output: $stage2_dir"

    cmd=(
      "$script_dir/run_adversarial_separate_attacker_addrs_sweep.sh"
      --victim-cores "$victim_cores"
      --attacker-cores "$attacker_cores"
      --victim-test "$vtest"
      --attacker-test "$attacker_test"
      --attacker-core-sweep "$attacker_thread_sweep"
      --seed-cores "$seed_rotation"
      --replicates "$replicates"
      --victim-reps "$victim_reps"
      --attacker-reps "$attacker_reps"
      --fixed-victim-addr "$fixed_victim_addr"
      --ccbench "$ccbench"
      --output-dir "$stage2_dir"
      "${common_flags[@]}"
    )
    [[ "$perf_counters" -eq 1 ]] && cmd+=(--perf-counters)

    if run_cmd "${cmd[@]}"; then
      stage_status[2]="ok"
      log_info "Stage 2 ($vtest): DONE — $stage2_dir/raw_phase_results.csv"
    else
      stage_status[2]="failed"
      log_warn "Stage 2 ($vtest): FAILED (exit $?) — continuing with next stages"
    fi
  done
else
  log_info "Stage 2 skipped"
fi


# ═════════════════════════════════════════════════════════════════════════════
#  STAGE 3 — Perf c2c diagnostic
# ═════════════════════════════════════════════════════════════════════════════

if stage_enabled 3; then
  log_stage "3 — Perf c2c diagnostic"

  # Only run perf c2c for the primary victim test (not extra tests)
  stage3_dir="$output_dir/perf_c2c_diagnostic"

  log_info "Victim test: $victim_test, attacker: $attacker_test"
  log_info "  c2c victim reps: $c2c_victim_reps, c2c attacker reps: $c2c_attacker_reps"
  log_info "  Output: $stage3_dir"

  cmd=(
    "$script_dir/run_perf_c2c_diagnostic.sh"
    --victim-cores "$victim_cores"
    --attacker-cores "$attacker_cores"
    --victim-test "$victim_test"
    --attacker-test "$attacker_test"
    --victim-reps "$c2c_victim_reps"
    --attacker-reps "$c2c_attacker_reps"
    --fixed-victim-addr "$fixed_victim_addr"
    --ccbench "$ccbench"
    --output-dir "$stage3_dir"
  )
  [[ "$dry_run" -eq 1 ]] && cmd+=(--dry-run)

  if run_cmd "${cmd[@]}"; then
    stage_status[3]="ok"
    log_info "Stage 3: DONE — $stage3_dir/c2c_summary_report.csv"
  else
    stage_status[3]="failed"
    log_warn "Stage 3: FAILED (exit $?) — continuing with plotting"
  fi
else
  log_info "Stage 3 skipped"
fi


# ═════════════════════════════════════════════════════════════════════════════
#  STAGE 4 — Plot all results
# ═════════════════════════════════════════════════════════════════════════════

if stage_enabled 4; then
  log_stage "4 — Generate plots"

  plots_dir="$output_dir/plots"
  log_info "Scanning $output_dir for CSVs, writing plots to $plots_dir"

  cmd=(
    python3 "$script_dir/plot_adversarial_interference.py"
    "$output_dir"
    --out-dir "$plots_dir"
    --format "$plot_format"
    --dpi "$plot_dpi"
  )

  if [[ "$dry_run" -eq 1 ]]; then
    log_info "[dry-run] Would run: ${cmd[*]}"
  else
    if "${cmd[@]}"; then
      stage_status[4]="ok"
      log_info "Stage 4: DONE — plots in $plots_dir/"
    else
      stage_status[4]="failed"
      log_warn "Stage 4: plotting failed (exit $?)"
    fi
  fi
else
  log_info "Stage 4 skipped"
fi


# ═════════════════════════════════════════════════════════════════════════════
#  Summary
# ═════════════════════════════════════════════════════════════════════════════

echo
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║                     STUDY COMPLETE                              ║"
echo "╠══════════════════════════════════════════════════════════════════╣"

for s in 1 2 3 4; do
  status="${stage_status[$s]:-skipped}"
  case "$status" in
    ok)      icon="[OK]  " ;;
    failed)  icon="[FAIL]" ;;
    skipped) icon="[SKIP]" ;;
  esac
  case "$s" in
    1) label="Lock-vs-FAI adversarial sweep" ;;
    2) label="Separate-address sweep" ;;
    3) label="Perf c2c diagnostic" ;;
    4) label="Plot results" ;;
  esac
  printf "║  %s  Stage %s: %-44s ║\n" "$icon" "$s" "$label"
done

echo "╠══════════════════════════════════════════════════════════════════╣"
echo "║  Output directory: $output_dir"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo
echo "Key files:"

# List generated artifacts
for f in \
    "$output_dir/adversarial_lock_vs_fai/summary.csv" \
    "$output_dir/adversarial_separate_attacker_addrs_sweep/raw_phase_results.csv" \
    "$output_dir/adversarial_separate_attacker_addrs_sweep/summary_by_attacker_threads.csv" \
    "$output_dir/adversarial_separate_attacker_addrs_sweep/trend_separate_minus_shared.csv" \
    "$output_dir/perf_c2c_diagnostic/c2c_summary_report.csv" \
    "$output_dir/perf_c2c_diagnostic/c2c_summary_analysis.txt" \
    "$output_dir/plots/A_latency_bars.${plot_format}" \
    "$output_dir/plots/B_delta_distributions.${plot_format}" \
    "$output_dir/plots/C_shared_separate_comparison.${plot_format}" \
    "$output_dir/plots/D_hitm_chart.${plot_format}"; do
  if [[ -f "$f" ]]; then
    printf '  %s\n' "$f"
  fi
done

# Also list any extra-test variants
if [[ "${#all_victim_tests[@]}" -gt 1 ]]; then
  for vtest in "${all_victim_tests[@]}"; do
    for d in \
        "$output_dir/adversarial_lock_vs_fai_${vtest}" \
        "$output_dir/adversarial_separate_attacker_addrs_sweep_${vtest}"; do
      if [[ -d "$d" ]]; then
        printf '  %s/\n' "$d"
      fi
    done
  done
fi

echo
