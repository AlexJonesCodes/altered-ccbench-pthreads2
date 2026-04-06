#!/usr/bin/env python3
"""Quantify dominance streaks from windowed stickiness analysis.

Reads a *_window_detail.csv produced by analyze_stickiness.py and computes
regime-level dominance metrics that capture how long individual threads
monopolise contention.

Key metrics per group:
  - Dominance streak lengths (max, mean, median, p90, p99)
  - Top-1/Top-2 thread dominance fraction (across all windows)
  - Streak coverage: fraction of windows in "long" streaks (>= threshold)
  - Effective number of dominant threads (1/HHI)
  - Gini coefficient of per-thread window share
  - Entropy of dominant-winner distribution

Outputs:
  <prefix>_dominance_summary.csv     One row per (group, window_size)
  <prefix>_dominance_streaks.csv     One row per individual streak

Example:
  python3 scripts/analyze_dominance_streaks.py results/analysis/stickiness \\
      --out-prefix results/analysis/dominance \\
      [--long-streak-threshold 10]
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Sequence, Tuple


#  CLI

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "prefix",
        help="Output prefix from analyze_stickiness.py "
             "(e.g. results/analysis/stickiness). "
             "Will read <prefix>_window_detail.csv.",
    )
    p.add_argument(
        "--out-prefix", default=None,
        help="Output prefix for dominance files (default: same as input prefix).",
    )
    p.add_argument(
        "--long-streak-threshold", type=int, default=10,
        help="Minimum streak length (in windows) to count as 'long' (default: 10).",
    )
    p.add_argument(
        "--dom-share-threshold", type=float, default=0.0,
        help="Only count a window as dominated if window_dominant_share >= this "
             "value (default: 0.0, i.e. count all windows).",
    )
    return p.parse_args()

#  I/O helpers

def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)
    return headers, rows


def write_csv(path: Path, rows: List[Dict[str, object]], fields: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    all_keys = set()
    for r in rows:
        all_keys.update(r.keys())
    extra = sorted(all_keys - set(fields))
    fieldnames = fields + extra

    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {path}  ({len(rows)} rows)")

#  Dominance streak computation

def compute_streaks(winners: List[str]) -> List[Tuple[str, int, int]]:
    """Compute consecutive dominance streaks.

    Returns list of (thread_id, start_index, length) for each streak.
    """
    if not winners:
        return []
    streaks: List[Tuple[str, int, int]] = []
    cur_thread = winners[0]
    cur_start = 0
    cur_len = 1
    for i in range(1, len(winners)):
        if winners[i] == cur_thread:
            cur_len += 1
        else:
            streaks.append((cur_thread, cur_start, cur_len))
            cur_thread = winners[i]
            cur_start = i
            cur_len = 1
    streaks.append((cur_thread, cur_start, cur_len))
    return streaks


def gini_coefficient(values: List[float]) -> float:
    """Compute Gini coefficient for a list of non-negative values."""
    if not values or all(v == 0 for v in values):
        return 0.0
    n = len(values)
    sorted_vals = sorted(values)
    cumsum = 0.0
    weighted_sum = 0.0
    for i, v in enumerate(sorted_vals):
        cumsum += v
        weighted_sum += (2 * (i + 1) - n - 1) * v
    total = sum(sorted_vals)
    return weighted_sum / (n * total) if total > 0 else 0.0


def hhi(counts: Counter) -> float:
    """Herfindahl-Hirschman Index: sum of squared shares."""
    total = sum(counts.values())
    if total == 0:
        return 0.0
    return sum((c / total) ** 2 for c in counts.values())


def shannon_entropy(counts: Counter) -> float:
    """Shannon entropy in bits."""
    total = sum(counts.values())
    if total == 0:
        return 0.0
    ent = 0.0
    for c in counts.values():
        if c > 0:
            p = c / total
            ent -= p * math.log2(p)
    return ent


def normalized_entropy(counts: Counter) -> float:
    """Entropy normalised to [0, 1] by dividing by log2(n_categories)."""
    n_cat = len(counts)
    if n_cat <= 1:
        return 0.0
    return shannon_entropy(counts) / math.log2(n_cat)


#  Main

def main() -> None:
    args = parse_args()
    prefix = Path(args.prefix)
    out_prefix = Path(args.out_prefix) if args.out_prefix else prefix

    window_csv = prefix.with_name(prefix.name + "_window_detail.csv")
    if not window_csv.exists():
        print(f"ERROR: cannot find {window_csv}", file=sys.stderr)
        sys.exit(1)

    headers, rows = read_csv(window_csv)
    print(f"Read {len(rows)} window rows from {window_csv}")

    # Identify group columns (everything that's not a window_* or baseline_mode column)
    window_cols = {h for h in headers if h.startswith("window_") or h == "baseline_mode"}
    group_cols = [h for h in headers if h not in window_cols]
    print(f"Group columns: {group_cols}")

    # Group rows by (group_key, window_size)
    grouped: Dict[Tuple[Tuple[str, ...], str], List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        gkey = tuple(row.get(c, "") for c in group_cols)
        ws = row.get("window_size", "")
        grouped[(gkey, ws)].append(row)

    summary_rows: List[Dict[str, object]] = []
    streak_rows: List[Dict[str, object]] = []

    for (gkey, ws), wrows in sorted(grouped.items()):
        # Sort by window_index
        wrows.sort(key=lambda r: int(r.get("window_index", "0")))

        # Extract dominant winners and shares
        winners = [r.get("window_dominant_winner", "") for r in wrows]
        shares = [float(r.get("window_dominant_share", "0")) for r in wrows]

        # Apply dom_share_threshold filter
        if args.dom_share_threshold > 0:
            filtered_winners = [
                w if s >= args.dom_share_threshold else ""
                for w, s in zip(winners, shares)
            ]
        else:
            filtered_winners = winners

        n_windows = len(filtered_winners)
        base_key = {c: gkey[i] for i, c in enumerate(group_cols)}

        # Compute streaks
        streaks = compute_streaks(filtered_winners)
        streak_lengths = [length for _, _, length in streaks]

        # Record individual streaks
        for thread_id, start_idx, length in streaks:
            streak_rows.append({
                **base_key,
                "window_size": ws,
                "streak_thread": thread_id,
                "streak_start_window": start_idx,
                "streak_length": length,
            })

        # Window-winner distribution
        winner_counts = Counter(filtered_winners)
        # Remove empty string counts (filtered-out windows)
        if "" in winner_counts:
            del winner_counts[""]

        # Top-1 and Top-2 dominance fractions
        most_common = winner_counts.most_common()
        top1_thread = most_common[0][0] if most_common else ""
        top1_frac = most_common[0][1] / n_windows if most_common else 0.0
        top2_frac = sum(c for _, c in most_common[:2]) / n_windows if len(most_common) >= 2 else top1_frac

        # HHI and effective number of dominant threads
        hhi_val = hhi(winner_counts)
        eff_threads = 1.0 / hhi_val if hhi_val > 0 else float("nan")

        # Gini coefficient of per-thread window shares
        per_thread_shares = [c / n_windows for c in winner_counts.values()]
        gini = gini_coefficient(per_thread_shares)

        # Entropy
        ent = shannon_entropy(winner_counts)
        norm_ent = normalized_entropy(winner_counts)

        # Streak statistics
        long_thresh = args.long_streak_threshold
        long_streaks = [l for l in streak_lengths if l >= long_thresh]
        coverage_long = sum(long_streaks) / n_windows if n_windows > 0 else 0.0

        max_streak = max(streak_lengths) if streak_lengths else 0
        mean_streak = statistics.fmean(streak_lengths) if streak_lengths else 0.0
        median_streak = statistics.median(streak_lengths) if streak_lengths else 0.0
        n_streaks = len(streak_lengths)

        # Percentiles
        sorted_lengths = sorted(streak_lengths)
        p90 = _percentile(sorted_lengths, 0.90)
        p99 = _percentile(sorted_lengths, 0.99)

        # Fraction of total windows consumed by the single longest streak
        max_streak_frac = max_streak / n_windows if n_windows > 0 else 0.0

        # Mean dominant share across windows (how "strong" is dominance typically)
        mean_dom_share = statistics.fmean(shares) if shares else float("nan")

        summary_rows.append({
            **base_key,
            "window_size": ws,
            "n_windows": n_windows,
            "n_streaks": n_streaks,
            # Streak length statistics
            "max_streak_length": max_streak,
            "max_streak_frac": round(max_streak_frac, 4),
            "mean_streak_length": round(mean_streak, 2),
            "median_streak_length": round(median_streak, 1),
            "p90_streak_length": round(p90, 1),
            "p99_streak_length": round(p99, 1),
            # Long streak coverage
            "long_streak_threshold": long_thresh,
            "n_long_streaks": len(long_streaks),
            "long_streak_coverage": round(coverage_long, 4),
            # Distribution of dominance across threads
            "top1_dominant_thread": top1_thread,
            "top1_dominance_frac": round(top1_frac, 4),
            "top2_dominance_frac": round(top2_frac, 4),
            "hhi": round(hhi_val, 4),
            "effective_dominant_threads": round(eff_threads, 2),
            "gini_coefficient": round(gini, 4),
            "entropy_bits": round(ent, 4),
            "normalized_entropy": round(norm_ent, 4),
            # Mean within-window dominant share
            "mean_window_dominant_share": round(mean_dom_share, 4),
        })

    # ── Write outputs ────────────────────────────────────────────────────────
    summary_fields = list(group_cols) + [
        "window_size", "n_windows", "n_streaks",
        "max_streak_length", "max_streak_frac",
        "mean_streak_length", "median_streak_length",
        "p90_streak_length", "p99_streak_length",
        "long_streak_threshold", "n_long_streaks", "long_streak_coverage",
        "top1_dominant_thread", "top1_dominance_frac", "top2_dominance_frac",
        "hhi", "effective_dominant_threads",
        "gini_coefficient", "entropy_bits", "normalized_entropy",
        "mean_window_dominant_share",
    ]

    streak_fields = list(group_cols) + [
        "window_size", "streak_thread", "streak_start_window", "streak_length",
    ]

    summary_out = out_prefix.with_name(out_prefix.name + "_dominance_summary.csv")
    streak_out = out_prefix.with_name(out_prefix.name + "_dominance_streaks.csv")

    write_csv(summary_out, summary_rows, summary_fields)
    write_csv(streak_out, streak_rows, streak_fields)

    # ── Print headline results ─────────────────────────────────────────────
    if summary_rows:
        # Show the most monopolised group
        by_max = sorted(summary_rows, key=lambda r: -r.get("max_streak_frac", 0))
        top = by_max[0]
        gcols_desc = ", ".join(f"{c}={top.get(c, '?')}" for c in group_cols)
        print(f"\nMost monopolised group (by max_streak_frac): {gcols_desc}, ws={top['window_size']}")
        print(f"  Max streak length:          {top['max_streak_length']} windows "
              f"({top['max_streak_frac']:.1%} of total)")
        print(f"  Mean streak length:         {top['mean_streak_length']}")
        print(f"  Long streak coverage (>={top['long_streak_threshold']}): "
              f"{top['long_streak_coverage']:.1%}")
        print(f"  Top-1 thread dominance:     {top['top1_dominant_thread']} "
              f"({top['top1_dominance_frac']:.1%} of windows)")
        print(f"  Effective dominant threads:  {top['effective_dominant_threads']}")
        print(f"  Gini coefficient:           {top['gini_coefficient']}")


def _percentile(sorted_vals: List[int], p: float) -> float:
    """Simple percentile from sorted list using linear interpolation."""
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    idx = p * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


if __name__ == "__main__":
    main()
