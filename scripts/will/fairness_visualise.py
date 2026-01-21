#!/usr/bin/env python3
"""
SMT fairness visualisation using Jain's fairness index per instruction type (test_num).

Input CSV schema (one row per run):
  test_num,pinned_thread,thread1,thread2,thread1_wins,thread2_wins

This script produces three types of visualisations:
1) Pair-seed fairness bars (per test_num):
   - For each thread pair (a,b), compute Jain’s index using only runs where -b ∈ {a,b}.
   - X-axis: core pairs (e.g., "0-6"), Y-axis: Jain’s index (1.0 is perfectly fair).

2) All-seed fairness bars (per test_num):
   - For each thread pair (a,b), compute Jain’s index using all runs (aggregate across all -b).

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
base_dir = "./results/r53600/"
INPUT_CSV = base_dir + "smt_fairness/smt_fairness_simple.csv"

OUTPUT_DIR = base_dir + "smt_fairness_plots_jain"
SUMMARY_CSV = os.path.join(OUTPUT_DIR, "jain_pair_vs_all_summary.csv")

FIG_DPI = 130
STYLE = "ggplot"      # "ggplot", "default", "seaborn-v0_8", etc.
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
    and compute wins mapped to (a,b) consistently:
      wins_a = wins for the smaller core id of the pair
      wins_b = wins for the larger core id of the pair
    """
    df = pd.read_csv(csv_path)
    # Ensure numeric
    for c in ["test_num", "pinned_thread", "thread1", "thread2", "thread1_wins", "thread2_wins"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["test_num", "pinned_thread", "thread1", "thread2", "thread1_wins", "thread2_wins"]).copy()

    # Canonical pair a-b with a < b
    df["a"] = df[["thread1", "thread2"]].min(axis=1).astype(int)
    df["b"] = df[["thread1", "thread2"]].max(axis=1).astype(int)
    df["pair"] = df.apply(lambda r: f"{r['a']}-{r['b']}", axis=1)

    # Map wins to (a,b) irrespective of (thread1,thread2) ordering in the row
    df["wins_a"] = np.where(df["thread1"] == df["a"], df["thread1_wins"], df["thread2_wins"])
    df["wins_b"] = np.where(df["thread2"] == df["b"], df["thread2_wins"], df["thread1_wins"])

    # For safety, ensure totals make sense
    df["total"] = df["wins_a"] + df["wins_b"]
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

    # Unique groups
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
    Bar chart of Jain fairness per pair for a single test_num.
    X: pair label (a-b), Y: Jain.
    """
    sub = fair_df[fair_df["test_num"] == test_num].copy()
    if sub.empty:
        return

    sub = sub.sort_values(["a", "b"])
    pairs = sub["pair"].tolist()
    y = sub["jain"].to_numpy()

    fig_w = max(8, 0.75 * len(pairs))
    fig, ax = plt.subplots(figsize=(fig_w, 4.5), dpi=FIG_DPI)
    ax.bar(np.arange(len(pairs)), y, color="#6699cc")
    ax.axhline(1.0, color="k", linestyle="--", linewidth=1, label="perfect fairness (J=1)")
    ax.set_title(f"Jain fairness per pair — {test_label(test_num)} ({test_num}): {title_suffix}")
    ax.set_ylabel("Jain fairness")
    ax.set_xticks(np.arange(len(pairs)))
    ax.set_xticklabels(pairs, rotation=45, ha="right")
    # Adjust y-limits gently around [0.9,1.0+epsilon]
    ymin = min(0.9, float(np.nanmin(y)) - 0.02) if len(y) else 0.9
    ax.set_ylim(ymin, 1.02)
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend(loc="lower left")

    fname = f"test{test_num}_jain_{fname_suffix}.png"
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
        # Place legend outside on the right
        legend = ax.legend(handles=handles + [ax.lines[-1]],
                           labels=labels + [f"50% of {int(common_total)}"],
                           loc="center left", bbox_to_anchor=(1.02, 0.5), borderaxespad=0.)
    else:
        legend = ax.legend(handles=handles, labels=labels,
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
    print(df.sort_values("thread1_wins").head(10))

    # Generate visualisations (comment out any you want to skip)
    #fair_pair_seeds = generate_pair_seed_bars(df)        # comment out to skip
    fair_all_seeds  = generate_all_seed_bars(df)         # comment out to skip
    #generate_wins_a_points_supercombined(df)             # comment out to skip

    # Save long-format summary (pair vs all) if you want a combined table
    if 'fair_pair_seeds' in locals() and 'fair_all_seeds' in locals():
        summary = pd.concat([fair_pair_seeds, fair_all_seeds], ignore_index=True)
        summary.to_csv(SUMMARY_CSV, index=False)
        print(f"Wrote summary CSV (long format): {SUMMARY_CSV}")
    print(f"Plots saved in: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
