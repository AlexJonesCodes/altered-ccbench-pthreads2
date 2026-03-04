#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/run_adversarial_separate_attacker_addrs_sweep.sh [options]

Run the separate-address adversary experiment across:
  - rotated victim seed cores, and
  - attacker core-count sweep (increasing adversary traffic intensity).

This script repeatedly calls:
  scripts/run_adversarial_separate_attacker_addrs.sh
and then aggregates results into deeper summaries.

Options:
  --victim-cores LIST             Comma-separated victim cores (required)
  --attacker-cores LIST           Comma-separated attacker cores (required)
  --attacker-core-sweep LIST      Attacker thread counts (default: full count only)
  --seed-cores LIST               Victim seed cores to rotate (default: all victim cores)
  --replicates N                  Repeats per (seed,attacker_count) point (default: 1)
  --victim-test NAME|ID           Victim primitive (default: CAS)
  --attacker-test NAME|ID         Attacker primitive (default: FAI)
  --victim-reps N                 Victim repetitions (default: 20000)
  --attacker-reps N               Attacker repetitions (default: 200000000)
  --victim-stride N               Victim stride (default: 1)
  --attacker-stride N             Attacker stride (default: 1)
  --fixed-victim-addr SPEC        static|0xHEX|none (default: static)
  --victim-fallback-addr HEX      Victim fallback fixed line (default: 0x700000200000)
  --shared-attacker-addr HEX      Shared attacker line (default: 0x700000100000)
  --separate-attacker-base HEX    Separate-address base (default: 0x700000300000)
  --separate-attacker-step HEX    Separate-address step (default: 0x1000)
  --fail-stats                    Enable fail stats
  --ccbench PATH                  Path to ccbench (default: ./ccbench)
  --output-dir DIR                Output dir (default: results/adversarial_separate_attacker_addrs_sweep)
  --dry-run                       Print planned commands only
  -h, --help                      Show help
USAGE
}

victim_cores=""
attacker_cores=""
attacker_core_sweep=""
seed_cores=""
replicates=1
victim_test="CAS"
attacker_test="FAI"
victim_reps=20000
attacker_reps=200000000
victim_stride=1
attacker_stride=1
fixed_victim_addr="static"
victim_fallback_addr="0x700000200000"
shared_attacker_addr="0x700000100000"
separate_attacker_base="0x700000300000"
separate_attacker_step="0x1000"
fail_stats=0
ccbench="./ccbench"
output_dir="results/adversarial_separate_attacker_addrs_sweep"
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --victim-cores) victim_cores="$2"; shift 2 ;;
    --attacker-cores) attacker_cores="$2"; shift 2 ;;
    --attacker-core-sweep) attacker_core_sweep="$2"; shift 2 ;;
    --seed-cores) seed_cores="$2"; shift 2 ;;
    --replicates) replicates="$2"; shift 2 ;;
    --victim-test) victim_test="$2"; shift 2 ;;
    --attacker-test) attacker_test="$2"; shift 2 ;;
    --victim-reps) victim_reps="$2"; shift 2 ;;
    --attacker-reps) attacker_reps="$2"; shift 2 ;;
    --victim-stride) victim_stride="$2"; shift 2 ;;
    --attacker-stride) attacker_stride="$2"; shift 2 ;;
    --fixed-victim-addr) fixed_victim_addr="$2"; shift 2 ;;
    --victim-fallback-addr) victim_fallback_addr="$2"; shift 2 ;;
    --shared-attacker-addr) shared_attacker_addr="$2"; shift 2 ;;
    --separate-attacker-base) separate_attacker_base="$2"; shift 2 ;;
    --separate-attacker-step) separate_attacker_step="$2"; shift 2 ;;
    --fail-stats) fail_stats=1; shift ;;
    --ccbench) ccbench="$2"; shift 2 ;;
    --output-dir) output_dir="$2"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

[[ -n "$victim_cores" && -n "$attacker_cores" ]] || { echo "--victim-cores and --attacker-cores are required." >&2; exit 1; }
[[ "$replicates" =~ ^[0-9]+$ ]] || { echo "--replicates must be an integer" >&2; exit 1; }
[[ -x "$ccbench" ]] || { echo "ccbench not found/executable: $ccbench" >&2; exit 1; }

base_runner="scripts/run_adversarial_separate_attacker_addrs.sh"
[[ -x "$base_runner" ]] || { echo "Missing executable: $base_runner" >&2; exit 1; }

as_array() { local csv="$1"; local -n ref="$2"; IFS=',' read -r -a ref <<<"$csv"; }
join_first_n_csv() {
  local n="$1"; shift
  local -a arr=("$@")
  local out=""
  local i
  for ((i=0; i<n; i++)); do
    [[ -n "$out" ]] && out+=",${arr[$i]}" || out="${arr[$i]}"
  done
  printf '%s' "$out"
}

as_array "$victim_cores" victim_arr
as_array "$attacker_cores" attacker_arr
[[ "${#victim_arr[@]}" -gt 0 && "${#attacker_arr[@]}" -gt 0 ]] || { echo "Core lists cannot be empty" >&2; exit 1; }

for c in "${victim_arr[@]}" "${attacker_arr[@]}"; do
  [[ "$c" =~ ^[0-9]+$ ]] || { echo "Non-integer core id: $c" >&2; exit 1; }
done

if [[ -z "$seed_cores" ]]; then
  seed_cores="$victim_cores"
fi
as_array "$seed_cores" seed_arr
for s in "${seed_arr[@]}"; do [[ "$s" =~ ^[0-9]+$ ]] || { echo "Non-integer seed core: $s" >&2; exit 1; }; done

max_attackers="${#attacker_arr[@]}"
if [[ -z "$attacker_core_sweep" ]]; then
  attacker_core_sweep="$max_attackers"
fi
as_array "$attacker_core_sweep" attacker_count_arr
for n in "${attacker_count_arr[@]}"; do
  [[ "$n" =~ ^[0-9]+$ ]] || { echo "Non-integer attacker count: $n" >&2; exit 1; }
  (( n >= 1 && n <= max_attackers )) || { echo "attacker count out of range: $n (max=$max_attackers)" >&2; exit 1; }
done

mkdir -p "$output_dir/runs"
raw_csv="$output_dir/raw_phase_results.csv"
agg_csv="$output_dir/summary_by_attacker_threads.csv"
trend_csv="$output_dir/trend_separate_minus_shared.csv"

# One-time preflight: run the inner script once to discover the effective
# fixed-victim-addr (the inner script's adaptive_victim_preflight may fall
# back from "static" to a hex address to "none").  Reuse that result for
# every subsequent run so we don't repeat the crash/fallback cycle.
if [[ "$dry_run" -eq 0 && "$fixed_victim_addr" == "static" ]]; then
  preflight_dir="$output_dir/runs/_preflight"
  mkdir -p "$preflight_dir"
  first_seed="${seed_arr[0]}"
  first_atk=$(join_first_n_csv "${attacker_count_arr[0]}" "${attacker_arr[@]}")
  echo "INFO: running one-time preflight to determine effective fixed-victim-addr" >&2
  "$base_runner" \
    --victim-cores "$victim_cores" \
    --attacker-cores "$first_atk" \
    --seed-core "$first_seed" \
    --victim-test "$victim_test" \
    --attacker-test "$attacker_test" \
    --victim-reps 1 \
    --attacker-reps 1 \
    --victim-stride "$victim_stride" \
    --attacker-stride "$attacker_stride" \
    --fixed-victim-addr "$fixed_victim_addr" \
    --victim-fallback-addr "$victim_fallback_addr" \
    --shared-attacker-addr "$shared_attacker_addr" \
    --separate-attacker-base "$separate_attacker_base" \
    --separate-attacker-step "$separate_attacker_step" \
    --ccbench "$ccbench" \
    --output-dir "$preflight_dir" >/dev/null 2>&1 || true
  if [[ -f "$preflight_dir/run_meta.txt" ]]; then
    eff_addr=$(grep '^fixed_victim_addr_effective=' "$preflight_dir/run_meta.txt" | cut -d= -f2)
    if [[ -n "$eff_addr" ]]; then
      if [[ "$eff_addr" != "$fixed_victim_addr" ]]; then
        echo "INFO: preflight resolved fixed-victim-addr: $fixed_victim_addr -> $eff_addr (will use for all runs)" >&2
      else
        echo "INFO: preflight confirmed fixed-victim-addr=$eff_addr works" >&2
      fi
      fixed_victim_addr="$eff_addr"
    fi
  fi
  rm -rf "$preflight_dir"
fi

printf '%s\n' 'run_id,seed_core,attacker_threads,replicate,phase,mean_avg,jain_fairness,success_rate,latency_ratio_vs_baseline,latency_delta_pct_vs_baseline,effect_vs_baseline,notes,run_dir' > "$raw_csv"

run_id=0
for seed in "${seed_arr[@]}"; do
  for atk_n in "${attacker_count_arr[@]}"; do
    atk_subset=$(join_first_n_csv "$atk_n" "${attacker_arr[@]}")
    for ((rep=1; rep<=replicates; rep++)); do
      run_id=$((run_id + 1))
      run_dir="$output_dir/runs/run_${run_id}_seed${seed}_atk${atk_n}_rep${rep}"
      mkdir -p "$run_dir"

      cmd=("$base_runner"
        --victim-cores "$victim_cores"
        --attacker-cores "$atk_subset"
        --seed-core "$seed"
        --victim-test "$victim_test"
        --attacker-test "$attacker_test"
        --victim-reps "$victim_reps"
        --attacker-reps "$attacker_reps"
        --victim-stride "$victim_stride"
        --attacker-stride "$attacker_stride"
        --fixed-victim-addr "$fixed_victim_addr"
        --victim-fallback-addr "$victim_fallback_addr"
        --shared-attacker-addr "$shared_attacker_addr"
        --separate-attacker-base "$separate_attacker_base"
        --separate-attacker-step "$separate_attacker_step"
        --ccbench "$ccbench"
        --output-dir "$run_dir")
      [[ "$fail_stats" -eq 1 ]] && cmd+=(--fail-stats)
      [[ "$dry_run" -eq 1 ]] && cmd+=(--dry-run)

      echo "INFO: run_id=$run_id seed=$seed attacker_threads=$atk_n rep=$rep out=$run_dir" >&2
      "${cmd[@]}"

      summary_file="$run_dir/summary.csv"
      [[ -f "$summary_file" ]] || { echo "Missing summary file: $summary_file" >&2; exit 1; }

      awk -F, -v run_id="$run_id" -v seed="$seed" -v atk_n="$atk_n" -v rep="$rep" -v run_dir="$run_dir" '
        NR==1 { next }
        {
          printf "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n", run_id, seed, atk_n, rep, $1, $2, $3, $4, $5, $6, $7, $8, run_dir
        }
      ' "$summary_file" >> "$raw_csv"
    done
  done
done

awk -F, '
  NR==1 { next }
  {
    key = $3 FS $5
    cnt[key]++

    mean = ($6 == "NA" ? 0 : $6 + 0)
    fair = ($7 == "NA" ? 0 : $7 + 0)
    succ = ($8 == "NA" ? 0 : $8 + 0)
    ratio = ($9 == "NA" ? 0 : $9 + 0)
    delta = ($10 == "NA" ? 0 : $10 + 0)

    has_mean[key] += ($6 != "NA")
    has_fair[key] += ($7 != "NA")
    has_succ[key] += ($8 != "NA")
    has_ratio[key] += ($9 != "NA")
    has_delta[key] += ($10 != "NA")

    sum_mean[key] += mean
    sum_fair[key] += fair
    sum_succ[key] += succ
    sum_ratio[key] += ratio
    sum_delta[key] += delta
  }
  END {
    print "attacker_threads,phase,samples,mean_avg_mean,jain_fairness_mean,success_rate_mean,latency_ratio_mean,latency_delta_pct_mean"
    for (k in cnt) {
      split(k, a, FS)
      atk=a[1]; phase=a[2]
      mm = (has_mean[k]  ? sum_mean[k]  / has_mean[k]  : "NA")
      mf = (has_fair[k]  ? sum_fair[k]  / has_fair[k]  : "NA")
      ms = (has_succ[k]  ? sum_succ[k]  / has_succ[k]  : "NA")
      mr = (has_ratio[k] ? sum_ratio[k] / has_ratio[k] : "NA")
      md = (has_delta[k] ? sum_delta[k] / has_delta[k] : "NA")
      if (mm != "NA") mm = sprintf("%.4f", mm)
      if (mf != "NA") mf = sprintf("%.4f", mf)
      if (ms != "NA") ms = sprintf("%.2f", ms)
      if (mr != "NA") mr = sprintf("%.4f", mr)
      if (md != "NA") md = sprintf("%.2f", md)
      printf "%s,%s,%d,%s,%s,%s,%s,%s\n", atk, phase, cnt[k], mm, mf, ms, mr, md
    }
  }
' "$raw_csv" | sort -t, -k1,1n -k2,2 > "$agg_csv"

awk -F, '
  NR==1 { next }
  {
    atk=$1; phase=$2; ratio=$7; delta=$8
    if (phase == "victim_plus_shared")   { shared_ratio[atk]=ratio; shared_delta[atk]=delta }
    if (phase == "victim_plus_separate") { sep_ratio[atk]=ratio;    sep_delta[atk]=delta }
  }
  END {
    print "attacker_threads,separate_minus_shared_ratio,separate_minus_shared_delta_pct"
    for (atk in sep_ratio) {
      if ((atk in shared_ratio) && sep_ratio[atk] != "NA" && shared_ratio[atk] != "NA") {
        r = sep_ratio[atk] - shared_ratio[atk]
      } else {
        r = "NA"
      }
      if ((atk in shared_delta) && sep_delta[atk] != "NA" && shared_delta[atk] != "NA") {
        d = sep_delta[atk] - shared_delta[atk]
      } else {
        d = "NA"
      }
      if (r != "NA") r = sprintf("%.4f", r)
      if (d != "NA") d = sprintf("%.2f", d)
      printf "%s,%s,%s\n", atk, r, d
    }
  }
' "$agg_csv" | sort -t, -k1,1n > "$trend_csv"

cat <<REPORT
Wrote detailed outputs:
  raw runs:    $raw_csv
  aggregated:  $agg_csv
  trend delta: $trend_csv

Interpretation:
  - Increase --attacker-core-sweep (e.g., 1,2,4,8,...) to test rising adversary traffic.
  - Rotate --seed-cores to stabilize results across placement bias.
  - In trend_separate_minus_shared, positive values mean separate-address attackers
    have higher latency impact than shared-address attackers at that attacker count.
REPORT
