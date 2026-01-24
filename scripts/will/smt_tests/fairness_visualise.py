#!/usr/bin/env python3
"""
SMT fairness visualisation using Jain's fairness index per instruction type (test_num).

Supports BOTH input schemas:

A) Original (long) per-run schema:
   test_num,pinned_thread,thread1,thread2,thread1_wins,thread2_wins

B) New (wide) per-iteration schema (one row per test/pair/seed with iteration columns):
   test_num,pinned_thread,thread1,thread2,
   thread1_rep_1_wins,...,thread1_rep_N_wins,
   thread2_rep_1_wins,...,thread2_rep_N_wins,
   thread1_total_wins,thread2_total_wins,total

This script produces three types of visualisations:
1) Pair-seed fairness bars (per test_num):
   - For each thread pair (a,b), compute Jain’s index using only rows where -b ∈ {a,b}.
   - X-axis: core pairs (e.g., "0-6"), Y-axis: Jain’s index (1.0 is perfectly fair).

2) All-seed fairness bars (per test_num):
   - For each thread pair (a,b), compute Jain’s index using all rows (aggregate across all -b).

3) Wins-A super-combined scatter (one figure for ALL tests):
   - Single scatter plot combining ALL datapoints across tests.
   - X-axis: global datapoint index (0..N-1) across the whole dataset.
   - Y-axis: wins_a (wins for the lower-ID thread of each pair).
   - Points are colored by instruction (test), with a legend using instruction names
     (loaded from test_nums_to_names.NUM_TO_TEST). If not available, numeric IDs are used.

You can comment out the individual generate_* calls at the end if you don’t want to re-run a given visualisation.
"""

import os
from typing import List

from matplotlib.ticker import MultipleLocator
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Try to import test names mapping
TEST_NAME_MAP = None
try:
    from test_nums_to_names import NUM_TO_TEST, TARGET_CORE  # TARGET_CORE not used here but imported for completeness
    TEST_NAME_MAP = NUM_TO_TEST
except Exception:
    TEST_NAME_MAP = None  # fallback to numeric labels if mapping module not found

# ==============================
# Configuration (edit in code)
# ==============================
base_dir = "./results/Xeon_Gold_6142/"
INPUT_CSV = base_dir + "smt_fairness_simple.csv"

OUTPUT_DIR = base_dir + "smt_fairness_plots_jain"
SUMMARY_CSV = os.path.join(OUTPUT_DIR, "jain_pair_vs_all_summary.csv")

FIG_DPI = 130
STYLE = "default"      # "ggplot", "default", "seaborn-v0_8", etc.
SAVE_PNG = True
SHOW_WINDOWS = False  # set True to show interactive windows (plt.show())


# ==============================
# Helpers
# ==============================

def ensure_dir(d: str) -> None:
    os.makedirs(d, exist_ok=True)

def jain(values: List[float]) -> float:
    """Jain’s fairness index: J(x) = (sum x)^2 / (n * sum x^2)."""
    arr = np.array(values, dtype=float)
    s = arr.sum()
    s2 = np.square(arr).sum()
    n = arr.size
    if n == 0 or s2 <= 0:
        return float("nan")
    return (s * s) / (n * s2)

def test_label(tid: int) -> str:
    """Return human-friendly test label using NUM_TO_TEST if available; else numeric ID."""
    try:
        if TEST_NAME_MAP is not None and 0 <= int(tid) < len(TEST_NAME_MAP):
            return str(TEST_NAME_MAP[int(tid)])
    except Exception:
        pass
    return f"test {int(tid)}"

def prepare_dataframe(csv_path: str) -> pd.DataFrame:
    """
    Load CSV, enforce numeric dtypes, define canonical pair (a-b),
    and compute wins mapped to (a,b) consistently (wins_a, wins_b, total).

    Supports both legacy long schema and new wide per-iteration schema.
    """
    df = pd.read_csv(csv_path)

    # Detect schema (long vs wide)
    has_long = {"thread1_wins", "thread2_wins"}.issubset(df.columns)
    has_wide = {"thread1_total_wins", "thread2_total_wins"}.issubset(df.columns)

    # Ensure numeric across both possible sets
    num_cols_common = ["test_num", "pinned_thread", "thread1", "thread2"]
    num_cols_long = ["thread1_wins", "thread2_wins"]
    num_cols_wide = ["thread1_total_wins", "thread2_total_wins", "total"]

    for c in num_cols_common:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")
    if has_long:
        for c in num_cols_long:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if has_wide:
        for c in num_cols_wide:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Basic required fields check
    req = ["test_num", "pinned_thread", "thread1", "thread2"]
    if not set(req).issubset(df.columns):
        missing = [c for c in req if c not in df.columns]
        raise ValueError(f"Missing required columns: {missing}")

    # Canonical pair a-b with a < b
    df["a"] = df[["thread1", "thread2"]].min(axis=1).astype(int)
    df["b"] = df[["thread1", "thread2"]].max(axis=1).astype(int)
    df["pair"] = df.apply(lambda r: f"{r['a']}-{r['b']}", axis=1)

    # Construct wins_a/wins_b and total for downstream (based on schema)
    if has_long:
        df = df.dropna(subset=req + num_cols_long).copy()
        # Map wins to (a,b) irrespective of (thread1,thread2) ordering in the row
        df["wins_a"] = np.where(df["thread1"] == df["a"], df["thread1_wins"], df["thread2_wins"])
        df["wins_b"] = np.where(df["thread2"] == df["b"], df["thread2_wins"], df["thread1_wins"])
        df["total"] = df["wins_a"] + df["wins_b"]
    elif has_wide:
        df = df.dropna(subset=req + ["thread1_total_wins", "thread2_total_wins"]).copy()
        # Use the per-row totals (sum over iterations) as wins
        # Map totals to (a,b) irrespective of (thread1,thread2) ordering
        df["wins_a"] = np.where(df["thread1"] == df["a"], df["thread1_total_wins"], df["thread2_total_wins"])
        df["wins_b"] = np.where(df["thread2"] == df["b"], df["thread2_total_wins"], df["thread1_total_wins"])
        # Prefer provided 'total' if present and valid; else sum
        if "total" not in df.columns or df["total"].isna().any():
            df["total"] = df["wins_a"] + df["wins_b"]
    else:
        raise ValueError("Neither long (thread1_wins/thread2_wins) nor wide (thread1_total_wins/thread2_total_wins) schema detected.")

    # Filter invalid totals
    df = df[df["total"] > 0].copy()

    return df

def fairness_summary_for_mode(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    """
    Compute Jain fairness per (test_num, pair) under a given mode:

    mode == "pair_seeds":
      For each (test_num, pair=(a,b)), aggregate only rows where pinned_thread ∈ {a,b}.

    mode == "all_seeds":
      For each (test_num, pair), aggregate all rows for that pair (regardless of pinned_thread).

    Returns a DataFrame with:
      test_num, pair, a, b, rows, tot_wins_a, tot_wins_b, jain
    """
    rows = []

    groups = df[["test_num", "pair", "a", "b"]].drop_duplicates().sort_values(["test_num", "a", "b"])
    for _, g in groups.iterrows():
        t = int(g["test_num"])
        a = int(g["a"])
        b = int(g["b"])
        p = g["pair"]

        sub = df[(df["test_num"] == t) & (df["pair"] == p)]
        if mode == "pair_seeds":
            sub = sub[sub["pinned_thread"].isin([a, b])]

        if sub.empty:
            continue

        tot_a = sub["wins_a"].sum()
        tot_b = sub["wins_b"].sum()
        j = jain([tot_a, tot_b])

        rows.append({
            "test_num": t,
            "pair": p,
            "a": a,
            "b": b,
            "rows": int(sub.shape[0]),
            "tot_wins_a": int(tot_a),
            "tot_wins_b": int(tot_b),
            "jain": float(j),
            "mode": mode
        })

    out = pd.DataFrame(rows)
    return out.sort_values(["test_num", "a", "b"]).reset_index(drop=True)

def plot_fairness_bars(fair_df: pd.DataFrame, test_num: int, title_suffix: str, out_dir: str, fname_suffix: str) -> None:
    """
    Grouped (side-by-side) bar chart of Jain fairness per pair for ALL tests together.

    - X: pair label (a-b), one group per pair
    - Bars within a group: one per test_num
    - Y: Jain fairness
    - Colors: one per test; legend shows test names

    Note: test_num is ignored in this combined view (kept for call compatibility).
    """
    if fair_df.empty:
        return

    # Ensure required columns and deterministic order of pairs
    sub = fair_df.copy()
    need = ["test_num", "pair", "a", "b", "jain"]
    for col in need:
        if col not in sub.columns:
            raise ValueError(f"Missing column '{col}' in fairness DataFrame")

    sub["a"] = sub["a"].astype(int)
    sub["b"] = sub["b"].astype(int)
    sub["test_num"] = sub["test_num"].astype(int)

    # Pair order: sort by (a,b); Test order: sorted test_num
    pair_order = sorted(sub["pair"].unique(), key=lambda s: (int(s.split("-")[0]), int(s.split("-")[1])))
    test_order = sorted(sub["test_num"].unique())

    # Pivot to pair x test matrix of jain values
    pivot = (sub.pivot_table(index="pair", columns="test_num", values="jain", aggfunc="mean")
                .reindex(index=pair_order, columns=test_order))

    n_pairs = len(pivot.index)
    n_tests = len(pivot.columns)
    if n_pairs == 0 or n_tests == 0:
        return

    x = np.arange(n_pairs)

    # Bar width and offsets so the group fits nicely
    # Keep total group width <= ~0.85; small gap between bars
    gap = 0.02
    bar_width = min(0.85 / max(n_tests, 1) - gap, 0.2)
    offsets = (np.arange(n_tests) - (n_tests - 1) / 2.0) * (bar_width + gap)

    # Color map per test and legend entries
    cmap = plt.get_cmap("tab10")
    color_map = {t: cmap(i % 10) for i, t in enumerate(test_order)}
    handles = []
    labels = []

    # Figure sizing scales with number of pairs
    fig_w = max(10, 0.75 * n_pairs)
    fig, ax = plt.subplots(figsize=(fig_w, 4.8), dpi=FIG_DPI)

    # Draw each test's bars side by side for all pairs
    for i, t in enumerate(test_order):
        heights = pivot[t].to_numpy()
        # Allow NaNs to skip bars (matplotlib ignores NaN height)
        h = ax.bar(x + offsets[i], heights, width=bar_width, color=color_map[t],
                   edgecolor="black", linewidth=0.2, alpha=0.9, label=test_label(int(t)))
        handles.append(h[0])
        labels.append(test_label(int(t)))

    # Formatting
    ax.axhline(1.0, color="k", linestyle="--", linewidth=1, label="perfect fairness (J=1)")
    ax.set_title(f"Jain fairness per pair — ALL tests side-by-side: {title_suffix}")
    ax.set_ylabel("Jain fairness")
    ax.set_xticks(x)
    ax.set_xticklabels(pivot.index.tolist(), rotation=45, ha="right")

    # Y range with gentle padding (default lower bound ~0.9)
    with np.errstate(all="ignore"):
        min_val = np.nanmin(pivot.to_numpy())
    ymin = min(0.9, float(min_val) - 0.02) if np.isfinite(min_val) else 0.9
    ax.set_ylim(ymin, 1.02)
    ax.grid(True, axis="y", alpha=0.3)

    # Legend outside on the right
    # Include the J=1.0 line as well
    jline = plt.Line2D([0], [0], color='k', linestyle='--', linewidth=1)
    ax.legend(handles + [jline], labels + ["perfect fairness (J=1)"],
              loc="center left", bbox_to_anchor=(1.02, 0.5), borderaxespad=0.)

    # Save
    fname = f"combined_jain_{fname_suffix}.png"
    if SAVE_PNG:
        fig.savefig(os.path.join(out_dir, fname), bbox_inches="tight")
    if SHOW_WINDOWS:
        plt.show()
    plt.close(fig)

# ----- Wins-A super-combined scatter (one plot for ALL tests) -----

def generate_wins_a_points_supercombined(df: pd.DataFrame) -> None:
    """
    Produce a single scatter plot with ALL datapoints across ALL tests:
      X = global datapoint index (0..N-1) over the entire dataset (deterministic order).
      Y = wins_a (wins for the lower-ID thread of each pair).
    Points are colored by instruction (test), and the legend uses instruction names.
    """
    # Stable global ordering for reproducibility: by (a,b), then pinned_thread, then test_num
    df_sorted = df.copy()
    df_sorted["a_int"] = df_sorted["a"].astype(int)
    df_sorted["b_int"] = df_sorted["b"].astype(int)
    df_sorted["test_num_int"] = df_sorted["test_num"].astype(int)
    df_sorted = df_sorted.sort_values(["a_int", "b_int", "pinned_thread", "test_num_int"]).reset_index(drop=True)
    df_sorted["gidx"] = np.arange(len(df_sorted))

    tests = list(dict.fromkeys(df_sorted["test_num_int"].tolist()))  # unique in order of appearance
    if not tests:
        return

    # Color cycle (enough distinct colors; fall back to repeating if >20 tests)
    cmap = plt.get_cmap("tab20")
    colors = [cmap(i % 20) for i in range(len(tests))]

    fig_w = max(12, 0.006 * len(df_sorted) + 8)  # scale with number of points
    fig, ax = plt.subplots(figsize=(fig_w, 4.8), dpi=FIG_DPI)
    ax.xaxis.set_major_locator(MultipleLocator(100))

    handles = []
    labels = []
    for i, t in enumerate(tests):
        sub = df_sorted[df_sorted["test_num_int"] == t]
        if sub.empty:
            continue
        x = sub["gidx"].to_numpy()
        y = sub["wins_a"].to_numpy(dtype=float)
        color = colors[i]
        h = ax.plot(x, y, "o", markersize=2.3, alpha=0.65, color=color, label=test_label(t))[0]
        handles.append(h)
        labels.append(test_label(t))

    # Add an indicative reference at the most common total/2 (if totals mostly equal)
    totals = df_sorted["total"].to_numpy()
    if len(totals):
        vals, counts = np.unique(totals, return_counts=True)
        common_total = vals[np.argmax(counts)]
        ax.axhline(common_total / 2.0, color="k", linestyle="--", linewidth=1, alpha=0.6,
                   label=f"50% of {int(common_total)}")
        ax.legend(handles=handles + [ax.lines[-1]],
                  labels=labels + [f"50% of {int(common_total)}"],
                  loc="center left", bbox_to_anchor=(1.02, 0.5), borderaxespad=0.)
    else:
        ax.legend(handles=handles, labels=labels,
                  loc="center left", bbox_to_anchor=(1.02, 0.5), borderaxespad=0.)

    ax.set_title(f"Wins for lower-ID thread (a) — ALL tests combined (N={len(df_sorted)})")
    ax.set_xlabel("global datapoint index")
    ax.set_ylabel("wins(a)")
    ax.grid(True, alpha=0.25)

    fname = "winsA_points_ALL_TESTS_supercombined.png"
    if SAVE_PNG:
        fig.savefig(os.path.join(OUTPUT_DIR, fname), bbox_inches="tight")
    if SHOW_WINDOWS:
        plt.show()
    plt.close(fig)

# ==============================
# Generators for each visualisation type
# ==============================

def generate_pair_seed_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Compute pair-seed fairness summary and generate bar plots per test."""
    fair_pair_seeds = fairness_summary_for_mode(df, mode="pair_seeds")
    tests = sorted(df["test_num"].unique())
    for t in tests:
        plot_fairness_bars(fair_pair_seeds, test_num=int(t),
                           title_suffix="seed within pair (b ∈ {a,b})",
                           out_dir=OUTPUT_DIR,
                           fname_suffix="pair_seeds")
    return fair_pair_seeds

def generate_all_seed_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all-seed fairness summary and generate bar plots per test."""
    fair_all_seeds = fairness_summary_for_mode(df, mode="all_seeds")
    tests = sorted(df["test_num"].unique())
    for t in tests:
        plot_fairness_bars(fair_all_seeds, test_num=int(t),
                           title_suffix="seed across all threads (b ∈ all)",
                           out_dir=OUTPUT_DIR,
                           fname_suffix="all_seeds")
    return fair_all_seeds

# ==============================
# Main
# ==============================

def main():
    ensure_dir(OUTPUT_DIR)
    plt.style.use(STYLE)

    if not os.path.exists(INPUT_CSV):
        raise FileNotFoundError(f"CSV not found: {INPUT_CSV}")

    df = prepare_dataframe(INPUT_CSV)

    print(df.sort_values("wins_a").head(10))

    # Generate visualisations (comment out any you want to skip)
    fair_pair_seeds = generate_pair_seed_bars(df)        # comment out to skip
    fair_all_seeds  = generate_all_seed_bars(df)         # comment out to skip
    generate_wins_a_points_supercombined(df)           # comment out to skip

    # Save long-format summary (pair vs all) if you want a combined table
    if 'fair_pair_seeds' in locals() and 'fair_all_seeds' in locals():
        summary = pd.concat([fair_pair_seeds, fair_all_seeds], ignore_index=True)
        summary.to_csv(SUMMARY_CSV, index=False)
        print(f"Wrote summary CSV (long format): {SUMMARY_CSV}")
    print(f"Plots saved in: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()