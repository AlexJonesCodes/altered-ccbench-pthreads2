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

# --- Generate combined summary report ---

generate_summary() {
  local summary_file="$output_dir/c2c_summary_report.txt"
  log_info "Generating combined summary report: $summary_file"

  # Python script to parse all three reports and produce a side-by-side summary
  python3 - "$output_dir" "${phase_arr[@]}" > "$summary_file" <<'PYEOF'
import sys, re, os
from collections import OrderedDict

output_dir = sys.argv[1]
phases = sys.argv[2:]

# --- Parse trace event info section ---
def parse_trace_events(text):
    """Extract key-value pairs from the Trace Event Information section."""
    metrics = OrderedDict()
    in_section = False
    for line in text.splitlines():
        if 'Trace Event Information' in line:
            in_section = True
            continue
        if in_section and line.strip().startswith('='):
            if metrics:  # end of section (second === line)
                break
            continue
        if in_section:
            m = re.match(r'\s+(.+?)\s{2,}:\s+(.+)', line)
            if m:
                metrics[m.group(1).strip()] = m.group(2).strip()
    return metrics

# --- Parse global shared cache line section ---
def parse_shared_cacheline_info(text):
    """Extract key-value pairs from the Global Shared Cache Line section."""
    metrics = OrderedDict()
    in_section = False
    for line in text.splitlines():
        if 'Global Shared Cache Line Event Information' in line:
            in_section = True
            continue
        if in_section and line.strip().startswith('='):
            if metrics:
                break
            continue
        if in_section:
            m = re.match(r'\s+(.+?)\s{2,}:\s+(.+)', line)
            if m:
                metrics[m.group(1).strip()] = m.group(2).strip()
    return metrics

# --- Parse top N hottest shared cache lines ---
def parse_top_cachelines(text, top_n=5):
    """Parse the Shared Data Cache Line Table for the top N entries."""
    lines = text.splitlines()
    rows = []
    in_table = False
    header_seen = False
    for line in lines:
        if 'Shared Data Cache Line Table' in line:
            in_table = True
            continue
        if not in_table:
            continue
        stripped = line.strip()
        # Skip comment/header lines
        if stripped.startswith('#') or stripped.startswith('='):
            if 'Index' in stripped:
                header_seen = True
            continue
        if not header_seen:
            continue
        if not stripped or stripped.startswith('-'):
            continue
        # Data row: index, address, node, pa_cnt, hitm%, ...
        parts = stripped.split()
        if len(parts) < 5:
            continue
        try:
            idx = int(parts[0])
        except ValueError:
            continue
        rows.append({
            'index': idx,
            'address': parts[1],
            'node': parts[2],
            'pa_cnt': parts[3],
            'hitm_pct': parts[4],
            'total_hitm': parts[5] if len(parts) > 5 else '-',
            'lcl_hitm': parts[6] if len(parts) > 6 else '-',
            'rmt_hitm': parts[7] if len(parts) > 7 else '-',
            'total_records': parts[8] if len(parts) > 8 else '-',
            'total_loads': parts[9] if len(parts) > 9 else '-',
            'total_stores': parts[10] if len(parts) > 10 else '-',
        })
        if len(rows) >= top_n:
            break
    return rows

# --- Load reports ---
report_data = OrderedDict()
for phase in phases:
    report_file = os.path.join(output_dir, f'c2c_{phase}_report.txt')
    if not os.path.isfile(report_file):
        continue
    with open(report_file, 'r') as f:
        text = f.read()
    report_data[phase] = {
        'trace': parse_trace_events(text),
        'shared': parse_shared_cacheline_info(text),
        'top_lines': parse_top_cachelines(text),
    }

if not report_data:
    print("No report files found to summarize.")
    sys.exit(0)

avail_phases = list(report_data.keys())

# === Output ===
W = 78  # report width

def banner(title):
    print('=' * W)
    print(f'{title:^{W}}')
    print('=' * W)

def section(title):
    print()
    print('-' * W)
    print(f'  {title}')
    print('-' * W)

def safe_int(val):
    """Try to convert a string to int, stripping % signs."""
    if val is None:
        return None
    val = val.replace('%', '').strip()
    try:
        return int(val)
    except ValueError:
        return None

def safe_float(val):
    if val is None:
        return None
    val = val.replace('%', '').strip()
    try:
        return float(val)
    except ValueError:
        return None

def delta_str(base_val, other_val):
    """Compute delta between two numeric values, return formatted string."""
    b = safe_int(base_val)
    o = safe_int(other_val)
    if b is None or o is None:
        return ''
    diff = o - b
    if b == 0:
        if diff == 0:
            return '(+0)'
        return f'(+{diff})'
    pct = (diff / b) * 100
    sign = '+' if diff >= 0 else ''
    return f'({sign}{diff}, {sign}{pct:.0f}%)'

# --- Title ---
print()
banner('perf c2c Combined Summary Report')
print()
print(f'  Phases analysed: {", ".join(avail_phases)}')
print(f'  Output directory: {output_dir}')
print()

# --- Side-by-side Trace Event Info ---
section('Trace Event Overview (side-by-side)')

# Collect all metric keys across phases
all_trace_keys = list(OrderedDict.fromkeys(
    k for p in avail_phases for k in report_data[p]['trace']
))

# Key metrics to highlight
key_metrics = [
    'Total records', 'Locked Load/Store Operations',
    'Load Operations', 'Loads - Miss',
    'Load Fill Buffer Hit', 'Load L1D hit', 'Load L2D hit',
    'Load LLC hit', 'Load Local HITM', 'Load Remote HITM',
    'LLC Misses to Local DRAM', 'LLC Misses to Remote DRAM',
    'Store Operations', 'Store L1D Hit', 'Store L1D Miss',
]

# Print header
label_w = 34
col_w = 14
hdr = f'  {"Metric":<{label_w}}'
for p in avail_phases:
    hdr += f'  {p:>{col_w}}'
if len(avail_phases) > 1 and 'baseline' in avail_phases:
    hdr += f'  {"delta (vs base)":>{col_w + 6}}'
print(hdr)
print('  ' + '-' * (label_w + (col_w + 2) * len(avail_phases) + (col_w + 8 if len(avail_phases) > 1 and 'baseline' in avail_phases else 0)))

for key in all_trace_keys:
    vals = [report_data[p]['trace'].get(key, '-') for p in avail_phases]
    row = f'  {key:<{label_w}}'
    for v in vals:
        row += f'  {v:>{col_w}}'
    # Add delta column for the last non-baseline phase vs baseline
    if len(avail_phases) > 1 and 'baseline' in avail_phases:
        base_val = report_data['baseline']['trace'].get(key, '-')
        last_phase = [p for p in avail_phases if p != 'baseline'][-1]
        other_val = report_data[last_phase]['trace'].get(key, '-')
        d = delta_str(base_val, other_val)
        row += f'  {d:>{col_w + 6}}'
    print(row)

# --- Side-by-side Shared Cache Line Info ---
section('Global Shared Cache Line Info (side-by-side)')

all_shared_keys = list(OrderedDict.fromkeys(
    k for p in avail_phases for k in report_data[p]['shared']
))

hdr = f'  {"Metric":<{label_w}}'
for p in avail_phases:
    hdr += f'  {p:>{col_w}}'
if len(avail_phases) > 1 and 'baseline' in avail_phases:
    hdr += f'  {"delta (vs base)":>{col_w + 6}}'
print(hdr)
print('  ' + '-' * (label_w + (col_w + 2) * len(avail_phases) + (col_w + 8 if len(avail_phases) > 1 and 'baseline' in avail_phases else 0)))

for key in all_shared_keys:
    vals = [report_data[p]['shared'].get(key, '-') for p in avail_phases]
    row = f'  {key:<{label_w}}'
    for v in vals:
        row += f'  {v:>{col_w}}'
    if len(avail_phases) > 1 and 'baseline' in avail_phases:
        base_val = report_data['baseline']['shared'].get(key, '-')
        last_phase = [p for p in avail_phases if p != 'baseline'][-1]
        other_val = report_data[last_phase]['shared'].get(key, '-')
        d = delta_str(base_val, other_val)
        row += f'  {d:>{col_w + 6}}'
    print(row)

# --- Top contended cache lines per phase ---
section('Top Contended Cache Lines (per phase)')

for phase in avail_phases:
    top = report_data[phase]['top_lines']
    if not top:
        print(f'\n  [{phase}] No shared cache line entries found.')
        continue
    print(f'\n  [{phase.upper()}] Top {len(top)} hottest shared lines:')
    print(f'  {"#":<4} {"Address":<22} {"HITM%":>7} {"TotHITM":>8} {"LclHITM":>8} '
          f'{"RmtHITM":>8} {"Records":>8} {"Loads":>8} {"Stores":>8}')
    print('  ' + '-' * 87)
    for r in top:
        print(f'  {r["index"]:<4} {r["address"]:<22} {r["hitm_pct"]:>7} '
              f'{r["total_hitm"]:>8} {r["lcl_hitm"]:>8} {r["rmt_hitm"]:>8} '
              f'{r["total_records"]:>8} {r["total_loads"]:>8} {r["total_stores"]:>8}')

# --- Key findings / analysis ---
section('Analysis & Key Findings')

def get_int(phase, section_name, key):
    if phase not in report_data:
        return None
    return safe_int(report_data[phase][section_name].get(key))

# 1. HITM comparison
print()
hitm_data = {}
for p in avail_phases:
    local = get_int(p, 'trace', 'Load Local HITM')
    remote = get_int(p, 'trace', 'Load Remote HITM')
    total = (local or 0) + (remote or 0)
    hitm_data[p] = {'local': local, 'remote': remote, 'total': total}
    print(f'  {p:<12} Total HITM = {total:>8}  (Local: {local if local is not None else "?":>6}, '
          f'Remote: {remote if remote is not None else "?":>6})')

# Compare phases if we have baseline
if 'baseline' in hitm_data and len(hitm_data) > 1:
    base_hitm = hitm_data['baseline']['total']
    print()
    for p in avail_phases:
        if p == 'baseline':
            continue
        p_hitm = hitm_data[p]['total']
        if base_hitm > 0:
            ratio = p_hitm / base_hitm
            print(f'  {p} vs baseline: {ratio:.1f}x HITM '
                  f'({"SIGNIFICANT contention increase" if ratio > 2 else "moderate increase" if ratio > 1.2 else "similar level"})')
        elif p_hitm > 0:
            print(f'  {p} vs baseline: baseline had 0 HITM, {p} has {p_hitm} — new contention introduced')
        else:
            print(f'  {p} vs baseline: both have minimal HITM — low contention')

# 2. Shared cache line count comparison
print()
for p in avail_phases:
    scl = get_int(p, 'shared', 'Total Shared Cache Lines')
    print(f'  {p:<12} Shared cache lines: {scl if scl is not None else "?"}')

# 3. Store vs Load ratio
print()
for p in avail_phases:
    loads = get_int(p, 'trace', 'Load Operations')
    stores = get_int(p, 'trace', 'Store Operations')
    if loads and stores and loads > 0:
        ratio = stores / loads
        print(f'  {p:<12} Store/Load ratio: {ratio:.2f} '
              f'({"store-heavy" if ratio > 1.5 else "balanced" if ratio > 0.5 else "load-heavy"})')

# 4. Cache hit distribution
print()
print('  Cache hit distribution (loads):')
for p in avail_phases:
    loads = get_int(p, 'trace', 'Load Operations')
    l1 = get_int(p, 'trace', 'Load L1D hit')
    fb = get_int(p, 'trace', 'Load Fill Buffer Hit')
    llc = get_int(p, 'trace', 'Load LLC hit')
    if loads and loads > 0:
        l1_pct = (l1 or 0) / loads * 100
        fb_pct = (fb or 0) / loads * 100
        llc_pct = (llc or 0) / loads * 100
        other_pct = 100 - l1_pct - fb_pct - llc_pct
        print(f'    {p:<12} L1D: {l1_pct:5.1f}%  FillBuf: {fb_pct:5.1f}%  '
              f'LLC: {llc_pct:5.1f}%  Other: {other_pct:5.1f}%')

# 5. Contention diagnosis
print()
if 'shared' in hitm_data and 'separate' in hitm_data and 'baseline' in hitm_data:
    sh = hitm_data['shared']['total']
    sep = hitm_data['separate']['total']
    base = hitm_data['baseline']['total']

    print('  Contention diagnosis:')
    if sh > base * 2 and sh > sep * 1.5:
        print('    -> SHARED attackers cause significantly more HITM than SEPARATE.')
        print('    -> This confirms TRUE contention on shared cache lines (not just')
        print('       general coherence traffic from having more threads).')
    elif sh > base * 2 and sep > base * 2:
        print('    -> Both SHARED and SEPARATE show elevated HITM vs baseline.')
        if abs(sh - sep) / max(sh, sep, 1) < 0.2:
            print('    -> HITM levels are similar — contention may be due to general')
            print('       coherence traffic rather than address-specific false sharing.')
        else:
            print('    -> SHARED is higher — some address-specific contention exists,')
            print('       but general coherence traffic also contributes.')
    elif sh <= base * 1.2 and sep <= base * 1.2:
        print('    -> Neither SHARED nor SEPARATE show significant HITM increase.')
        print('    -> Low contention — the workload may not be contention-sensitive,')
        print('       or the perf c2c sampling rate is too low to capture it.')
    else:
        print('    -> Mixed results. Review the per-cacheline tables above for')
        print('       address-specific hotspots.')
elif len(avail_phases) >= 2:
    print('  (Partial phases available — run all three for full contention diagnosis)')

# 6. Hottest address check
print()
print('  Hottest cache lines across phases:')
for p in avail_phases:
    top = report_data[p]['top_lines']
    if top:
        hottest = top[0]
        print(f'    {p:<12} #{hottest["index"]} {hottest["address"]}  '
              f'HITM={hottest["total_hitm"]}  ({hottest["hitm_pct"]} of total)')

# Check if the same address appears as hottest across phases
if len(avail_phases) >= 2:
    addrs = {}
    for p in avail_phases:
        top = report_data[p]['top_lines']
        if top:
            addrs[p] = set(r['address'] for r in top)
    all_top = set()
    for s in addrs.values():
        all_top |= s
    common = all_top.copy()
    for s in addrs.values():
        common &= s
    if common:
        print(f'\n    Addresses appearing in top lines across ALL phases: {", ".join(sorted(common))}')
        print('    -> These are persistent hotspots worth investigating.')

print()
print('=' * W)
print(f'{"End of Summary Report":^{W}}')
print('=' * W)
PYEOF
}

# --- Run requested phases ---

# (phases were already run above; now generate summary if we have reports)
has_reports=0
for phase in "${phase_arr[@]}"; do
  [[ -f "$output_dir/c2c_${phase}_report.txt" ]] && has_reports=1 && break
done

if [[ "$has_reports" -eq 1 && "$dry_run" -eq 0 ]]; then
  generate_summary
fi

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

summary_file="$output_dir/c2c_summary_report.txt"
if [[ -f "$summary_file" ]]; then
  echo "  $summary_file  <-- COMBINED SUMMARY"
fi

cat <<'GUIDE'

How to interpret:
  1. Look at the "Shared Data Cache Line Table" in each report.
  2. The "Hitm" column shows cross-core invalidation hits (the expensive ones).
  3. Compare HITM counts between baseline, shared, and separate reports.
  4. High HITM on the victim's cache line address in the "shared" report
     but not in "separate" confirms coherence-hotspot contention.
  5. The "Snoop" column shows snoop filter activity — high snoop counts
     on a line indicate it's bouncing between cores.
  6. See c2c_summary_report.txt for a combined side-by-side comparison
     with automated contention diagnosis.

For deeper analysis:
  perf c2c report -i <data_file> --stdio --full-symbols
GUIDE
