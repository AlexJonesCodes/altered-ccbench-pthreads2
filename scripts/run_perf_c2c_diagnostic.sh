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
  local summary_file="$output_dir/c2c_summary_report.csv"
  log_info "Generating combined summary CSV: $summary_file"

  # Python script to parse all three reports and produce a CSV summary
  python3 - "$output_dir" "${phase_arr[@]}" > "$summary_file" <<'PYEOF'
import sys, re, os, csv, io
from collections import OrderedDict

output_dir = sys.argv[1]
phases = sys.argv[2:]

writer = csv.writer(sys.stdout)

def parse_trace_events(text):
    metrics = OrderedDict()
    in_section = False
    for line in text.splitlines():
        if 'Trace Event Information' in line:
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

def parse_shared_cacheline_info(text):
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

def parse_top_cachelines(text, top_n=5):
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
        if stripped.startswith('#') or stripped.startswith('='):
            if 'Index' in stripped:
                header_seen = True
            continue
        if not header_seen:
            continue
        if not stripped or stripped.startswith('-'):
            continue
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
            'total_hitm': parts[5] if len(parts) > 5 else '',
            'lcl_hitm': parts[6] if len(parts) > 6 else '',
            'rmt_hitm': parts[7] if len(parts) > 7 else '',
            'total_records': parts[8] if len(parts) > 8 else '',
            'total_loads': parts[9] if len(parts) > 9 else '',
            'total_stores': parts[10] if len(parts) > 10 else '',
        })
        if len(rows) >= top_n:
            break
    return rows

def safe_int(val):
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

def delta_val(base_val, other_val):
    b = safe_int(base_val)
    o = safe_int(other_val)
    if b is None or o is None:
        return '', ''
    diff = o - b
    if b == 0:
        return str(diff), ''
    pct = (diff / b) * 100
    return str(diff), f'{pct:.1f}%'

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
    writer.writerow(['No report files found to summarize.'])
    sys.exit(0)

avail_phases = list(report_data.keys())
has_baseline = 'baseline' in avail_phases

# ===== Sheet 1: Trace Event Metrics =====
writer.writerow(['TRACE EVENT METRICS'])
header = ['section', 'metric'] + avail_phases
if has_baseline and len(avail_phases) > 1:
    for p in avail_phases:
        if p != 'baseline':
            header += [f'delta_vs_baseline ({p})', f'delta_pct_vs_baseline ({p})']
writer.writerow(header)

all_trace_keys = list(OrderedDict.fromkeys(
    k for p in avail_phases for k in report_data[p]['trace']
))

for key in all_trace_keys:
    row = ['trace_events', key]
    for p in avail_phases:
        row.append(report_data[p]['trace'].get(key, ''))
    if has_baseline and len(avail_phases) > 1:
        base_val = report_data['baseline']['trace'].get(key, '')
        for p in avail_phases:
            if p != 'baseline':
                other_val = report_data[p]['trace'].get(key, '')
                d, dpct = delta_val(base_val, other_val)
                row += [d, dpct]
    writer.writerow(row)

# ===== Sheet 2: Global Shared Cache Line Info =====
writer.writerow([])
writer.writerow(['GLOBAL SHARED CACHE LINE INFO'])
writer.writerow(header)

all_shared_keys = list(OrderedDict.fromkeys(
    k for p in avail_phases for k in report_data[p]['shared']
))

for key in all_shared_keys:
    row = ['shared_cacheline_info', key]
    for p in avail_phases:
        row.append(report_data[p]['shared'].get(key, ''))
    if has_baseline and len(avail_phases) > 1:
        base_val = report_data['baseline']['shared'].get(key, '')
        for p in avail_phases:
            if p != 'baseline':
                other_val = report_data[p]['shared'].get(key, '')
                d, dpct = delta_val(base_val, other_val)
                row += [d, dpct]
    writer.writerow(row)

# ===== Sheet 3: Top Contended Cache Lines =====
writer.writerow([])
writer.writerow(['TOP CONTENDED CACHE LINES'])
writer.writerow(['phase', 'rank', 'address', 'node', 'pa_cnt', 'hitm_pct',
                 'total_hitm', 'lcl_hitm', 'rmt_hitm',
                 'total_records', 'total_loads', 'total_stores'])

for phase in avail_phases:
    for r in report_data[phase]['top_lines']:
        writer.writerow([
            phase, r['index'], r['address'], r['node'], r['pa_cnt'],
            r['hitm_pct'], r['total_hitm'], r['lcl_hitm'], r['rmt_hitm'],
            r['total_records'], r['total_loads'], r['total_stores'],
        ])

# ===== Sheet 4: Derived Analysis =====
writer.writerow([])
writer.writerow(['DERIVED ANALYSIS'])

def get_int(phase, section_name, key):
    if phase not in report_data:
        return None
    return safe_int(report_data[phase][section_name].get(key))

# HITM summary
writer.writerow(['phase', 'local_hitm', 'remote_hitm', 'total_hitm',
                 'hitm_ratio_vs_baseline', 'hitm_verdict'])

hitm_data = {}
for p in avail_phases:
    local = get_int(p, 'trace', 'Load Local HITM')
    remote = get_int(p, 'trace', 'Load Remote HITM')
    total = (local or 0) + (remote or 0)
    hitm_data[p] = {'local': local, 'remote': remote, 'total': total}

base_hitm = hitm_data.get('baseline', {}).get('total', 0)
for p in avail_phases:
    h = hitm_data[p]
    if p == 'baseline' or not has_baseline:
        ratio = ''
        verdict = 'baseline'
    elif base_hitm > 0:
        r = h['total'] / base_hitm
        ratio = f'{r:.2f}'
        verdict = ('SIGNIFICANT contention increase' if r > 2
                   else 'moderate increase' if r > 1.2
                   else 'similar level')
    elif h['total'] > 0:
        ratio = 'inf'
        verdict = 'new contention introduced'
    else:
        ratio = '1.00'
        verdict = 'both minimal'
    writer.writerow([p, h['local'] if h['local'] is not None else '',
                     h['remote'] if h['remote'] is not None else '',
                     h['total'], ratio, verdict])

# Store/Load ratio
writer.writerow([])
writer.writerow(['STORE/LOAD RATIO'])
writer.writerow(['phase', 'loads', 'stores', 'store_load_ratio', 'characterisation'])
for p in avail_phases:
    loads = get_int(p, 'trace', 'Load Operations')
    stores = get_int(p, 'trace', 'Store Operations')
    if loads and stores and loads > 0:
        ratio = stores / loads
        char = ('store-heavy' if ratio > 1.5
                else 'balanced' if ratio > 0.5
                else 'load-heavy')
        writer.writerow([p, loads, stores, f'{ratio:.3f}', char])

# Cache hit distribution
writer.writerow([])
writer.writerow(['CACHE HIT DISTRIBUTION (loads)'])
writer.writerow(['phase', 'total_loads', 'l1d_hit', 'l1d_pct',
                 'fill_buf_hit', 'fill_buf_pct', 'llc_hit', 'llc_pct',
                 'other_pct'])
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
        writer.writerow([p, loads, l1 or 0, f'{l1_pct:.1f}%',
                         fb or 0, f'{fb_pct:.1f}%',
                         llc or 0, f'{llc_pct:.1f}%',
                         f'{other_pct:.1f}%'])

# Shared cache line counts
writer.writerow([])
writer.writerow(['SHARED CACHE LINE COUNTS'])
writer.writerow(['phase', 'total_shared_cache_lines'])
for p in avail_phases:
    scl = get_int(p, 'shared', 'Total Shared Cache Lines')
    writer.writerow([p, scl if scl is not None else ''])

# Contention diagnosis
writer.writerow([])
writer.writerow(['CONTENTION DIAGNOSIS'])
if 'shared' in hitm_data and 'separate' in hitm_data and 'baseline' in hitm_data:
    sh = hitm_data['shared']['total']
    sep = hitm_data['separate']['total']
    base = hitm_data['baseline']['total']

    writer.writerow(['baseline_hitm', 'shared_hitm', 'separate_hitm',
                     'shared_vs_baseline_ratio', 'separate_vs_baseline_ratio',
                     'shared_vs_separate_ratio', 'diagnosis'])

    sh_base_ratio = f'{sh / base:.2f}' if base > 0 else 'inf' if sh > 0 else '0'
    sep_base_ratio = f'{sep / base:.2f}' if base > 0 else 'inf' if sep > 0 else '0'
    sh_sep_ratio = f'{sh / sep:.2f}' if sep > 0 else 'inf' if sh > 0 else '0'

    if sh > base * 2 and sh > sep * 1.5:
        diag = 'TRUE contention on shared cache lines confirmed (shared >> separate >> baseline)'
    elif sh > base * 2 and sep > base * 2:
        if abs(sh - sep) / max(sh, sep, 1) < 0.2:
            diag = 'General coherence traffic (shared ~ separate; both elevated vs baseline)'
        else:
            diag = 'Mixed: some address-specific contention plus general coherence traffic'
    elif sh <= base * 1.2 and sep <= base * 1.2:
        diag = 'Low contention (neither shared nor separate significantly above baseline)'
    else:
        diag = 'Mixed results; review per-cacheline hotspot tables'

    writer.writerow([base, sh, sep, sh_base_ratio, sep_base_ratio, sh_sep_ratio, diag])
else:
    writer.writerow(['Partial phases available; run all three for full diagnosis'])

# Hottest addresses across phases
writer.writerow([])
writer.writerow(['HOTTEST CACHE LINE PER PHASE'])
writer.writerow(['phase', 'address', 'hitm_pct', 'total_hitm'])
for p in avail_phases:
    top = report_data[p]['top_lines']
    if top:
        h = top[0]
        writer.writerow([p, h['address'], h['hitm_pct'], h['total_hitm']])

# Common hotspot addresses
if len(avail_phases) >= 2:
    addrs = {}
    for p in avail_phases:
        top = report_data[p]['top_lines']
        if top:
            addrs[p] = set(r['address'] for r in top)
    common = None
    for s in addrs.values():
        common = s if common is None else common & s
    if common:
        writer.writerow([])
        writer.writerow(['PERSISTENT HOTSPOT ADDRESSES (appear in top lines across ALL phases)'])
        writer.writerow(['address'])
        for addr in sorted(common):
            writer.writerow([addr])
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

summary_file="$output_dir/c2c_summary_report.csv"
if [[ -f "$summary_file" ]]; then
  echo "  $summary_file  <-- COMBINED SUMMARY (CSV)"
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
  6. See c2c_summary_report.csv for a combined side-by-side comparison
     with automated contention diagnosis (open in any spreadsheet app).

For deeper analysis:
  perf c2c report -i <data_file> --stdio --full-symbols
GUIDE
