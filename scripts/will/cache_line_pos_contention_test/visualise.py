#!/usr/bin/env python3
"""
Placement fairness visualisation (with test name mapping and selectable core ordering)

Input CSV:
  test_id,pinned_thread,latency_0,latency_1,...,latency_N
"""

import os
from typing import List, Dict

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import FixedLocator, MultipleLocator

# ==============================
# Optional test name mapping
# ==============================
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

base_dir = "./results/" + processor_name + "/"
INPUT_CSV = os.path.join(base_dir, "placement_latency.csv")

OUT_DIR = os.path.join(base_dir, "placement_plots")
OUTPUT_PNG_SEED   = os.path.join(OUT_DIR, "fairness_vs_seed.png")
OUTPUT_PNG_WORKER = os.path.join(OUT_DIR, "fairness_vs_worker.png")

FIG_DPI = 140

# Ordering toggles derived from processor name
XEON_E5_2630V3_ORDER = processor_name == "XeonE5-2630v3"
XEON_GOLD_6142_ORDER = processor_name == "Xeon_Gold_6142"

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
    vals = np.asarray(values, dtype=float)
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return np.nan
    s = np.sum(vals)
    s2 = np.sum(vals * vals)
    return (s * s) / (vals.size * s2) if s2 > 0 else np.nan

def extract_latency_cols(df: pd.DataFrame) -> List[str]:
    cols = [c for c in df.columns if c.startswith("latency_")]
    cols.sort(key=lambda c: int(c.split("_")[1]))
    return cols

# ==============================
# Ordering logic
# ==============================

def e5v3_target_order() -> List[int]:
    return list(range(0, 8)) + list(range(16, 24)) + list(range(8, 16)) + list(range(24, 32))

def gold6142_even_odd_order(labels: List[int]) -> List[int]:
    evens = sorted(x for x in labels if x % 2 == 0)
    odds  = sorted(x for x in labels if x % 2 == 1)
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
# Fairness vs seed (-b)
# ==============================

def plot_fairness_vs_seed(df: pd.DataFrame, latency_cols: List[str]):
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "test_id": int(r["test_id"]),
            "pinned_thread": int(r["pinned_thread"]),
            "fairness": jain(r[latency_cols].to_numpy())
        })

    g = pd.DataFrame(rows).groupby(
        ["test_id", "pinned_thread"], as_index=False
    ).mean()

    x_domain = reorder_for_mode(sorted(g["pinned_thread"].unique()))
    tests = sorted(g["test_id"].unique())

    fig, ax = plt.subplots(figsize=(10, 5.8), dpi=FIG_DPI)
    cmap = plt.get_cmap("tab20")

    for i, t in enumerate(tests):
        sub = g[g["test_id"] == t].set_index("pinned_thread")
        y = [sub.loc[x, "fairness"] if x in sub.index else np.nan for x in x_domain]
        ax.plot(x_domain, y, "-o", linewidth=2, markersize=4,
                color=cmap(i % 20), label=test_label(t))

    ax.axhline(1.0, linestyle="--", color="black", linewidth=1)
    ax.set_ylim(0.0, 1.1)
    ax.yaxis.set_major_locator(MultipleLocator(0.1))

    ax.set_title(f"{processor_name}: Jain Fairness vs Pinned Thread")
    ax.set_xlabel("Pinned Thread (-b)")
    ax.set_ylabel("Jain Fairness Index")
    ax.set_xticks(x_domain)

    if XEON_GOLD_6142_ORDER:
        ax.tick_params(axis="x", labelsize=7)

    ax.margins(x=0)
    enforce_white_theme(ax)
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5))

    fig.tight_layout()
    fig.savefig(OUTPUT_PNG_SEED, bbox_inches="tight")
    plt.close(fig)

# ==============================
# Fairness vs worker
# ==============================

def plot_fairness_vs_worker(df: pd.DataFrame, latency_cols: List[str]):
    workers = reorder_for_mode(
        [int(c.split("_")[1]) for c in latency_cols]
    )
    tests = sorted(df["test_id"].unique())

    rows = []
    for t in tests:
        sub = df[df["test_id"] == t]
        for w in workers:
            vals = sub[f"latency_{w}"].to_numpy(dtype=float)
            rows.append({
                "test_id": t,
                "worker": w,
                "fairness": jain(vals)
            })

    g = pd.DataFrame(rows)

    fig, ax = plt.subplots(figsize=(10, 5.8), dpi=FIG_DPI)
    cmap = plt.get_cmap("tab20")

    for i, t in enumerate(tests):
        sub = g[g["test_id"] == t].set_index("worker")
        y = [sub.loc[w, "fairness"] for w in workers]
        ax.plot(workers, y, "-o", linewidth=2, markersize=4,
                color=cmap(i % 20), label=test_label(t))

    ax.axhline(1.0, linestyle="--", color="black", linewidth=1)
    ax.set_ylim(0.0, 1.1)
    ax.yaxis.set_major_locator(MultipleLocator(0.1))

    ax.set_title(f"{processor_name}: Jain Fairness vs Worker Thread")
    ax.set_xlabel("Worker Thread")
    ax.set_ylabel("Jain Fairness Index")
    ax.set_xticks(workers)

    if XEON_GOLD_6142_ORDER:
        ax.tick_params(axis="x", labelsize=7)

    ax.margins(x=0)
    enforce_white_theme(ax)
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5))

    fig.tight_layout()
    fig.savefig(OUTPUT_PNG_WORKER, bbox_inches="tight")
    plt.close(fig)

# ==============================
# Main
# ==============================

def main():
    ensure_dir(OUT_DIR)

    df = pd.read_csv(INPUT_CSV)
    df["test_id"] = pd.to_numeric(df["test_id"], errors="coerce")
    df["pinned_thread"] = pd.to_numeric(df["pinned_thread"], errors="coerce")

    latency_cols = extract_latency_cols(df)

    plot_fairness_vs_seed(df, latency_cols)
    plot_fairness_vs_worker(df, latency_cols)

    print(f"Saved plots to {OUT_DIR}")

if __name__ == "__main__":
    main()
