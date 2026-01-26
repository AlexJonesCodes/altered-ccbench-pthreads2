#!/usr/bin/env python3
"""
Placement visualisation with selectable axis ordering.

Input CSV (from non-contention runner):
  test_id,seed_thread,worker_thread,latency

This script produces:
- Heatmap per test_id:
    rows = worker_thread, cols = seed_thread (-b), color = latency (cycles)
    Axis order is selectable:
      * default: numeric ascending
      * XEON_E5_2630V3_ORDER: 0..7, 16..23, 8..15, 24..31 (labels outside 0..31 appended ascending)
      * XEON_GOLD_6142_ORDER: even cores ascending, then odd cores ascending (e.g., 0,2,4,...,1,3,5,...)
        (labels outside any expected range are naturally included and ordered by parity then value)
    Ticks and tick labels show on all four sides. Each cell has a visible border grid.

- Grouped bar chart:
    x = worker_thread (same selected order),
    y = fairness (Jain’s index) across seeds for that worker,
    one bar per test_id per worker (different colors, legend).

Select exactly one ordering mode by setting the booleans below, or set both False for default.
"""

import os
from typing import List

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Optional: test name mapping
TEST_NAME_MAP = None
try:
    from test_nums_to_names import NUM_TO_TEST
    TEST_NAME_MAP = NUM_TO_TEST
except Exception:
    TEST_NAME_MAP = None

# ==============================
# Configuration
# ==============================
base_dir = "./Xeon_Gold_6142/"
INPUT_CSV = os.path.join(base_dir, "noncontention_latency.csv")

OUT_DIR = os.path.join(base_dir, "noncontention_latency_plots")
HEATMAP_PREFIX = "heatmap_latency_test"
GROUPED_BARS_PNG = "fairness_across_seeds_grouped.png"

FIG_DPI = 140

# Select one ordering mode (mutually exclusive). If both False => default ascending.
XEON_E5_2630V3_ORDER = False  # 0..7, 16..23, 8..15, 24..31
XEON_GOLD_6142_ORDER = True   # even cores ascending, then odd cores ascending

# White theme with black axes/spines
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

def test_label(tid: int) -> str:
    try:
        if TEST_NAME_MAP is not None and 0 <= int(tid) < len(TEST_NAME_MAP):
            return str(TEST_NAME_MAP[int(tid)])
    except Exception:
        pass
    return f"test {int(tid)}"

def enforce_white_theme(ax):
    ax.set_facecolor("white")
    for spine in ax.spines.values():
        spine.set_edgecolor("black")
        spine.set_linewidth(1.0)
    ax.tick_params(colors="black")

def jain(values: np.ndarray) -> float:
    """Jain’s fairness index over a 1D array of non-negative values."""
    vals = np.asarray(values, dtype=float)
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return np.nan
    s = np.sum(vals)
    s2 = np.sum(vals * vals)
    n = vals.size
    if s2 <= 0:
        return np.nan
    return (s * s) / (n * s2)

def load_and_prepare(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"CSV not found: {path}")
    df = pd.read_csv(path)
    # Ensure numeric
    for c in ["test_id", "seed_thread", "worker_thread", "latency"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    # Drop any incomplete
    df = df.dropna(subset=["test_id", "seed_thread", "worker_thread", "latency"]).copy()
    df["test_id"] = df["test_id"].astype(int)
    df["seed_thread"] = df["seed_thread"].astype(int)
    df["worker_thread"] = df["worker_thread"].astype(int)
    return df

# ----- Ordering modes -----

def e5v3_target_order() -> List[int]:
    """Target order: 0..7, 16..23, 8..15, 24..31."""
    return list(range(0, 8)) + list(range(16, 24)) + list(range(8, 16)) + list(range(24, 32))

def gold6142_even_odd_order(labels: List[int]) -> List[int]:
    """
    Even-then-odd ordering for Xeon Gold 6142:
    evens ascending first, then odds ascending. Works for any integer label set.
    """
    evens = sorted([x for x in labels if x % 2 == 0])
    odds  = sorted([x for x in labels if x % 2 == 1])
    return evens + odds

def reorder_for_mode(labels: List[int]) -> List[int]:
    """
    Reorder axis labels according to the selected mode.
    Only labels present in 'labels' are kept (no insertion).
    If both order flags are False -> default ascending.
    """
    labels_unique_sorted = sorted(set(int(x) for x in labels))
    if XEON_E5_2630V3_ORDER and XEON_GOLD_6142_ORDER:
        raise ValueError("Select only one ordering mode: set either XEON_E5_2630V3_ORDER or XEON_GOLD_6142_ORDER to True, not both.")
    if XEON_E5_2630V3_ORDER:
        target = e5v3_target_order()
        return [x for x in target if x in labels_unique_sorted] + [x for x in labels_unique_sorted if x not in target]
    if XEON_GOLD_6142_ORDER:
        return gold6142_even_odd_order(labels_unique_sorted)
    return labels_unique_sorted

# ==============================
# Heatmaps per test
# ==============================

def plot_heatmaps(df: pd.DataFrame, out_dir: str) -> None:
    tests = sorted(df["test_id"].unique())
    for t in tests:
        sub = df[df["test_id"] == t].copy()
        if sub.empty:
            continue

        # Pivot: rows=worker, cols=seed, values=latency
        piv = sub.pivot_table(index="worker_thread",
                              columns="seed_thread",
                              values="latency",
                              aggfunc="mean")

        # Default linear order then apply selected mode
        row_order = reorder_for_mode(piv.index.astype(int).tolist())
        col_order = reorder_for_mode(piv.columns.astype(int).tolist())
        piv = piv.reindex(index=row_order, columns=col_order)

        n_rows, n_cols = piv.shape
        fig_h = max(5.0, 0.45 * n_rows)
        fig_w = max(9.0, 0.32 * n_cols + 6)
        fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=FIG_DPI)

        data = piv.to_numpy(dtype=float)
        cmap_obj = plt.get_cmap("viridis").copy()
        cmap_obj.set_bad(color="#eeeeee")

        finite_vals = data[np.isfinite(data)]
        if finite_vals.size:
            vmin = np.percentile(finite_vals, 5)
            vmax = np.percentile(finite_vals, 95)
            if vmin == vmax:
                vmin = finite_vals.min()
                vmax = finite_vals.max() if finite_vals.max() > vmin else vmin + 1.0
        else:
            vmin, vmax = None, None

        # Draw as grid of cells
        im = ax.imshow(data, aspect="equal", interpolation="nearest",
                       cmap=cmap_obj, vmin=vmin, vmax=vmax)

        # Major ticks/labels at cell centers
        ax.set_yticks(np.arange(n_rows))
        ax.set_yticklabels([str(x) for x in piv.index.tolist()])
        ax.set_xticks(np.arange(n_cols))
        ax.set_xticklabels([str(x) for x in piv.columns.tolist()], rotation=45, ha="right")

        # Cell borders via minor ticks
        ax.grid(False)
        ax.set_xticks(np.arange(n_cols + 1) - 0.5, minor=True)
        ax.set_yticks(np.arange(n_rows + 1) - 0.5, minor=True)
        ax.grid(which="minor", color="#aaaaaa", linestyle="-", linewidth=0.5)
        ax.tick_params(which="minor", bottom=False, left=False)

        # Ticks on all four sides with labels
        ax.tick_params(axis="both", which="both",
                       direction="out",
                       top=True, bottom=True, left=True, right=True,
                       labeltop=True, labelbottom=True, labelleft=True, labelright=True)
        ax.xaxis.set_ticks_position("both")
        ax.yaxis.set_ticks_position("both")

        # Title suffix for clarity
        if XEON_E5_2630V3_ORDER:
            suffix = " (order 0–7, 16–23, 8–15, 24–31)"
        elif XEON_GOLD_6142_ORDER:
            suffix = " (even-then-odd order)"
        else:
            suffix = " (ascending order)"
        ax.set_title(f"Latency heatmap — {test_label(t)} ({t}){suffix}")

        ax.set_xlabel("seed_thread (-b)")
        ax.set_ylabel("worker_thread")

        enforce_white_theme(ax)

        cbar = plt.colorbar(im, ax=ax, shrink=0.85)
        cbar.set_label("latency (cycles)", rotation=270, labelpad=15)

        fig.tight_layout()
        fname = f"{HEATMAP_PREFIX}{t}.png"
        fig.savefig(os.path.join(out_dir, fname), bbox_inches="tight")
        plt.close(fig)

# ==============================
# Grouped bars: fairness across seeds per worker
# ==============================

def plot_grouped_fairness(df: pd.DataFrame, out_dir: str) -> None:
    # Compute fairness per (test_id, worker_thread): Jain over latencies across all seeds
    rows = []
    for (t, w), g in df.groupby(["test_id", "worker_thread"]):
        vals = g["latency"].to_numpy(dtype=float)
        rows.append({"test_id": int(t), "worker_thread": int(w), "fairness": jain(vals)})
    fair = pd.DataFrame(rows)
    if fair.empty:
        return

    # Domain order for workers according to selected mode
    worker_order = reorder_for_mode(fair["worker_thread"].astype(int).unique().tolist())
    tests = sorted(fair["test_id"].unique())

    # Matrix: workers x tests
    mat = fair.pivot(index="worker_thread", columns="test_id", values="fairness")
    mat = mat.reindex(index=worker_order, columns=tests)

    n_workers = len(worker_order)
    n_tests = len(tests)
    x = np.arange(n_workers)

    # Bar sizing
    gap = 0.03
    bar_width = min(0.85 / max(n_tests, 1) - gap, 0.25)
    offsets = (np.arange(n_tests) - (n_tests - 1) / 2.0) * (bar_width + gap)

    cmap = plt.get_cmap("tab20")
    color_map = {t: cmap(i % 20) for i, t in enumerate(tests)}

    fig_w = max(14, 0.8 * n_workers)
    fig, ax = plt.subplots(figsize=(fig_w, 5.8), dpi=FIG_DPI)

    handles = []
    labels = []
    for i, t in enumerate(tests):
        heights = mat[t].to_numpy(dtype=float)
        h = ax.bar(x + offsets[i], heights, width=bar_width,
                   color=color_map[t], edgecolor="black", linewidth=0.3,
                   alpha=0.95, label=test_label(int(t)))
        handles.append(h[0])
        labels.append(test_label(int(t)))

    # Formatting
    ax.axhline(1.0, color="k", linestyle="--", linewidth=1, label="J=1 (perfect)")
    if XEON_E5_2630V3_ORDER:
        tsuffix = " (order 0–7, 16–23, 8–15, 24–31)"
    elif XEON_GOLD_6142_ORDER:
        tsuffix = " (even-then-odd order)"
    else:
        tsuffix = " (ascending order)"
    ax.set_title(f"Fairness (Jain) across seeds per worker thread{tsuffix}")
    ax.set_xlabel("worker_thread")
    ax.set_ylabel("fairness (Jain across seeds)")
    ax.set_xticks(x)
    ax.set_xticklabels([str(w) for w in worker_order], rotation=0, ha="center")

    with np.errstate(all="ignore"):
        min_val = np.nanmin(mat.to_numpy())
    ymin = min(0.9, float(min_val) - 0.02) if np.isfinite(min_val) else 0.9
    ax.set_ylim(ymin, 1.02)
    ax.grid(True, axis="y", alpha=0.3)
    enforce_white_theme(ax)

    # Legend outside
    jline = plt.Line2D([0], [0], color='k', linestyle='--', linewidth=1)
    ax.legend(handles + [jline], labels + ["J=1 (perfect)"],
              loc="center left", bbox_to_anchor=(1.02, 0.5), borderaxespad=0.)

    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, GROUPED_BARS_PNG), bbox_inches="tight")
    plt.close(fig)

# ==============================
# Main
# ==============================

def main():
    ensure_dir(OUT_DIR)

    # Validate ordering selection early
    if XEON_E5_2630V3_ORDER and XEON_GOLD_6142_ORDER:
        raise SystemExit("Error: select only one ordering mode. Set either XEON_E5_2630V3_ORDER or XEON_GOLD_6142_ORDER to True, not both.")

    df = load_and_prepare(INPUT_CSV)

    # Heatmaps per test_id
    plot_heatmaps(df, OUT_DIR)

    # Grouped bars of fairness across seeds per worker
    plot_grouped_fairness(df, OUT_DIR)

    print(f"Saved plots to: {OUT_DIR}")

if __name__ == "__main__":
    main()
