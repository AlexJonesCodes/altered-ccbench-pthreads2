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
  <output-dir>/c2c_baseline.data                perf c2c raw data
  <output-dir>/c2c_baseline_report.txt          human-readable HITM report
  <output-dir>/c2c_shared.data                  ...
  <output-dir>/c2c_shared_report.txt
  <output-dir>/c2c_separate.data
  <output-dir>/c2c_separate_report.txt
  <output-dir>/c2c_summary_report.csv           side-by-side metrics summary
  <output-dir>/c2c_summary_analysis.txt         plain-English analysis summary

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

if ! command -v perf &>/dev/null; then
  echo "ERROR: 'perf' not found in PATH." >&2
  exit 1
fi

if ! perf c2c record -e ldlat-loads -- true 2>/dev/null; then
  if ! perf c2c record -- true 2>/dev/null; then
    echo "ERROR: perf c2c not available on this system. Requires perf with c2c support and kernel >=4.2." >&2
    exit 1
  fi
  c2c_record_args=()
  echo "WARNING: ldlat-loads not supported; using default perf c2c events." >&2
else
  c2c_record_args=(-e ldlat-loads -e ldlat-stores)
fi
rm -f perf.data

as_array() { local csv="$1"; local -n ref="$2"; IFS=',' read -r -a ref <<<"$csv"; }
hex_to_dec() { printf '%d' "$(( $1 ))"; }
dec_to_hex() { printf '0x%x' "$1"; }
make_list() {
  local n="$1" v="$2" out=""
  for ((i=0;i<n;i++)); do
    [[ -n "$out" ]] && out+=",$v" || out="$v"
  done
  printf '[%s]' "$out"
}
log_info() { echo "INFO: $*" >&2; }

resolve_test_id() {
  local spec="$1"
  if [[ "$spec" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$spec"
    return 0
  fi

  python3 - "$spec" <<'PY2'
import re
import sys

name = sys.argv[1]
idx = 0
in_arr = False

for raw in open('include/ccbench.h', encoding='utf-8'):
    if 'const char* moesi_type_des[' in raw:
        in_arr = True
        continue
    if in_arr and '};' in raw:
        break
    if in_arr and '"' in raw:
        m = re.search(r'"([^"]+)"', raw)
        if not m:
            continue
        if m.group(1) == name:
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

mkdir -p "$output_dir"

build_victim_cmd() {
  local reps="$1"
  local -a cmd=("$ccbench" -r "$reps" -t "$victim_tests" -x "$victim_core_list" -b "$seed_core" -s "$victim_stride")
  [[ "$fixed_victim_addr" != "none" ]] && cmd+=(-Z "$fixed_victim_addr")
  printf '%s\n' "${cmd[@]}"
}

run_c2c_baseline() {
  log_info "perf c2c phase: baseline (victim only)"
  local data_file="$output_dir/c2c_baseline.data"
  local report_file="$output_dir/c2c_baseline_report.txt"

  local -a victim_cmd
  mapfile -t victim_cmd < <(build_victim_cmd "$victim_reps")

  if [[ "$dry_run" -eq 1 ]]; then
    printf '[dry-run] perf c2c record -o %s --' "$data_file" >&2
    printf ' %q' "${victim_cmd[@]}" >&2
    echo >&2
    return
  fi

  perf c2c record "${c2c_record_args[@]}" --ldlat="$ldlat" \
    -o "$data_file" -- "${victim_cmd[@]}" >/dev/null 2>&1 || true

  perf c2c report -i "$data_file" --stdio > "$report_file" 2>&1 || true
  log_info "perf c2c baseline done: $report_file"
}

run_c2c_with_attackers() {
  local mode="$1"
  log_info "perf c2c phase: $mode"

  local data_file="$output_dir/c2c_${mode}.data"
  local report_file="$output_dir/c2c_${mode}_report.txt"

  local -a victim_cmd
  mapfile -t victim_cmd < <(build_victim_cmd "$victim_reps")

  local base_dec step_dec
  base_dec=$(hex_to_dec "$separate_attacker_base")
  step_dec=$(hex_to_dec "$separate_attacker_step")

  local wrapper="$output_dir/.c2c_wrapper_${mode}.sh"
  {
    echo '#!/usr/bin/env bash'
    echo 'set -euo pipefail'
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

    printf '%q' "${victim_cmd[0]}"
    for arg in "${victim_cmd[@]:1}"; do
      printf ' %q' "$arg"
    done
    printf ' >/dev/null 2>&1\n'

    echo 'VICTIM_RC=$?'
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

generate_summary() {
  local csv_file="$output_dir/c2c_summary_report.csv"
  local analysis_file="$output_dir/c2c_summary_analysis.txt"
  log_info "Generating combined summary: $csv_file + $analysis_file"

  python3 - "$output_dir" "$csv_file" "$analysis_file" "${phase_arr[@]}" <<'PYEOF'
import csv
import os
import re
import sys
from collections import OrderedDict

output_dir, csv_file, analysis_file = sys.argv[1:4]
phases = sys.argv[4:]


def parse_kv_section(text, title):
    metrics = OrderedDict()
    in_section = False
    for line in text.splitlines():
        if title in line:
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
    rows = []
    in_table = False
    header_seen = False
    for line in text.splitlines():
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
        if not header_seen or not stripped or stripped.startswith('-'):
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
    s = str(val).replace('%', '').replace(',', '').strip()
    return int(s) if re.fullmatch(r'-?\d+', s) else None


def delta_val(base_val, other_val):
    b = safe_int(base_val)
    o = safe_int(other_val)
    if b is None or o is None:
        return '', ''
    diff = o - b
    if b == 0:
        return str(diff), ''
    pct = (diff / b) * 100.0
    return str(diff), f'{pct:.1f}%'


report_data = OrderedDict()
for phase in phases:
    report_file = os.path.join(output_dir, f'c2c_{phase}_report.txt')
    if not os.path.isfile(report_file):
        continue
    with open(report_file, 'r', encoding='utf-8', errors='ignore') as f:
        text = f.read()
    report_data[phase] = {
        'trace': parse_kv_section(text, 'Trace Event Information'),
        'shared': parse_kv_section(text, 'Global Shared Cache Line Event Information'),
        'top_lines': parse_top_cachelines(text),
    }

if not report_data:
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow(['No report files found to summarize.'])
    with open(analysis_file, 'w', encoding='utf-8') as f:
        f.write('No report files found to summarize.\n')
    sys.exit(0)

avail_phases = list(report_data.keys())
has_baseline = 'baseline' in report_data

with open(csv_file, 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)

    writer.writerow(['TRACE EVENT METRICS'])
    header = ['section', 'metric'] + avail_phases
    if has_baseline and len(avail_phases) > 1:
        for p in avail_phases:
            if p != 'baseline':
                header += [f'delta_vs_baseline ({p})', f'delta_pct_vs_baseline ({p})']
    writer.writerow(header)

    all_trace_keys = list(OrderedDict.fromkeys(k for p in avail_phases for k in report_data[p]['trace']))
    for key in all_trace_keys:
        row = ['trace_events', key]
        for p in avail_phases:
            row.append(report_data[p]['trace'].get(key, ''))
        if has_baseline and len(avail_phases) > 1:
            base_val = report_data['baseline']['trace'].get(key, '')
            for p in avail_phases:
                if p == 'baseline':
                    continue
                d, dpct = delta_val(base_val, report_data[p]['trace'].get(key, ''))
                row += [d, dpct]
        writer.writerow(row)

    writer.writerow([])
    writer.writerow(['GLOBAL SHARED CACHE LINE INFO'])
    writer.writerow(header)

    all_shared_keys = list(OrderedDict.fromkeys(k for p in avail_phases for k in report_data[p]['shared']))
    for key in all_shared_keys:
        row = ['shared_cacheline_info', key]
        for p in avail_phases:
            row.append(report_data[p]['shared'].get(key, ''))
        if has_baseline and len(avail_phases) > 1:
            base_val = report_data['baseline']['shared'].get(key, '')
            for p in avail_phases:
                if p == 'baseline':
                    continue
                d, dpct = delta_val(base_val, report_data[p]['shared'].get(key, ''))
                row += [d, dpct]
        writer.writerow(row)

    writer.writerow([])
    writer.writerow(['TOP CONTENDED CACHE LINES'])
    writer.writerow(['phase', 'rank', 'address', 'node', 'pa_cnt', 'hitm_pct', 'total_hitm', 'lcl_hitm', 'rmt_hitm', 'total_records', 'total_loads', 'total_stores'])
    for phase in avail_phases:
        for r in report_data[phase]['top_lines']:
            writer.writerow([phase, r['index'], r['address'], r['node'], r['pa_cnt'], r['hitm_pct'], r['total_hitm'], r['lcl_hitm'], r['rmt_hitm'], r['total_records'], r['total_loads'], r['total_stores']])


def metric(phase, section, key):
    return safe_int(report_data.get(phase, {}).get(section, {}).get(key))


hitm = {}
for p in avail_phases:
    local = metric(p, 'trace', 'Load Local HITM') or 0
    remote = metric(p, 'trace', 'Load Remote HITM') or 0
    hitm[p] = {'local': local, 'remote': remote, 'total': local + remote}

baseline_total = hitm.get('baseline', {}).get('total', 0)

def ratio(a, b):
    if b == 0:
        return 'inf' if a > 0 else '0.00'
    return f'{a / b:.2f}'


def diagnose():
    if not {'baseline', 'shared', 'separate'}.issubset(hitm.keys()):
        return 'Partial phases available; run baseline,shared,separate for full diagnosis.'
    base = hitm['baseline']['total']
    shared = hitm['shared']['total']
    separate = hitm['separate']['total']

    if shared > base * 2 and shared > separate * 1.5:
        return 'TRUE contention on shared cache lines (shared >> separate and baseline).'
    if shared > base * 2 and separate > base * 2:
        if abs(shared - separate) / max(shared, separate, 1) < 0.2:
            return 'General coherence traffic (shared ~= separate, both elevated vs baseline).'
        return 'Mixed: address-specific contention plus elevated general coherence traffic.'
    if shared <= base * 1.2 and separate <= base * 1.2:
        return 'Low contention (neither shared nor separate significantly above baseline).'
    return 'Mixed results; inspect top cache-line hotspots.'


with open(analysis_file, 'w', encoding='utf-8') as out:
    out.write('perf c2c overall summary\n')
    out.write('========================\n\n')
    out.write(f'Phases included: {", ".join(avail_phases)}\n\n')

    out.write('HITM totals by phase:\n')
    for p in avail_phases:
        h = hitm[p]
        vs_base = ''
        if p != 'baseline' and has_baseline:
            vs_base = f' (ratio vs baseline: {ratio(h["total"], baseline_total)})'
        out.write(f'  - {p}: local={h["local"]}, remote={h["remote"]}, total={h["total"]}{vs_base}\n')

    if {'shared', 'separate'}.issubset(hitm.keys()):
        out.write(f'\nshared/separate HITM ratio: {ratio(hitm["shared"]["total"], hitm["separate"]["total"])}\n')

    out.write(f'\nDiagnosis: {diagnose()}\n\n')

    out.write('Hottest cache line by phase:\n')
    for p in avail_phases:
        top = report_data[p]['top_lines']
        if not top:
            out.write(f'  - {p}: (no top-line table parsed)\n')
            continue
        row = top[0]
        out.write(f'  - {p}: addr={row["address"]}, hitm_pct={row["hitm_pct"]}, total_hitm={row["total_hitm"]}\n')

    out.write('\nNotes:\n')
    out.write('  - shared >> separate suggests true cache-line contention on common address(es).\n')
    out.write('  - shared ~= separate with both high suggests general coherence pressure.\n')
    out.write('  - use c2c_summary_report.csv for side-by-side metric details.\n')
PYEOF
}

IFS=',' read -r -a phase_arr <<<"$phases"
for phase in "${phase_arr[@]}"; do
  case "$phase" in
    baseline) run_c2c_baseline ;;
    shared) run_c2c_with_attackers shared ;;
    separate) run_c2c_with_attackers separate ;;
    *) echo "WARNING: unknown phase '$phase', skipping" >&2 ;;
  esac
done

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

summary_csv="$output_dir/c2c_summary_report.csv"
summary_analysis="$output_dir/c2c_summary_analysis.txt"
if [[ -f "$summary_csv" ]]; then
  echo "  $summary_csv  <-- COMBINED METRICS SUMMARY (CSV)"
fi
if [[ -f "$summary_analysis" ]]; then
  echo "  $summary_analysis  <-- OVERALL HUMAN-READABLE ANALYSIS"
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
  6. Read c2c_summary_analysis.txt for an overall diagnosis and top hotspots.
  7. Use c2c_summary_report.csv for side-by-side metric breakdown.

For deeper analysis:
  perf c2c report -i <data_file> --stdio --full-symbols
GUIDE
