#!/usr/bin/env python3
"""
Placement fairness visualisation (supports three CSV shapes)

This script generates TWO sets of plots from a single input CSV:
  - b4/                 -> uses Common-start latencies (B4 -> success)
  - cross_core_summary/ -> uses Cross-core summary (PFD avg)

Input CSV variants supported:

1) Wide-dual (your current contention CSV):
   Columns:
     test_id, seed_thread,
     core_0, b4_mean_0, pfd_avg_0, pfd_min_0, pfd_max_0, pfd_std_0, pfd_absdev_0,
     core_1, b4_mean_1, pfd_avg_1, ...
   This will be mapped to two wide frames internally:
     - B4:   latency_i := b4_mean_i
     - CCS:  latency_i := pfd_avg_i
   and seed_thread is renamed to pinned_thread.

2) Long (non-contention):
   Columns: test_id, seed_thread, worker_thread, latency_b4, pfd_avg
   This will be pivoted per metric to a wide format internally.

3) Legacy wide:
   Columns: test_id, pinned_thread, latency_0, latency_1, ..., latency_N
   This will be treated as the B4 dataset; CCS plots skipped unless present separately.

Fairness metric:
- Jain’s fairness index is computed on inverse-latency utilities: xi = 1 / latency_i for latency_i > 0.
"""

import os
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

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

# Adjust these paths to your layout
base_dir = "./results/" + processor_name + "/"
INPUT_CSV = os.path.join(base_dir, "placement_latency_contention.csv")

# Output base; two subfolders will be created: b4/ and cross_core_summary/
OUT_BASE_DIR = os.path.join(base_dir, "placement_plots")
OUT_DIR_B4 = os.path.join(OUT_BASE_DIR, "b4")
OUT_DIR_CCS = os.path.join(OUT_BASE_DIR, "cross_core_summary")

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
    """
    Jain's fairness index over inverse-latency utilities (xi = 1/latency_i).
    """
    vals = np.asarray(values, dtype=float)
    vals = vals[np.isfinite(vals) & (vals > 0)]  # positive finite latencies only
    if vals.size == 0:
        return np.nan
    x = 1.0 / vals
    s = np.sum(x)
    s2 = np.sum(x * x)
    return (s * s) / (x.size * s2) if s2 > 0 else np.nan

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
# Data shaping
# ==============================

def pivot_long_to_wide(df: pd.DataFrame, value_col: str,
                       seed_col: str = "seed_thread",
                       worker_col: str = "worker_thread") -> pd.DataFrame:
    """
    Pivot a long non-contention CSV into a wide contention-like DataFrame:
      index: (test_id, seed_thread) -> becomes column 'pinned_thread'
      columns: worker_thread        -> renamed to 'latency_<worker>'
      values: value_col (e.g., 'latency_b4' or 'pfd_avg')
    """
    tmp = df.copy()
    tmp["test_id"] = pd.to_numeric(tmp["test_id"], errors="coerce")
    tmp[seed_col] = pd.to_numeric(tmp[seed_col], errors="coerce")
    tmp[worker_col] = pd.to_numeric(tmp[worker_col], errors="coerce")
    tmp[value_col] = pd.to_numeric(tmp[value_col], errors="coerce")

    wide = tmp.pivot_table(index=["test_id", seed_col],
                           columns=worker_col,
                           values=value_col,
                           aggfunc="mean")
    wide = wide.reset_index()
    wide = wide.rename(columns={seed_col: "pinned_thread"})

    # Rename worker columns to latency_<worker>
    new_cols = []
    for c in wide.columns:
        try:
            ci = int(c)
            new_cols.append(f"latency_{ci}")
        except Exception:
            new_cols.append(c)
    wide.columns = new_cols

    # Order columns
    lat_cols = extract_latency_cols(wide)
    wide = wide[["test_id", "pinned_thread"] + lat_cols]
    return wide

def map_wide_dual_to_wide(df_in: pd.DataFrame, src_prefix: str) -> Optional[pd.DataFrame]:
    """
    Map 'wide-dual' CSV (seed_thread, b4_mean_i / pfd_avg_i per thread) to wide format:
      test_id, pinned_thread, latency_<i>

    src_prefix: "b4_mean" or "pfd_avg"
    """
    if "seed_thread" not in df_in.columns or not any(c.startswith(src_prefix + "_") for c in df_in.columns):
        return None

    df = df_in.copy()
    df["test_id"] = pd.to_numeric(df["test_id"], errors="coerce")
    df["pinned_thread"] = pd.to_numeric(df["seed_thread"], errors="coerce")

    # Find all indices i for which src_prefix_i exists
    idxs = []
    for c in df.columns:
        if c.startswith(src_prefix + "_"):
            try:
                i = int(c.split("_")[2] if src_prefix == "pfd" else c.split("_")[2])  # not used, kept for safety
            except Exception:
                i = int(c.split("_")[2]) if src_prefix not in ("b4_mean", "pfd_avg") else None
    # Simpler: gather indices via parsing the tail
    idxs = sorted({
        int(c.split("_")[-1])
        for c in df.columns
        if c.startswith(src_prefix + "_")
        if c.split("_")[-1].isdigit()
    })

    if not idxs:
        return None

    # Build latency_<i> columns from src_prefix_<i>
    out = df[["test_id", "pinned_thread"]].copy()
    for i in idxs:
        col = f"{src_prefix}_{i}"
        if col in df.columns:
            out[f"latency_{i}"] = pd.to_numeric(df[col], errors="coerce")
    return out

def detect_and_prepare_datasets(df_in: pd.DataFrame) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """
    Detect CSV shape and prepare two wide DataFrames:
      - df_b4_wide: from latency_b4 or b4_mean_i (or from existing latency_* if already wide)
      - df_ccs_wide: from pfd_avg or pfd_avg_i (only if present in input)
    Returns (df_b4_wide, df_ccs_wide). Either can be None if not derivable.
    """
    cols = set(df_in.columns)

    # Case A: wide-dual (seed_thread present with b4_mean_i / pfd_avg_i per thread)
    if "seed_thread" in cols and any(c.startswith("b4_mean_") for c in cols):
        b4 = map_wide_dual_to_wide(df_in, "b4_mean")
        ccs = map_wide_dual_to_wide(df_in, "pfd_avg") if any(c.startswith("pfd_avg_") for c in cols) else None
        return b4, ccs

    # Case B: long non-contention CSV
    if {"test_id", "seed_thread", "worker_thread"}.issubset(cols):
        df_b4_wide = pivot_long_to_wide(df_in, "latency_b4") if "latency_b4" in cols else None
        df_ccs_wide = pivot_long_to_wide(df_in, "pfd_avg") if "pfd_avg" in cols else None
        return df_b4_wide, df_ccs_wide

    # Case C: already wide (legacy)
    if "pinned_thread" in cols and any(c.startswith("latency_") for c in cols):
        df = df_in.copy()
        df["test_id"] = pd.to_numeric(df["test_id"], errors="coerce")
        df["pinned_thread"] = pd.to_numeric(df["pinned_thread"], errors="coerce")
        keep = ["test_id", "pinned_thread"] + extract_latency_cols(df)
        return df[keep], None

    return None, None

# ==============================
# Plots
# ==============================

def plot_fairness_vs_seed(df: pd.DataFrame, latency_cols: List[str], output_path: str, title_suffix: str = ""):
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "test_id": int(r["test_id"]),
            "pinned_thread": int(r["pinned_thread"]),
            "fairness": jain(r[latency_cols].to_numpy())
        })
    g = pd.DataFrame(rows).groupby(["test_id", "pinned_thread"], as_index=False).mean()

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

    title = f"{processor_name}: Jain Fairness (1/latency) vs Pinned Thread"
    if title_suffix:
        title += f" — {title_suffix}"
    ax.set_title(title)
    ax.set_xlabel("Pinned Thread (-b)")
    ax.set_ylabel("Jain Fairness Index")
    ax.set_xticks(x_domain)

    if XEON_GOLD_6142_ORDER:
        ax.tick_params(axis="x", labelsize=7)

    ax.margins(x=0)
    enforce_white_theme(ax)
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5))

    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)

def plot_fairness_vs_worker(df: pd.DataFrame, latency_cols: List[str], output_path: str, title_suffix: str = ""):
    workers = reorder_for_mode([int(c.split("_")[1]) for c in latency_cols])
    tests = sorted(df["test_id"].unique())

    rows = []
    for t in tests:
        sub = df[df["test_id"] == t]
        for w in workers:
            col = f"latency_{w}"
            vals = sub[col].to_numpy(dtype=float) if col in sub.columns else np.array([], dtype=float)
            rows.append({"test_id": t, "worker": w, "fairness": jain(vals)})
    g = pd.DataFrame(rows)

    fig, ax = plt.subplots(figsize=(10, 5.8), dpi=FIG_DPI)
    cmap = plt.get_cmap("tab20")

    for i, t in enumerate(tests):
        sub = g[g["test_id"] == t].set_index("worker")
        y = [sub.loc[w, "fairness"] if w in sub.index else np.nan for w in workers]
        ax.plot(workers, y, "-o", linewidth=2, markersize=4,
                color=cmap(i % 20), label=test_label(t))

    ax.axhline(1.0, linestyle="--", color="black", linewidth=1)
    ax.set_ylim(0.0, 1.1)
    ax.yaxis.set_major_locator(MultipleLocator(0.1))

    title = f"{processor_name}: Jain Fairness (1/latency) vs Worker Thread"
    if title_suffix:
        title += f" — {title_suffix}"
    ax.set_title(title)
    ax.set_xlabel("Worker Thread")
    ax.set_ylabel("Jain Fairness Index")
    ax.set_xticks(workers)

    if XEON_GOLD_6142_ORDER:
        ax.tick_params(axis="x", labelsize=7)

    ax.margins(x=0)
    enforce_white_theme(ax)
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5))

    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)

# ==============================
# Main
# ==============================

def main():
    ensure_dir(OUT_BASE_DIR)
    ensure_dir(OUT_DIR_B4)
    ensure_dir(OUT_DIR_CCS)

    df_in = pd.read_csv(INPUT_CSV)

    # Build wide datasets for plotting
    df_b4_wide, df_ccs_wide = detect_and_prepare_datasets(df_in)

    if df_b4_wide is None and df_ccs_wide is None:
        # Help diagnose columns present
        print("Columns found:", list(df_in.columns))
        raise SystemExit("Input CSV is neither a recognized wide-dual CSV, a long non-contention CSV, nor a legacy wide CSV.")

    # B4 plots
    if df_b4_wide is not None:
        df_b4_wide = df_b4_wide.copy()
        df_b4_wide["test_id"] = pd.to_numeric(df_b4_wide["test_id"], errors="coerce")
        df_b4_wide["pinned_thread"] = pd.to_numeric(df_b4_wide["pinned_thread"], errors="coerce")
        latency_cols_b4 = extract_latency_cols(df_b4_wide)
        if latency_cols_b4:
            plot_fairness_vs_seed(df_b4_wide, latency_cols_b4, os.path.join(OUT_DIR_B4, "fairness_vs_seed.png"), "B4")
            plot_fairness_vs_worker(df_b4_wide, latency_cols_b4, os.path.join(OUT_DIR_B4, "fairness_vs_worker.png"), "B4")
            print(f"Saved B4 plots to {OUT_DIR_B4}")
        else:
            print("Warning: no latency_* columns in B4 dataset; skipping B4 plots.")

    # Cross-core summary (PFD avg) plots
    if df_ccs_wide is not None:
        df_ccs_wide = df_ccs_wide.copy()
        df_ccs_wide["test_id"] = pd.to_numeric(df_ccs_wide["test_id"], errors="coerce")
        df_ccs_wide["pinned_thread"] = pd.to_numeric(df_ccs_wide["pinned_thread"], errors="coerce")
        latency_cols_ccs = extract_latency_cols(df_ccs_wide)
        if latency_cols_ccs:
            plot_fairness_vs_seed(df_ccs_wide, latency_cols_ccs, os.path.join(OUT_DIR_CCS, "fairness_vs_seed.png"), "Cross-core Summary (avg)")
            plot_fairness_vs_worker(df_ccs_wide, latency_cols_ccs, os.path.join(OUT_DIR_CCS, "fairness_vs_worker.png"), "Cross-core Summary (avg)")
            print(f"Saved Cross-core Summary plots to {OUT_DIR_CCS}")
        else:
            print("Warning: no latency_* columns in Cross-core Summary dataset; skipping CCS plots.")

    print(f"Done. Input: {INPUT_CSV}")

if __name__ == "__main__":
    main()
