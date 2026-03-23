#!/usr/bin/env python3
"""Visualise dominance streak analysis results.

Reads outputs from analyze_dominance_streaks.py and produces:
  1. Streak-length distribution (histogram/box per group)
  2. Dominance concentration comparison (bar chart of key metrics across groups)
  3. Streak timeline (per-group horizontal bar showing streak blocks)

Usage:
  python3 scripts/plot_dominance_streaks.py results/analysis/stickiness \\
      [--out-dir results/dominance_plots] [--format png] [--dpi 150]
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.ticker as ticker
from matplotlib.patches import Patch
import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "prefix",
        help="Output prefix from analyze_dominance_streaks.py "
             "(e.g. results/analysis/stickiness). "
             "Reads <prefix>_dominance_summary.csv and <prefix>_dominance_streaks.csv.",
    )
    p.add_argument("--out-dir", default=None,
                   help="Directory for plots (default: <prefix>_dominance_plots/)")
    p.add_argument("--format", default="png", choices=["png", "pdf", "svg"])
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--window-size", type=int, default=0,
                   help="Filter to a specific window size (0 = use first found)")
    p.add_argument("--max-groups", type=int, default=30,
                   help="Max groups to show in comparison plots (0 = unlimited)")
    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
#  I/O
# ─────────────────────────────────────────────────────────────────────────────

def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)
    return headers, rows


# ─────────────────────────────────────────────────────────────────────────────
#  Colour helpers
# ─────────────────────────────────────────────────────────────────────────────

_THREAD_CMAP = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf", "#aec7e8", "#ffbb78",
    "#98df8a", "#ff9896", "#c5b0d5", "#c49c94",
]

def thread_color(tid: str, palette: Dict[str, str]) -> str:
    if tid not in palette:
        idx = len(palette) % len(_THREAD_CMAP)
        palette[tid] = _THREAD_CMAP[idx]
    return palette[tid]


# ─────────────────────────────────────────────────────────────────────────────
#  Group label helper
# ─────────────────────────────────────────────────────────────────────────────

def group_label(row: Dict[str, str], group_cols: List[str]) -> str:
    """Short human-readable label from group columns."""
    parts = []
    for c in group_cols:
        v = row.get(c, "")
        if v:
            # Use short key names
            short = c.replace("thread_count", "t").replace("contention_size", "cs") \
                     .replace("operation", "op").replace("seed", "s")
            parts.append(f"{short}={v}")
    return " ".join(parts) if parts else "all"


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 1: Streak length distributions (box + strip)
# ─────────────────────────────────────────────────────────────────────────────

def plot_streak_distributions(
    streak_rows: List[Dict[str, str]],
    group_cols: List[str],
    ws_filter: str,
    out_path: Path,
    dpi: int,
    max_groups: int,
) -> None:
    # Group streak lengths
    groups: Dict[str, List[int]] = defaultdict(list)
    for r in streak_rows:
        if r.get("window_size", "") != ws_filter:
            continue
        label = group_label(r, group_cols)
        length = int(r.get("streak_length", "1"))
        groups[label].append(length)

    if not groups:
        print("  SKIP streak distribution plot: no data")
        return

    # Sort by median streak length descending
    sorted_labels = sorted(groups.keys(),
                          key=lambda k: np.median(groups[k]),
                          reverse=True)
    if max_groups > 0:
        sorted_labels = sorted_labels[:max_groups]

    data = [groups[l] for l in sorted_labels]

    fig, ax = plt.subplots(figsize=(max(10, len(sorted_labels) * 0.6), 6))
    bp = ax.boxplot(data, vert=True, patch_artist=True, showfliers=True,
                    flierprops=dict(marker=".", markersize=3, alpha=0.4))
    for patch in bp["boxes"]:
        patch.set_facecolor("#4c72b0")
        patch.set_alpha(0.6)

    ax.set_xticklabels(sorted_labels, rotation=60, ha="right", fontsize=7)
    ax.set_ylabel("Streak Length (windows)")
    ax.set_title(f"Dominance Streak Length Distribution  [window_size={ws_filter}]")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 2: Dominance concentration comparison
# ─────────────────────────────────────────────────────────────────────────────

def plot_dominance_comparison(
    summary_rows: List[Dict[str, str]],
    group_cols: List[str],
    ws_filter: str,
    out_path: Path,
    dpi: int,
    max_groups: int,
) -> None:
    filtered = [r for r in summary_rows if r.get("window_size", "") == ws_filter]
    if not filtered:
        print("  SKIP dominance comparison plot: no data")
        return

    # Sort by max_streak_frac descending
    filtered.sort(key=lambda r: -float(r.get("max_streak_frac", "0")))
    if max_groups > 0:
        filtered = filtered[:max_groups]

    labels = [group_label(r, group_cols) for r in filtered]
    metrics = {
        "max_streak_frac": ("Max Streak Fraction", "#d62728"),
        "long_streak_coverage": ("Long Streak Coverage", "#ff7f0e"),
        "top1_dominance_frac": ("Top-1 Thread Fraction", "#2ca02c"),
        "gini_coefficient": ("Gini Coefficient", "#9467bd"),
    }

    fig, axes = plt.subplots(2, 2, figsize=(max(12, len(labels) * 0.5), 10))
    axes = axes.flatten()

    for ax, (col, (title, color)) in zip(axes, metrics.items()):
        vals = [float(r.get(col, "0")) for r in filtered]
        bars = ax.barh(range(len(labels)), vals, color=color, alpha=0.7)
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels, fontsize=7)
        ax.set_xlabel(title)
        ax.set_title(title)
        ax.invert_yaxis()
        ax.grid(axis="x", alpha=0.3)
        # Value labels
        for bar, v in zip(bars, vals):
            ax.text(bar.get_width() + 0.01, bar.get_y() + bar.get_height() / 2,
                    f"{v:.2f}", va="center", fontsize=6)

    fig.suptitle(f"Dominance Concentration  [window_size={ws_filter}]", fontsize=13)
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 3: Streak timeline (horizontal stacked bar per group)
# ─────────────────────────────────────────────────────────────────────────────

def plot_streak_timelines(
    streak_rows: List[Dict[str, str]],
    summary_rows: List[Dict[str, str]],
    group_cols: List[str],
    ws_filter: str,
    out_dir: Path,
    fmt: str,
    dpi: int,
    max_groups: int,
) -> None:
    # Group streaks
    groups: Dict[str, List[Tuple[str, int, int]]] = defaultdict(list)
    group_nwindows: Dict[str, int] = {}

    for r in summary_rows:
        if r.get("window_size", "") != ws_filter:
            continue
        label = group_label(r, group_cols)
        group_nwindows[label] = int(r.get("n_windows", "0"))

    for r in streak_rows:
        if r.get("window_size", "") != ws_filter:
            continue
        label = group_label(r, group_cols)
        tid = r.get("streak_thread", "?")
        start = int(r.get("streak_start_window", "0"))
        length = int(r.get("streak_length", "1"))
        groups[label].append((tid, start, length))

    if not groups:
        print("  SKIP streak timeline: no data")
        return

    # Sort by max streak frac (from summary)
    sorted_labels = sorted(
        groups.keys(),
        key=lambda k: max((l for _, _, l in groups[k]), default=0) / max(group_nwindows.get(k, 1), 1),
        reverse=True,
    )
    if max_groups > 0:
        sorted_labels = sorted_labels[:max_groups]

    n_groups = len(sorted_labels)
    fig_h = max(4, n_groups * 0.5 + 1)
    fig, ax = plt.subplots(figsize=(14, fig_h))

    palette: Dict[str, str] = {}

    for y_idx, label in enumerate(reversed(sorted_labels)):
        streaks = groups[label]
        for tid, start, length in streaks:
            color = thread_color(tid, palette)
            ax.barh(y_idx, length, left=start, height=0.7,
                    color=color, edgecolor="none", alpha=0.85)

    ax.set_yticks(range(len(sorted_labels)))
    ax.set_yticklabels(list(reversed(sorted_labels)), fontsize=7)
    ax.set_xlabel("Window Index")
    ax.set_title(f"Dominance Streak Timeline  [window_size={ws_filter}]")
    ax.grid(axis="x", alpha=0.3)

    # Legend
    handles = [Patch(facecolor=c, label=t) for t, c in sorted(palette.items())]
    if len(handles) <= 16:
        ax.legend(handles=handles, loc="upper right", fontsize=7, ncol=2)

    fig.tight_layout()
    out_path = out_dir / f"streak_timeline.{fmt}"
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 4: Effective threads vs Gini scatter
# ─────────────────────────────────────────────────────────────────────────────

def plot_concentration_scatter(
    summary_rows: List[Dict[str, str]],
    group_cols: List[str],
    ws_filter: str,
    out_path: Path,
    dpi: int,
) -> None:
    filtered = [r for r in summary_rows if r.get("window_size", "") == ws_filter]
    if not filtered:
        print("  SKIP concentration scatter: no data")
        return

    eff = [float(r.get("effective_dominant_threads", "1")) for r in filtered]
    gini = [float(r.get("gini_coefficient", "0")) for r in filtered]
    max_frac = [float(r.get("max_streak_frac", "0")) for r in filtered]
    labels = [group_label(r, group_cols) for r in filtered]

    fig, ax = plt.subplots(figsize=(8, 6))
    sc = ax.scatter(eff, gini, c=max_frac, cmap="YlOrRd", s=60, alpha=0.8,
                    edgecolors="grey", linewidths=0.5)
    cb = fig.colorbar(sc, ax=ax)
    cb.set_label("Max Streak Fraction")

    ax.set_xlabel("Effective Dominant Threads (1/HHI)")
    ax.set_ylabel("Gini Coefficient")
    ax.set_title(f"Dominance Concentration  [window_size={ws_filter}]")
    ax.grid(alpha=0.3)

    # Annotate extreme points
    for i, (x, y, l) in enumerate(zip(eff, gini, labels)):
        if max_frac[i] >= 0.3 or y >= 0.5:
            ax.annotate(l, (x, y), fontsize=5, alpha=0.7,
                       xytext=(3, 3), textcoords="offset points")

    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    prefix = Path(args.prefix)

    summary_csv = prefix.with_name(prefix.name + "_dominance_summary.csv")
    streaks_csv = prefix.with_name(prefix.name + "_dominance_streaks.csv")

    for p in [summary_csv, streaks_csv]:
        if not p.exists():
            print(f"ERROR: cannot find {p}", file=sys.stderr)
            sys.exit(1)

    _, summary_rows = read_csv(summary_csv)
    _, streak_rows = read_csv(streaks_csv)
    s_headers, _ = read_csv(summary_csv)

    # Determine group columns
    skip_cols = {
        "window_size", "n_windows", "n_streaks",
        "max_streak_length", "max_streak_frac",
        "mean_streak_length", "median_streak_length",
        "p90_streak_length", "p99_streak_length",
        "long_streak_threshold", "n_long_streaks", "long_streak_coverage",
        "top1_dominant_thread", "top1_dominance_frac", "top2_dominance_frac",
        "hhi", "effective_dominant_threads",
        "gini_coefficient", "entropy_bits", "normalized_entropy",
        "mean_window_dominant_share",
        "streak_thread", "streak_start_window", "streak_length",
    }
    group_cols = [h for h in s_headers if h not in skip_cols]

    # Pick window size
    all_ws = sorted(set(r.get("window_size", "") for r in summary_rows))
    if args.window_size > 0:
        ws_filter = str(args.window_size)
    elif all_ws:
        ws_filter = all_ws[0]
    else:
        print("ERROR: no window sizes found")
        sys.exit(1)
    print(f"Using window_size={ws_filter}  (available: {all_ws})")

    out_dir = Path(args.out_dir) if args.out_dir else prefix.with_name(prefix.name + "_dominance_plots")
    out_dir.mkdir(parents=True, exist_ok=True)

    fmt = args.format
    dpi = args.dpi

    plot_streak_distributions(
        streak_rows, group_cols, ws_filter,
        out_dir / f"streak_length_distribution.{fmt}", dpi, args.max_groups,
    )
    plot_dominance_comparison(
        summary_rows, group_cols, ws_filter,
        out_dir / f"dominance_comparison.{fmt}", dpi, args.max_groups,
    )
    plot_streak_timelines(
        streak_rows, summary_rows, group_cols, ws_filter,
        out_dir, fmt, dpi, args.max_groups,
    )
    plot_concentration_scatter(
        summary_rows, group_cols, ws_filter,
        out_dir / f"concentration_scatter.{fmt}", dpi,
    )

    print(f"\nAll plots saved to {out_dir}/")


if __name__ == "__main__":
    main()
