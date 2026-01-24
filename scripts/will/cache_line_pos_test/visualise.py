#!/usr/bin/env python3
"""
Plot fairness (Jain's index) vs pinned thread (-b) for placement-latency runs.

Input CSV schema (one row per run):
  test_id,pinned_thread,latency_0,latency_1,...,latency_N

What this script does:
- Loads the CSV with pandas.
- Computes Jain’s fairness index across the latency_i columns for each row
  (higher is fairer; 1.0 means all threads have equal latency for that run).
- Aggregates fairness by (test_id, pinned_thread) (mean if duplicates).
- Plots ONE line chart (x = pinned_thread, y = fairness), with one colored line per test_id.
- Uses human-readable test names from test_nums_to_names.NUM_TO_TEST if available.

Edit the INPUT_CSV and OUTPUT paths below to your setup.
"""

import os
from typing import List

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Try to import test names mapping (optional)
TEST_NAME_MAP = None
try:
    from test_nums_to_names import NUM_TO_TEST
    TEST_NAME_MAP = NUM_TO_TEST
except Exception:
    TEST_NAME_MAP = None

# ==============================
# Configuration (edit in code)
# ==============================
base_dir = "./results/r53600/"
INPUT_CSV = os.path.join(base_dir, "placement_latency.csv")

OUT_DIR = os.path.join(base_dir, "placement_plots")
OUTPUT_PNG = os.path.join(OUT_DIR, "fairness_vs_seed.png")

FIG_DPI = 140

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

def ensure_dir(d: str) -> None:
    os.makedirs(d, exist_ok=True)

def test_label(tid: int) -> str:
    try:
        if TEST_NAME_MAP is not None and 0 <= int(tid) < len(TEST_NAME_MAP):
            return str(TEST_NAME_MAP[int(tid)])
    except Exception:
        pass
    return f"test {int(tid)}"

def jain(values: np.ndarray) -> float:
    """Jain’s fairness index over an array of non-negative values."""
    vals = np.asarray(values, dtype=float)
    vals = vals[np.isfinite(vals)]        # drop NaN/inf if any
    if vals.size == 0:
        return np.nan
    s = np.sum(vals)
    s2 = np.sum(vals * vals)
    n = vals.size
    if s2 <= 0:
        return np.nan
    return (s * s) / (n * s2)

def enforce_white_theme(ax):
    ax.set_facecolor("white")
    for spine in ax.spines.values():
        spine.set_edgecolor("black")
        spine.set_linewidth(1.0)
    ax.tick_params(colors="black")

def main():
    ensure_dir(OUT_DIR)
    if not os.path.exists(INPUT_CSV):
        raise FileNotFoundError(f"CSV not found: {INPUT_CSV}")

    df = pd.read_csv(INPUT_CSV)

    # Ensure numeric types
    for c in ["test_id", "pinned_thread"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Detect latency columns automatically
    latency_cols = [c for c in df.columns if c.startswith("latency_")]
    if not latency_cols:
        raise ValueError("No latency_* columns found in the CSV.")

    # Compute Jain fairness per row
    def row_fairness(row) -> float:
        vals = row[latency_cols].to_numpy(dtype=float)
        return jain(vals)

    df["fairness"] = df.apply(row_fairness, axis=1)

    # Aggregate fairness by (test_id, pinned_thread)
    g = (df.groupby(["test_id", "pinned_thread"], as_index=False)
            .agg(fairness=("fairness", "mean")))

    # Build the x-axis domain: all pinned_thread values in sorted order
    x_domain = sorted(g["pinned_thread"].unique())

    # One line per test_id, aligned on the common x_domain
    tests = sorted(g["test_id"].unique())
    cmap = plt.get_cmap("tab20")
    colors = {t: cmap(i % 20) for i, t in enumerate(tests)}

    fig_w = max(10, 0.5 * len(x_domain))
    fig, ax = plt.subplots(figsize=(fig_w, 5.0), dpi=FIG_DPI)

    for i, t in enumerate(tests):
        sub = g[g["test_id"] == t].set_index("pinned_thread")
        y = [sub.loc[x, "fairness"] if x in sub.index else np.nan for x in x_domain]
        ax.plot(x_domain, y, "-o", markersize=4, linewidth=1.8, color=colors[t], label=test_label(int(t)))

    # Formatting
    ax.axhline(1.0, color="k", linestyle="--", linewidth=1, label="J=1 (perfect fairness)")
    ax.set_title("Fairness (Jain's index) vs seeded (-b) core")
    ax.set_xlabel("pinned_thread (-b)")
    ax.set_ylabel("Jain fairness across threads")

    # Y range with gentle padding
    with np.errstate(all="ignore"):
        min_val = np.nanmin(g["fairness"].to_numpy())
    ymin = min(0.9, float(min_val) - 0.02) if np.isfinite(min_val) else 0.9
    ax.set_ylim(ymin, 1.02)

    ax.grid(True, axis="both", alpha=0.3)
    enforce_white_theme(ax)

    # Legend outside on the right
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), borderaxespad=0.)

    fig.tight_layout()
    fig.savefig(OUTPUT_PNG, bbox_inches="tight")
    print(f"Saved: {OUTPUT_PNG}")

if __name__ == "__main__":
    main()
