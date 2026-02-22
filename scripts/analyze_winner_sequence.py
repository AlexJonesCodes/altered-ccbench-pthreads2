#!/usr/bin/env python3
"""Analyze temporal locality in run_winner_sequence_sweep CSV/TSV outputs.

This script focuses on winner-sequence behavior (e.g., winner_thread_id over rep)
and computes metrics that help detect temporal locality, burstiness, and non-random
winning streaks.

Example:
    python3 scripts/analyze_winner_sequence.py results.csv \
      --out-prefix analysis/winner_seq --trials 500 --lags 1,2,4,8

Null model used by baseline checks:
  Keep winner counts fixed but randomize order (permutation null). This tests whether
  observed temporal structure is stronger than expected from winner popularity alone.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", help="Input CSV/TSV file from run_winner_sequence_sweep.")
    p.add_argument(
        "--out-prefix",
        default="winner_sequence_analysis",
        help="Prefix for generated outputs (summary and transitions CSV).",
    )
    p.add_argument(
        "--winner-col",
        default="winner_thread_id",
        help="Column containing the winner ID sequence.",
    )
    p.add_argument(
        "--rep-col",
        default="rep",
        help=("Column used as sequence order within each group. "
              "If missing, seq_idx is used automatically when available."),
    )
    p.add_argument(
        "--group-cols",
        default="",
        help=(
            "Comma-separated list of grouping columns. If empty, the script auto-selects "
            "stable experiment columns (e.g., run_id/op/core_set_id/thread_count/seed_core)."
        ),
    )
    p.add_argument(
        "--lags",
        default="1,2,4,8",
        help=(
            "Comma-separated positive integer offsets for lag-k same-winner rates. "
            "Example: lag1 checks winner[t] == winner[t-1], lag4 checks winner[t] == winner[t-4]."
        ),
    )
    p.add_argument(
        "--trials",
        type=int,
        default=300,
        help="Monte Carlo permutation trials per group for non-random baseline comparison.",
    )
    p.add_argument(
        "--mc-max-n",
        type=int,
        default=200_000,
        help=(
            "Skip O(trials*N) Monte Carlo when group length exceeds this value. "
            "Large groups still get exact repeat-rate expectation under the null."
        ),
    )
    p.add_argument(
        "--seed",
        type=int,
        default=7,
        help="Random seed for reproducible Monte Carlo baselines.",
    )
    return p.parse_args()


def detect_dialect(path: Path) -> csv.Dialect:
    sample = path.read_text(encoding="utf-8", errors="replace")[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        class Fallback(csv.excel):
            delimiter = ","

        return Fallback()


def read_rows(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    dialect = detect_dialect(path)
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError("Input file has no header row.")
        rows = list(reader)
        if not rows:
            raise ValueError("Input file has no data rows.")
        return reader.fieldnames, rows


def safe_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: str, default: float = float("nan")) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def longest_run(seq: Sequence[str]) -> int:
    if not seq:
        return 0
    best = 1
    cur = 1
    for i in range(1, len(seq)):
        if seq[i] == seq[i - 1]:
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    return best


def run_lengths(seq: Sequence[str]) -> List[int]:
    if not seq:
        return []
    runs: List[int] = []
    cur = 1
    for i in range(1, len(seq)):
        if seq[i] == seq[i - 1]:
            cur += 1
        else:
            runs.append(cur)
            cur = 1
    runs.append(cur)
    return runs


def repeat_rate(seq: Sequence[str]) -> float:
    if len(seq) < 2:
        return 0.0
    same = sum(1 for i in range(1, len(seq)) if seq[i] == seq[i - 1])
    return same / (len(seq) - 1)


def lag_same_rate(seq: Sequence[str], lag: int) -> float:
    if lag <= 0 or len(seq) <= lag:
        return float("nan")
    same = sum(1 for i in range(lag, len(seq)) if seq[i] == seq[i - lag])
    return same / (len(seq) - lag)


def normalized_entropy(counts: Counter) -> float:
    total = sum(counts.values())
    k = len(counts)
    if total == 0 or k <= 1:
        return 0.0
    probs = [c / total for c in counts.values()]
    h = -sum(p * math.log2(p) for p in probs if p > 0)
    return h / math.log2(k)


def transition_counts(seq: Sequence[str]) -> Counter:
    c: Counter = Counter()
    for i in range(1, len(seq)):
        c[(seq[i - 1], seq[i])] += 1
    return c


def shuffle_in_place(seq: List[str], rng: random.Random) -> None:
    """Shuffle sequence in-place.

    Python's random.shuffle uses the Fisher-Yates/Knuth family of algorithms in
    optimized C code, so it is typically faster than a pure-Python manual
    Fisher-Yates loop while providing the same unbiased permutation behavior.
    """
    rng.shuffle(seq)


def baseline_shuffle_metrics(
    seq: Sequence[str],
    trials: int,
    mc_max_n: int,
    rng: random.Random,
) -> Dict[str, float]:
    n = len(seq)
    if n < 2:
        return {
            "repeat_mean": float("nan"),
            "repeat_std": float("nan"),
            "repeat_p_ge": float("nan"),
            "maxrun_mean": float("nan"),
            "maxrun_std": float("nan"),
            "maxrun_p_ge": float("nan"),
            "repeat_z": float("nan"),
            "maxrun_z": float("nan"),
            "baseline_mode": "insufficient_data",
        }

    counts = Counter(seq)
    # Exact E[P(X_t == X_{t-1})] under permutation null with fixed counts.
    repeat_mean_exact = sum(c * (c - 1) for c in counts.values()) / (n * (n - 1))
    observed_repeat = repeat_rate(seq)

    if trials <= 0 or n > mc_max_n:
        return {
            "repeat_mean": repeat_mean_exact,
            "repeat_std": float("nan"),
            "repeat_p_ge": float("nan"),
            "maxrun_mean": float("nan"),
            "maxrun_std": float("nan"),
            "maxrun_p_ge": float("nan"),
            "repeat_z": float("nan"),
            "maxrun_z": float("nan"),
            "baseline_mode": (
                "exact_repeat_only_n_too_large" if n > mc_max_n else "exact_repeat_only_trials_0"
            ),
        }

    observed_maxrun = float(longest_run(seq))
    repeats: List[float] = []
    maxruns: List[float] = []

    work = list(seq)
    for _ in range(trials):
        shuffle_in_place(work, rng)
        repeats.append(repeat_rate(work))
        maxruns.append(float(longest_run(work)))

    rep_mean = repeat_mean_exact
    max_mean = statistics.fmean(maxruns)
    rep_std = statistics.pstdev(repeats) if len(repeats) > 1 else 0.0
    max_std = statistics.pstdev(maxruns) if len(maxruns) > 1 else 0.0

    rep_p_ge = sum(x >= observed_repeat for x in repeats) / len(repeats)
    max_p_ge = sum(x >= observed_maxrun for x in maxruns) / len(maxruns)

    rep_z = (observed_repeat - rep_mean) / rep_std if rep_std > 0 else float("nan")
    max_z = (observed_maxrun - max_mean) / max_std if max_std > 0 else float("nan")

    return {
        "repeat_mean": rep_mean,
        "repeat_std": rep_std,
        "repeat_p_ge": rep_p_ge,
        "maxrun_mean": max_mean,
        "maxrun_std": max_std,
        "maxrun_p_ge": max_p_ge,
        "repeat_z": rep_z,
        "maxrun_z": max_z,
        "baseline_mode": "exact_repeat_plus_mc",
    }


def choose_group_columns(headers: Sequence[str], user_group_cols: str) -> List[str]:
    if user_group_cols.strip():
        cols = [c.strip() for c in user_group_cols.split(",") if c.strip()]
        return [c for c in cols if c in headers]

    preferred = [
        "run_id",
        "op",
        "op_id",
        "core_set_id",
        "thread_count",
        "seed_core",
    ]
    picked = [c for c in preferred if c in headers]

    if picked:
        return picked

    exclude = {"rep", "winner_thread_id", "winner_core"}
    return [h for h in headers if h not in exclude]


def write_csv(path: Path, rows: Iterable[Dict[str, object]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def main() -> None:
    args = parse_args()
    in_path = Path(args.input)
    out_prefix = Path(args.out_prefix)
    lag_values = [int(x) for x in args.lags.split(",") if x.strip()]
    if not lag_values:
        raise ValueError("--lags must contain at least one integer, e.g. 1,2,4,8")
    if any(lag <= 0 for lag in lag_values):
        raise ValueError("--lags values must be positive integers")

    headers, rows = read_rows(in_path)

    if args.winner_col not in headers:
        raise ValueError(f"Missing winner column: {args.winner_col}")

    rep_col = args.rep_col
    if rep_col not in headers:
        if "seq_idx" in headers:
            rep_col = "seq_idx"
            print("INFO: --rep-col not found; using seq_idx for sequence ordering.")
        else:
            raise ValueError(f"Missing sequence column: {args.rep_col}")

    group_cols = choose_group_columns(headers, args.group_cols)

    grouped: Dict[Tuple[str, ...], List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = tuple(row.get(c, "") for c in group_cols)
        grouped[key].append(row)

    rng = random.Random(args.seed)

    summary_rows: List[Dict[str, object]] = []
    transition_rows: List[Dict[str, object]] = []

    for key, grows in grouped.items():
        grows.sort(key=lambda r: safe_int(r.get(rep_col, "0"), 0))
        seq = [str(r.get(args.winner_col, "")) for r in grows]
        seq = [x for x in seq if x != ""]
        if not seq:
            continue

        counts = Counter(seq)
        n = len(seq)
        rr = repeat_rate(seq)
        runs = run_lengths(seq)
        max_run = longest_run(seq)
        mean_run = statistics.fmean(runs) if runs else 0.0
        med_run = statistics.median(runs) if runs else 0.0
        ent = normalized_entropy(counts)

        trans = transition_counts(seq)
        total_trans = sum(trans.values())
        self_trans = sum(v for (a, b), v in trans.items() if a == b)
        stay_prob = (self_trans / total_trans) if total_trans else 0.0

        baseline = baseline_shuffle_metrics(seq, args.trials, args.mc_max_n, rng)

        top_winner, top_count = counts.most_common(1)[0]

        row_out: Dict[str, object] = {c: key[i] for i, c in enumerate(group_cols)}
        row_out.update(
            {
                "n_samples": n,
                "unique_winners": len(counts),
                "dominant_winner": top_winner,
                "dominant_share": top_count / n,
                "repeat_rate": rr,
                "stay_probability": stay_prob,
                "mean_run_length": mean_run,
                "median_run_length": med_run,
                "max_run_length": max_run,
                "normalized_entropy": ent,
                "repeat_rate_baseline_mean": baseline["repeat_mean"],
                "repeat_rate_baseline_std": baseline["repeat_std"],
                "repeat_rate_zscore": baseline["repeat_z"],
                "repeat_rate_p_ge": baseline["repeat_p_ge"],
                "max_run_baseline_mean": baseline["maxrun_mean"],
                "max_run_baseline_std": baseline["maxrun_std"],
                "max_run_zscore": baseline["maxrun_z"],
                "max_run_p_ge": baseline["maxrun_p_ge"],
                "baseline_mode": baseline["baseline_mode"],
                "temporal_locality_score": (
                    safe_float(str(baseline["repeat_z"]), 0.0)
                    + safe_float(str(baseline["maxrun_z"]), 0.0)
                ),
            }
        )

        for lag in lag_values:
            row_out[f"lag{lag}_same_rate"] = lag_same_rate(seq, lag)

        summary_rows.append(row_out)

        for (from_w, to_w), c in trans.items():
            transition_rows.append(
                {
                    **{col: key[i] for i, col in enumerate(group_cols)},
                    "from_winner": from_w,
                    "to_winner": to_w,
                    "count": c,
                    "probability": c / total_trans if total_trans else float("nan"),
                }
            )

    summary_rows.sort(key=lambda r: safe_float(str(r.get("temporal_locality_score", "nan")), -1e9), reverse=True)

    summary_fields = (
        list(group_cols)
        + [
            "n_samples",
            "unique_winners",
            "dominant_winner",
            "dominant_share",
            "repeat_rate",
            "stay_probability",
            "mean_run_length",
            "median_run_length",
            "max_run_length",
            "normalized_entropy",
            "repeat_rate_baseline_mean",
            "repeat_rate_baseline_std",
            "repeat_rate_zscore",
            "repeat_rate_p_ge",
            "max_run_baseline_mean",
            "max_run_baseline_std",
            "max_run_zscore",
            "max_run_p_ge",
            "baseline_mode",
            "temporal_locality_score",
        ]
        + [f"lag{lag}_same_rate" for lag in lag_values]
    )

    transition_fields = list(group_cols) + ["from_winner", "to_winner", "count", "probability"]

    summary_path = out_prefix.with_name(out_prefix.name + "_summary.csv")
    trans_path = out_prefix.with_name(out_prefix.name + "_transitions.csv")

    write_csv(summary_path, summary_rows, summary_fields)
    write_csv(trans_path, transition_rows, transition_fields)

    print(f"Read {len(rows)} rows across {len(grouped)} groups")
    print(f"Wrote summary: {summary_path}")
    print(f"Wrote transitions: {trans_path}")

    if summary_rows:
        top = summary_rows[0]
        gdesc = ", ".join(f"{c}={top[c]}" for c in group_cols)
        print("Top temporal-locality group:")
        print(f"  {gdesc}")
        print(
            "  "
            f"score={top['temporal_locality_score']:.3f}, "
            f"repeat_rate={top['repeat_rate']:.3f}, "
            f"max_run={top['max_run_length']}, "
            f"baseline_mode={top['baseline_mode']}"
        )

    print(
        "Baseline interpretation: low p-values / high positive z-scores indicate more "
        "winner persistence than expected from random ordering with the same winner mix."
    )


if __name__ == "__main__":
    main()
