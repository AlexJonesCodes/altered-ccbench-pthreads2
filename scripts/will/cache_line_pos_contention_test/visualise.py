#!/usr/bin/env python3
"""
Placement fairness visualisation (with test name mapping and selectable core ordering)

Input CSV (one row per run):
  test_id,pinned_thread,latency_0,latency_1,...,latency_N

This script produces two line charts:
1) Fairness vs pinned thread (-b):
   - x = pinned_thread (-b) (order selectable via toggles)
   - y = Jain’s fairness across all worker latencies for that run (per test)
   - One colored line per test_id (legend uses human-readable names if available)

2) Fairness vs worker thread:
   - x = worker_thread index (taken from latency_* column suffix; order selectable via toggles)
   - y = Jain’s fairness across pinned threads for that worker (per test)
   - One colored line per test_id

Core ordering modes (mutually exclusive):
- default ascending:     numeric ascending
- XEON_E5_2630V3_ORDER:  0..7, 16..23, 8..15, 24..31 (others appended ascending)
- XEON_GOLD_6142_ORDER:  even cores ascending, then odd cores ascending (0,2,4,...,1,3,5,...)

Titles annotate the chosen non-linear ordering.

Test name mapping:
- If test_nums_to_name.py (or test_nums_to_names.py) is present with NUM_TO_TEST list,
  test_id values are rendered as human-readable names in titles/legends and filenames.
"""

import os
from typing import List, Dict

from matplotlib.ticker import FixedLocator
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Try to import test names mapping (optional)
TEST_NAME_MAP = None
try:
    from test_nums_to_name import NUM_TO_TEST  # preferred
    TEST_NAME_MAP = NUM_TO_TEST
except Exception:
    try:
        from test_nums_to_names import NUM_TO_TEST  # fallback
        TEST_NAME_MAP = NUM_TO_TEST
    except Exception:
        TEST_NAME_MAP = None

# ==============================
# Configuration (edit in code)
# ==============================
base_dir = "./results/XeonE5530/"
INPUT_CSV = os.path.join(base_dir, "placement_latency.csv")

OUT_DIR = os.path.join(base_dir, "placement_plots")
OUTPUT_PNG_SEED   = os.path.join(OUT_DIR, "fairness_vs_seed.png")
OUTPUT_PNG_WORKER = os.path.join(OUT_DIR, "fairness_vs_worker.png")

FIG_DPI = 140

# Core ordering toggles (mutually exclusive). If both False => ascending.
XEON_E5_2630V3_ORDER = False   # 0..7, 16..23, 8..15, 24..31 (others appended ascending)
XEON_GOLD_6142_ORDER = False   # even cores ascending, then odd cores ascending

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

def extract_latency_cols(df: pd.DataFrame) -> List[str]:
    cols = [c for c in df.columns if c.startswith("latency_")]
    if not cols:
        raise ValueError("No latency_* columns found in the CSV.")
    # Sort by numeric suffix
    def key(c: str) -> int:
        try:
            return int(c.split("_", 1)[1])
        except Exception:
            return 10**9
    cols.sort(key=key)
    return cols

# ----- Ordering modes -----

def e5v3_target_order() -> List[int]:
    """Target order: 0..7, 16..23, 8..15, 24..31."""
    return list(range(0, 8)) + list(range(16, 24)) + list(range(8, 16)) + list(range(24, 32))

def gold6142_even_odd_order(labels: List[int]) -> List[int]:
    """Even-then-odd ordering: evens ascending then odds ascending."""
    evens = sorted([x for x in labels if x % 2 == 0])
    odds  = sorted([x for x in labels if x % 2 == 1])
    return evens + odds

def reorder_for_mode(labels: List[int]) -> List[int]:
    """
    Reorder labels according to selected mode. Only labels present are kept.
    If both toggles False -> ascending.
    """
    uniq = sorted(set(int(x) for x in labels))
    if XEON_E5_2630V3_ORDER and XEON_GOLD_6142_ORDER:
        raise SystemExit("Select only one ordering mode: XEON_E5_2630V3_ORDER or XEON_GOLD_6142_ORDER.")
    if XEON_E5_2630V3_ORDER:
        target = e5v3_target_order()
        return [x for x in target if x in uniq] + [x for x in uniq if x not in target]
    if XEON_GOLD_6142_ORDER:
        return gold6142_even_odd_order(uniq)
    return uniq

def order_suffix() -> str:
    if XEON_E5_2630V3_ORDER or XEON_GOLD_6142_ORDER:
        return " (Ordered Socket 0 Threads, Socket 2 Threads)"
    return " (ascending order)"

# ==============================
# Fairness vs seed (B4)
# ==============================

def compute_fairness_vs_seed(df: pd.DataFrame, latency_cols: List[str]) -> pd.DataFrame:
    """Compute Jain fairness across worker latencies per row, then aggregate by (test_id, pinned_thread)."""
    work = df.copy()
    # Ensure numeric types present
    for c in ["test_id", "pinned_thread"]:
        if c in work.columns:
            work[c] = pd.to_numeric(work[c], errors="coerce")
    # Compute row fairness
    work["fairness_row"] = work[latency_cols].apply(lambda r: jain(r.to_numpy(dtype=float)), axis=1)
    # Aggregate by (test_id, pinned_thread)
    g = (work.groupby(["test_id", "pinned_thread"], as_index=False)
              .agg(fairness=("fairness_row", "mean")))
    return g

def plot_fairness_vs_seed(g: pd.DataFrame, out_path: str):
    # x domain reordered by mode
    x_domain = reorder_for_mode(sorted(g["pinned_thread"].unique()))
    tests = sorted(g["test_id"].unique())
    cmap = plt.get_cmap("tab20")
    colors: Dict[int, tuple] = {t: cmap(i % 20) for i, t in enumerate(tests)}

    fig_w = max(10, 0.5 * len(x_domain))
    fig, ax = plt.subplots(figsize=(fig_w, 5.0), dpi=FIG_DPI)

    for i, t in enumerate(tests):
        sub = g[g["test_id"] == t].set_index("pinned_thread")
        y = [sub.loc[x, "fairness"] if x in sub.index else np.nan for x in x_domain]
        ax.plot(x_domain, y, "-o", markersize=4, linewidth=1.8, color=colors[t], label=test_label(int(t)))

    # Formatting
    ax.axhline(1.0, color="k", linestyle="--", linewidth=1, label="J=1 (perfect fairness)")
    ax.set_title(f"Fairness (Jain) vs pinned thread (-b){order_suffix()}")
    ax.set_xlabel("pinned_thread (-b)")
    ax.set_ylabel("Jain fairness across worker threads")

    with np.errstate(all="ignore"):
        min_val = np.nanmin(g["fairness"].to_numpy())
    ymin = min(0.9, float(min_val) - 0.02) if np.isfinite(min_val) else 0.9
    ax.set_ylim(ymin, 1.02)

    ax.grid(True, axis="both", alpha=0.3)
    enforce_white_theme(ax)
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), borderaxespad=0.)
    ax.xaxis.set_major_locator(FixedLocator(x_domain))
    ax.set_xticklabels([str(x) for x in x_domain], rotation=0, ha="center")

    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    print(f"Saved: {out_path}")
    plt.close(fig)

# ==============================
# Fairness vs worker (across seeds)
# ==============================

def compute_fairness_vs_worker(df: pd.DataFrame, latency_cols: List[str]) -> pd.DataFrame:
    """
    For each test_id and worker index w (from latency_* suffix),
    compute Jain fairness across pinned_thread values for that worker:
      fairness_w = Jain( { latency_w for each seed row } ).
    """
    work = df.copy()
    work["test_id"] = pd.to_numeric(work["test_id"], errors="coerce").astype("Int64")
    tests = sorted([int(x) for x in work["test_id"].dropna().unique()])
    # Map worker index -> column
    idx_to_col = {}
    for c in latency_cols:
        try:
            idx = int(c.split("_", 1)[1])
            idx_to_col[idx] = c
        except Exception:
            continue
    worker_indices = reorder_for_mode(sorted(idx_to_col.keys()))

    rows = []
    for t in tests:
        sub = work[work["test_id"] == t]
        for w in worker_indices:
            col = idx_to_col[w]
            vals = pd.to_numeric(sub[col], errors="coerce").to_numpy(dtype=float)
            f = jain(vals)
            rows.append({"test_id": int(t), "worker": int(w), "fairness": f})
    return pd.DataFrame(rows)

def plot_fairness_vs_worker(gw: pd.DataFrame, out_path: str):
    worker_domain = reorder_for_mode(sorted(gw["worker"].unique()))
    tests = sorted(gw["test_id"].unique())
    cmap = plt.get_cmap("tab20")
    colors: Dict[int, tuple] = {t: cmap(i % 20) for i, t in enumerate(tests)}

    fig_w = max(10, 0.5 * len(worker_domain))
    fig, ax = plt.subplots(figsize=(fig_w, 5.0), dpi=FIG_DPI)

    for i, t in enumerate(tests):
        sub = gw[gw["test_id"] == t].set_index("worker")
        y = [sub.loc[x, "fairness"] if x in sub.index else np.nan for x in worker_domain]
        ax.plot(worker_domain, y, "-o", markersize=4, linewidth=1.8, color=colors[t], label=test_label(int(t)))

    ax.axhline(1.0, color="k", linestyle="--", linewidth=1, label="J=1 (perfect fairness)")
    ax.set_title(f"Jain Fairness Index Over Initial Seed Thread Data Location{order_suffix()}")
    ax.set_xlabel("Worker Thread")
    ax.set_ylabel("Jain fairness Index")

    with np.errstate(all="ignore"):
        min_val = np.nanmin(gw["fairness"].to_numpy())
    ymin = min(0.9, float(min_val) - 0.02) if np.isfinite(min_val) else 0.9
    ax.set_ylim(ymin, 1.02)

    ax.grid(True, axis="both", alpha=0.3)
    enforce_white_theme(ax)
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), borderaxespad=0.)
    ax.xaxis.set_major_locator(FixedLocator(worker_domain)) 
    ax.set_xticklabels([str(x) for x in worker_domain], rotation=0, ha="center")

    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    print(f"Saved: {out_path}")
    plt.close(fig)

# ==============================
# Main
# ==============================

def main():
    ensure_dir(OUT_DIR)
    if XEON_E5_2630V3_ORDER and XEON_GOLD_6142_ORDER:
        raise SystemExit("Error: select only one ordering mode (XEON_E5_2630V3_ORDER or XEON_GOLD_6142_ORDER).")

    if not os.path.exists(INPUT_CSV):
        raise FileNotFoundError(f"CSV not found: {INPUT_CSV}")

    df = pd.read_csv(INPUT_CSV)

    # Ensure numeric types for ID/seed columns
    for c in ["test_id", "pinned_thread"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    latency_cols = extract_latency_cols(df)

    # Fairness vs seed (-b)
    g_seed = compute_fairness_vs_seed(df, latency_cols)
    plot_fairness_vs_seed(g_seed, OUTPUT_PNG_SEED)

    # Fairness vs worker (across seeds)
    g_worker = compute_fairness_vs_worker(df, latency_cols)
    plot_fairness_vs_worker(g_worker, OUTPUT_PNG_WORKER)

if __name__ == "__main__":
    main()
