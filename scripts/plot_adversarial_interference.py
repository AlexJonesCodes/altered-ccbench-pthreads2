#!/usr/bin/env python3
"""Plot adversarial interference experiment results.

Produces four families of plots from the CSV outputs of:
  1. run_adversarial_lock_vs_fai.sh           -> summary.csv
  2. run_adversarial_separate_attacker_addrs_sweep.sh -> raw_phase_results.csv
  3. run_adversarial_separate_attacker_addrs.sh       -> summary.csv
  4. run_perf_c2c_diagnostic.sh               -> c2c_summary_report.csv

Plot families:
  A) Latency bars — victim mean latency per phase, grouped by attacker count
  B) Delta distributions — box plots of paired deltas across replicates
  C) Shared-vs-separate comparison — grouped bars with diagnosis annotation
  D) Perf c2c HITM chart — stacked bars of local vs remote HITM counts

Usage:
  python3 scripts/plot_adversarial_interference.py <results-dir> [options]

  <results-dir> is the top-level results directory.  The script auto-discovers
  CSV files in the expected subdirectory layout:

    <results-dir>/
      adversarial_lock_vs_fai/summary.csv          (plot A)
      adversarial_separate_attacker_addrs_sweep/
        raw_phase_results.csv                       (plots B, C)
      adversarial_separate_attacker_addrs/
        summary.csv                                 (plot C fallback)
      perf_c2c_diagnostic/
        c2c_summary_report.csv                      (plot D)

  Any missing CSVs are silently skipped — only found data is plotted.

Examples:
  python3 scripts/plot_adversarial_interference.py results/
  python3 scripts/plot_adversarial_interference.py results/ --format pdf --dpi 300
  python3 scripts/plot_adversarial_interference.py results/ --out-dir results/plots
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from collections import defaultdict, OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np


# ---------------------------------------------------------------------------
#  Constants
# ---------------------------------------------------------------------------

PHASE_ORDER = ["victim_baseline", "victim_plus_attacker_rmw",
               "victim_plus_attacker_control"]
PHASE_LABELS = {
    "victim_baseline": "Baseline",
    "victim_plus_attacker_rmw": "Attacker (RMW)",
    "victim_plus_attacker_control": "Attacker (control)",
}
PHASE_COLORS = {
    "victim_baseline": "#4C72B0",
    "victim_plus_attacker_rmw": "#DD8452",
    "victim_plus_attacker_control": "#55A868",
}

SEP_PHASE_ORDER = ["victim_baseline", "victim_plus_shared",
                   "victim_plus_separate"]
SEP_PHASE_LABELS = {
    "victim_baseline": "Baseline",
    "victim_plus_shared": "Shared line",
    "victim_plus_separate": "Separate lines",
}
SEP_PHASE_COLORS = {
    "victim_baseline": "#4C72B0",
    "victim_plus_shared": "#DD8452",
    "victim_plus_separate": "#55A868",
}

HITM_COLORS = {"local": "#5B9BD5", "remote": "#ED7D31"}


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("results_dir",
                   help="Top-level results directory to scan for CSV files")
    p.add_argument("--out-dir", default=None,
                   help="Output directory for plots (default: <results_dir>/plots)")
    p.add_argument("--format", default="png", choices=["png", "pdf", "svg"],
                   help="Image format (default: png)")
    p.add_argument("--dpi", type=int, default=150,
                   help="DPI for raster formats (default: 150)")
    return p.parse_args()


# ---------------------------------------------------------------------------
#  I/O helpers
# ---------------------------------------------------------------------------

def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    """Read a CSV file into a list of dicts; return [] if file missing."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def safe_float(v: Optional[str], default: float = float("nan")) -> float:
    if v is None or v.strip() == "" or v.strip().upper() == "NA":
        return default
    try:
        return float(v.replace(",", ""))
    except (TypeError, ValueError):
        return default


def sorted_unique_int(values: list) -> list:
    """Return sorted unique values, preferring integer sort."""
    try:
        return sorted(set(values), key=lambda x: int(x))
    except (ValueError, TypeError):
        return sorted(set(values))


def extract_values(rows: List[dict], field: str) -> np.ndarray:
    vals = [safe_float(r.get(field, "")) for r in rows]
    arr = np.array(vals)
    return arr[~np.isnan(arr)]


# ---------------------------------------------------------------------------
#  Auto-discovery of CSV files
# ---------------------------------------------------------------------------

def discover_csvs(results_dir: Path) -> dict:
    """Locate known CSV files under the results directory tree."""
    found = {}

    # 1. adversarial_lock_vs_fai summary
    for candidate in [
        results_dir / "adversarial_lock_vs_fai" / "summary.csv",
        results_dir / "summary.csv",
    ]:
        if candidate.exists():
            rows = read_csv_rows(candidate)
            if rows and "phase" in rows[0] and "attacker_threads" in rows[0]:
                found["lock_vs_fai_summary"] = (candidate, rows)
                break

    # 2. sweep raw results
    for candidate in [
        results_dir / "adversarial_separate_attacker_addrs_sweep" / "raw_phase_results.csv",
        results_dir / "raw_phase_results.csv",
    ]:
        if candidate.exists():
            rows = read_csv_rows(candidate)
            if rows and "phase" in rows[0]:
                found["sweep_raw"] = (candidate, rows)
                break

    # 3. single separate-address summary
    for candidate in [
        results_dir / "adversarial_separate_attacker_addrs" / "summary.csv",
    ]:
        if candidate.exists():
            rows = read_csv_rows(candidate)
            if rows and "phase" in rows[0] and "effect_vs_baseline" in rows[0]:
                found["sep_addr_summary"] = (candidate, rows)
                break

    # 4. perf c2c summary
    for candidate in [
        results_dir / "perf_c2c_diagnostic" / "c2c_summary_report.csv",
        results_dir / "c2c_summary_report.csv",
    ]:
        if candidate.exists():
            found["c2c_summary"] = candidate
            break

    return found


# ---------------------------------------------------------------------------
#  Plot A: Latency bars — victim mean latency per phase × attacker count
# ---------------------------------------------------------------------------

def plot_A_latency_bars(rows: List[dict], out_dir: Path, fmt: str, dpi: int,
                        source: str):
    """Bar chart of victim mean latency across phases for each attacker count.

    Works with both lock_vs_fai summary.csv and sweep raw_phase_results.csv.
    If the data has 'attacker_threads' we facet by that; otherwise one panel.
    """
    # Detect which phase vocabulary is in use
    phases_present = set(r.get("phase", "") for r in rows)
    if "victim_plus_attacker_rmw" in phases_present:
        phase_order = PHASE_ORDER
        phase_labels = PHASE_LABELS
        phase_colors = PHASE_COLORS
    else:
        phase_order = SEP_PHASE_ORDER
        phase_labels = SEP_PHASE_LABELS
        phase_colors = SEP_PHASE_COLORS

    if "attacker_threads" in rows[0]:
        atk_counts = sorted_unique_int([r["attacker_threads"] for r in rows
                                        if r.get("attacker_threads", "0") != "0"])
        # Include "0" rows (baseline) with every attacker count
        baseline_rows = [r for r in rows
                         if r.get("attacker_threads", "0") == "0"
                         or r.get("phase", "") == "victim_baseline"]
    else:
        atk_counts = ["all"]
        baseline_rows = []

    if not atk_counts:
        atk_counts = ["all"]

    n_panels = len(atk_counts)
    fig, axes = plt.subplots(1, n_panels,
                             figsize=(3.5 * n_panels + 1, 5),
                             squeeze=False)
    axes = axes[0]

    for i, atk in enumerate(atk_counts):
        ax = axes[i]
        if atk == "all":
            subset = rows
        else:
            subset = ([r for r in rows if r["attacker_threads"] == str(atk)]
                      + baseline_rows)

        means = []
        errs = []
        colors = []
        labels = []

        for phase in phase_order:
            phase_rows = [r for r in subset if r.get("phase") == phase]
            vals = extract_values(phase_rows, "mean_avg")
            if len(vals) == 0:
                continue
            means.append(np.mean(vals))
            errs.append(np.std(vals) if len(vals) > 1 else 0)
            colors.append(phase_colors.get(phase, "#999999"))
            labels.append(phase_labels.get(phase, phase))

        if not means:
            ax.set_title(f"{atk} attacker(s)\n(no data)", fontsize=9)
            continue

        x = np.arange(len(means))
        bars = ax.bar(x, means, yerr=errs, color=colors, capsize=4,
                      edgecolor="white", linewidth=0.5, width=0.6)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        title = f"{atk} attacker(s)" if atk != "all" else "All"
        ax.set_title(title, fontsize=10)
        if i == 0:
            ax.set_ylabel("Mean latency (cycles)")

        # Annotate bar values
        for bar, m in zip(bars, means):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                    f"{m:.0f}", ha="center", va="bottom", fontsize=7)

    fig.suptitle("Victim mean latency by phase",
                 fontsize=12, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    out = out_dir / f"A_latency_bars.{fmt}"
    fig.savefig(out, dpi=dpi)
    plt.close(fig)
    print(f"  [A] Saved: {out}")
    return out


# ---------------------------------------------------------------------------
#  Plot B: Delta distributions — box plots of paired deltas across replicates
# ---------------------------------------------------------------------------

def plot_B_delta_distributions(rows: List[dict], out_dir: Path, fmt: str,
                               dpi: int):
    """Box plots of per-run latency delta % vs baseline.

    Requires sweep raw_phase_results.csv with multiple replicates per
    (attacker_threads, phase) combination.
    """
    non_baseline = [r for r in rows if r.get("phase") != "victim_baseline"]
    if not non_baseline:
        print("  [B] Skipped: no non-baseline rows")
        return None

    phases_present = sorted(set(r["phase"] for r in non_baseline))
    atk_counts = sorted_unique_int([r["attacker_threads"]
                                    for r in non_baseline])
    if not atk_counts:
        print("  [B] Skipped: no attacker thread counts")
        return None

    # Detect phase vocabulary
    if "victim_plus_shared" in phases_present:
        treatment_phases = ["victim_plus_shared", "victim_plus_separate"]
        phase_colors = SEP_PHASE_COLORS
        phase_labels = SEP_PHASE_LABELS
    else:
        treatment_phases = ["victim_plus_attacker_rmw",
                            "victim_plus_attacker_control"]
        phase_colors = PHASE_COLORS
        phase_labels = PHASE_LABELS

    n_phases = len(treatment_phases)
    width = 0.35
    fig, ax = plt.subplots(figsize=(max(6, 2.2 * len(atk_counts)), 5))

    legend_patches = []
    for pi, phase in enumerate(treatment_phases):
        data = []
        positions = []
        for ai, atk in enumerate(atk_counts):
            subset = [r for r in non_baseline
                      if r["attacker_threads"] == str(atk)
                      and r["phase"] == phase]
            vals = extract_values(subset, "latency_delta_pct_vs_baseline")
            if len(vals) == 0:
                # Try computing delta from mean_avg vs baseline
                base_rows = [r for r in rows
                             if r.get("phase") == "victim_baseline"
                             and r.get("attacker_threads", "0") in ("0", str(atk))]
                base_vals = extract_values(base_rows, "mean_avg")
                treat_vals = extract_values(subset, "mean_avg")
                if len(base_vals) > 0 and len(treat_vals) > 0:
                    base_mean = np.mean(base_vals)
                    if base_mean > 0:
                        vals = ((treat_vals - base_mean) / base_mean) * 100.0
                    else:
                        continue
                else:
                    continue
            center = ai * 1.0
            offset = (pi - (n_phases - 1) / 2) * width
            positions.append(center + offset)
            data.append(vals)

        if data:
            bp = ax.boxplot(data, positions=positions, widths=width * 0.75,
                            patch_artist=True, notch=False,
                            medianprops=dict(color="black", linewidth=1.5),
                            whiskerprops=dict(linewidth=0.8),
                            capprops=dict(linewidth=0.8))
            c = phase_colors.get(phase, "#999999")
            for patch in bp["boxes"]:
                patch.set_facecolor(c)
                patch.set_alpha(0.7)

            # Overlay individual points
            rng = np.random.RandomState(42 + pi)
            for pos, vals in zip(positions, data):
                jitter = rng.uniform(-0.06, 0.06, size=len(vals))
                ax.scatter(pos + jitter, vals, alpha=0.45, s=12, color=c,
                           edgecolors="none", zorder=3)

            legend_patches.append(
                Patch(facecolor=c, alpha=0.7,
                      label=phase_labels.get(phase, phase)))

    ax.axhline(0, color="gray", linewidth=0.8, linestyle="--", zorder=0)
    ax.set_xticks([i * 1.0 for i in range(len(atk_counts))])
    ax.set_xticklabels(atk_counts)
    ax.set_xlabel("Attacker threads")
    ax.set_ylabel("Latency delta vs baseline (%)")
    ax.set_title("Per-replicate slowdown distribution",
                 fontsize=12, fontweight="bold")
    if legend_patches:
        ax.legend(handles=legend_patches, loc="best", fontsize=9)
    fig.tight_layout()
    out = out_dir / f"B_delta_distributions.{fmt}"
    fig.savefig(out, dpi=dpi)
    plt.close(fig)
    print(f"  [B] Saved: {out}")
    return out


# ---------------------------------------------------------------------------
#  Plot C: Shared-vs-separate comparison bars with diagnosis annotation
# ---------------------------------------------------------------------------

def plot_C_shared_separate_comparison(rows: List[dict], out_dir: Path,
                                      fmt: str, dpi: int):
    """Grouped bars of baseline / shared / separate latency + fairness.

    Works with either sweep raw_phase_results.csv or single-run summary.csv.
    Adds a text annotation showing the interference/fairness diagnosis.
    """
    phase_order = SEP_PHASE_ORDER
    phase_labels = SEP_PHASE_LABELS
    phase_colors = SEP_PHASE_COLORS

    # Check that the right phases exist
    phases_present = set(r.get("phase", "") for r in rows)
    needed = {"victim_baseline", "victim_plus_shared", "victim_plus_separate"}
    if not needed.issubset(phases_present):
        print(f"  [C] Skipped: need phases {needed}, found {phases_present}")
        return None

    # If we have attacker_threads, group by it; otherwise single group
    has_atk = "attacker_threads" in rows[0]
    if has_atk:
        atk_counts = sorted_unique_int([
            r["attacker_threads"] for r in rows
            if r.get("attacker_threads", "0") != "0"
            and r.get("phase") != "victim_baseline"
        ])
    else:
        atk_counts = ["all"]

    if not atk_counts:
        atk_counts = ["all"]

    # Two subplots: left = latency, right = fairness
    fig, (ax_lat, ax_fair) = plt.subplots(1, 2,
                                           figsize=(5 * len(atk_counts) + 2, 5))

    for ax, metric, ylabel in [
        (ax_lat, "mean_avg", "Mean latency (cycles)"),
        (ax_fair, "jain_fairness", "Jain fairness index"),
    ]:
        n_groups = len(atk_counts)
        n_phases = len(phase_order)
        bar_width = 0.25
        group_width = n_phases * bar_width

        for gi, atk in enumerate(atk_counts):
            for pi, phase in enumerate(phase_order):
                if atk == "all":
                    subset = [r for r in rows if r.get("phase") == phase]
                else:
                    if phase == "victim_baseline":
                        subset = [r for r in rows if r.get("phase") == phase]
                    else:
                        subset = [r for r in rows
                                  if r.get("phase") == phase
                                  and r.get("attacker_threads") == str(atk)]
                vals = extract_values(subset, metric)
                if len(vals) == 0:
                    continue
                x = gi * (group_width + 0.3) + pi * bar_width
                mean_val = np.mean(vals)
                err_val = np.std(vals) if len(vals) > 1 else 0
                ax.bar(x, mean_val, width=bar_width * 0.9, yerr=err_val,
                       color=phase_colors[phase], capsize=3,
                       edgecolor="white", linewidth=0.5)

        # X-axis
        tick_positions = [gi * (group_width + 0.3) + group_width / 2 - bar_width / 2
                          for gi in range(n_groups)]
        tick_labels = [str(a) for a in atk_counts]
        ax.set_xticks(tick_positions)
        ax.set_xticklabels(tick_labels)
        ax.set_xlabel("Attacker threads" if has_atk else "")
        ax.set_ylabel(ylabel)

    # Add diagnosis annotation
    diagnosis = _compute_diagnosis(rows, atk_counts)
    if diagnosis:
        fig.text(0.5, 0.01, diagnosis, ha="center", fontsize=8,
                 style="italic", color="#555555",
                 bbox=dict(boxstyle="round,pad=0.3", facecolor="#f0f0f0",
                           edgecolor="#cccccc"))

    # Legend
    legend_patches = [Patch(facecolor=phase_colors[p], label=phase_labels[p])
                      for p in phase_order]
    ax_lat.legend(handles=legend_patches, loc="best", fontsize=8)

    fig.suptitle("Shared vs separate attacker comparison",
                 fontsize=12, fontweight="bold")
    fig.tight_layout(rect=[0, 0.06, 1, 0.93])
    out = out_dir / f"C_shared_separate_comparison.{fmt}"
    fig.savefig(out, dpi=dpi)
    plt.close(fig)
    print(f"  [C] Saved: {out}")
    return out


def _compute_diagnosis(rows: List[dict], atk_counts: list) -> str:
    """Derive interference + fairness diagnosis from the data."""
    # Use the first (or only) attacker count for the diagnosis
    base_vals = extract_values(
        [r for r in rows if r.get("phase") == "victim_baseline"], "mean_avg")
    shared_vals = extract_values(
        [r for r in rows if r.get("phase") == "victim_plus_shared"], "mean_avg")
    sep_vals = extract_values(
        [r for r in rows if r.get("phase") == "victim_plus_separate"], "mean_avg")

    if len(base_vals) == 0 or len(shared_vals) == 0 or len(sep_vals) == 0:
        return ""

    base_m = np.mean(base_vals)
    if base_m == 0:
        return ""
    shared_ratio = np.mean(shared_vals) / base_m
    sep_ratio = np.mean(sep_vals) / base_m

    # Interference pattern
    both_slow = shared_ratio > 1.05 and sep_ratio > 1.05
    shared_only = shared_ratio > 1.05 and sep_ratio <= 1.05
    neither = shared_ratio <= 1.05 and sep_ratio <= 1.05

    if shared_only:
        interf = "coherence hotspot"
    elif both_slow and sep_ratio >= shared_ratio * 0.90:
        interf = "broad interconnect pressure"
    elif both_slow:
        interf = "mixed (hotspot + interconnect)"
    elif neither:
        interf = "no significant interference"
    else:
        interf = "inconclusive"

    # Fairness pattern
    base_fair = extract_values(
        [r for r in rows if r.get("phase") == "victim_baseline"],
        "jain_fairness")
    shared_fair = extract_values(
        [r for r in rows if r.get("phase") == "victim_plus_shared"],
        "jain_fairness")
    sep_fair = extract_values(
        [r for r in rows if r.get("phase") == "victim_plus_separate"],
        "jain_fairness")

    fair_txt = ""
    if len(base_fair) > 0 and len(shared_fair) > 0 and len(sep_fair) > 0:
        sd = np.mean(base_fair) - np.mean(shared_fair)
        pd = np.mean(base_fair) - np.mean(sep_fair)
        if sd > 0.05 and pd <= 0.02:
            fair_txt = " | fairness: shared-contention unfair"
        elif sd > 0.05 and pd > 0.05:
            fair_txt = " | fairness: both unfair"
        elif sd <= 0.02 and pd <= 0.02:
            fair_txt = " | fairness: preserved"
        else:
            fair_txt = " | fairness: marginal"

    return (f"Diagnosis: {interf} "
            f"(shared ratio={shared_ratio:.3f}, "
            f"separate ratio={sep_ratio:.3f}){fair_txt}")


# ---------------------------------------------------------------------------
#  Plot D: Perf c2c HITM chart — stacked bars of local vs remote HITM
# ---------------------------------------------------------------------------

def plot_D_hitm_chart(csv_path: Path, out_dir: Path, fmt: str, dpi: int):
    """Stacked bar chart of local vs remote HITM counts from perf c2c.

    Parses the c2c_summary_report.csv which has a structured format with
    sections (TRACE EVENT METRICS, GLOBAL SHARED CACHE LINE INFO, etc.).
    """
    if not csv_path.exists():
        print(f"  [D] Skipped: {csv_path} not found")
        return None

    with csv_path.open("r", encoding="utf-8") as f:
        all_lines = list(csv.reader(f))

    # Parse the trace event rows to find HITM metrics
    # The CSV has section headers like "TRACE EVENT METRICS" then a header row
    # then data rows. We look for rows where metric contains "HITM".
    phases = []
    local_hitm = {}
    remote_hitm = {}

    in_trace = False
    header = []
    for line in all_lines:
        if not line:
            in_trace = False
            continue
        if line[0] == "TRACE EVENT METRICS":
            in_trace = True
            continue
        if in_trace and not header:
            header = line
            # Find phase columns (after 'section' and 'metric')
            phases = [h for h in header[2:]
                      if not h.startswith("delta") and h.strip()]
            for p in phases:
                local_hitm[p] = 0
                remote_hitm[p] = 0
            continue
        if in_trace and header and len(line) >= 2:
            metric_name = line[1].strip() if len(line) > 1 else ""
            if "Local HITM" in metric_name and "Load" in metric_name:
                for pi, phase in enumerate(phases):
                    val = safe_float(line[2 + pi] if 2 + pi < len(line) else "")
                    if not math.isnan(val):
                        local_hitm[phase] = int(val)
            elif "Remote HITM" in metric_name and "Load" in metric_name:
                for pi, phase in enumerate(phases):
                    val = safe_float(line[2 + pi] if 2 + pi < len(line) else "")
                    if not math.isnan(val):
                        remote_hitm[phase] = int(val)
            # Also catch "Total HITM" variants without Local/Remote prefix
        if line and line[0] == "GLOBAL SHARED CACHE LINE INFO":
            in_trace = False
            header = []

    if not phases:
        print("  [D] Skipped: no phases found in c2c summary CSV")
        return None

    # Preferred order
    preferred = ["baseline", "shared", "separate"]
    ordered_phases = [p for p in preferred if p in phases]
    ordered_phases += [p for p in phases if p not in ordered_phases]

    fig, ax = plt.subplots(figsize=(max(5, 1.8 * len(ordered_phases)), 5))

    x = np.arange(len(ordered_phases))
    local_vals = [local_hitm.get(p, 0) for p in ordered_phases]
    remote_vals = [remote_hitm.get(p, 0) for p in ordered_phases]

    bars_local = ax.bar(x, local_vals, width=0.5, label="Local HITM",
                        color=HITM_COLORS["local"], edgecolor="white",
                        linewidth=0.5)
    bars_remote = ax.bar(x, remote_vals, width=0.5, bottom=local_vals,
                         label="Remote HITM", color=HITM_COLORS["remote"],
                         edgecolor="white", linewidth=0.5)

    # Annotate totals
    for xi, (l, r) in enumerate(zip(local_vals, remote_vals)):
        total = l + r
        if total > 0:
            ax.text(xi, total, f"{total:,}", ha="center", va="bottom",
                    fontsize=8, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels([p.capitalize() for p in ordered_phases])
    ax.set_ylabel("HITM count")
    ax.set_title("Cache-line HITM events by phase (perf c2c)",
                 fontsize=12, fontweight="bold")
    ax.legend(loc="best", fontsize=9)

    # Add ratio annotations vs baseline
    if "baseline" in local_hitm:
        base_total = local_hitm["baseline"] + remote_hitm["baseline"]
        if base_total > 0:
            annotations = []
            for p in ordered_phases:
                if p == "baseline":
                    continue
                p_total = local_hitm.get(p, 0) + remote_hitm.get(p, 0)
                ratio = p_total / base_total
                annotations.append(f"{p}: {ratio:.2f}x baseline")
            if annotations:
                ax.text(0.02, 0.97, "\n".join(annotations),
                        transform=ax.transAxes, fontsize=8,
                        verticalalignment="top",
                        bbox=dict(boxstyle="round,pad=0.3",
                                  facecolor="#f0f0f0", edgecolor="#cccccc"))

    fig.tight_layout()
    out = out_dir / f"D_hitm_chart.{fmt}"
    fig.savefig(out, dpi=dpi)
    plt.close(fig)
    print(f"  [D] Saved: {out}")
    return out


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    results_dir = Path(args.results_dir)
    if not results_dir.is_dir():
        print(f"ERROR: {results_dir} is not a directory.", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir) if args.out_dir else results_dir / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)

    csvs = discover_csvs(results_dir)
    fmt = args.format
    dpi = args.dpi

    print(f"Results dir : {results_dir}")
    print(f"Output dir  : {out_dir}")
    print(f"CSVs found  : {', '.join(csvs.keys()) or '(none)'}")
    print()

    generated = []

    # --- Plot A: Latency bars ---
    # Prefer lock_vs_fai summary, fall back to sweep raw data
    if "lock_vs_fai_summary" in csvs:
        path, rows = csvs["lock_vs_fai_summary"]
        print(f"[A] Latency bars from: {path}")
        out = plot_A_latency_bars(rows, out_dir, fmt, dpi, "lock_vs_fai")
        if out:
            generated.append(out)
    elif "sweep_raw" in csvs:
        path, rows = csvs["sweep_raw"]
        print(f"[A] Latency bars from: {path}")
        out = plot_A_latency_bars(rows, out_dir, fmt, dpi, "sweep")
        if out:
            generated.append(out)
    else:
        print("[A] Skipped: no lock_vs_fai summary or sweep raw data found")

    # --- Plot B: Delta distributions ---
    if "sweep_raw" in csvs:
        path, rows = csvs["sweep_raw"]
        print(f"[B] Delta distributions from: {path}")
        out = plot_B_delta_distributions(rows, out_dir, fmt, dpi)
        if out:
            generated.append(out)
    else:
        print("[B] Skipped: no sweep raw_phase_results.csv found")

    # --- Plot C: Shared-separate comparison ---
    if "sweep_raw" in csvs:
        path, rows = csvs["sweep_raw"]
        print(f"[C] Shared-separate comparison from: {path}")
        out = plot_C_shared_separate_comparison(rows, out_dir, fmt, dpi)
        if out:
            generated.append(out)
    elif "sep_addr_summary" in csvs:
        path, rows = csvs["sep_addr_summary"]
        print(f"[C] Shared-separate comparison from: {path}")
        out = plot_C_shared_separate_comparison(rows, out_dir, fmt, dpi)
        if out:
            generated.append(out)
    else:
        print("[C] Skipped: no sweep or separate-address summary found")

    # --- Plot D: HITM chart ---
    if "c2c_summary" in csvs:
        path = csvs["c2c_summary"]
        print(f"[D] HITM chart from: {path}")
        out = plot_D_hitm_chart(path, out_dir, fmt, dpi)
        if out:
            generated.append(out)
    else:
        print("[D] Skipped: no c2c_summary_report.csv found")

    # Summary
    print()
    if generated:
        print(f"Generated {len(generated)} plot(s) in {out_dir}/:")
        for p in generated:
            print(f"  {p.name}")
    else:
        print("No plots generated. Ensure experiment CSVs exist under "
              f"{results_dir}/")

    return 0 if generated else 1


if __name__ == "__main__":
    sys.exit(main())
