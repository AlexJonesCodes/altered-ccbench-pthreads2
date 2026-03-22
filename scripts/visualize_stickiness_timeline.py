#!/usr/bin/env python3
"""Visualise how stickiness evolves over time (window by window).

Reads the CSV outputs from analyze_stickiness.py and produces temporal
visualisations showing stickiness dynamics across the sequence, plus
distribution summaries.

Plots generated:
  1. Stickiness timeline  – window z-score over time, one subplot per group,
     coloured by window size.  Change-points from the regime summary are
     overlaid as vertical lines.
  2. Repeat-rate timeline – observed vs expected repeat rate per window.
  3. Fairness timeline    – Jain's fairness index per window over time.
  4. Dominant-winner ribbon – colour-coded strip showing which thread
     dominates each window.
  5. Z-score distributions – violin + box plots of window z-scores,
     faceted by operation and window size.
  6. Repeat-excess distributions – histograms of window repeat-excess %
     with KDE overlay, faceted by operation.
  7. Multi-scale heatmap  – (window_index × window_size) heatmap of z-scores
     per group.

Compatible with the raw data produced by run_stickiness_study.sh:
  - reads <prefix>_window_detail.csv   (primary data source)
  - reads <prefix>_group_summary.csv   (for context / labels)
  - reads <prefix>_regime_summary.csv  (optional, for change-point overlay)

Usage:
  python3 scripts/visualize_stickiness_timeline.py results/analysis/stickiness \\
      [--out-dir results/timeline_plots] [--format png] [--dpi 150]
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
        help="Output prefix from analyze_stickiness.py "
             "(e.g. results/analysis/stickiness)",
    )
    p.add_argument("--out-dir", default=None,
                   help="Directory for plots (default: <prefix>_timeline_plots/)")
    p.add_argument("--format", default="png", choices=["png", "pdf", "svg"],
                   help="Output image format")
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--max-groups", type=int, default=0,
                   help="Maximum number of groups to plot individually "
                        "(0 = unlimited, prevents enormous multi-panel figures)")
    p.add_argument("--per-group", action="store_true",
                   help="Generate separate plot files for each group instead "
                        "of combining all groups into one figure")
    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
#  I/O helpers
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


def safe_int(v: str, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────────────────────────────────────
#  Data structures
# ─────────────────────────────────────────────────────────────────────────────

# Group key: (op, core_set_id, thread_count, seed_core)
GROUP_COLS = ["op", "core_set_id", "thread_count", "seed_core"]


def group_key(row: Dict[str, str]) -> Tuple[str, ...]:
    return tuple(row.get(c, "") for c in GROUP_COLS)


def group_label(key: Tuple[str, ...]) -> str:
    op, csid, tc, sc = key
    return f"{op}  cs={csid} t={tc} seed={sc}"


def short_group_label(key: Tuple[str, ...]) -> str:
    op, _csid, tc, _sc = key
    return f"{op} {tc}T"


def group_filename(key: Tuple[str, ...]) -> str:
    """Filesystem-safe string for use in per-group filenames."""
    op, csid, tc, sc = key
    return f"{op}_cs{csid}_t{tc}_seed{sc}"


# ─────────────────────────────────────────────────────────────────────────────
#  Colour helpers
# ─────────────────────────────────────────────────────────────────────────────

OP_COLOURS = {
    "CAS_UNTIL_SUCCESS": "#2196F3",
    "FAI": "#4CAF50",
    "TAS": "#FF9800",
    "SWAP": "#9C27B0",
    "CAS": "#F44336",
}

WINDOW_SIZE_COLOURS = {
    50:   "#E91E63",
    100:  "#9C27B0",
    200:  "#3F51B5",
    500:  "#009688",
    1000: "#FF9800",
    2000: "#795548",
    5000: "#607D8B",
}


def op_colour(op: str) -> str:
    return OP_COLOURS.get(op, "#9E9E9E")


def ws_colour(ws: int) -> str:
    return WINDOW_SIZE_COLOURS.get(ws, "#9E9E9E")


def make_thread_cmap(n_threads: int):
    """Return a list of distinct colours for up to n_threads."""
    if n_threads <= 10:
        cmap = plt.cm.tab10
    elif n_threads <= 20:
        cmap = plt.cm.tab20
    else:
        cmap = plt.cm.gist_ncar
    return [cmap(i / max(n_threads - 1, 1)) for i in range(n_threads)]


# ─────────────────────────────────────────────────────────────────────────────
#  Organise window-detail data
# ─────────────────────────────────────────────────────────────────────────────

def organise_windows(windows: List[Dict[str, str]]):
    """Return {group_key: {window_size: [rows sorted by window_index]}}."""
    data: Dict[Tuple[str, ...], Dict[int, List[Dict[str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for r in windows:
        gk = group_key(r)
        ws = safe_int(r.get("window_size", "0"))
        if ws > 0:
            data[gk][ws].append(r)

    # Sort each list by window_index
    for gk in data:
        for ws in data[gk]:
            data[gk][ws].sort(key=lambda r: safe_int(r.get("window_index", "0")))

    return data


def organise_regimes(regimes: List[Dict[str, str]]):
    """Return {group_key: {window_size: [(cp_position, cp_score, left_mean, right_mean)]}}."""
    data: Dict[Tuple[str, ...], Dict[int, List[Tuple]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for r in regimes:
        gk = group_key(r)
        ws = safe_int(r.get("window_size", "0"))
        pos = safe_int(r.get("cp_position", "0"))
        score = safe_float(r.get("cp_score", "0"))
        lm = safe_float(r.get("left_mean", "nan"))
        rm = safe_float(r.get("right_mean", "nan"))
        data[gk][ws].append((pos, score, lm, rm))
    return data


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 1: Stickiness Z-score timeline
# ─────────────────────────────────────────────────────────────────────────────

def plot_zscore_timeline(win_data, regime_data, out_dir: Path, fmt: str,
                         dpi: int, max_groups: int):
    """Window z-score over time with change-point overlays.

    Uses only the smallest available window size for highest temporal resolution.
    """
    groups = list(win_data.keys()) if max_groups <= 0 else list(win_data.keys())[:max_groups]
    if not groups:
        return

    n = len(groups)
    fig, axes = plt.subplots(n, 1, figsize=(14, 3.0 * n + 1), squeeze=False,
                             sharex=False)

    for idx, gk in enumerate(groups):
        ax = axes[idx, 0]
        ws_dict = win_data[gk]
        # Use only the smallest window size for highest temporal resolution
        ws = min(ws_dict.keys())
        rows = ws_dict[ws]

        x = [safe_int(r.get("window_index", "0")) for r in rows]
        z = [safe_float(r.get("window_repeat_zscore", "nan")) for r in rows]
        colour = ws_colour(ws)
        ax.plot(x, z, "-", color=colour, linewidth=1.2, alpha=0.8,
                label=f"w={ws}")
        # Light fill between to show magnitude
        ax.fill_between(x, 0, z, color=colour, alpha=0.08)

        # Significance thresholds
        ax.axhline(0, color="grey", linewidth=0.6, linestyle="-", alpha=0.4)
        ax.axhline(2, color="red", linewidth=0.7, linestyle=":", alpha=0.5)
        ax.axhline(-2, color="blue", linewidth=0.7, linestyle=":", alpha=0.5)

        # Overlay change-points if available
        if gk in regime_data:
            cps = regime_data.get(gk, {}).get(ws, [])
            for cp_pos, cp_score, lm, rm in cps:
                ax.axvline(cp_pos, color=ws_colour(ws), linewidth=1.5,
                           linestyle="--", alpha=0.6)

        ax.set_ylabel("Z-score")
        ax.set_title(f"{group_label(gk)}  [window={ws}]", fontsize=10, fontweight="bold")
        ax.legend(fontsize=7, loc="upper right")
        ax.grid(True, alpha=0.2)

    axes[-1, 0].set_xlabel("Window Index")
    fig.suptitle("Stickiness Z-Score Over Time", fontsize=13,
                 fontweight="bold", y=1.0)
    fig.tight_layout()
    out = out_dir / f"zscore_timeline.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 2: Repeat-rate timeline (observed vs expected)
# ─────────────────────────────────────────────────────────────────────────────

def plot_repeat_rate_timeline(win_data, out_dir: Path, fmt: str, dpi: int,
                               max_groups: int):
    """Observed vs expected repeat rate per window over time."""
    groups = list(win_data.keys()) if max_groups <= 0 else list(win_data.keys())[:max_groups]
    if not groups:
        return

    # Use smallest window size for highest temporal resolution
    n = len(groups)
    fig, axes = plt.subplots(n, 1, figsize=(14, 3.0 * n + 1), squeeze=False,
                             sharex=False)

    for idx, gk in enumerate(groups):
        ax = axes[idx, 0]
        ws_dict = win_data[gk]
        # Pick smallest window size for most detail
        ws = min(ws_dict.keys())
        rows = ws_dict[ws]

        x = [safe_int(r.get("window_index", "0")) for r in rows]
        obs = [safe_float(r.get("window_repeat_rate", "nan")) for r in rows]
        exp = [safe_float(r.get("window_expected_repeat", "nan")) for r in rows]

        ax.plot(x, obs, "-", color="#E91E63", linewidth=1.2, alpha=0.85,
                label="Observed")
        ax.plot(x, exp, "--", color="#607D8B", linewidth=1.0, alpha=0.7,
                label="Expected")
        # Shade excess
        ax.fill_between(x, exp, obs,
                        where=[o > e for o, e in zip(obs, exp)],
                        color="red", alpha=0.12, interpolate=True, label="Excess (sticky)")
        ax.fill_between(x, exp, obs,
                        where=[o < e for o, e in zip(obs, exp)],
                        color="blue", alpha=0.12, interpolate=True, label="Deficit (anti-sticky)")

        ax.set_ylabel("Repeat Rate")
        ax.set_title(f"{group_label(gk)}  [window={ws}]", fontsize=10,
                     fontweight="bold")
        ax.legend(fontsize=7, loc="upper right")
        ax.grid(True, alpha=0.2)

    axes[-1, 0].set_xlabel("Window Index")
    fig.suptitle("Repeat Rate Over Time (Observed vs Expected)",
                 fontsize=13, fontweight="bold", y=1.0)
    fig.tight_layout()
    out = out_dir / f"repeat_rate_timeline.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 3: Fairness timeline
# ─────────────────────────────────────────────────────────────────────────────

def plot_fairness_timeline(win_data, out_dir: Path, fmt: str, dpi: int,
                            max_groups: int):
    """Jain's fairness index per window over time."""
    groups = list(win_data.keys()) if max_groups <= 0 else list(win_data.keys())[:max_groups]
    if not groups:
        return

    n = len(groups)
    fig, axes = plt.subplots(n, 1, figsize=(14, 2.8 * n + 1), squeeze=False,
                             sharex=False)

    for idx, gk in enumerate(groups):
        ax = axes[idx, 0]
        ws_dict = win_data[gk]

        for ws in sorted(ws_dict.keys()):
            rows = ws_dict[ws]
            x = [safe_int(r.get("window_index", "0")) for r in rows]
            jfi = [safe_float(r.get("window_jains_fairness", "nan")) for r in rows]
            ax.plot(x, jfi, "-", color=ws_colour(ws), linewidth=1.1,
                    alpha=0.8, label=f"w={ws}")

        ax.set_ylabel("Jain's Fairness")
        ax.set_ylim(-0.05, 1.05)
        ax.set_title(group_label(gk), fontsize=10, fontweight="bold")
        ax.legend(fontsize=7, loc="lower right")
        ax.grid(True, alpha=0.2)

    axes[-1, 0].set_xlabel("Window Index")
    fig.suptitle("Fairness Index Over Time", fontsize=13,
                 fontweight="bold", y=1.0)
    fig.tight_layout()
    out = out_dir / f"fairness_timeline.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 4: Dominant-winner ribbon
# ─────────────────────────────────────────────────────────────────────────────

def plot_dominant_winner_ribbon(win_data, out_dir: Path, fmt: str, dpi: int,
                                 max_groups: int):
    """Colour-coded strip showing which thread dominates each window."""
    groups = list(win_data.keys()) if max_groups <= 0 else list(win_data.keys())[:max_groups]
    if not groups:
        return

    n = len(groups)
    fig, axes = plt.subplots(n, 1, figsize=(14, 1.8 * n + 2), squeeze=False,
                             sharex=False)

    for idx, gk in enumerate(groups):
        ax = axes[idx, 0]
        ws_dict = win_data[gk]
        ws = min(ws_dict.keys())  # use smallest window for max resolution
        rows = ws_dict[ws]

        # Collect all dominant winners
        winners = [r.get("window_dominant_winner", "") for r in rows]
        unique_winners = sorted(set(winners))
        winner_to_idx = {w: i for i, w in enumerate(unique_winners)}
        n_winners = len(unique_winners)

        colours = make_thread_cmap(n_winners)
        colour_map = {w: colours[i] for w, i in winner_to_idx.items()}

        # Build colour array
        x = [safe_int(r.get("window_index", "0")) for r in rows]
        dom_share = [safe_float(r.get("window_dominant_share", "nan")) for r in rows]

        # Draw as coloured bars
        for i, (xi, w) in enumerate(zip(x, winners)):
            ax.bar(xi, 1, width=1.0, color=colour_map.get(w, "grey"),
                   alpha=0.85, edgecolor="none")

        # Overlay dominant share as a black line
        ax2 = ax.twinx()
        ax2.plot(x, dom_share, "-", color="black", linewidth=0.8, alpha=0.6)
        ax2.set_ylabel("Dom. Share", fontsize=8, color="black")
        ax2.set_ylim(0, 1.05)

        ax.set_yticks([])
        ax.set_title(f"{group_label(gk)}  [window={ws}]", fontsize=10,
                     fontweight="bold")

        # Legend for threads (compact)
        if n_winners <= 12:
            patches = [Patch(facecolor=colour_map[w], label=f"T{w}")
                       for w in unique_winners]
            ax.legend(handles=patches, fontsize=6, ncol=min(n_winners, 6),
                      loc="upper right", framealpha=0.7)

    axes[-1, 0].set_xlabel("Window Index")
    fig.suptitle("Dominant Winner Per Window", fontsize=13,
                 fontweight="bold", y=1.0)
    fig.tight_layout()
    out = out_dir / f"dominant_winner_ribbon.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 5: Z-score distribution (violin + box)
# ─────────────────────────────────────────────────────────────────────────────

def plot_zscore_distributions(win_data, out_dir: Path, fmt: str, dpi: int):
    """Violin+box plots of window z-scores, faceted by operation and window size."""
    # Aggregate by (op, ws) -> list of z-scores
    agg: Dict[Tuple[str, int], List[float]] = defaultdict(list)
    for gk, ws_dict in win_data.items():
        op = gk[0]  # op is first element
        for ws, rows in ws_dict.items():
            for r in rows:
                z = safe_float(r.get("window_repeat_zscore", "nan"))
                if not math.isnan(z):
                    agg[(op, ws)].append(z)

    if not agg:
        return

    ops = sorted(set(k[0] for k in agg))
    window_sizes = sorted(set(k[1] for k in agg))
    n_ws = len(window_sizes)

    fig, axes = plt.subplots(1, len(ops), figsize=(4.5 * len(ops), 6),
                             sharey=True, squeeze=False)

    for col, op in enumerate(ops):
        ax = axes[0, col]
        plot_data = []
        labels = []
        for ws in window_sizes:
            vals = agg.get((op, ws), [])
            if vals:
                plot_data.append(vals)
                labels.append(f"w={ws}")

        if not plot_data:
            continue

        positions = list(range(1, len(plot_data) + 1))

        # Violin plot
        parts = ax.violinplot(plot_data, positions=positions, showmedians=False,
                              showextrema=False)
        for i, pc in enumerate(parts["bodies"]):
            pc.set_facecolor(ws_colour(window_sizes[i]) if i < len(window_sizes) else "#9E9E9E")
            pc.set_alpha(0.35)

        # Box plot overlay
        bp = ax.boxplot(plot_data, positions=positions, widths=0.2,
                        patch_artist=True, showfliers=True,
                        flierprops=dict(marker=".", markersize=2, alpha=0.3))
        for i, patch in enumerate(bp["boxes"]):
            patch.set_facecolor(ws_colour(window_sizes[i]) if i < len(window_sizes) else "#9E9E9E")
            patch.set_alpha(0.7)

        ax.axhline(0, color="grey", linewidth=0.6, linestyle="-", alpha=0.4)
        ax.axhline(2, color="red", linewidth=0.7, linestyle=":", alpha=0.5)
        ax.axhline(-2, color="blue", linewidth=0.7, linestyle=":", alpha=0.5)

        ax.set_xticks(positions)
        ax.set_xticklabels(labels, fontsize=8, rotation=30)
        ax.set_title(op, fontsize=11, fontweight="bold")
        ax.grid(axis="y", alpha=0.2)

        # Annotate median and % sticky
        for i, vals in enumerate(plot_data):
            med = float(np.median(vals))
            pct_sticky = 100.0 * sum(1 for v in vals if v > 2) / len(vals)
            ax.text(positions[i], ax.get_ylim()[1] * 0.95,
                    f"med={med:.1f}\n{pct_sticky:.0f}% sticky",
                    ha="center", va="top", fontsize=6.5, color="#333")

    axes[0, 0].set_ylabel("Window Z-Score")
    fig.suptitle("Distribution of Window Stickiness Z-Scores",
                 fontsize=13, fontweight="bold", y=1.01)
    fig.tight_layout()
    out = out_dir / f"zscore_distribution.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 6: Repeat-excess distribution (histogram + KDE)
# ─────────────────────────────────────────────────────────────────────────────

def plot_repeat_excess_distributions(win_data, out_dir: Path, fmt: str,
                                      dpi: int):
    """Histograms of window repeat-excess %, faceted by operation."""
    # Aggregate by (op, ws) -> list of excess values
    agg: Dict[Tuple[str, int], List[float]] = defaultdict(list)
    for gk, ws_dict in win_data.items():
        op = gk[0]
        for ws, rows in ws_dict.items():
            for r in rows:
                v = safe_float(r.get("window_repeat_excess_pct", "nan"))
                if not math.isnan(v):
                    agg[(op, ws)].append(v)

    if not agg:
        return

    ops = sorted(set(k[0] for k in agg))
    window_sizes = sorted(set(k[1] for k in agg))

    fig, axes = plt.subplots(len(ops), 1, figsize=(10, 3.5 * len(ops)),
                             squeeze=False, sharex=False)

    for row, op in enumerate(ops):
        ax = axes[row, 0]
        for ws in window_sizes:
            vals = agg.get((op, ws), [])
            if not vals:
                continue
            colour = ws_colour(ws)
            # Histogram
            ax.hist(vals, bins=40, color=colour, alpha=0.35,
                    edgecolor=colour, linewidth=0.5,
                    label=f"w={ws} (n={len(vals)})", density=True)
            # KDE approximation using numpy
            if len(vals) > 5:
                arr = np.array(vals)
                # Simple Gaussian KDE via histogram smoothing
                kde_x = np.linspace(arr.min() - 5, arr.max() + 5, 300)
                bw = 1.06 * np.std(arr) * len(arr) ** (-0.2)  # Silverman's rule
                if bw > 0:
                    kde_y = np.zeros_like(kde_x)
                    for v in arr:
                        kde_y += np.exp(-0.5 * ((kde_x - v) / bw) ** 2)
                    kde_y /= (len(arr) * bw * np.sqrt(2 * np.pi))
                    ax.plot(kde_x, kde_y, "-", color=colour, linewidth=1.5,
                            alpha=0.8)

        ax.axvline(0, color="grey", linewidth=0.8, linestyle="--", alpha=0.5)
        ax.set_xlabel("Repeat Excess (%)")
        ax.set_ylabel("Density")
        ax.set_title(op, fontsize=11, fontweight="bold")
        ax.legend(fontsize=7, loc="upper right")
        ax.grid(True, alpha=0.2)

    fig.suptitle("Distribution of Window Repeat Excess (%)",
                 fontsize=13, fontweight="bold", y=1.01)
    fig.tight_layout()
    out = out_dir / f"repeat_excess_distribution.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 7: Multi-scale heatmap (window_index × window_size)
# ─────────────────────────────────────────────────────────────────────────────

def plot_multiscale_heatmap(win_data, out_dir: Path, fmt: str, dpi: int,
                             max_groups: int):
    """Heatmap: x=window position (normalised 0-1), y=window_size, colour=z-score."""
    groups = list(win_data.keys()) if max_groups <= 0 else list(win_data.keys())[:max_groups]
    if not groups:
        return

    n = len(groups)
    fig, axes = plt.subplots(n, 1, figsize=(14, 2.5 * n + 1), squeeze=False,
                             sharex=True)

    cmap = plt.cm.RdBu_r
    vmax = 6.0

    for idx, gk in enumerate(groups):
        ax = axes[idx, 0]
        ws_dict = win_data[gk]
        window_sizes = sorted(ws_dict.keys())

        if not window_sizes:
            continue

        # Build 2D array: rows=window_size, cols=normalised position bins
        n_bins = 100
        heatmap = np.full((len(window_sizes), n_bins), float("nan"))

        for row_i, ws in enumerate(window_sizes):
            rows = ws_dict[ws]
            n_windows = len(rows)
            if n_windows == 0:
                continue
            for r in rows:
                wi = safe_int(r.get("window_index", "0"))
                z = safe_float(r.get("window_repeat_zscore", "nan"))
                # Map window_index to normalised bin
                bin_idx = min(int(wi / n_windows * n_bins), n_bins - 1)
                if math.isnan(heatmap[row_i, bin_idx]):
                    heatmap[row_i, bin_idx] = z
                else:
                    # Average if multiple windows map to same bin
                    heatmap[row_i, bin_idx] = (heatmap[row_i, bin_idx] + z) / 2

        im = ax.imshow(heatmap, aspect="auto", cmap=cmap, vmin=-vmax, vmax=vmax,
                       origin="lower", extent=[0, 100, -0.5, len(window_sizes) - 0.5])
        ax.set_yticks(range(len(window_sizes)))
        ax.set_yticklabels([str(ws) for ws in window_sizes], fontsize=8)
        ax.set_ylabel("Window Size")
        ax.set_title(group_label(gk), fontsize=10, fontweight="bold")

    axes[-1, 0].set_xlabel("Sequence Position (% through run)")
    fig.colorbar(im, ax=axes.ravel().tolist(), shrink=0.6, label="Z-Score",
                 pad=0.02)
    fig.suptitle("Multi-Scale Stickiness Heatmap", fontsize=13,
                 fontweight="bold", y=1.0)
    fig.tight_layout()
    out = out_dir / f"multiscale_heatmap.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Per-group plotting (one file per group)
# ─────────────────────────────────────────────────────────────────────────────

def _plot_single_zscore(gk, ws_dict, regime_data, out_dir: Path, fmt: str, dpi: int):
    """Single-group z-score timeline (smallest window size only)."""
    fig, ax = plt.subplots(figsize=(14, 4))
    # Use only the smallest window size for highest temporal resolution
    ws = min(ws_dict.keys())
    rows = ws_dict[ws]
    x = [safe_int(r.get("window_index", "0")) for r in rows]
    z = [safe_float(r.get("window_repeat_zscore", "nan")) for r in rows]
    colour = ws_colour(ws)
    ax.plot(x, z, "-", color=colour, linewidth=1.2, alpha=0.8, label=f"w={ws}")
    ax.fill_between(x, 0, z, color=colour, alpha=0.08)
    ax.axhline(0, color="grey", linewidth=0.6, linestyle="-", alpha=0.4)
    ax.axhline(2, color="red", linewidth=0.7, linestyle=":", alpha=0.5)
    ax.axhline(-2, color="blue", linewidth=0.7, linestyle=":", alpha=0.5)
    if gk in regime_data:
        for cp_pos, cp_score, lm, rm in regime_data.get(gk, {}).get(ws, []):
            ax.axvline(cp_pos, color=ws_colour(ws), linewidth=1.5,
                       linestyle="--", alpha=0.6)
    ax.set_ylabel("Z-score")
    ax.set_xlabel("Window Index")
    ax.set_title(f"{group_label(gk)}  [window={ws}]", fontsize=11, fontweight="bold")
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    out = out_dir / f"zscore_{group_filename(gk)}.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return out


def _plot_single_repeat_rate(gk, ws_dict, out_dir: Path, fmt: str, dpi: int):
    """Single-group repeat rate timeline."""
    fig, ax = plt.subplots(figsize=(14, 4))
    ws = min(ws_dict.keys())
    rows = ws_dict[ws]
    x = [safe_int(r.get("window_index", "0")) for r in rows]
    obs = [safe_float(r.get("window_repeat_rate", "nan")) for r in rows]
    exp = [safe_float(r.get("window_expected_repeat", "nan")) for r in rows]
    ax.plot(x, obs, "-", color="#E91E63", linewidth=1.2, alpha=0.85, label="Observed")
    ax.plot(x, exp, "--", color="#607D8B", linewidth=1.0, alpha=0.7, label="Expected")
    ax.fill_between(x, exp, obs,
                    where=[o > e for o, e in zip(obs, exp)],
                    color="red", alpha=0.12, interpolate=True, label="Excess (sticky)")
    ax.fill_between(x, exp, obs,
                    where=[o < e for o, e in zip(obs, exp)],
                    color="blue", alpha=0.12, interpolate=True, label="Deficit (anti-sticky)")
    ax.set_ylabel("Repeat Rate")
    ax.set_xlabel("Window Index")
    ax.set_title(f"{group_label(gk)}  [window={ws}]", fontsize=11, fontweight="bold")
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    out = out_dir / f"repeat_rate_{group_filename(gk)}.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return out


def _plot_single_fairness(gk, ws_dict, out_dir: Path, fmt: str, dpi: int):
    """Single-group fairness timeline."""
    fig, ax = plt.subplots(figsize=(14, 4))
    for ws in sorted(ws_dict.keys()):
        rows = ws_dict[ws]
        x = [safe_int(r.get("window_index", "0")) for r in rows]
        jfi = [safe_float(r.get("window_jains_fairness", "nan")) for r in rows]
        ax.plot(x, jfi, "-", color=ws_colour(ws), linewidth=1.1, alpha=0.8,
                label=f"w={ws}")
    ax.set_ylabel("Jain's Fairness")
    ax.set_xlabel("Window Index")
    ax.set_ylim(-0.05, 1.05)
    ax.set_title(group_label(gk), fontsize=11, fontweight="bold")
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    out = out_dir / f"fairness_{group_filename(gk)}.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return out


def _plot_single_ribbon(gk, ws_dict, out_dir: Path, fmt: str, dpi: int):
    """Single-group dominant winner ribbon."""
    fig, ax = plt.subplots(figsize=(14, 3))
    ws = min(ws_dict.keys())
    rows = ws_dict[ws]
    winners = [r.get("window_dominant_winner", "") for r in rows]
    unique_winners = sorted(set(winners))
    n_winners = len(unique_winners)
    colours = make_thread_cmap(n_winners)
    colour_map = {w: colours[i] for i, w in enumerate(unique_winners)}
    x = [safe_int(r.get("window_index", "0")) for r in rows]
    dom_share = [safe_float(r.get("window_dominant_share", "nan")) for r in rows]
    for xi, w in zip(x, winners):
        ax.bar(xi, 1, width=1.0, color=colour_map.get(w, "grey"),
               alpha=0.85, edgecolor="none")
    ax2 = ax.twinx()
    ax2.plot(x, dom_share, "-", color="black", linewidth=0.8, alpha=0.6)
    ax2.set_ylabel("Dom. Share", fontsize=8, color="black")
    ax2.set_ylim(0, 1.05)
    ax.set_yticks([])
    ax.set_xlabel("Window Index")
    ax.set_title(f"{group_label(gk)}  [window={ws}]", fontsize=11, fontweight="bold")
    if n_winners <= 12:
        patches = [Patch(facecolor=colour_map[w], label=f"T{w}") for w in unique_winners]
        ax.legend(handles=patches, fontsize=7, ncol=min(n_winners, 6),
                  loc="upper right", framealpha=0.7)
    fig.tight_layout()
    out = out_dir / f"ribbon_{group_filename(gk)}.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return out


def _plot_single_heatmap(gk, ws_dict, out_dir: Path, fmt: str, dpi: int):
    """Single-group multi-scale heatmap."""
    window_sizes = sorted(ws_dict.keys())
    if not window_sizes:
        return None
    fig, ax = plt.subplots(figsize=(14, max(3, 0.8 * len(window_sizes) + 1)))
    cmap = plt.cm.RdBu_r
    vmax = 6.0
    n_bins = 100
    heatmap = np.full((len(window_sizes), n_bins), float("nan"))
    for row_i, ws in enumerate(window_sizes):
        rows = ws_dict[ws]
        n_windows = len(rows)
        if n_windows == 0:
            continue
        for r in rows:
            wi = safe_int(r.get("window_index", "0"))
            z = safe_float(r.get("window_repeat_zscore", "nan"))
            bin_idx = min(int(wi / n_windows * n_bins), n_bins - 1)
            if math.isnan(heatmap[row_i, bin_idx]):
                heatmap[row_i, bin_idx] = z
            else:
                heatmap[row_i, bin_idx] = (heatmap[row_i, bin_idx] + z) / 2
    im = ax.imshow(heatmap, aspect="auto", cmap=cmap, vmin=-vmax, vmax=vmax,
                   origin="lower", extent=[0, 100, -0.5, len(window_sizes) - 0.5])
    ax.set_yticks(range(len(window_sizes)))
    ax.set_yticklabels([str(ws) for ws in window_sizes], fontsize=8)
    ax.set_ylabel("Window Size")
    ax.set_xlabel("Sequence Position (% through run)")
    ax.set_title(group_label(gk), fontsize=11, fontweight="bold")
    fig.colorbar(im, ax=ax, shrink=0.8, label="Z-Score", pad=0.02)
    fig.tight_layout()
    out = out_dir / f"heatmap_{group_filename(gk)}.{fmt}"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return out


def plot_all_per_group(win_data, regime_data, out_dir: Path, fmt: str, dpi: int):
    """Generate all plot types as separate files for each group."""
    groups = sorted(win_data.keys(), key=lambda k: group_label(k))
    total = len(groups)
    count = 0
    for i, gk in enumerate(groups, 1):
        ws_dict = win_data[gk]
        gdir = out_dir / group_filename(gk)
        gdir.mkdir(parents=True, exist_ok=True)

        _plot_single_zscore(gk, ws_dict, regime_data, gdir, fmt, dpi)
        _plot_single_repeat_rate(gk, ws_dict, gdir, fmt, dpi)
        _plot_single_fairness(gk, ws_dict, gdir, fmt, dpi)
        _plot_single_ribbon(gk, ws_dict, gdir, fmt, dpi)
        _plot_single_heatmap(gk, ws_dict, gdir, fmt, dpi)
        count += 5

        if i % 10 == 0 or i == total:
            print(f"  [{i}/{total}] groups done ...")

    print(f"  Wrote {count} per-group plots into {out_dir}/")
    return count


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────

def resolve_prefix(raw: str) -> Path:
    """Resolve the analysis prefix, accepting either a directory or file prefix.

    Accepts any of:
      results/stickiness_study_2/analysis              (directory)
      results/stickiness_study_2/analysis/stickiness   (file prefix)
      results/stickiness_study_2/analysis/stickiness_group_summary.csv  (full path)
    """
    p = Path(raw)
    # If it's a directory, look for the standard 'stickiness' prefix inside it
    if p.is_dir():
        # Try to find *_window_detail.csv in the directory
        matches = list(p.glob("*_window_detail.csv"))
        if matches:
            # Derive prefix from the filename
            name = matches[0].name  # e.g. stickiness_window_detail.csv
            prefix_name = name.replace("_window_detail.csv", "")
            return p / prefix_name
        # Default: assume standard 'stickiness' prefix
        return p / "stickiness"
    # If it points at a CSV file, strip the suffix to get the prefix
    if p.suffix == ".csv":
        name = p.name
        for suffix in ("_window_detail", "_group_summary", "_thread_summary",
                        "_regime_summary"):
            if name.endswith(suffix + ".csv"):
                return p.parent / name.replace(suffix + ".csv", "")
        return p.with_suffix("")
    return p


def _prefix_path(prefix: Path, suffix: str) -> Path:
    """Build a sibling path from a prefix: <parent>/<prefix_name><suffix>."""
    return prefix.parent / (prefix.name + suffix)


def main() -> None:
    args = parse_args()
    prefix = resolve_prefix(args.prefix)

    window_path = _prefix_path(prefix, "_window_detail.csv")
    group_path = _prefix_path(prefix, "_group_summary.csv")
    regime_path = _prefix_path(prefix, "_regime_summary.csv")

    windows = read_csv(window_path)
    groups = read_csv(group_path)
    regimes = read_csv(regime_path)

    if not windows:
        print(f"ERROR: No data in {window_path}")
        print("This script requires window_detail.csv from analyze_stickiness.py.")
        sys.exit(1)

    win_data = organise_windows(windows)
    regime_data = organise_regimes(regimes)

    out_dir = Path(args.out_dir) if args.out_dir else prefix.with_name(
        prefix.name + "_timeline_plots"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    n_groups = len(win_data)
    n_windows = len(windows)
    print(f"Loaded {n_windows} window rows across {n_groups} groups from {window_path}")
    print(f"Generating timeline plots -> {out_dir}/\n")

    if args.per_group:
        # Per-group mode: one directory per group, each with its own set of plots
        n_plots = plot_all_per_group(win_data, regime_data, out_dir,
                                     args.format, args.dpi)
        # Also generate the aggregated distribution plots
        plot_zscore_distributions(win_data, out_dir, args.format, args.dpi)
        plot_repeat_excess_distributions(win_data, out_dir, args.format, args.dpi)
        print(f"\nDone. {n_plots + 2} plots written to {out_dir}/")
    else:
        # Combined mode: all groups as subplots in shared figures
        plot_zscore_timeline(win_data, regime_data, out_dir, args.format, args.dpi,
                             args.max_groups)
        plot_repeat_rate_timeline(win_data, out_dir, args.format, args.dpi,
                                  args.max_groups)
        plot_fairness_timeline(win_data, out_dir, args.format, args.dpi,
                               args.max_groups)
        plot_dominant_winner_ribbon(win_data, out_dir, args.format, args.dpi,
                                    args.max_groups)

        # Distribution plots (aggregated across groups)
        plot_zscore_distributions(win_data, out_dir, args.format, args.dpi)
        plot_repeat_excess_distributions(win_data, out_dir, args.format, args.dpi)

        # Multi-scale heatmap
        plot_multiscale_heatmap(win_data, out_dir, args.format, args.dpi,
                                args.max_groups)

        n_plots = len(list(out_dir.glob(f"*.{args.format}")))
        print(f"\nDone. {n_plots} plots written to {out_dir}/")


if __name__ == "__main__":
    main()
