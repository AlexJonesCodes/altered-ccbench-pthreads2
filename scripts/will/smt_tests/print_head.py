#!/usr/bin/env python3
"""
Show most-unfair SMT rows (with all three rounds reported) and fairness aligned with your graphs.

Reads a wins CSV (wide or long) and prints the head of the DataFrame sorted by the
lowest per-row fairness (Jain), including:
- All per-iteration round wins (thread1_rep_1/2/3_wins, thread2_rep_1/2/3_wins) if present
- Per-row totals
- Per-row fairness (thread1 vs thread2 for that row)  -> fairness_row
- Aggregated fairness matching your graphs:
  * fairness_pair_agg: aggregated over rows where pinned_thread ∈ {a,b} for the same (test_num, pair)
  * fairness_all_agg:  aggregated over all pinned_thread values for the same (test_num, pair)

Schema supported:
A) Wide (per test/pair/seed, with totals or reps):
   Required: test_num, pinned_thread, thread1, thread2
   Optional:
     - thread1_total_wins, thread2_total_wins (preferred totals)
     - or thread1_rep_1_wins,... & thread2_rep_1_wins,... (will be summed if totals absent)
     - total (optional; recomputed if missing)

B) Long (per run):
   Required: test_num, pinned_thread, thread1, thread2, thread1_wins, thread2_wins

Usage:
- Edit INPUT_CSV and HEAD_N below.
- If test_nums_to_name.py or test_nums_to_names.py defines NUM_TO_TEST, test names are shown.

Output columns (printed):
  test_num, test_name, pinned_thread, thread1, thread2,
  thread1_rep_1_wins, thread1_rep_2_wins, thread1_rep_3_wins (if present),
  thread2_rep_1_wins, thread2_rep_2_wins, thread2_rep_3_wins (if present),
  thread1_total_wins, thread2_total_wins, total,
  fairness_row, fairness_pair_agg, fairness_all_agg
"""

import os
import re
import numpy as np
import pandas as pd

# Optional test name mapping (prefer singular filename, fallback to plural)
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
# Configuration
# ==============================
base_dir = "./E52630V3"
INPUT_CSV = os.path.join(base_dir, "smt_fairness_simple.csv")
HEAD_N = 20

# ==============================
# Helpers
# ==============================

def test_label(tid: int) -> str:
    try:
        if TEST_NAME_MAP is not None and 0 <= int(tid) < len(TEST_NAME_MAP):
            return str(TEST_NAME_MAP[int(tid)])
    except Exception:
        pass
    return f"test {int(tid)}"

def jain_2(a: float, b: float) -> float:
    """Jain’s fairness index for two non-negative values."""
    a = float(a); b = float(b)
    if a < 0 or b < 0:
        return float("nan")
    denom = (a*a + b*b)
    if denom <= 0:
        return float("nan")
    s = (a + b)
    return (s * s) / (2.0 * denom)

def load_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"CSV not found: {path}")
    return pd.read_csv(path)

def canonicalize_pairs(df: pd.DataFrame) -> pd.DataFrame:
    """Add canonical a,b,pair columns (a=min(thread1,thread2))."""
    df = df.copy()
    # Ensure numeric
    for c in ["test_num", "pinned_thread", "thread1", "thread2"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["test_num", "pinned_thread", "thread1", "thread2"]).copy()
    df["a"] = df[["thread1", "thread2"]].min(axis=1).astype(int)
    df["b"] = df[["thread1", "thread2"]].max(axis=1).astype(int)
    df["pair"] = df["a"].astype(str) + "-" + df["b"].astype(str)
    df["test_num"] = df["test_num"].astype(int)
    df["pinned_thread"] = df["pinned_thread"].astype(int)
    return df

def compute_wins_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Produce thread1_total_wins & thread2_total_wins if not present by summing rep columns.
    If only long schema present, derive totals from thread1_wins/thread2_wins.
    Also produce total.
    """
    df = df.copy()

    has_totals = {"thread1_total_wins", "thread2_total_wins"}.issubset(df.columns)
    has_long   = {"thread1_wins", "thread2_wins"}.issubset(df.columns)

    if has_totals:
        df["thread1_total_wins"] = pd.to_numeric(df["thread1_total_wins"], errors="coerce")
        df["thread2_total_wins"] = pd.to_numeric(df["thread2_total_wins"], errors="coerce")
    elif has_long:
        df["thread1_total_wins"] = pd.to_numeric(df["thread1_wins"], errors="coerce")
        df["thread2_total_wins"] = pd.to_numeric(df["thread2_wins"], errors="coerce")
    else:
        # Sum rep columns
        rep1 = [c for c in df.columns if re.match(r"^thread1_rep_\d+_wins$", c)]
        rep2 = [c for c in df.columns if re.match(r"^thread2_rep_\d+_wins$", c)]
        if rep1 and rep2:
            df["thread1_total_wins"] = df[rep1].sum(axis=1, numeric_only=True)
            df["thread2_total_wins"] = df[rep2].sum(axis=1, numeric_only=True)
        else:
            raise ValueError("No usable wins columns: need totals or per-run or rep_* columns.")

    if "total" in df.columns:
        df["total"] = pd.to_numeric(df["total"], errors="coerce")
    else:
        df["total"] = df["thread1_total_wins"] + df["thread2_total_wins"]

    # Per-row fairness (based on thread1 vs thread2 of that row)
    df["fairness_row"] = df.apply(lambda r: jain_2(r["thread1_total_wins"], r["thread2_total_wins"]), axis=1)

    return df

def aggregate_fairness_like_graph(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add aggregated fairness columns matching your graphs:
      - fairness_pair_agg: aggregate wins over rows where pinned_thread ∈ {a,b} for same (test_num,pair)
      - fairness_all_agg:  aggregate wins over all pinned_thread for same (test_num,pair)
    """
    df = df.copy()

    # Build key for grouping
    grp_cols = ["test_num", "pair"]

    # Precompute sums for pair-seeds aggregation
    def pair_seeds_filter(sub):
        # inside-pair seeds only
        inside = sub[sub["pinned_thread"].isin([sub["a"].iloc[0], sub["b"].iloc[0]])]
        a_sum = inside["thread1_total_wins"].sum()
        b_sum = inside["thread2_total_wins"].sum()
        return pd.Series({
            "pair_a_sum": a_sum,
            "pair_b_sum": b_sum,
            "fairness_pair_agg": jain_2(a_sum, b_sum)
        })

    def all_seeds_agg(sub):
        a_sum = sub["thread1_total_wins"].sum()
        b_sum = sub["thread2_total_wins"].sum()
        return pd.Series({
            "all_a_sum": a_sum,
            "all_b_sum": b_sum,
            "fairness_all_agg": jain_2(a_sum, b_sum)
        })

    pair_agg = df.groupby(grp_cols, as_index=False).apply(pair_seeds_filter)
    all_agg  = df.groupby(grp_cols, as_index=False).apply(all_seeds_agg)

    # Merge back
    out = df.merge(pair_agg, on=grp_cols, how="left").merge(all_agg, on=grp_cols, how="left")
    return out

def select_columns_to_print(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a tidy view with per-iteration wins (if present), totals, and fairnesses.
    """
    base_cols = ["test_num", "pinned_thread", "thread1", "thread2", "test_name",
                 "thread1_total_wins", "thread2_total_wins", "total",
                 "fairness_row", "fairness_pair_agg", "fairness_all_agg"]

    # Collect up to three rep columns if available (1..3)
    rep1_cols = [c for c in df.columns if re.match(r"^thread1_rep_\d+_wins$", c)]
    rep2_cols = [c for c in df.columns if re.match(r"^thread2_rep_\d+_wins$", c)]

    # Sort rep columns by numeric index
    def rep_key(c: str) -> int:
        try:
            return int(re.findall(r"\d+", c)[0])
        except Exception:
            return 10**9
    rep1_cols.sort(key=rep_key)
    rep2_cols.sort(key=rep_key)

    # We only show first three by default if more exist
    rep1_cols = rep1_cols[:3]
    rep2_cols = rep2_cols[:3]

    view_cols = ["test_num", "test_name", "pinned_thread", "thread1", "thread2"] + rep1_cols + rep2_cols + \
                ["thread1_total_wins", "thread2_total_wins", "total",
                 "fairness_pair_agg"]

    # Keep only columns that exist
    view_cols = [c for c in view_cols if c in df.columns]
    return df[view_cols]

def main():
    # Load and canonicalize
    raw = load_csv(INPUT_CSV)
    df = canonicalize_pairs(raw)

    # Compute totals (or from reps/long), per-row fairness
    df = compute_wins_columns(df)

    # Optional test names
    df["test_name"] = df["test_num"].apply(lambda x: test_label(int(x)))

    # Aggregate fairness matching graphs
    df = aggregate_fairness_like_graph(df)

    # Build view and sort by lowest per-row fairness
    view = select_columns_to_print(df)
    view_sorted = view.sort_values("fairness_pair_agg", ascending=True)

    # Print head
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 220)
    print(view_sorted.head(HEAD_N).to_string(index=False))

if __name__ == "__main__":
    main()
