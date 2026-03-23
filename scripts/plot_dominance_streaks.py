#!/usr/bin/env python3
"""Visualise dominance streak analysis results.

Reads outputs from analyze_dominance_streaks.py and produces:
  1. Streak-length distribution (log-scale violin + box per group)
  2. Dominance heatmap (single compact view replacing 4 redundant bar panels)
  3. Streak timeline (per-group horizontal bar showing streak blocks)
  4. Concentration scatter (effective threads vs Gini, clean annotations)
  5. Aggregated view by operation type and thread count

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
from typing import Dict, List, Optional, Tuple

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
#  Colour helpers — deterministic thread-id mapping
# ─────────────────────────────────────────────────────────────────────────────

_THREAD_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf", "#aec7e8", "#ffbb78",
    "#98df8a", "#ff9896", "#c5b0d5", "#c49c94",
]

# Operation-type colours for aggregate plots
_OP_COLORS = {
    "TAS": "#1f77b4",
    "CAS_UNTIL_SUCCESS": "#d62728",
    "FAI": "#2ca02c",
}


def thread_color_deterministic(tid: str) -> str:
    """Deterministic colour: thread '0' always maps to the same colour."""
    try:
        idx = int(tid) % len(_THREAD_COLORS)
    except (ValueError, TypeError):
        idx = hash(tid) % len(_THREAD_COLORS)
    return _THREAD_COLORS[idx]


def op_color(op: str) -> str:
    for key, c in _OP_COLORS.items():
        if key in op.upper():
            return c
    return "#7f7f7f"


# ─────────────────────────────────────────────────────────────────────────────
#  Short label helper
# ─────────────────────────────────────────────────────────────────────────────

# Map of verbose column names to short abbreviations
_COL_ABBREV = {
    "run_id": "r",
    "operation": "op",
    "op_id": None,          # drop — redundant with operation
    "contention_size": "cs",
    "core_set_id": None,    # drop — rarely informative in labels
    "thread_count": "t",
    "seed": "s",
    "s_core": None,         # drop — too verbose
}

# Map long operation names to short names
_OP_SHORT = {
    "CAS_UNTIL_SUCCESS": "CAS",
    "TAS": "TAS",
    "FAI": "FAI",
}


def short_label(row: Dict[str, str], group_cols: List[str]) -> str:
    """Compact human-readable label: 'CAS t=8 s=3' instead of
    'run_id=96 op=CAS_UNTIL_SUCCESS op_id=34 core_set_id=3 t=8 s_core=5'."""
    parts = []
    for c in group_cols:
        abbrev = _COL_ABBREV.get(c, c)
        if abbrev is None:
            continue  # skip this column entirely
        v = row.get(c, "")
        if not v:
            continue
        # Shorten operation names
        v = _OP_SHORT.get(v, v)
        if abbrev == "r":
            parts.append(f"#{v}")
        else:
            parts.append(f"{abbrev}={v}")
    return " ".join(parts) if parts else "all"


def extract_op(row: Dict[str, str]) -> str:
    """Extract operation type from row."""
    op = row.get("operation", "")
    return _OP_SHORT.get(op, op)


def extract_threads(row: Dict[str, str]) -> str:
    return row.get("thread_count", "?")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 1: Streak length distributions — log-scale violin
# ─────────────────────────────────────────────────────────────────────────────

def plot_streak_distributions(
    streak_rows: List[Dict[str, str]],
    group_cols: List[str],
    ws_filter: str,
    out_path: Path,
    dpi: int,
    max_groups: int,
) -> None:
    groups: Dict[str, List[int]] = defaultdict(list)
    group_op: Dict[str, str] = {}
    for r in streak_rows:
        if r.get("window_size", "") != ws_filter:
            continue
        label = short_label(r, group_cols)
        length = int(r.get("streak_length", "1"))
        groups[label].append(length)
        if label not in group_op:
            group_op[label] = extract_op(r)

    if not groups:
        print("  SKIP streak distribution plot: no data")
        return

    # Sort by p90 streak length descending (more informative than median)
    sorted_labels = sorted(
        groups.keys(),
        key=lambda k: np.percentile(groups[k], 90) if len(groups[k]) >= 2 else max(groups[k]),
        reverse=True,
    )
    if max_groups > 0:
        sorted_labels = sorted_labels[:max_groups]

    data = [groups[l] for l in sorted_labels]
    # Add small offset for log scale (can't plot 0)
    data_log = [[max(v, 0.5) for v in d] for d in data]

    fig, ax = plt.subplots(figsize=(max(10, len(sorted_labels) * 0.5), 7))

    # Violin plot on log scale
    parts = ax.violinplot(data_log, positions=range(len(data_log)),
                          showmedians=True, showextrema=False)
    for pc in parts["bodies"]:
        pc.set_facecolor("#4c72b0")
        pc.set_alpha(0.5)
    parts["cmedians"].set_color("black")

    # Overlay individual max streak as a marker
    for i, d in enumerate(data):
        ax.plot(i, max(d), "v", color="#d62728", markersize=6, zorder=5)

    ax.set_yscale("log")
    ax.set_xticks(range(len(sorted_labels)))
    ax.set_xticklabels(sorted_labels, rotation=55, ha="right", fontsize=7)
    ax.set_ylabel("Streak Length (windows, log scale)")
    ax.set_title(f"Dominance Streak Length Distribution  [window={ws_filter}]")
    ax.grid(axis="y", alpha=0.3, which="both")
    ax.yaxis.set_major_formatter(ticker.ScalarFormatter())

    # Reference lines
    ax.axhline(y=10, color="grey", linestyle="--", alpha=0.4, linewidth=0.8)
    ax.text(len(sorted_labels) - 0.5, 10, "10", fontsize=7, color="grey",
            va="bottom", ha="right")

    # Legend for max marker
    ax.plot([], [], "v", color="#d62728", markersize=6, label="max streak")
    ax.legend(fontsize=8, loc="upper right")

    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 2: Dominance heatmap (replaces 4-panel bar chart)
# ─────────────────────────────────────────────────────────────────────────────

def plot_dominance_heatmap(
    summary_rows: List[Dict[str, str]],
    group_cols: List[str],
    ws_filter: str,
    out_path: Path,
    dpi: int,
    max_groups: int,
) -> None:
    filtered = [r for r in summary_rows if r.get("window_size", "") == ws_filter]
    if not filtered:
        print("  SKIP dominance heatmap: no data")
        return

    # Sort by max_streak_frac descending
    filtered.sort(key=lambda r: -float(r.get("max_streak_frac", "0")))
    if max_groups > 0:
        filtered = filtered[:max_groups]

    labels = [short_label(r, group_cols) for r in filtered]
    metrics = ["max_streak_frac", "long_streak_coverage",
               "top1_dominance_frac", "gini_coefficient"]
    metric_labels = ["Max Streak\nFraction", "Long Streak\nCoverage",
                     "Top-1 Thread\nFraction", "Gini\nCoefficient"]

    # Build matrix
    mat = np.zeros((len(filtered), len(metrics)))
    for i, r in enumerate(filtered):
        for j, m in enumerate(metrics):
            mat[i, j] = float(r.get(m, "0"))

    fig, ax = plt.subplots(figsize=(6, max(5, len(labels) * 0.3 + 1)))
    im = ax.imshow(mat, aspect="auto", cmap="YlOrRd", vmin=0, vmax=1)

    # Annotate cells
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            v = mat[i, j]
            color = "white" if v > 0.55 else "black"
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                    fontsize=7, color=color)

    ax.set_xticks(range(len(metrics)))
    ax.set_xticklabels(metric_labels, fontsize=9)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_title(f"Dominance Concentration  [window={ws_filter}]", fontsize=12)

    cb = fig.colorbar(im, ax=ax, shrink=0.6, pad=0.02)
    cb.set_label("Value (0–1)", fontsize=9)

    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 3: Streak timeline — deterministic thread colours
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
    groups: Dict[str, List[Tuple[str, int, int]]] = defaultdict(list)
    group_nwindows: Dict[str, int] = {}
    group_op: Dict[str, str] = {}

    for r in summary_rows:
        if r.get("window_size", "") != ws_filter:
            continue
        label = short_label(r, group_cols)
        group_nwindows[label] = int(r.get("n_windows", "0"))
        group_op[label] = extract_op(r)

    for r in streak_rows:
        if r.get("window_size", "") != ws_filter:
            continue
        label = short_label(r, group_cols)
        tid = r.get("streak_thread", "?")
        start = int(r.get("streak_start_window", "0"))
        length = int(r.get("streak_length", "1"))
        groups[label].append((tid, start, length))

    if not groups:
        print("  SKIP streak timeline: no data")
        return

    # Sort by max streak frac
    sorted_labels = sorted(
        groups.keys(),
        key=lambda k: max((l for _, _, l in groups[k]), default=0) / max(group_nwindows.get(k, 1), 1),
        reverse=True,
    )
    if max_groups > 0:
        sorted_labels = sorted_labels[:max_groups]

    # Collect all thread IDs for a consistent legend
    all_tids = sorted(set(tid for label in sorted_labels for tid, _, _ in groups[label]),
                      key=lambda t: (int(t) if t.isdigit() else 999, t))

    n_groups = len(sorted_labels)
    fig_h = max(4, n_groups * 0.45 + 1.5)
    fig, ax = plt.subplots(figsize=(14, fig_h))

    for y_idx, label in enumerate(reversed(sorted_labels)):
        streaks = groups[label]
        for tid, start, length in streaks:
            color = thread_color_deterministic(tid)
            ax.barh(y_idx, length, left=start, height=0.7,
                    color=color, edgecolor="none", alpha=0.85)

    ax.set_yticks(range(len(sorted_labels)))
    ax.set_yticklabels(list(reversed(sorted_labels)), fontsize=7)
    ax.set_xlabel("Window Index")
    ax.set_title(f"Dominance Streak Timeline  [window={ws_filter}]")
    ax.grid(axis="x", alpha=0.3)

    # Deterministic legend
    handles = [Patch(facecolor=thread_color_deterministic(t), label=f"T{t}")
               for t in all_tids if t]
    if len(handles) <= 16:
        ax.legend(handles=handles, loc="upper right", fontsize=7, ncol=2,
                  title="Thread", title_fontsize=8)

    fig.tight_layout()
    out_path = out_dir / f"streak_timeline.{fmt}"
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 4: Concentration scatter — clean annotations
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

    eff = np.array([float(r.get("effective_dominant_threads", "1")) for r in filtered])
    gini = np.array([float(r.get("gini_coefficient", "0")) for r in filtered])
    max_frac = np.array([float(r.get("max_streak_frac", "0")) for r in filtered])
    ops = [extract_op(r) for r in filtered]
    threads = [extract_threads(r) for r in filtered]

    # Colour by operation, size by thread count
    fig, ax = plt.subplots(figsize=(9, 7))

    # Group by operation for legend
    op_set = sorted(set(ops))
    for op_name in op_set:
        mask = [o == op_name for o in ops]
        idx = np.where(mask)[0]
        sizes = [30 + int(threads[i]) * 8 for i in idx]
        ax.scatter(eff[idx], gini[idx], c=op_color(op_name), s=sizes,
                   alpha=0.65, edgecolors="white", linewidths=0.5,
                   label=op_name, zorder=3)

    ax.set_xlabel("Effective Dominant Threads (1/HHI)", fontsize=11)
    ax.set_ylabel("Gini Coefficient", fontsize=11)
    ax.set_title(f"Dominance Concentration  [window={ws_filter}]", fontsize=12)
    ax.grid(alpha=0.2)
    ax.legend(fontsize=9, title="Operation", title_fontsize=10)

    # Only annotate the most extreme points (top 5 by max_frac)
    top_idx = np.argsort(max_frac)[-5:]
    for i in top_idx:
        label = short_label(filtered[i], group_cols)
        ax.annotate(
            label, (eff[i], gini[i]),
            fontsize=6, alpha=0.8,
            xytext=(6, 6), textcoords="offset points",
            arrowprops=dict(arrowstyle="-", color="grey", alpha=0.5, lw=0.5),
        )

    # Size legend
    for tc in sorted(set(threads)):
        ax.scatter([], [], c="grey", s=30 + int(tc) * 8, label=f"t={tc}",
                   edgecolors="white", linewidths=0.5)
    ax.legend(fontsize=8, title="Operation / Threads", title_fontsize=9,
              loc="upper right", ncol=2)

    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 5: Aggregated by operation and thread count
# ─────────────────────────────────────────────────────────────────────────────

def plot_aggregated_by_factor(
    summary_rows: List[Dict[str, str]],
    group_cols: List[str],
    ws_filter: str,
    out_path: Path,
    dpi: int,
) -> None:
    filtered = [r for r in summary_rows if r.get("window_size", "") == ws_filter]
    if not filtered:
        print("  SKIP aggregated plot: no data")
        return

    # Aggregate by (operation, thread_count)
    agg: Dict[Tuple[str, str], List[Dict[str, str]]] = defaultdict(list)
    for r in filtered:
        key = (extract_op(r), extract_threads(r))
        agg[key].append(r)

    if len(agg) < 2:
        print("  SKIP aggregated plot: fewer than 2 factor combinations")
        return

    sorted_keys = sorted(agg.keys())
    labels = [f"{op} t={tc}" for op, tc in sorted_keys]

    metrics = {
        "max_streak_frac": "Max Streak Fraction",
        "long_streak_coverage": "Long Streak Coverage",
        "gini_coefficient": "Gini Coefficient",
        "effective_dominant_threads": "Effective Dominant Threads",
    }

    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    axes = axes.flatten()

    for ax, (col, title) in zip(axes, metrics.items()):
        means = []
        stds = []
        colors = []
        for key in sorted_keys:
            vals = [float(r.get(col, "0")) for r in agg[key]]
            means.append(np.mean(vals))
            stds.append(np.std(vals))
            colors.append(op_color(key[0]))

        x = np.arange(len(labels))
        bars = ax.bar(x, means, yerr=stds, color=colors, alpha=0.7,
                      edgecolor="white", linewidth=0.5, capsize=3,
                      error_kw=dict(lw=1, alpha=0.6))
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        ax.set_title(title, fontsize=10)
        ax.grid(axis="y", alpha=0.3)

        # Value labels on bars
        for bar, m in zip(bars, means):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.01,
                    f"{m:.2f}", ha="center", fontsize=7, va="bottom")

    fig.suptitle(f"Dominance by Operation & Thread Count  [window={ws_filter}]",
                 fontsize=13)
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

    print("Generating plots...")
    plot_streak_distributions(
        streak_rows, group_cols, ws_filter,
        out_dir / f"streak_length_distribution.{fmt}", dpi, args.max_groups,
    )
    plot_dominance_heatmap(
        summary_rows, group_cols, ws_filter,
        out_dir / f"dominance_heatmap.{fmt}", dpi, args.max_groups,
    )
    plot_streak_timelines(
        streak_rows, summary_rows, group_cols, ws_filter,
        out_dir, fmt, dpi, args.max_groups,
    )
    plot_concentration_scatter(
        summary_rows, group_cols, ws_filter,
        out_dir / f"concentration_scatter.{fmt}", dpi,
    )
    plot_aggregated_by_factor(
        summary_rows, group_cols, ws_filter,
        out_dir / f"aggregated_by_factor.{fmt}", dpi,
    )

    print(f"\nAll plots saved to {out_dir}/")


if __name__ == "__main__":
    main()
