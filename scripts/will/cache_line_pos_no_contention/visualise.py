#!/usr/bin/env python3
"""
Placement visualisation with selectable axis ordering (with test name mapping).

Input CSV (from non-contention runner):
  Required columns: test_id, seed_thread, worker_thread
  Metric columns supported:
    - latency_b4  (Common-start latency, B4 -> success)
    - pfd_avg     (Cross-core summary average)

This script produces TWO sets of plots (without changing the core visualisations):
- Using B4 data      -> saved under: <OUT_BASE>/b4/
- Using PFD (avg)    -> saved under: <OUT_BASE>/cross_core_summary/

Each set contains:
- Heatmap per test_id
- Line plot: Jain fairness across seeds for each worker
- Line plot: Jain fairness across workers for each seed

Note: Jain fairness is computed on inverse-latency utilities (xi = 1 / latency_i),
so that smaller latency corresponds to larger utility as per Jain’s definition.
"""

import os
import re
from typing import List

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

# Try to import test name mapping
TEST_NAME_MAP = None
try:
    from test_nums_to_name import NUM_TO_TEST
    TEST_NAME_MAP = NUM_TO_TEST
except Exception:
    try:
        from test_nums_to_names import NUM_TO_TEST
        TEST_NAME_MAP = NUM_TO_TEST
    except Exception:
        TEST_NAME_MAP = None

# ==============================
# Configuration
# ==============================
processor_name = "r53600"

base_dir = "./" + processor_name + "/"
INPUT_CSV = os.path.join(base_dir, "noncontention_latency.csv")

OUT_BASE = os.path.join(base_dir, "noncontention_latency_plots")
OUT_DIR_B4 = os.path.join(OUT_BASE, "b4")
OUT_DIR_CCS = os.path.join(OUT_BASE, "cross_core_summary")

HEATMAP_PREFIX = "heatmap_latency_test"
GROUPED_BARS_SEEDS_PNG = "fairness_across_seeds.png"
GROUPED_BARS_WORKERS_PNG = "fairness_across_workers.png"

FIG_DPI = 140

XEON_GOLD_6142_ORDER = False
if processor_name == "Xeon_Gold_6142":
    XEON_GOLD_6142_ORDER = True

XEON_E5_2630V3_ORDER = False
if processor_name == "Xeon_E5_2630V3":
    XEON_E5_2630V3_ORDER = True

plt.style.use("default")
plt.rcParams.update({
    "figure.facecolor":  "white",
    "axes.facecolor":    "white",
    "savefig.facecolor": "white",
    "axes.edgecolor":    "black",
    "axes.labelcolor":   "black",
    "xtick.color":       "black",
    "ytick.color":       "black",
    "axes.grid":         True,
    "grid.color":        "#dddddd",
})

# ==============================
# Helpers
# ==============================

def ensure_dir(d: str) -> None:
    os.makedirs(d, exist_ok=True)

def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)

def test_label(tid: int) -> str:
    try:
        if TEST_NAME_MAP is not None and 0 <= tid < len(TEST_NAME_MAP):
            return str(TEST_NAME_MAP[tid])
    except Exception:
        pass
    return f"test {tid}"

def enforce_white_theme(ax):
    ax.set_facecolor("white")
    for spine in ax.spines.values():
        spine.set_edgecolor("black")
        spine.set_linewidth(1.0)
    ax.tick_params(colors="black")

def jain(values: np.ndarray) -> float:
    """
    Jain's fairness index computed on inverse-latency utilities:
      xi = 1 / latency_i, for latency_i > 0 and finite.
    """
    lat = np.asarray(values, dtype=float)
    lat = lat[np.isfinite(lat) & (lat > 0)]
    if lat.size == 0:
        return np.nan
    x = 1.0 / lat
    s = np.sum(x)
    s2 = np.sum(x * x)
    if s2 <= 0:
        return np.nan
    return (s * s) / (x.size * s2)

def load_and_prepare(path: str) -> pd.DataFrame:
    """
    Load CSV and ensure numeric types. Accepts either/both metric columns:
      - latency_b4
      - pfd_avg
    Returns a DataFrame with at least:
      test_id (int), seed_thread (int), worker_thread (int),
      and any available of latency_b4, pfd_avg (float).
    """
    df = pd.read_csv(path)

    required = ["test_id", "seed_thread", "worker_thread"]
    for c in required:
        if c not in df.columns:
            raise ValueError(f"Missing required column '{c}' in {path}")

    # Ensure numeric
    df["test_id"] = pd.to_numeric(df["test_id"], errors="coerce")
    df["seed_thread"] = pd.to_numeric(df["seed_thread"], errors="coerce")
    df["worker_thread"] = pd.to_numeric(df["worker_thread"], errors="coerce")

    # Ensure metric columns exist (create NaN if missing)
    if "latency_b4" not in df.columns:
        df["latency_b4"] = np.nan
    else:
        df["latency_b4"] = pd.to_numeric(df["latency_b4"], errors="coerce")

    if "pfd_avg" in df.columns:
        df["pfd_avg"] = pd.to_numeric(df["pfd_avg"], errors="coerce")

    # Drop rows missing required identifiers
    df = df.dropna(subset=["test_id", "seed_thread", "worker_thread"])
    # Cast IDs to int
    df["test_id"] = df["test_id"].astype(int)
    df["seed_thread"] = df["seed_thread"].astype(int)
    df["worker_thread"] = df["worker_thread"].astype(int)

    return df

# ----- Ordering -----

def e5v3_target_order() -> List[int]:
    return list(range(0, 8)) + list(range(16, 24)) + list(range(8, 16)) + list(range(24, 32))

def gold6142_even_odd_order(labels: List[int]) -> List[int]:
    evens = sorted(x for x in labels if x % 2 == 0)
    odds = sorted(x for x in labels if x % 2 == 1)
    return evens + odds

def reorder_for_mode(labels: List[int]) -> List[int]:
    labels = sorted(set(labels))
    if XEON_E5_2630V3_ORDER:
        tgt = e5v3_target_order()
        return [x for x in tgt if x in labels] + [x for x in labels if x not in tgt]
    if XEON_GOLD_6142_ORDER:
        return gold6142_even_odd_order(labels)
    return labels

# ==============================
# Heatmaps
# ==============================

def plot_heatmaps(df: pd.DataFrame, out_dir: str) -> None:
    for t in sorted(df["test_id"].unique()):
        sub = df[df["test_id"] == t]
        piv = sub.pivot_table(
            index="worker_thread",
            columns="seed_thread",
            values="latency_b4",
            aggfunc="mean",
        )

        piv = piv.reindex(
            index=reorder_for_mode(piv.index.tolist()),
            columns=reorder_for_mode(piv.columns.tolist()),
        )

        fig, ax = plt.subplots(
            figsize=(max(9, 0.35 * piv.shape[1] + 6),
                     max(5, 0.45 * piv.shape[0])),
            dpi=FIG_DPI,
        )

        im = ax.imshow(piv.to_numpy(), cmap="viridis", interpolation="nearest")
        ax.set_xticks(np.arange(piv.shape[1]))
        ax.set_yticks(np.arange(piv.shape[0]))
        ax.set_xticklabels(piv.columns)
        ax.set_yticklabels(piv.index)
        # Minor ticks at cell boundaries
        ax.set_xticks(np.arange(-0.5, piv.shape[1], 1), minor=True)
        ax.set_yticks(np.arange(-0.5, piv.shape[0], 1), minor=True)

        # Grid aligned to cell boundaries
        ax.grid(which="minor", color="black", linestyle="-", linewidth=0.5)
        ax.grid(which="major", visible=False)

        ax.set_title(f"{processor_name}: Latency Heatmap — {test_label(t)}")
        ax.set_xlabel("Seed Core (-b)")
        ax.set_ylabel("Worker Core")
        ax.tick_params(top=True, labeltop=True, bottom=True, labelbottom=True)
        ax.tick_params(left=True, labelleft=True, right=True, labelright=True)

        enforce_white_theme(ax)
        plt.colorbar(im, ax=ax, shrink=0.85)

        fig.tight_layout()
        fig.savefig(
            os.path.join(out_dir, f"{HEATMAP_PREFIX}_{safe_name(test_label(t))}.png"),
            bbox_inches="tight",
            pad_inches=0.02,
        )
        plt.close(fig)

# ==============================
# Fairness plots (LINES)
# ==============================

def plot_fairness_across_seeds(df: pd.DataFrame, out_dir: str) -> None:
    rows = []
    for (t, w), g in df.groupby(["test_id", "worker_thread"]):
        rows.append((t, w, jain(g["latency_b4"])))

    fair = pd.DataFrame(rows, columns=["test_id", "worker_thread", "fairness"])
    mat = fair.pivot(
        index="worker_thread",
        columns="test_id",
        values="fairness",
    )

    mat = mat.reindex(index=reorder_for_mode(mat.index.tolist()))

    fig, ax = plt.subplots(figsize=(10, 5.8), dpi=FIG_DPI)
    x = np.arange(len(mat.index))
    cmap = plt.get_cmap("tab20")

    for i, t in enumerate(mat.columns):
        ax.plot(x, mat[t], marker="o", linewidth=2,
                label=test_label(int(t)), color=cmap(i % 20))

    ax.axhline(1.0, linestyle="--", color="black", linewidth=1)
    ax.set_ylim(0.0, 1.1)
    ax.yaxis.set_major_locator(MultipleLocator(0.1))

    ax.set_title(f"{processor_name}: Jain Fairness Index (1/latency) Across Seeds")
    ax.set_xlabel("Worker Core")
    ax.set_ylabel("Jain Fairness Index")
    ax.set_xticks(x)
    ax.set_xticklabels(mat.index)
    if XEON_GOLD_6142_ORDER:
        ax.tick_params(axis="x", labelsize=7)

    ax.margins(x=0)

    enforce_white_theme(ax)
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5))

    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, GROUPED_BARS_SEEDS_PNG), bbox_inches="tight")
    plt.close(fig)

def plot_fairness_across_workers(df: pd.DataFrame, out_dir: str) -> None:
    rows = []
    for (t, s), g in df.groupby(["test_id", "seed_thread"]):
        rows.append((t, s, jain(g["latency_b4"])))

    fair = pd.DataFrame(rows, columns=["test_id", "seed_thread", "fairness"])
    mat = fair.pivot(
        index="seed_thread",
        columns="test_id",
        values="fairness",
    )

    mat = mat.reindex(index=reorder_for_mode(mat.index.tolist()))

    fig, ax = plt.subplots(figsize=(10, 5.8), dpi=FIG_DPI)
    x = np.arange(len(mat.index))
    cmap = plt.get_cmap("tab20")

    for i, t in enumerate(mat.columns):
        ax.plot(x, mat[t], marker="o", linewidth=2,
                label=test_label(int(t)), color=cmap(i % 20))

    ax.axhline(1.0, linestyle="--", color="black", linewidth=1)
    ax.set_ylim(0.0, 1.1)
    ax.yaxis.set_major_locator(MultipleLocator(0.1))

    ax.set_title(f"{processor_name}: Jain Fairness Index (1/latency) Across Workers per Seed")
    ax.set_xlabel("Seed Thread (-b)")
    ax.set_ylabel("Jain Fairness Index")
    ax.set_xticks(x)
    ax.set_xticklabels(mat.index)
    if XEON_GOLD_6142_ORDER:
        ax.tick_params(axis="x", labelsize=7)
    ax.margins(x=0)

    enforce_white_theme(ax)
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5))

    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, GROUPED_BARS_WORKERS_PNG), bbox_inches="tight")
    plt.close(fig)

# ==============================
# Main
# ==============================

def main():
    ensure_dir(OUT_BASE)
    ensure_dir(OUT_DIR_B4)
    ensure_dir(OUT_DIR_CCS)

    df_all = load_and_prepare(INPUT_CSV)

    # -------- B4 dataset (as-is) --------
    df_b4 = df_all.copy()
    # We will drop rows where latency_b4 is NaN for plotting, but allow partial coverage
    df_b4_plot = df_b4.dropna(subset=["latency_b4"])
    if not df_b4_plot.empty:
        plot_heatmaps(df_b4_plot, OUT_DIR_B4)
        plot_fairness_across_seeds(df_b4_plot, OUT_DIR_B4)
        plot_fairness_across_workers(df_b4_plot, OUT_DIR_B4)
        print(f"Saved B4 plots to: {OUT_DIR_B4}")
    else:
        print("No B4 data (latency_b4) found; skipping B4 plots.")

    # -------- Cross-core summary dataset (pfd_avg -> reuse visuals) --------
    if "pfd_avg" in df_all.columns:
        df_ccs = df_all.copy()
        # Reuse the plotting functions by mapping pfd_avg into 'latency_b4'
        df_ccs["latency_b4"] = df_ccs["pfd_avg"]
        df_ccs_plot = df_ccs.dropna(subset=["latency_b4"])
        if not df_ccs_plot.empty:
            plot_heatmaps(df_ccs_plot, OUT_DIR_CCS)
            plot_fairness_across_seeds(df_ccs_plot, OUT_DIR_CCS)
            plot_fairness_across_workers(df_ccs_plot, OUT_DIR_CCS)
            print(f"Saved Cross-core Summary plots to: {OUT_DIR_CCS}")
        else:
            print("pfd_avg column present but empty after cleaning; skipping Cross-core Summary plots.")
    else:
        print("No Cross-core Summary data (pfd_avg) found; skipping Cross-core Summary plots.")

if __name__ == "__main__":
    main()
