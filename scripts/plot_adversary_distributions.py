#!/usr/bin/env python3
"""Visualise per-run distributions from adversarial sweep experiments.

Reads the raw_phase_results.csv produced by
run_adversarial_separate_attacker_addrs_sweep.sh and generates a set of
distribution-level plots (violin, box, heatmap, strip) that reveal spread,
outliers, and shape — information lost in the summary means.

Usage:
  python3 scripts/plot_adversary_distributions.py results/sweep/raw_phase_results.csv \\
      [--out-dir results/sweep/distribution_plots] [--format png] [--dpi 150]
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
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

PHASE_ORDER = ["victim_baseline", "victim_plus_shared", "victim_plus_separate"]
PHASE_LABELS = {
    "victim_baseline": "Baseline",
    "victim_plus_shared": "Shared line",
    "victim_plus_separate": "Separate lines",
}
PHASE_COLORS = {
    "victim_baseline": "#4C72B0",
    "victim_plus_shared": "#DD8452",
    "victim_plus_separate": "#55A868",
}
# For two-group comparisons (shared vs separate)
TWO_COLORS = {"victim_plus_shared": "#DD8452", "victim_plus_separate": "#55A868"}
TWO_LABELS = {"victim_plus_shared": "Shared line", "victim_plus_separate": "Separate lines"}


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("input", help="Path to raw_phase_results.csv")
    p.add_argument(
        "--out-dir", default=None,
        help="Directory for plots (default: <input_dir>/distribution_plots/)",
    )
    p.add_argument("--format", default="png", choices=["png", "pdf", "svg"],
                   help="Output image format")
    p.add_argument("--dpi", type=int, default=150)
    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
#  I/O helpers
# ─────────────────────────────────────────────────────────────────────────────

def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        print(f"ERROR: {path} not found.", file=sys.stderr)
        sys.exit(1)
    with path.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def safe_float(v: str, default: float = float("nan")) -> float:
    if v is None or v.strip() == "" or v.strip().upper() == "NA":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def sorted_unique(values: list) -> list:
    """Return sorted unique values, handling mixed int/str gracefully."""
    try:
        return sorted(set(values), key=lambda x: int(x))
    except (ValueError, TypeError):
        return sorted(set(values))


# ─────────────────────────────────────────────────────────────────────────────
#  Data grouping
# ─────────────────────────────────────────────────────────────────────────────

def group_by(rows: List[dict], keys: List[str]) -> Dict[tuple, List[dict]]:
    """Group rows by a composite key (tuple of field values)."""
    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for r in rows:
        k = tuple(r[k] for k in keys)
        groups[k].append(r)
    return groups


def extract_values(rows: List[dict], field: str) -> np.ndarray:
    """Extract a numeric field from rows, dropping NaNs."""
    vals = [safe_float(r.get(field, "")) for r in rows]
    arr = np.array(vals)
    return arr[~np.isnan(arr)]


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 1: Latency violin plots by phase, faceted by attacker_threads
# ─────────────────────────────────────────────────────────────────────────────

def plot_latency_violins(rows: List[dict], out_dir: Path, fmt: str, dpi: int):
    atk_counts = sorted_unique([r["attacker_threads"] for r in rows])
    n_panels = len(atk_counts)
    if n_panels == 0:
        return

    fig, axes = plt.subplots(1, n_panels, figsize=(4 * n_panels, 5), squeeze=False)
    axes = axes[0]

    for i, atk in enumerate(atk_counts):
        ax = axes[i]
        subset = [r for r in rows if r["attacker_threads"] == atk]
        positions = []
        datasets = []
        colors = []
        labels = []
        for j, phase in enumerate(PHASE_ORDER):
            vals = extract_values([r for r in subset if r["phase"] == phase], "mean_avg")
            if len(vals) == 0:
                continue
            positions.append(j)
            datasets.append(vals)
            colors.append(PHASE_COLORS[phase])
            labels.append(PHASE_LABELS[phase])

        if not datasets:
            ax.set_title(f"{atk} attackers\n(no data)")
            continue

        parts = ax.violinplot(datasets, positions=positions, showmedians=True,
                              showextrema=True)
        for pc, c in zip(parts["bodies"], colors):
            pc.set_facecolor(c)
            pc.set_alpha(0.6)
        for key in ("cbars", "cmins", "cmaxes", "cmedians"):
            if key in parts:
                parts[key].set_color("black")

        # Overlay individual points (jittered strip)
        rng = np.random.RandomState(42)
        for pos, vals, c in zip(positions, datasets, colors):
            jitter = rng.uniform(-0.08, 0.08, size=len(vals))
            ax.scatter(pos + jitter, vals, alpha=0.4, s=12, color=c,
                       edgecolors="none", zorder=3)

        ax.set_xticks(positions)
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        ax.set_title(f"{atk} attacker(s)", fontsize=10)
        if i == 0:
            ax.set_ylabel("Mean latency (cycles)")

    fig.suptitle("Latency distribution by phase", fontsize=12, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    out = out_dir / f"1_latency_violins.{fmt}"
    fig.savefig(out, dpi=dpi)
    plt.close(fig)
    print(f"  Saved: {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 2: Latency delta % box plots — shared vs separate
# ─────────────────────────────────────────────────────────────────────────────

def plot_delta_pct_boxes(rows: List[dict], out_dir: Path, fmt: str, dpi: int):
    non_baseline = [r for r in rows if r["phase"] != "victim_baseline"]
    atk_counts = sorted_unique([r["attacker_threads"] for r in non_baseline])
    if not atk_counts:
        return

    fig, ax = plt.subplots(figsize=(max(6, 2 * len(atk_counts)), 5))

    width = 0.35
    positions_shared = []
    positions_separate = []
    data_shared = []
    data_separate = []

    for i, atk in enumerate(atk_counts):
        center = i * 1.0
        subset = [r for r in non_baseline if r["attacker_threads"] == atk]
        sh = extract_values([r for r in subset if r["phase"] == "victim_plus_shared"],
                            "latency_delta_pct_vs_baseline")
        sep = extract_values([r for r in subset if r["phase"] == "victim_plus_separate"],
                             "latency_delta_pct_vs_baseline")
        if len(sh) > 0:
            positions_shared.append(center - width / 2)
            data_shared.append(sh)
        if len(sep) > 0:
            positions_separate.append(center + width / 2)
            data_separate.append(sep)

    bp_kw = dict(widths=width * 0.8, patch_artist=True, notch=False,
                 medianprops=dict(color="black", linewidth=1.5))

    if data_shared:
        bp1 = ax.boxplot(data_shared, positions=positions_shared, **bp_kw)
        for patch in bp1["boxes"]:
            patch.set_facecolor(TWO_COLORS["victim_plus_shared"])
            patch.set_alpha(0.7)
    if data_separate:
        bp2 = ax.boxplot(data_separate, positions=positions_separate, **bp_kw)
        for patch in bp2["boxes"]:
            patch.set_facecolor(TWO_COLORS["victim_plus_separate"])
            patch.set_alpha(0.7)

    ax.axhline(0, color="gray", linewidth=0.8, linestyle="--", zorder=0)
    ax.set_xticks([i * 1.0 for i in range(len(atk_counts))])
    ax.set_xticklabels(atk_counts)
    ax.set_xlabel("Attacker threads")
    ax.set_ylabel("Latency delta vs baseline (%)")
    ax.set_title("Slowdown distribution: shared vs separate attackers",
                 fontsize=11, fontweight="bold")

    # Legend
    from matplotlib.patches import Patch
    legend_items = [
        Patch(facecolor=TWO_COLORS["victim_plus_shared"], alpha=0.7,
              label="Shared line"),
        Patch(facecolor=TWO_COLORS["victim_plus_separate"], alpha=0.7,
              label="Separate lines"),
    ]
    ax.legend(handles=legend_items, loc="best", fontsize=9)
    fig.tight_layout()
    out = out_dir / f"2_delta_pct_boxes.{fmt}"
    fig.savefig(out, dpi=dpi)
    plt.close(fig)
    print(f"  Saved: {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 3: Jain fairness violin plots
# ─────────────────────────────────────────────────────────────────────────────

def plot_fairness_violins(rows: List[dict], out_dir: Path, fmt: str, dpi: int):
    atk_counts = sorted_unique([r["attacker_threads"] for r in rows])
    n_panels = len(atk_counts)
    if n_panels == 0:
        return

    fig, axes = plt.subplots(1, n_panels, figsize=(4 * n_panels, 5), squeeze=False)
    axes = axes[0]

    for i, atk in enumerate(atk_counts):
        ax = axes[i]
        subset = [r for r in rows if r["attacker_threads"] == atk]
        positions = []
        datasets = []
        colors = []
        labels = []
        for j, phase in enumerate(PHASE_ORDER):
            vals = extract_values([r for r in subset if r["phase"] == phase],
                                  "jain_fairness")
            if len(vals) == 0:
                continue
            positions.append(j)
            datasets.append(vals)
            colors.append(PHASE_COLORS[phase])
            labels.append(PHASE_LABELS[phase])

        if not datasets:
            ax.set_title(f"{atk} attackers\n(no data)")
            continue

        parts = ax.violinplot(datasets, positions=positions, showmedians=True,
                              showextrema=True)
        for pc, c in zip(parts["bodies"], colors):
            pc.set_facecolor(c)
            pc.set_alpha(0.6)
        for key in ("cbars", "cmins", "cmaxes", "cmedians"):
            if key in parts:
                parts[key].set_color("black")

        rng = np.random.RandomState(42)
        for pos, vals, c in zip(positions, datasets, colors):
            jitter = rng.uniform(-0.08, 0.08, size=len(vals))
            ax.scatter(pos + jitter, vals, alpha=0.4, s=12, color=c,
                       edgecolors="none", zorder=3)

        ax.set_xticks(positions)
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        ax.set_title(f"{atk} attacker(s)", fontsize=10)
        if i == 0:
            ax.set_ylabel("Jain fairness index")

    fig.suptitle("Fairness distribution by phase", fontsize=12, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    out = out_dir / f"3_fairness_violins.{fmt}"
    fig.savefig(out, dpi=dpi)
    plt.close(fig)
    print(f"  Saved: {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 4: Paired difference — separate ratio minus shared ratio
# ─────────────────────────────────────────────────────────────────────────────

def plot_paired_difference(rows: List[dict], out_dir: Path, fmt: str, dpi: int):
    # Group by run_id to pair shared and separate measurements
    by_run = group_by(rows, ["run_id"])
    atk_deltas: Dict[str, List[float]] = defaultdict(list)

    for run_id, run_rows in by_run.items():
        shared = [r for r in run_rows if r["phase"] == "victim_plus_shared"]
        separate = [r for r in run_rows if r["phase"] == "victim_plus_separate"]
        if not shared or not separate:
            continue
        sh_ratio = safe_float(shared[0].get("latency_ratio_vs_baseline", ""))
        sep_ratio = safe_float(separate[0].get("latency_ratio_vs_baseline", ""))
        if math.isnan(sh_ratio) or math.isnan(sep_ratio):
            continue
        atk = shared[0]["attacker_threads"]
        atk_deltas[atk].append(sep_ratio - sh_ratio)

    atk_counts = sorted_unique(list(atk_deltas.keys()))
    if not atk_counts:
        return

    fig, ax = plt.subplots(figsize=(max(6, 1.5 * len(atk_counts)), 5))

    data = [np.array(atk_deltas[a]) for a in atk_counts]
    bp = ax.boxplot(data, positions=range(len(atk_counts)), widths=0.5,
                    patch_artist=True, notch=False,
                    medianprops=dict(color="black", linewidth=1.5))
    for patch in bp["boxes"]:
        patch.set_facecolor("#9B59B6")
        patch.set_alpha(0.6)

    # Overlay points
    rng = np.random.RandomState(42)
    for i, vals in enumerate(data):
        jitter = rng.uniform(-0.12, 0.12, size=len(vals))
        ax.scatter(i + jitter, vals, alpha=0.5, s=14, color="#7D3C98",
                   edgecolors="none", zorder=3)

    ax.axhline(0, color="gray", linewidth=0.8, linestyle="--", zorder=0)
    ax.set_xticks(range(len(atk_counts)))
    ax.set_xticklabels(atk_counts)
    ax.set_xlabel("Attacker threads")
    ax.set_ylabel("Separate ratio − Shared ratio")
    ax.set_title("Paired difference: separate vs shared attackers\n"
                 "(>0 means separate is slower)",
                 fontsize=11, fontweight="bold")
    fig.tight_layout()
    out = out_dir / f"4_paired_difference.{fmt}"
    fig.savefig(out, dpi=dpi)
    plt.close(fig)
    print(f"  Saved: {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 5: Per-seed-core latency ratio heatmaps
# ─────────────────────────────────────────────────────────────────────────────

def plot_seed_heatmaps(rows: List[dict], out_dir: Path, fmt: str, dpi: int):
    non_baseline = [r for r in rows if r["phase"] != "victim_baseline"]
    if not non_baseline:
        return

    seeds = sorted_unique([r["seed_core"] for r in non_baseline])
    atk_counts = sorted_unique([r["attacker_threads"] for r in non_baseline])
    if len(seeds) < 2 or len(atk_counts) < 1:
        # Not enough variation to make a heatmap useful
        return

    fig, axes = plt.subplots(1, 2, figsize=(6 * 2, max(3, 0.6 * len(seeds))),
                             squeeze=False)
    axes = axes[0]

    for ax_idx, phase in enumerate(["victim_plus_shared", "victim_plus_separate"]):
        ax = axes[ax_idx]
        phase_rows = [r for r in non_baseline if r["phase"] == phase]
        grouped = group_by(phase_rows, ["seed_core", "attacker_threads"])

        matrix = np.full((len(seeds), len(atk_counts)), np.nan)
        for si, s in enumerate(seeds):
            for ai, a in enumerate(atk_counts):
                vals = extract_values(grouped.get((s, a), []),
                                      "latency_ratio_vs_baseline")
                if len(vals) > 0:
                    matrix[si, ai] = np.mean(vals)

        # Determine color range symmetrically around 1.0
        valid = matrix[~np.isnan(matrix)]
        if len(valid) == 0:
            ax.set_title(f"{TWO_LABELS[phase]}\n(no data)")
            continue
        max_dev = max(abs(valid.max() - 1.0), abs(valid.min() - 1.0), 0.05)
        vmin, vmax = 1.0 - max_dev, 1.0 + max_dev

        im = ax.imshow(matrix, aspect="auto", cmap="RdYlGn_r",
                        vmin=vmin, vmax=vmax, interpolation="nearest")
        ax.set_xticks(range(len(atk_counts)))
        ax.set_xticklabels(atk_counts, fontsize=9)
        ax.set_yticks(range(len(seeds)))
        ax.set_yticklabels(seeds, fontsize=9)
        ax.set_xlabel("Attacker threads")
        ax.set_ylabel("Seed core")
        ax.set_title(TWO_LABELS[phase], fontsize=10, fontweight="bold")

        # Annotate cells
        for si in range(len(seeds)):
            for ai in range(len(atk_counts)):
                v = matrix[si, ai]
                if not np.isnan(v):
                    ax.text(ai, si, f"{v:.3f}", ha="center", va="center",
                            fontsize=7, color="black")
        fig.colorbar(im, ax=ax, shrink=0.8, label="Latency ratio vs baseline")

    fig.suptitle("Latency ratio by seed core placement",
                 fontsize=12, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    out = out_dir / f"5_seed_core_heatmap.{fmt}"
    fig.savefig(out, dpi=dpi)
    plt.close(fig)
    print(f"  Saved: {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Plot 6: Success rate strip plot
# ─────────────────────────────────────────────────────────────────────────────

def plot_success_rate_strip(rows: List[dict], out_dir: Path, fmt: str, dpi: int):
    atk_counts = sorted_unique([r["attacker_threads"] for r in rows])
    if not atk_counts:
        return

    fig, ax = plt.subplots(figsize=(max(6, 2 * len(atk_counts)), 5))

    rng = np.random.RandomState(42)
    spacing = 1.0
    offsets = {
        "victim_baseline": -0.25,
        "victim_plus_shared": 0.0,
        "victim_plus_separate": 0.25,
    }

    for i, atk in enumerate(atk_counts):
        subset = [r for r in rows if r["attacker_threads"] == atk]
        for phase in PHASE_ORDER:
            vals = extract_values([r for r in subset if r["phase"] == phase],
                                  "success_rate")
            if len(vals) == 0:
                continue
            x = i * spacing + offsets[phase]
            jitter = rng.uniform(-0.06, 0.06, size=len(vals))
            ax.scatter(x + jitter, vals, alpha=0.6, s=20,
                       color=PHASE_COLORS[phase], edgecolors="none", zorder=3,
                       label=PHASE_LABELS[phase] if i == 0 else None)

    ax.set_xticks([i * spacing for i in range(len(atk_counts))])
    ax.set_xticklabels(atk_counts)
    ax.set_xlabel("Attacker threads")
    ax.set_ylabel("Success rate (%)")
    ax.set_title("Success rate distribution by phase",
                 fontsize=11, fontweight="bold")
    ax.legend(loc="best", fontsize=9)
    fig.tight_layout()
    out = out_dir / f"6_success_rate_strip.{fmt}"
    fig.savefig(out, dpi=dpi)
    plt.close(fig)
    print(f"  Saved: {out}")


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    input_path = Path(args.input)
    out_dir = Path(args.out_dir) if args.out_dir else input_path.parent / "distribution_plots"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = read_csv(input_path)
    if not rows:
        print("ERROR: No data rows found.", file=sys.stderr)
        sys.exit(1)

    # Summary
    phases_found = sorted_unique([r["phase"] for r in rows])
    atk_counts = sorted_unique([r["attacker_threads"] for r in rows])
    seeds = sorted_unique([r.get("seed_core", "?") for r in rows])
    print(f"Loaded {len(rows)} rows from {input_path}")
    print(f"  Phases: {phases_found}")
    print(f"  Attacker thread counts: {atk_counts}")
    print(f"  Seed cores: {seeds}")
    print(f"  Output dir: {out_dir}")
    print()

    print("Generating plots...")
    plot_latency_violins(rows, out_dir, args.format, args.dpi)
    plot_delta_pct_boxes(rows, out_dir, args.format, args.dpi)
    plot_fairness_violins(rows, out_dir, args.format, args.dpi)
    plot_paired_difference(rows, out_dir, args.format, args.dpi)
    plot_seed_heatmaps(rows, out_dir, args.format, args.dpi)
    plot_success_rate_strip(rows, out_dir, args.format, args.dpi)
    print("\nDone.")


if __name__ == "__main__":
    main()
