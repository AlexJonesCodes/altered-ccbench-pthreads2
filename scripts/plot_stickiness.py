#!/usr/bin/env python3
"""Visualise stickiness study results from analyze_stickiness.py output CSVs.

Produces a set of summary plots:
  1. Stickiness heatmap: repeat_excess_pct by op x thread_count
  2. Wald-Wolfowitz z-score heatmap (same axes)
  3. Window z-score vs scale (multi-panel by op)
  4. Per-thread conditional repeat rate breakdown
  5. Seed-core advantage bar chart
  6. Lag autocorrelation profiles

Usage:
  python3 scripts/plot_stickiness.py results/analysis/stickiness \\
      [--out-dir results/plots]

The positional argument is the --out-prefix used with analyze_stickiness.py.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("prefix", help="Output prefix from analyze_stickiness.py "
                   "(e.g. results/analysis/stickiness)")
    p.add_argument("--out-dir", default=None,
                   help="Directory for plots (default: <prefix>_plots/)")
    p.add_argument("--format", default="png", choices=["png", "pdf", "svg"],
                   help="Output image format")
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--split-by", default=None,
                   choices=["core_set_id", "op", "thread_count"],
                   help="Generate separate plot sets for each unique value "
                        "of this column (e.g. --split-by core_set_id)")
    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
#  I/O
# ─────────────────────────────────────────────────────────────────────────────

def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        print(f"WARNING: {path} not found, skipping.")
        return []
    with path.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def safe_float(v: str, default: float = float("nan")) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────────────────────────────────────
#  Colour helpers
# ─────────────────────────────────────────────────────────────────────────────

def diverging_cmap():
    """Red (sticky) / white (neutral) / blue (anti-sticky)."""
    return plt.cm.RdBu_r


OP_COLOURS = {
    "CAS_UNTIL_SUCCESS": "#2196F3",
    "FAI": "#4CAF50",
    "TAS": "#FF9800",
}

def op_colour(op: str) -> str:
    return OP_COLOURS.get(op, "#9E9E9E")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 1 & 2: Heatmaps (repeat_excess_pct and ww_runs_zscore)
# ─────────────────────────────────────────────────────────────────────────────

def plot_heatmaps(groups: List[Dict[str, str]], out_dir: Path, fmt: str, dpi: int):
    """Two heatmaps: repeat_excess_pct and ww_runs_zscore, by op x thread_count.

    Averages over seed_core and core_set_id to give one cell per (op, thread_count).
    """
    # Aggregate: (op, thread_count) -> list of values
    excess_agg: Dict[Tuple[str, int], List[float]] = defaultdict(list)
    ww_agg: Dict[Tuple[str, int], List[float]] = defaultdict(list)

    for r in groups:
        op = r.get("op", "")
        tc = int(safe_float(r.get("thread_count", "0")))
        if tc == 0 or not op:
            continue
        excess_agg[(op, tc)].append(safe_float(r.get("repeat_excess_pct", "nan")))
        ww_agg[(op, tc)].append(safe_float(r.get("ww_runs_zscore", "nan")))

    ops = sorted(set(k[0] for k in excess_agg))
    tcs = sorted(set(k[1] for k in excess_agg))

    if not ops or not tcs:
        return

    for metric_name, agg, label, cmap_lim in [
        ("repeat_excess_pct", excess_agg, "Repeat Excess (%)", 8),
        ("ww_runs_zscore", ww_agg, "Wald-Wolfowitz Z-Score", 80),
    ]:
        fig, ax = plt.subplots(figsize=(max(4, len(tcs) * 1.2), max(3, len(ops) * 1.0)))

        data = np.full((len(ops), len(tcs)), float("nan"))
        for i, op in enumerate(ops):
            for j, tc in enumerate(tcs):
                vals = [v for v in agg.get((op, tc), []) if not math.isnan(v)]
                if vals:
                    data[i, j] = sum(vals) / len(vals)

        vmax = cmap_lim
        vmin = -cmap_lim
        im = ax.imshow(data, cmap=diverging_cmap(), aspect="auto", vmin=vmin, vmax=vmax)

        ax.set_xticks(range(len(tcs)))
        ax.set_xticklabels([str(t) for t in tcs])
        ax.set_yticks(range(len(ops)))
        ax.set_yticklabels(ops)
        ax.set_xlabel("Thread Count")
        ax.set_ylabel("Operation")
        ax.set_title(label)

        # Annotate cells
        for i in range(len(ops)):
            for j in range(len(tcs)):
                val = data[i, j]
                if not math.isnan(val):
                    colour = "white" if abs(val) > cmap_lim * 0.6 else "black"
                    ax.text(j, i, f"{val:.1f}", ha="center", va="center",
                            fontsize=10, fontweight="bold", color=colour)

        fig.colorbar(im, ax=ax, shrink=0.8, label=label)
        fig.tight_layout()
        out = out_dir / f"heatmap_{metric_name}.{fmt}"
        fig.savefig(out, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 3: Window z-score vs scale (multi-panel by op)
# ─────────────────────────────────────────────────────────────────────────────

def plot_window_zscores_by_scale(groups: List[Dict[str, str]], out_dir: Path,
                                  fmt: str, dpi: int):
    """Line plot showing how window z-score mean scales with window size, per op and thread_count."""
    # Find window size columns
    sample = groups[0] if groups else {}
    ws_cols = sorted(set(
        int(k.split("_")[0][1:])
        for k in sample
        if k.startswith("w") and k.endswith("_z_mean") and k[1:].split("_")[0].isdigit()
    ))
    if not ws_cols:
        return

    # Aggregate by (op, tc) -> {ws: [z_means]}
    data: Dict[Tuple[str, int], Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
    for r in groups:
        op = r.get("op", "")
        tc = int(safe_float(r.get("thread_count", "0")))
        if tc == 0:
            continue
        for ws in ws_cols:
            z = safe_float(r.get(f"w{ws}_z_mean", "nan"))
            if not math.isnan(z):
                data[(op, tc)][ws].append(z)

    ops = sorted(set(k[0] for k in data))
    if not ops:
        return

    fig, axes = plt.subplots(1, len(ops), figsize=(5 * len(ops), 4), sharey=True)
    if len(ops) == 1:
        axes = [axes]

    for ax, op in zip(axes, ops):
        tcs_for_op = sorted(set(k[1] for k in data if k[0] == op))
        for tc in tcs_for_op:
            ws_list = sorted(data[(op, tc)].keys())
            means = [sum(data[(op, tc)][ws]) / len(data[(op, tc)][ws]) for ws in ws_list]
            ax.plot(ws_list, means, "o-", label=f"{tc} threads", linewidth=2, markersize=6)

        ax.axhline(0, color="grey", linestyle="--", linewidth=0.8, alpha=0.5)
        ax.axhline(2, color="red", linestyle=":", linewidth=0.8, alpha=0.5, label="z=2 (sticky)")
        ax.axhline(-2, color="blue", linestyle=":", linewidth=0.8, alpha=0.5, label="z=-2 (anti-sticky)")
        ax.set_xscale("log")
        ax.set_xlabel("Window Size")
        ax.set_title(op)
        ax.legend(fontsize=7, loc="best")
        ax.grid(True, alpha=0.3)

    axes[0].set_ylabel("Mean Window Z-Score")
    fig.suptitle("Stickiness Z-Score vs Window Scale", fontweight="bold", y=1.02)
    fig.tight_layout()
    out = out_dir / f"window_zscore_by_scale.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 4: Stickiness profile bar chart (repeat_excess_pct by tc, grouped by op)
# ─────────────────────────────────────────────────────────────────────────────

def plot_stickiness_profile(groups: List[Dict[str, str]], out_dir: Path,
                             fmt: str, dpi: int):
    """Bar chart of repeat_excess_pct by thread_count, grouped by op."""
    # Aggregate
    agg: Dict[Tuple[str, int], List[float]] = defaultdict(list)
    for r in groups:
        op = r.get("op", "")
        tc = int(safe_float(r.get("thread_count", "0")))
        val = safe_float(r.get("repeat_excess_pct", "nan"))
        if tc > 0 and not math.isnan(val):
            agg[(op, tc)].append(val)

    ops = sorted(set(k[0] for k in agg))
    tcs = sorted(set(k[1] for k in agg))
    if not ops or not tcs:
        return

    fig, ax = plt.subplots(figsize=(max(6, len(tcs) * 1.5), 5))

    x = np.arange(len(tcs))
    width = 0.8 / len(ops)

    for i, op in enumerate(ops):
        means = []
        stds = []
        for tc in tcs:
            vals = [v for v in agg.get((op, tc), []) if not math.isnan(v)]
            if vals:
                means.append(sum(vals) / len(vals))
                if len(vals) > 1:
                    m = means[-1]
                    stds.append((sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5)
                else:
                    stds.append(0)
            else:
                means.append(0)
                stds.append(0)

        offset = (i - len(ops) / 2 + 0.5) * width
        bars = ax.bar(x + offset, means, width, yerr=stds, capsize=3,
                      label=op, color=op_colour(op), alpha=0.85, edgecolor="white")

    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([str(t) for t in tcs])
    ax.set_xlabel("Thread Count")
    ax.set_ylabel("Repeat Excess (%)")
    ax.set_title("Thread Stickiness by Operation and Thread Count")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    # Annotate regions
    ax.axhspan(0, ax.get_ylim()[1], alpha=0.04, color="red")
    ax.axhspan(ax.get_ylim()[0], 0, alpha=0.04, color="blue")
    ax.text(0.98, 0.97, "STICKY", transform=ax.transAxes, ha="right", va="top",
            fontsize=9, color="red", alpha=0.5, fontstyle="italic")
    ax.text(0.98, 0.03, "ANTI-STICKY", transform=ax.transAxes, ha="right", va="bottom",
            fontsize=9, color="blue", alpha=0.5, fontstyle="italic")

    fig.tight_layout()
    out = out_dir / f"stickiness_profile.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 5: Per-thread conditional repeat rate (seed core advantage)
# ─────────────────────────────────────────────────────────────────────────────

def plot_seed_core_advantage(threads: List[Dict[str, str]], groups: List[Dict[str, str]],
                              out_dir: Path, fmt: str, dpi: int):
    """Show conditional repeat rate per thread, highlighting the seed core thread."""
    # For each (op, tc, seed_core), compare seed thread's cond rate vs others
    # We need to match thread_id == seed_core
    seed_adv: Dict[Tuple[str, int], List[float]] = defaultdict(list)
    other_rates: Dict[Tuple[str, int], List[float]] = defaultdict(list)

    for r in threads:
        op = r.get("op", "")
        tc = int(safe_float(r.get("thread_count", "0")))
        seed = r.get("seed_core", "")
        tid = r.get("thread_id", "")
        cond = safe_float(r.get("repeat_rate_given_prev", "nan"))
        if tc <= 0 or math.isnan(cond):
            continue

        if tid == seed:
            seed_adv[(op, tc)].append(cond)
        else:
            other_rates[(op, tc)].append(cond)

    ops = sorted(set(k[0] for k in seed_adv))
    tcs = sorted(set(k[1] for k in seed_adv))
    if not ops or not tcs:
        return

    fig, ax = plt.subplots(figsize=(max(6, len(tcs) * 1.5), 5))

    x = np.arange(len(tcs))
    width = 0.35

    for i, op in enumerate(ops):
        seed_means = []
        other_means = []
        for tc in tcs:
            sv = seed_adv.get((op, tc), [])
            ov = other_rates.get((op, tc), [])
            seed_means.append(sum(sv) / len(sv) if sv else 0)
            other_means.append(sum(ov) / len(ov) if ov else 0)

        x_off = x + i * (width * 2 + 0.1)
        ax.bar(x_off, seed_means, width, label=f"{op} seed core",
               color=op_colour(op), alpha=0.9, edgecolor="white")
        ax.bar(x_off + width, other_means, width, label=f"{op} others",
               color=op_colour(op), alpha=0.4, edgecolor="white")

    ax.set_xticks(x + (len(ops) - 1) * (width + 0.05))
    ax.set_xticklabels([str(t) for t in tcs])
    ax.set_xlabel("Thread Count")
    ax.set_ylabel("P(win | won previous)")
    ax.set_title("Seed Core Advantage: Conditional Repeat Rate")
    ax.legend(fontsize=7, ncol=2)
    ax.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    out = out_dir / f"seed_core_advantage.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 6: Lag autocorrelation profiles
# ─────────────────────────────────────────────────────────────────────────────

def plot_lag_profiles(groups: List[Dict[str, str]], out_dir: Path, fmt: str, dpi: int):
    """Line plot of lag-k same rate normalised by expected, by op and thread_count."""
    # Find lag columns
    sample = groups[0] if groups else {}
    lags = sorted(
        int(k.replace("lag", "").replace("_same_rate", ""))
        for k in sample
        if k.startswith("lag") and k.endswith("_same_rate")
    )
    if not lags:
        return

    # Aggregate by (op, tc) -> {lag: [normalised_values]}
    data: Dict[Tuple[str, int], Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
    for r in groups:
        op = r.get("op", "")
        tc = int(safe_float(r.get("thread_count", "0")))
        expected = safe_float(r.get("expected_repeat_rate", "nan"))
        if tc == 0 or math.isnan(expected) or expected == 0:
            continue
        for lag in lags:
            v = safe_float(r.get(f"lag{lag}_same_rate", "nan"))
            if not math.isnan(v):
                # Normalise: ratio to expected
                data[(op, tc)][lag].append(v / expected)

    ops = sorted(set(k[0] for k in data))
    if not ops:
        return

    fig, axes = plt.subplots(1, len(ops), figsize=(5 * len(ops), 4), sharey=True)
    if len(ops) == 1:
        axes = [axes]

    for ax, op in zip(axes, ops):
        tcs_for_op = sorted(set(k[1] for k in data if k[0] == op))
        for tc in tcs_for_op:
            lag_list = sorted(data[(op, tc)].keys())
            means = [sum(data[(op, tc)][lag]) / len(data[(op, tc)][lag]) for lag in lag_list]
            ax.plot(lag_list, means, "o-", label=f"{tc} threads", linewidth=2, markersize=5)

        ax.axhline(1.0, color="grey", linestyle="--", linewidth=0.8, alpha=0.5,
                   label="Expected (random)")
        ax.set_xlabel("Lag")
        ax.set_title(op)
        ax.legend(fontsize=7, loc="best")
        ax.grid(True, alpha=0.3)

    axes[0].set_ylabel("Lag-k Same Rate / Expected")
    fig.suptitle("Autocorrelation Decay Profile", fontweight="bold", y=1.02)
    fig.tight_layout()
    out = out_dir / f"lag_autocorrelation.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 7: Fraction of sticky windows by scale
# ─────────────────────────────────────────────────────────────────────────────

def plot_frac_sticky_by_scale(groups: List[Dict[str, str]], out_dir: Path,
                               fmt: str, dpi: int):
    """Bar chart of frac_sticky at each window scale, by op and thread_count."""
    sample = groups[0] if groups else {}
    ws_cols = sorted(set(
        int(k.split("_")[0][1:])
        for k in sample
        if k.startswith("w") and k.endswith("_frac_sticky") and k[1:].split("_")[0].isdigit()
    ))
    if not ws_cols:
        return

    # Aggregate
    data: Dict[Tuple[str, int], Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
    for r in groups:
        op = r.get("op", "")
        tc = int(safe_float(r.get("thread_count", "0")))
        if tc == 0:
            continue
        for ws in ws_cols:
            v = safe_float(r.get(f"w{ws}_frac_sticky", "nan"))
            if not math.isnan(v):
                data[(op, tc)][ws].append(v)

    ops = sorted(set(k[0] for k in data))
    tcs = sorted(set(k[1] for k in data))
    if not ops or not tcs:
        return

    fig, axes = plt.subplots(1, len(ops), figsize=(5 * len(ops), 4), sharey=True)
    if len(ops) == 1:
        axes = [axes]

    for ax, op in zip(axes, ops):
        tcs_for_op = sorted(set(k[1] for k in data if k[0] == op))
        x = np.arange(len(ws_cols))
        width = 0.8 / max(len(tcs_for_op), 1)

        for i, tc in enumerate(tcs_for_op):
            means = []
            for ws in ws_cols:
                vals = data.get((op, tc), {}).get(ws, [])
                means.append(sum(vals) / len(vals) if vals else 0)
            offset = (i - len(tcs_for_op) / 2 + 0.5) * width
            ax.bar(x + offset, means, width, label=f"{tc}T", alpha=0.85)

        ax.set_xticks(x)
        ax.set_xticklabels([str(ws) for ws in ws_cols], fontsize=8)
        ax.set_xlabel("Window Size")
        ax.set_title(op)
        ax.legend(fontsize=6, ncol=2, loc="best")
        ax.grid(axis="y", alpha=0.3)

    axes[0].set_ylabel("Fraction Sticky Windows (z > 2)")
    fig.suptitle("Sticky Window Fraction by Scale", fontweight="bold", y=1.02)
    fig.tight_layout()
    out = out_dir / f"frac_sticky_by_scale.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 8: Combined summary dashboard
# ─────────────────────────────────────────────────────────────────────────────

def plot_summary_dashboard(groups: List[Dict[str, str]], out_dir: Path,
                            fmt: str, dpi: int):
    """2x2 dashboard with key metrics."""
    # Aggregate by (op, tc)
    agg: Dict[Tuple[str, int], Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    for r in groups:
        op = r.get("op", "")
        tc = int(safe_float(r.get("thread_count", "0")))
        if tc == 0:
            continue
        for col in ["repeat_excess_pct", "ww_runs_zscore", "jains_fairness", "markov_stickiness"]:
            v = safe_float(r.get(col, "nan"))
            if not math.isnan(v):
                agg[(op, tc)][col].append(v)

    ops = sorted(set(k[0] for k in agg))
    tcs = sorted(set(k[1] for k in agg))
    if not ops or not tcs:
        return

    fig, axes = plt.subplots(2, 2, figsize=(12, 9))
    metrics = [
        ("repeat_excess_pct", "Repeat Excess (%)", axes[0, 0]),
        ("ww_runs_zscore", "Wald-Wolfowitz Z-Score", axes[0, 1]),
        ("jains_fairness", "Jain's Fairness Index", axes[1, 0]),
        ("markov_stickiness", "Markov Stickiness", axes[1, 1]),
    ]

    for col, title, ax in metrics:
        for op in ops:
            xs = []
            ys = []
            for tc in tcs:
                vals = agg.get((op, tc), {}).get(col, [])
                if vals:
                    xs.append(tc)
                    ys.append(sum(vals) / len(vals))
            ax.plot(xs, ys, "o-", label=op, color=op_colour(op), linewidth=2, markersize=7)

        ax.axhline(0, color="grey", linestyle="--", linewidth=0.8, alpha=0.5)
        ax.set_xlabel("Thread Count")
        ax.set_ylabel(title)
        ax.set_title(title)
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)

    fig.suptitle("Stickiness Study Summary", fontsize=14, fontweight="bold", y=1.01)
    fig.tight_layout()
    out = out_dir / f"summary_dashboard.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────

def resolve_prefix(raw: str) -> Path:
    """Resolve the analysis prefix, accepting a directory, file prefix, or CSV path."""
    p = Path(raw)
    if p.is_dir():
        matches = list(p.glob("*_group_summary.csv"))
        if matches:
            name = matches[0].name
            prefix_name = name.replace("_group_summary.csv", "")
            return p / prefix_name
        return p / "stickiness"
    if p.suffix == ".csv":
        name = p.name
        for suffix in ("_group_summary", "_thread_summary", "_window_detail",
                        "_regime_summary"):
            if name.endswith(suffix + ".csv"):
                return p.parent / name.replace(suffix + ".csv", "")
        return p.with_suffix("")
    return p


def _generate_all_plots(groups, threads, out_dir, fmt, dpi):
    """Run all 8 plot functions into out_dir."""
    plot_heatmaps(groups, out_dir, fmt, dpi)
    plot_stickiness_profile(groups, out_dir, fmt, dpi)
    plot_window_zscores_by_scale(groups, out_dir, fmt, dpi)
    plot_frac_sticky_by_scale(groups, out_dir, fmt, dpi)
    plot_lag_profiles(groups, out_dir, fmt, dpi)
    plot_summary_dashboard(groups, out_dir, fmt, dpi)
    if threads:
        plot_seed_core_advantage(threads, groups, out_dir, fmt, dpi)


def main() -> None:
    args = parse_args()
    prefix = resolve_prefix(args.prefix)

    group_path = prefix.parent / (prefix.name + "_group_summary.csv")
    thread_path = prefix.parent / (prefix.name + "_thread_summary.csv")

    groups = read_csv(group_path)
    threads = read_csv(thread_path)

    if not groups:
        print(f"ERROR: No data in {group_path}")
        sys.exit(1)

    out_dir = Path(args.out_dir) if args.out_dir else prefix.with_name(prefix.name + "_plots")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating plots from {group_path} -> {out_dir}/")

    if args.split_by:
        col = args.split_by
        split_values = sorted(set(r.get(col, "") for r in groups if r.get(col, "")))
        print(f"Splitting by {col}: {split_values}\n")

        for sv in split_values:
            sub_groups = [r for r in groups if r.get(col, "") == sv]
            sub_threads = [r for r in threads if r.get(col, "") == sv]

            sub_dir = out_dir / f"{col}_{sv}"
            sub_dir.mkdir(parents=True, exist_ok=True)

            print(f"--- {col}={sv} ({len(sub_groups)} groups) -> {sub_dir}/")
            _generate_all_plots(sub_groups, sub_threads, sub_dir, args.format, args.dpi)

        total = sum(len(list((out_dir / f"{col}_{sv}").glob(f"*.{args.format}")))
                    for sv in split_values)
        print(f"\nDone. {total} plots across {len(split_values)} "
              f"{col} splits written to {out_dir}/")
    else:
        _generate_all_plots(groups, threads, out_dir, args.format, args.dpi)
        print(f"\nDone. {len(list(out_dir.glob(f'*.{args.format}')))} plots written to {out_dir}/")


if __name__ == "__main__":
    main()
