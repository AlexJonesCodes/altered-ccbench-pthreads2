#!/usr/bin/env python3
"""Analyze repeat rates per seed core overall/per-thread with optional window regime analysis.

Outputs:
  * <prefix>_seed_summary.csv
  * <prefix>_seed_thread_summary.csv
  * <prefix>_window_summary.csv (when --window-size > 0)
  * <prefix>_window_thread_summary.csv (when --window-size > 0)
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
        help=(
            "Output prefix. Writes <prefix>_seed_summary.csv, "
            "<prefix>_seed_thread_summary.csv, and optional window CSVs"
        ),
    )
    p.add_argument("--winner-col", default="winner_thread_id", help="Winner id column.")
    p.add_argument("--rep-col", default="rep", help="Sequence order column; falls back to seq_idx if missing.")
    p.add_argument("--seed-col", default="seed_core", help="Column identifying seed core grouping.")
    p.add_argument(
        "--group-cols",
        default="",
        help=(
            "Optional comma-separated group columns. Default groups by op/run context + seed_core "
            "(run_id,op,op_id,core_set_id,thread_count,seed_core when available)."
        ),
    )
    p.add_argument("--trials", type=int, default=300, help="Monte Carlo shuffle trials per group.")
    p.add_argument("--mc-max-n", type=int, default=200_000, help="Skip O(trials*N) Monte Carlo for groups larger than this.")
    p.add_argument("--seed", type=int, default=7, help="RNG seed for reproducibility.")
    p.add_argument(
        "--window-size",
        type=int,
        default=0,
        help="If >0, compute windowed metrics using this many samples per window.",
    )
    p.add_argument(
        "--window-step",
        type=int,
        default=0,
        help="Window step size; if <=0, uses --window-size (non-overlapping windows).",
    )
    p.add_argument(
        "--cp-threshold",
        type=float,
        default=2.0,
        help="Change-point score threshold for cp_flag in seed summary.",
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


def dominant_share(seq: Sequence[str]) -> float:
    if not seq:
        return float("nan")
    counts = Counter(seq)
    return counts.most_common(1)[0][1] / len(seq)


def jains_fairness_index(seq: Sequence[str]) -> float:
    """Compute Jain's fairness index over winner frequencies in a window/sequence.

    J = (sum x_i)^2 / (n * sum x_i^2), where x_i are per-thread winner counts and
    n is number of observed unique winners in the sequence.
    """
    if not seq:
        return float("nan")
    counts = Counter(seq)
    vals = list(counts.values())
    n = len(vals)
    if n == 0:
        return float("nan")
    denom = n * sum(v * v for v in vals)
    if denom == 0:
        return float("nan")
    total = sum(vals)
    return (total * total) / denom


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


def detect_change_point(values: Sequence[float], min_seg: int = 2) -> Tuple[float, int, float, float, float]:
    clean = [v for v in values if not (v != v)]
    if len(clean) < (2 * min_seg):
        return float("nan"), -1, float("nan"), float("nan"), float("nan")

    best_score = float("-inf")
    best_idx = -1
    best_lm = float("nan")
    best_rm = float("nan")

    for i in range(min_seg, len(clean) - min_seg + 1):
        left = clean[:i]
        right = clean[i:]
        lm = statistics.fmean(left)
        rm = statistics.fmean(right)
        delta = abs(lm - rm)
        pooled = statistics.pstdev(clean)
        score = (delta / pooled) if pooled > 0 else 0.0
        if score > best_score:
            best_score = score
            best_idx = i
            best_lm = lm
            best_rm = rm

    return best_score, best_idx, best_lm, best_rm, abs(best_lm - best_rm)


def main() -> None:
    args = parse_args()
    in_path = Path(args.input)
    out_prefix = Path(args.out_prefix)

    window_size = args.window_size
    window_step = args.window_step if args.window_step > 0 else args.window_size
    if window_size < 0:
        raise ValueError("--window-size must be >= 0")
    if window_size > 0 and window_step <= 0:
        raise ValueError("--window-step must be > 0 when --window-size > 0")

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
    window_rows: List[Dict[str, object]] = []
    window_thread_rows: List[Dict[str, object]] = []

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
            overall = metric_baseline(observed_overall, [], mode)
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

        group_window_z: List[float] = []
        if window_size > 0 and n >= window_size:
            widx = 0
            for start in range(0, n - window_size + 1, window_step):
                wseq = seq[start:start + window_size]
                w_obs = repeat_rate(wseq)
                w_dom = dominant_share(wseq)
                w_jfi = jains_fairness_index(wseq)
                wn = len(wseq)
                w_thread_obs = per_thread_metrics(wseq)

                if args.trials <= 0 or wn > args.mc_max_n:
                    w_mode = "exact_repeat_only_n_too_large" if wn > args.mc_max_n else "exact_repeat_only_trials_0"
                    w_res = metric_baseline(w_obs, [], w_mode)
                    w_thread_global_trials: Dict[str, List[float]] = {}
                    w_thread_cond_trials: Dict[str, List[float]] = {}
                else:
                    w_mode = "mc_shuffle"
                    wwork = list(wseq)
                    w_trials: List[float] = []
                    w_thread_global_trials = {t: [] for t in w_thread_obs}
                    w_thread_cond_trials = {t: [] for t in w_thread_obs}
                    for _ in range(args.trials):
                        rng.shuffle(wwork)
                        w_trials.append(repeat_rate(wwork))
                        wt_metrics = per_thread_metrics(wwork)
                        for t in w_thread_obs:
                            m = wt_metrics.get(t)
                            if not m:
                                continue
                            w_thread_global_trials[t].append(m["repeat_rate_global"])
                            if m["prev_count"] > 0:
                                w_thread_cond_trials[t].append(m["repeat_rate_given_prev"])
                    w_res = metric_baseline(w_obs, w_trials, w_mode)

                group_window_z.append(w_res["zscore"])
                window_rows.append(
                    {
                        **base_key,
                        "window_index": widx,
                        "window_start": start,
                        "window_end_exclusive": start + window_size,
                        "window_n_samples": wn,
                        "window_repeat_rate": w_res["observed"],
                        "window_repeat_baseline_mean": w_res["baseline_mean"],
                        "window_repeat_baseline_std": w_res["baseline_std"],
                        "window_repeat_zscore": w_res["zscore"],
                        "window_repeat_p_ge": w_res["p_ge"],
                        "window_dominant_share": w_dom,
                        "window_jains_fairness": w_jfi,
                        "baseline_mode": w_res["baseline_mode"],
                    }
                )

                for thread_id, tobs in w_thread_obs.items():
                    wt_g = metric_baseline(tobs["repeat_rate_global"], w_thread_global_trials.get(thread_id, []), w_mode)
                    wt_c = metric_baseline(tobs["repeat_rate_given_prev"], w_thread_cond_trials.get(thread_id, []), w_mode)
                    window_thread_rows.append(
                        {
                            **base_key,
                            "window_index": widx,
                            "window_start": start,
                            "window_end_exclusive": start + window_size,
                            "window_n_samples": wn,
                            "thread_id": thread_id,
                            "prev_count": int(tobs["prev_count"]),
                            "repeat_count": int(tobs["repeat_count"]),
                            "thread_repeat_rate_global": wt_g["observed"],
                            "thread_repeat_global_baseline_mean": wt_g["baseline_mean"],
                            "thread_repeat_global_baseline_std": wt_g["baseline_std"],
                            "thread_repeat_global_zscore": wt_g["zscore"],
                            "thread_repeat_global_p_ge": wt_g["p_ge"],
                            "thread_repeat_rate_given_prev": wt_c["observed"],
                            "thread_repeat_given_prev_baseline_mean": wt_c["baseline_mean"],
                            "thread_repeat_given_prev_baseline_std": wt_c["baseline_std"],
                            "thread_repeat_given_prev_zscore": wt_c["zscore"],
                            "thread_repeat_given_prev_p_ge": wt_c["p_ge"],
                            "baseline_mode": w_mode,
                        }
                    )
                widx += 1

        cp_score, cp_idx, cp_left_mean, cp_right_mean, cp_abs_delta = detect_change_point(group_window_z)
        cp_flag = int(cp_score == cp_score and cp_score >= args.cp_threshold)

        clean_z = [z for z in group_window_z if z == z]
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
                "window_size": window_size,
                "window_step": window_step if window_size > 0 else 0,
                "n_windows": len(group_window_z),
                "window_repeat_zscore_mean": statistics.fmean(clean_z) if clean_z else float("nan"),
                "window_repeat_zscore_std": statistics.pstdev(clean_z) if len(clean_z) > 1 else float("nan"),
                "cp_score": cp_score,
                "cp_index": cp_idx,
                "cp_left_mean_z": cp_left_mean,
                "cp_right_mean_z": cp_right_mean,
                "cp_abs_delta_z": cp_abs_delta,
                "cp_flag": cp_flag,
                "baseline_mode": overall["baseline_mode"],
            }
        )

        for thread_id, obs in thread_obs.items():
            g_res = metric_baseline(obs["repeat_rate_global"], thread_global_trials.get(thread_id, []), mode)
            c_res = metric_baseline(obs["repeat_rate_given_prev"], thread_cond_trials.get(thread_id, []), mode)
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
    window_rows.sort(key=lambda r: (str(tuple(r.get(c, "") for c in group_cols)), safe_int(str(r.get("window_index", "0")), 0)))
    window_thread_rows.sort(
        key=lambda r: (
            str(tuple(r.get(c, "") for c in group_cols)),
            safe_int(str(r.get("window_index", "0")), 0),
            safe_int(str(r.get("thread_id", "0")), 0),
        )
    )

    seed_fields = list(group_cols) + [
        "n_samples",
        "n_transitions",
        "overall_repeat_rate",
        "overall_repeat_baseline_mean",
        "overall_repeat_baseline_std",
        "overall_repeat_zscore",
        "overall_repeat_p_ge",
        "window_size",
        "window_step",
        "n_windows",
        "window_repeat_zscore_mean",
        "window_repeat_zscore_std",
        "cp_score",
        "cp_index",
        "cp_left_mean_z",
        "cp_right_mean_z",
        "cp_abs_delta_z",
        "cp_flag",
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

    window_fields = list(group_cols) + [
        "window_index",
        "window_start",
        "window_end_exclusive",
        "window_n_samples",
        "window_repeat_rate",
        "window_repeat_baseline_mean",
        "window_repeat_baseline_std",
        "window_repeat_zscore",
        "window_repeat_p_ge",
        "window_dominant_share",
        "window_jains_fairness",
        "baseline_mode",
    ]

    window_thread_fields = list(group_cols) + [
        "window_index",
        "window_start",
        "window_end_exclusive",
        "window_n_samples",
        "thread_id",
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

    if window_size > 0:
        window_out = out_prefix.with_name(out_prefix.name + "_window_summary.csv")
        window_thread_out = out_prefix.with_name(out_prefix.name + "_window_thread_summary.csv")
        write_csv(window_out, window_rows, window_fields)
        write_csv(window_thread_out, window_thread_rows, window_thread_fields)
        print(f"Wrote {window_out}")
        print(f"Wrote {window_thread_out}")


if __name__ == "__main__":
    main()
