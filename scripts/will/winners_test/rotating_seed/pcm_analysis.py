#!/usr/bin/env python3
"""
ccbench_pcm_runner_quiet.py

Cross-run aware analysis of Intel PCM CSV during ccbench (e.g., CAS).
Quiet version:
- No per-run plots (optional single cross-run plot only).
- Minimal console output.
- Full per-run metrics saved to summary.csv/json.

Configure the CONFIG section and run.
"""

from __future__ import annotations
import os, re, json, glob
from typing import Dict, List, Tuple, Optional
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# ================
# CONFIG (edit me)
# ================
CSV_FILES = [
    "Xeon_Silver_4114/pcm_4k_1mill_cas/2/pcm_flattened.csv",
]
OUT_ROOT = "out"
SAMPLE_PERIOD_SEC = 0.1

# Plots
MAKE_CROSS_RUN_PLOT = True     # only 1 image per file
MAKE_PER_RUN_PLOTS = False     # keep False to avoid 4k*2 images
SAVE_DERIVED_PER_RUN = False   # per-run derived CSVs (large) -> keep False

# Console verbosity
VERBOSE_PER_RUN = False        # per-run debug line
PRINT_PER_RUN_TABLE = False    # large; keep False
# If you want a small peek, set the window sizes below (>0)
PRINT_HEAD_RUNS = 0            # print first N runs
PRINT_TAIL_RUNS = 0            # print last N runs
PRINT_AROUND_ONSET_WINDOW = 0  # print +/- K runs around detected onset

# Cross-run onset detection
RUN_SMOOTH_WINDOW = 21         # runs
RUN_BIAS_THRESHOLD = 0.55      # share0 must exceed this or be below 1-this
RUN_STABILITY = 200            # must remain biased this many runs

# Intra-run smoothing (used only to compute per-run means)
SMOOTH_WINDOW_SEC = 0.5
# ================


def load_csv_clean(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, engine="python", skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    if 'run' not in df.columns:
        df['run'] = 0
    # strip % and whitespace on object cols
    obj = df.select_dtypes(include=['object']).columns
    if len(obj):
        def clean(x):
            if isinstance(x, str):
                x = x.replace('%', '').strip()
                if x == '' or x.upper() == 'N/A':
                    return np.nan
            return x
        df[obj] = df[obj].applymap(clean)
    to_numeric(df, ['run', 'pcm_sample'])
    return df


def to_numeric(df: pd.DataFrame, cols: List[str]) -> None:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')


def find_cols(df: pd.DataFrame, pattern: str) -> List[str]:
    rgx = re.compile(pattern)
    return [c for c in df.columns if rgx.search(c)]


def sum_cols(df: pd.DataFrame, cols: List[str]) -> pd.Series:
    if not cols:
        return pd.Series([np.nan] * len(df), index=df.index)
    return df[cols].apply(pd.to_numeric, errors='coerce').sum(axis=1, skipna=True)


def jains_fairness(x0: float, x1: float) -> float:
    num = (x0 + x1) ** 2
    den = 2.0 * (x0 ** 2 + x1 ** 2) if (x0 != 0 or x1 != 0) else np.nan
    return float(num / den) if den and den != 0 else np.nan


def smooth_series(x: pd.Series, window: int) -> pd.Series:
    return x.rolling(window=max(1, int(window)), min_periods=1, center=True).mean()


def detect_onset_threshold_series(x: pd.Series,
                                  upper_thresh: float,
                                  lower_thresh: float,
                                  stability_len: int) -> Optional[int]:
    n = len(x)
    if n == 0 or x.isna().all():
        return None
    biased = ((x > upper_thresh) | (x < lower_thresh)).fillna(False).values
    for i in range(0, n - stability_len + 1):
        if biased[i:i + stability_len].all():
            return i
    return None


def analyze_intra_run(run_id: int,
                      g: pd.DataFrame,
                      sample_period: float,
                      smooth_window_sec: float) -> Tuple[Dict, pd.DataFrame]:
    # Time axis
    if 'pcm_sample' in g.columns:
        g = g.sort_values(by=['pcm_sample']).reset_index(drop=True)
        t = (g['pcm_sample'] - g['pcm_sample'].min()) * sample_period
    else:
        g = g.reset_index(drop=True)
        t = np.arange(len(g)) * sample_period

    cols_req = [
        'Socket 0_INST', 'Socket 1_INST',
        'Socket 0_IPC',  'Socket 1_IPC',
        'Socket 0_AFREQ','Socket 1_AFREQ',
        'Socket 0_TEMP', 'Socket 1_TEMP',
    ]
    to_numeric(g, cols_req)

    eps = 1e-12
    inst0 = g['Socket 0_INST'] if 'Socket 0_INST' in g.columns else pd.Series(np.nan, index=g.index)
    inst1 = g['Socket 1_INST'] if 'Socket 1_INST' in g.columns else pd.Series(np.nan, index=g.index)
    inst_sum = inst0.add(inst1, fill_value=np.nan)
    share0 = inst0 / (inst_sum.replace(0, np.nan) + eps)

    upi_out0_cols = find_cols(g, r'^SKT0trafficOut_UPI\d+')
    upi_out1_cols = find_cols(g, r'^SKT1trafficOut_UPI\d+')
    upi_in0_cols  = find_cols(g, r'^SKT0dataIn_UPI\d+')
    upi_in1_cols  = find_cols(g, r'^SKT1dataIn_UPI\d+')
    to_numeric(g, upi_out0_cols + upi_out1_cols + upi_in0_cols + upi_in1_cols)
    upi_out0 = sum_cols(g, upi_out0_cols); upi_out1 = sum_cols(g, upi_out1_cols)
    upi_in0  = sum_cols(g, upi_in0_cols);  upi_in1  = sum_cols(g, upi_in1_cols)

    unc0 = next((g[c] for c in ['UncFREQ (Ghz)_SKT0', 'SKT0 Pack C-States_UncFREQ (Ghz)'] if c in g.columns), pd.Series(np.nan, index=g.index))
    unc1 = next((g[c] for c in ['UncFREQ (Ghz)_SKT1', 'SKT1 Pack C-States_UncFREQ (Ghz)'] if c in g.columns), pd.Series(np.nan, index=g.index))
    to_numeric(g, [c for c in ['UncFREQ (Ghz)_SKT0','SKT0 Pack C-States_UncFREQ (Ghz)','UncFREQ (Ghz)_SKT1','SKT1 Pack C-States_UncFREQ (Ghz)'] if c in g.columns])

    afreq0 = g['Socket 0_AFREQ'] if 'Socket 0_AFREQ' in g.columns else pd.Series(np.nan, index=g.index)
    afreq1 = g['Socket 1_AFREQ'] if 'Socket 1_AFREQ' in g.columns else pd.Series(np.nan, index=g.index)
    ipc0   = g['Socket 0_IPC']   if 'Socket 0_IPC'   in g.columns else pd.Series(np.nan, index=g.index)
    ipc1   = g['Socket 1_IPC']   if 'Socket 1_IPC'   in g.columns else pd.Series(np.nan, index=g.index)
    temp0  = g['Socket 0_TEMP']  if 'Socket 0_TEMP'  in g.columns else pd.Series(np.nan, index=g.index)
    temp1  = g['Socket 1_TEMP']  if 'Socket 1_TEMP'  in g.columns else pd.Series(np.nan, index=g.index)

    # Smooth to compute per-run means robustly
    win = max(1, int(round(smooth_window_sec / sample_period)))
    def sm(x: pd.Series) -> pd.Series:
        return x.rolling(window=win, min_periods=1, center=True).mean()

    derived = pd.DataFrame({
        't_sec': t,
        'share0': share0, 'share0_sm': sm(share0),
        'inst0': inst0, 'inst1': inst1,
        'upi_out0': upi_out0, 'upi_out1': upi_out1,
        'upi_in0': upi_in0, 'upi_in1': upi_in1,
        'upi_out_diff_sm': sm(upi_out0 - upi_out1),
        'upi_in_diff_sm': sm(upi_in0 - upi_in1),
        'afreq0': afreq0, 'afreq1': afreq1, 'afreq_diff_sm': sm(afreq0 - afreq1),
        'ipc0': ipc0, 'ipc1': ipc1, 'ipc_diff_sm': sm(ipc0 - ipc1),
        'temp0': temp0, 'temp1': temp1, 'temp_diff_sm': sm(temp0 - temp1),
        'unc0': unc0, 'unc1': unc1, 'unc_diff_sm': sm(unc0 - unc1),
    })

    total_inst0 = float(np.nansum(inst0))
    total_inst1 = float(np.nansum(inst1))
    inst_total  = total_inst0 + total_inst1
    share0_total = (total_inst0 / inst_total) if inst_total > 0 else np.nan

    metrics = {
        'run_id': int(run_id),
        'n_samples': int(len(g)),
        'onset_idx': None,                 # intra-run onset suppressed (not used)
        'onset_time_sec': None,
        'share0_mean_before': float(np.nanmean(derived['share0_sm'])) if len(derived) else None,
        'share0_mean_after': None,
        'winner_socket_after': None,
        'jains_fairness_over_run': float(jains_fairness(total_inst0, total_inst1)) if inst_total > 0 else None,
        'total_inst_socket0': total_inst0,
        'total_inst_socket1': total_inst1,
        'share0_total': share0_total,
        'upi_out_diff_mean': float(np.nanmean(derived['upi_out_diff_sm'])) if len(derived) else None,
        'upi_in_diff_mean': float(np.nanmean(derived['upi_in_diff_sm'])) if len(derived) else None,
        'unc_diff_mean': float(np.nanmean(derived['unc_diff_sm'])) if len(derived) else None,
        'afreq_diff_mean': float(np.nanmean(derived['afreq_diff_sm'])) if len(derived) else None,
        'ipc_diff_mean': float(np.nanmean(derived['ipc_diff_sm'])) if len(derived) else None,
        'temp_diff_mean': float(np.nanmean(derived['temp_diff_sm'])) if len(derived) else None,
    }

    if VERBOSE_PER_RUN:
        print(f"[run {run_id}] samples={metrics['n_samples']} share0_total={metrics['share0_total']:.4f} Jain={metrics['jains_fairness_over_run']:.6f}")

    return metrics, derived


def cross_run_analysis(sm: pd.DataFrame, outdir: str) -> Dict:
    sr = sm.sort_values('run_id').reset_index(drop=True).copy()
    sr['share0_total_sm'] = smooth_series(sr['share0_total'], RUN_SMOOTH_WINDOW)

    onset_idx = detect_onset_threshold_series(
        sr['share0_total_sm'],
        upper_thresh=RUN_BIAS_THRESHOLD,
        lower_thresh=1 - RUN_BIAS_THRESHOLD,
        stability_len=RUN_STABILITY,
    )
    onset_run_id = int(sr.loc[onset_idx, 'run_id']) if onset_idx is not None else None

    winner = None
    mean_share_before = float(np.nanmean(sr['share0_total'][:onset_idx])) if onset_idx is not None else float(np.nanmean(sr['share0_total']))
    mean_share_after  = float(np.nanmean(sr['share0_total'][onset_idx:])) if onset_idx is not None else None
    if onset_idx is not None and not np.isnan(mean_share_after):
        winner = 0 if mean_share_after > 0.5 else 1

    if MAKE_CROSS_RUN_PLOT:
        fig, ax = plt.subplots(figsize=(12, 5))
        ax.plot(sr['run_id'], sr['share0_total'], label='Per-run Share0 (totals)', color='tab:blue', alpha=0.35)
        ax.plot(sr['run_id'], sr['share0_total_sm'], label=f'Smoothed (w={RUN_SMOOTH_WINDOW})', color='tab:blue')
        ax.axhline(0.5, color='gray', linestyle='--', linewidth=1)
        if onset_idx is not None:
            ax.axvline(sr.loc[onset_idx, 'run_id'], color='red', linestyle='--', label='Cross-run Onset')
        ax.set_xlabel("Run id")
        ax.set_ylabel("Socket 0 share (by total INST)")
        ax.set_title("Cross-run fairness: share0_total vs run")
        ax.legend(loc='best')
        fig.tight_layout()
        fig.savefig(os.path.join(outdir, "cross_run_timeseries.png"), dpi=150)
        plt.close(fig)

    return {
        'onset_run_idx': int(onset_idx) if onset_idx is not None else None,
        'onset_run_id': onset_run_id,
        'winner_after_onset': int(winner) if winner is not None else None,
        'mean_share_before': mean_share_before,
        'mean_share_after': mean_share_after,
    }


def analyze_csv(path: str) -> pd.DataFrame:
    basename = os.path.splitext(os.path.basename(path))[0]
    out_dir = os.path.join(OUT_ROOT, basename)
    os.makedirs(out_dir, exist_ok=True)

    df = load_csv_clean(path)
    to_numeric(df, ['run', 'pcm_sample'])

    groups = [(int(r), g.copy()) for r, g in df.groupby('run', dropna=False)]
    all_metrics: List[Dict] = []
    derived_cache: Dict[int, pd.DataFrame] = {}

    for run_id, g in groups:
        m, derived = analyze_intra_run(
            run_id=run_id, g=g,
            sample_period=SAMPLE_PERIOD_SEC,
            smooth_window_sec=SMOOTH_WINDOW_SEC,
        )
        all_metrics.append(m)
        if SAVE_DERIVED_PER_RUN:
            derived.to_csv(os.path.join(out_dir, f"run_{run_id}_derived.csv"), index=False)
        if MAKE_PER_RUN_PLOTS:
            # Disabled by default to avoid thousands of images
            fig, ax = plt.subplots()
            ax.plot(derived['t_sec'], derived['share0_sm'])
            ax.set_title(f"Run {run_id}")
            fig.savefig(os.path.join(out_dir, f"run_{run_id}_timeseries.png"), dpi=120)
            plt.close(fig)
        derived_cache[run_id] = derived

    sm = pd.DataFrame(all_metrics).sort_values('run_id')
    sm_path_csv = os.path.join(out_dir, "summary.csv")
    sm_path_json = os.path.join(out_dir, "summary.json")
    sm.to_csv(sm_path_csv, index=False)
    with open(sm_path_json, "w") as f:
        json.dump(sm.to_dict(orient='records'), f, indent=2)

    print(f"\n===== File: {path} =====")
    print("=== Aggregate Summary ===")
    print(f"Runs analyzed: {len(sm)}")
    print(f"Median Jain fairness over run: {float(sm['jains_fairness_over_run'].dropna().median()) if sm['jains_fairness_over_run'].notna().any() else None}")
    print(f"Saved summary to: {sm_path_csv} and {sm_path_json}")

    cra = cross_run_analysis(sm, out_dir)
    print("\n=== Cross-run Epoch Analysis ===")
    print(f"Onset run index: {cra['onset_run_idx']}")
    print(f"Onset run id: {cra['onset_run_id']}")
    print(f"Winner after onset (0/1): {cra['winner_after_onset']}")
    print(f"Mean share before onset: {cra['mean_share_before']}")
    print(f"Mean share after onset: {cra['mean_share_after']}")
    if MAKE_CROSS_RUN_PLOT:
        print(f"Saved cross-run plot to: {os.path.join(out_dir, 'cross_run_timeseries.png')}")

    # Optional very small per-run print
    if PRINT_PER_RUN_TABLE or PRINT_HEAD_RUNS or PRINT_TAIL_RUNS or PRINT_AROUND_ONSET_WINDOW:
        print("\n=== Per-run Metrics (sample) ===")
        cols = ['run_id', 'share0_total', 'jains_fairness_over_run',
                'upi_out_diff_mean', 'unc_diff_mean', 'afreq_diff_mean', 'ipc_diff_mean', 'temp_diff_mean']
        print(",".join(cols))

        sel_idx = []
        if PRINT_HEAD_RUNS:
            sel_idx.extend(list(sm.index[:PRINT_HEAD_RUNS]))
        if PRINT_TAIL_RUNS:
            sel_idx.extend(list(sm.index[-PRINT_TAIL_RUNS:]))
        if PRINT_AROUND_ONSET_WINDOW and cra['onset_run_idx'] is not None:
            i0 = max(0, cra['onset_run_idx'] - PRINT_AROUND_ONSET_WINDOW)
            i1 = min(len(sm), cra['onset_run_idx'] + PRINT_AROUND_ONSET_WINDOW + 1)
            sel_idx.extend(list(range(i0, i1)))

        sel_idx = sorted(set(sel_idx))
        for i in sel_idx:
            row = sm.iloc[i]
            vals = [row.get(c, None) for c in cols]
            out = []
            for v in vals:
                if isinstance(v, (float, np.floating)):
                    out.append(f"{v:.6g}")
                else:
                    out.append(str(v))
            print(",".join(out))

    return sm


def main():
    paths: List[str] = []
    for pat in CSV_FILES:
        expanded = glob.glob(pat) or ([pat] if os.path.isfile(pat) else [])
        paths.extend(expanded)
    if not paths:
        print("No CSV files matched. Edit CSV_FILES in CONFIG.")
        return

    print(f"Analyzing {len(paths)} file(s)...")
    for p in paths:
        try:
            analyze_csv(p)
        except Exception as e:
            print(f"[ERROR] {p}: {e}")


if __name__ == "__main__":
    main()
