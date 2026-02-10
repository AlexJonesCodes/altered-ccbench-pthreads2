#!/usr/bin/env python3
"""Analyze repeat rates per seed core overall and per winner thread with shuffle baselines.

This script is specialized for run_winner_sequence_sweep-style CSV/TSV files.
For each seed_core group it reports:
  1) overall repeat rate (winner[t] == winner[t-1])
  2) per-thread repeat rates and per-thread self-transition propensity
  3) Monte Carlo permutation baseline statistics for each metric.
"""

from __future__ import annotations

import argparse
import csv
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", help="Input CSV/TSV path.")
    p.add_argument(
        "--out-prefix",
        default="winner_repeat_by_seed",
        help="Output prefix. Writes <prefix>_seed_summary.csv and <prefix>_seed_thread_summary.csv",
    )
    p.add_argument("--winner-col", default="winner_thread_id", help="Winner id column.")
    p.add_argument(
        "--rep-col",
        default="rep",
        help="Sequence order column; falls back to seq_idx if missing.",
    )
    p.add_argument(
        "--seed-col",
        default="seed_core",
        help="Column identifying seed core grouping.",
    )
    p.add_argument(
        "--group-cols",
        default="",
        help=(
            "Optional comma-separated group columns. Default groups by op/run context + seed_core "
            "(run_id,op,op_id,core_set_id,thread_count,seed_core when available)."
        ),
    )
    p.add_argument("--trials", type=int, default=300, help="Monte Carlo shuffle trials per group.")
    p.add_argument(
        "--mc-max-n",
        type=int,
        default=200_000,
        help="Skip O(trials*N) Monte Carlo for groups larger than this.",
    )
    p.add_argument("--seed", type=int, default=7, help="RNG seed for reproducibility.")
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


def safe_int(v: str, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def repeat_rate(seq: Sequence[str]) -> float:
    if len(seq) < 2:
        return 0.0
    same = sum(1 for i in range(1, len(seq)) if seq[i] == seq[i - 1])
    return same / (len(seq) - 1)


def choose_group_columns(headers: Sequence[str], user_cols: str) -> List[str]:
    if user_cols.strip():
        cols = [c.strip() for c in user_cols.split(",") if c.strip()]
        kept = [c for c in cols if c in headers]
        if not kept:
            raise ValueError("None of --group-cols exist in input headers")
        return kept

    preferred = ["run_id", "op", "op_id", "core_set_id", "thread_count", "seed_core"]
    picked = [c for c in preferred if c in headers]
    if picked:
        return picked
    exclude = {"rep", "seq_idx", "winner_thread_id", "winner_core"}
    return [h for h in headers if h not in exclude]


def write_csv(path: Path, rows: Iterable[Dict[str, object]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def _nan_result(mode: str) -> Dict[str, float]:
    return {
        "observed": float("nan"),
        "baseline_mean": float("nan"),
        "baseline_std": float("nan"),
        "zscore": float("nan"),
        "p_ge": float("nan"),
        "baseline_mode": mode,
    }


def metric_baseline(observed: float, trial_values: Sequence[float], baseline_mode: str) -> Dict[str, float]:
    if not trial_values:
        return {
            "observed": observed,
            "baseline_mean": float("nan"),
            "baseline_std": float("nan"),
            "zscore": float("nan"),
            "p_ge": float("nan"),
            "baseline_mode": baseline_mode,
        }
    mu = statistics.fmean(trial_values)
    sd = statistics.pstdev(trial_values) if len(trial_values) > 1 else 0.0
    z = (observed - mu) / sd if sd > 0 else float("nan")
    p_ge = sum(v >= observed for v in trial_values) / len(trial_values)
    return {
        "observed": observed,
        "baseline_mean": mu,
        "baseline_std": sd,
        "zscore": z,
        "p_ge": p_ge,
        "baseline_mode": baseline_mode,
    }


def per_thread_metrics(seq: Sequence[str]) -> Dict[str, Dict[str, float]]:
    if len(seq) < 2:
        return {}
    ntrans = len(seq) - 1
    same_counts = Counter()
    prev_counts = Counter()

    for i in range(1, len(seq)):
        prev = seq[i - 1]
        cur = seq[i]
        prev_counts[prev] += 1
        if cur == prev:
            same_counts[cur] += 1

    out: Dict[str, Dict[str, float]] = {}
    for t in sorted(set(seq), key=lambda x: (safe_int(x, 10**18), x)):
        same = same_counts[t]
        prev_n = prev_counts[t]
        out[t] = {
            "repeat_rate_global": same / ntrans if ntrans else float("nan"),
            "repeat_rate_given_prev": same / prev_n if prev_n else float("nan"),
            "prev_count": float(prev_n),
            "repeat_count": float(same),
        }
    return out


def main() -> None:
    args = parse_args()
    in_path = Path(args.input)
    out_prefix = Path(args.out_prefix)

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
    if args.seed_col not in group_cols and args.seed_col in headers:
        group_cols.append(args.seed_col)

    grouped: Dict[Tuple[str, ...], List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = tuple(row.get(c, "") for c in group_cols)
        grouped[key].append(row)

    rng = random.Random(args.seed)

    seed_rows: List[Dict[str, object]] = []
    seed_thread_rows: List[Dict[str, object]] = []

    for key, grows in grouped.items():
        grows.sort(key=lambda r: safe_int(r.get(rep_col, "0"), 0))
        seq = [str(r.get(args.winner_col, "")) for r in grows]
        seq = [x for x in seq if x != ""]
        if len(seq) < 2:
            continue

        n = len(seq)
        ntrans = n - 1
        observed_overall = repeat_rate(seq)

        thread_obs = per_thread_metrics(seq)

        if args.trials <= 0 or n > args.mc_max_n:
            mode = "exact_repeat_only_n_too_large" if n > args.mc_max_n else "exact_repeat_only_trials_0"
            overall = {
                "observed": observed_overall,
                "baseline_mean": float("nan"),
                "baseline_std": float("nan"),
                "zscore": float("nan"),
                "p_ge": float("nan"),
                "baseline_mode": mode,
            }
            thread_global_trials: Dict[str, List[float]] = {}
            thread_cond_trials: Dict[str, List[float]] = {}
        else:
            mode = "mc_shuffle"
            work = list(seq)
            overall_trials: List[float] = []
            thread_global_trials = {t: [] for t in thread_obs}
            thread_cond_trials = {t: [] for t in thread_obs}

            for _ in range(args.trials):
                rng.shuffle(work)
                overall_trials.append(repeat_rate(work))
                tmetrics = per_thread_metrics(work)
                for t in thread_obs:
                    m = tmetrics.get(t)
                    if not m:
                        continue
                    thread_global_trials[t].append(m["repeat_rate_global"])
                    if m["prev_count"] > 0:
                        thread_cond_trials[t].append(m["repeat_rate_given_prev"])

            overall = metric_baseline(observed_overall, overall_trials, mode)

        base_key = {c: key[i] for i, c in enumerate(group_cols)}
        seed_rows.append(
            {
                **base_key,
                "n_samples": n,
                "n_transitions": ntrans,
                "overall_repeat_rate": overall["observed"],
                "overall_repeat_baseline_mean": overall["baseline_mean"],
                "overall_repeat_baseline_std": overall["baseline_std"],
                "overall_repeat_zscore": overall["zscore"],
                "overall_repeat_p_ge": overall["p_ge"],
                "baseline_mode": overall["baseline_mode"],
            }
        )

        for thread_id, obs in thread_obs.items():
            g_res = metric_baseline(obs["repeat_rate_global"], thread_global_trials.get(thread_id, []), mode)
            c_trials = thread_cond_trials.get(thread_id, [])
            c_res = metric_baseline(obs["repeat_rate_given_prev"], c_trials, mode)

            seed_thread_rows.append(
                {
                    **base_key,
                    "thread_id": thread_id,
                    "n_samples": n,
                    "n_transitions": ntrans,
                    "prev_count": int(obs["prev_count"]),
                    "repeat_count": int(obs["repeat_count"]),
                    "thread_repeat_rate_global": g_res["observed"],
                    "thread_repeat_global_baseline_mean": g_res["baseline_mean"],
                    "thread_repeat_global_baseline_std": g_res["baseline_std"],
                    "thread_repeat_global_zscore": g_res["zscore"],
                    "thread_repeat_global_p_ge": g_res["p_ge"],
                    "thread_repeat_rate_given_prev": c_res["observed"],
                    "thread_repeat_given_prev_baseline_mean": c_res["baseline_mean"],
                    "thread_repeat_given_prev_baseline_std": c_res["baseline_std"],
                    "thread_repeat_given_prev_zscore": c_res["zscore"],
                    "thread_repeat_given_prev_p_ge": c_res["p_ge"],
                    "baseline_mode": mode,
                }
            )

    seed_rows.sort(key=lambda r: str(tuple(r.get(c, "") for c in group_cols)))
    seed_thread_rows.sort(key=lambda r: (str(tuple(r.get(c, "") for c in group_cols)), safe_int(str(r.get("thread_id", "0")), 0)))

    seed_fields = list(group_cols) + [
        "n_samples",
        "n_transitions",
        "overall_repeat_rate",
        "overall_repeat_baseline_mean",
        "overall_repeat_baseline_std",
        "overall_repeat_zscore",
        "overall_repeat_p_ge",
        "baseline_mode",
    ]

    thread_fields = list(group_cols) + [
        "thread_id",
        "n_samples",
        "n_transitions",
        "prev_count",
        "repeat_count",
        "thread_repeat_rate_global",
        "thread_repeat_global_baseline_mean",
        "thread_repeat_global_baseline_std",
        "thread_repeat_global_zscore",
        "thread_repeat_global_p_ge",
        "thread_repeat_rate_given_prev",
        "thread_repeat_given_prev_baseline_mean",
        "thread_repeat_given_prev_baseline_std",
        "thread_repeat_given_prev_zscore",
        "thread_repeat_given_prev_p_ge",
        "baseline_mode",
    ]

    seed_out = out_prefix.with_name(out_prefix.name + "_seed_summary.csv")
    seed_thread_out = out_prefix.with_name(out_prefix.name + "_seed_thread_summary.csv")
    write_csv(seed_out, seed_rows, seed_fields)
    write_csv(seed_thread_out, seed_thread_rows, thread_fields)

    print(f"Wrote {seed_out}")
    print(f"Wrote {seed_thread_out}")


if __name__ == "__main__":
    main()
