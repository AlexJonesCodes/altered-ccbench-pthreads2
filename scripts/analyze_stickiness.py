#!/usr/bin/env python3
"""Comprehensive thread-contention stickiness / temporal-locality analysis.

Reads a unified winner_sequence.csv (from run_stickiness_study.sh or
run_winner_sequence_sweep.sh) and produces multi-scale analysis of whether
thread winning patterns exhibit statistically significant temporal structure
("stickiness") that aggregate fairness metrics hide.

Key analyses:
  1. Overall repeat rate with Monte Carlo permutation baseline
  2. Run-length distribution analysis with KS-like comparison
  3. Wald-Wolfowitz runs test (multi-category generalisation)
  4. Multi-scale windowed analysis (repeat rate, Jain's fairness, dominant share)
  5. Per-thread conditional repeat rates (P(win | won last))
  6. Transition matrix with chi-squared independence test
  7. Lag-k autocorrelation of same-winner indicator
  8. Recursive binary-segmentation change-point detection
  9. Benjamini-Hochberg FDR correction across all p-values
 10. Effect sizes and Monte Carlo confidence intervals

Outputs:
  <prefix>_group_summary.csv      One row per experiment group
  <prefix>_window_detail.csv      One row per (group, window_size, window_index)
  <prefix>_thread_summary.csv     Per-thread metrics per group
  <prefix>_regime_summary.csv     Change-point / regime detail per group

Example:
  python3 scripts/analyze_stickiness.py results/collect/winner_sequence.csv \\
    --out-prefix results/analysis/stickiness \\
    --window-sizes 50,200,1000 --trials 1000
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("input", help="Input CSV path (winner_sequence.csv).")
    p.add_argument(
        "--out-prefix", default="stickiness",
        help="Output file prefix (default: stickiness).",
    )
    p.add_argument("--winner-col", default="winner_thread_id", help="Winner ID column.")
    p.add_argument("--rep-col", default="rep", help="Sequence-order column (falls back to seq_idx).")
    p.add_argument(
        "--group-cols", default="",
        help="Comma-separated grouping columns. If empty, auto-selects from standard columns.",
    )
    p.add_argument(
        "--window-sizes", default="50,200,1000",
        help="Comma-separated window sizes for multi-scale analysis.",
    )
    p.add_argument("--trials", type=int, default=1000, help="Monte Carlo permutation trials.")
    p.add_argument("--mc-max-n", type=int, default=200_000, help="Skip MC for groups exceeding this length.")
    p.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility.")
    p.add_argument("--lags", default="1,2,4,8,16", help="Comma-separated lag values for autocorrelation.")
    p.add_argument(
        "--n-threads", type=int, default=0,
        help="Total competing threads (for correct Jain's index). 0 = infer from thread_count column or data.",
    )
    p.add_argument("--max-changepoints", type=int, default=5, help="Max change-points per group.")
    p.add_argument("--cp-min-segment", type=int, default=3, help="Minimum segment length for change-point detection.")
    p.add_argument(
        "--fdr-alpha", type=float, default=0.05,
        help="FDR significance level for Benjamini-Hochberg correction.",
    )
    return p.parse_args()


# ═══════════════════════════════════════════════════════════════════════════════
#  I/O Utilities
# ═══════════════════════════════════════════════════════════════════════════════

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
        return list(reader.fieldnames), rows


def write_csv(path: Path, rows: List[Dict[str, object]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def safe_int(v: str, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def choose_group_columns(headers: Sequence[str], user_cols: str) -> List[str]:
    if user_cols.strip():
        cols = [c.strip() for c in user_cols.split(",") if c.strip()]
        out = [c for c in cols if c in headers]
        if not out:
            raise ValueError("None of --group-cols found in input headers")
        return out
    preferred = ["run_id", "op", "op_id", "core_set_id", "thread_count", "seed_core"]
    picked = [c for c in preferred if c in headers]
    if picked:
        return picked
    exclude = {"rep", "seq_idx", "winner_thread_id", "winner_core", "group", "role"}
    return [h for h in headers if h not in exclude]


# ═══════════════════════════════════════════════════════════════════════════════
#  Core Metrics
# ═══════════════════════════════════════════════════════════════════════════════

def repeat_rate(seq: Sequence[str]) -> float:
    """Fraction of transitions where the same thread wins consecutively."""
    if len(seq) < 2:
        return 0.0
    return sum(1 for i in range(1, len(seq)) if seq[i] == seq[i - 1]) / (len(seq) - 1)


def exact_repeat_expectation(counts: Counter) -> float:
    """Exact E[repeat_rate] under permutation null with fixed marginal counts.

    E[P(X_t == X_{t-1})] = sum(n_i * (n_i - 1)) / (N * (N - 1))
    """
    n = sum(counts.values())
    if n < 2:
        return 0.0
    return sum(c * (c - 1) for c in counts.values()) / (n * (n - 1))


def run_lengths(seq: Sequence[str]) -> List[int]:
    """Return list of consecutive same-winner run lengths."""
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


def lag_same_rate(seq: Sequence[str], lag: int) -> float:
    """P(winner[t] == winner[t-lag])."""
    if lag <= 0 or len(seq) <= lag:
        return float("nan")
    return sum(1 for i in range(lag, len(seq)) if seq[i] == seq[i - lag]) / (len(seq) - lag)


def normalized_entropy(counts: Counter) -> float:
    """Shannon entropy normalised to [0, 1]."""
    total = sum(counts.values())
    k = len(counts)
    if total == 0 or k <= 1:
        return 0.0
    probs = [c / total for c in counts.values()]
    h = -sum(p * math.log2(p) for p in probs if p > 0)
    return h / math.log2(k)


def dominant_share(seq: Sequence[str]) -> float:
    if not seq:
        return float("nan")
    return Counter(seq).most_common(1)[0][1] / len(seq)


def jains_fairness(counts: Counter, n_threads: int) -> float:
    """Jain's fairness index using the TOTAL number of competing threads.

    Using only observed unique winners hides the unfairness of threads that
    got zero wins.  We include zero-win threads as explicit zeros.
    """
    if n_threads <= 0:
        n_threads = len(counts)
    if n_threads == 0:
        return float("nan")
    vals = list(counts.values())
    # Pad with zeros for threads that never won
    while len(vals) < n_threads:
        vals.append(0)
    total = sum(vals)
    sum_sq = sum(v * v for v in vals)
    if sum_sq == 0:
        return float("nan")
    return (total * total) / (n_threads * sum_sq)


# ═══════════════════════════════════════════════════════════════════════════════
#  Wald-Wolfowitz Runs Test (multi-category generalisation)
# ═══════════════════════════════════════════════════════════════════════════════

def wald_wolfowitz_runs_test(seq: Sequence[str]) -> Dict[str, float]:
    """Multi-category Wald-Wolfowitz runs test.

    Tests H0: the sequence is a random arrangement of the observed counts.
    Fewer runs than expected => stickiness/clustering.

    Returns observed runs, expected, variance, z-score, and two-sided p-value
    (normal approximation).
    """
    n = len(seq)
    if n < 2:
        return {"runs_observed": float("nan"), "runs_expected": float("nan"),
                "runs_variance": float("nan"), "runs_zscore": float("nan"),
                "runs_pvalue_2sided": float("nan")}

    # Count runs
    r = 1
    for i in range(1, n):
        if seq[i] != seq[i - 1]:
            r += 1

    counts = Counter(seq)
    ni_vals = list(counts.values())

    # E[R] = 1 + (N^2 - sum(n_i^2)) / N
    sum_ni2 = sum(c * c for c in ni_vals)
    sum_ni3 = sum(c * c * c for c in ni_vals)
    e_r = 1.0 + (n * n - sum_ni2) / n

    # Var[R] = [sum(n_i^2) * (sum(n_i^2) + N^2) - 2*N*sum(n_i^3) - N^3] / [N^2 * (N-1)]
    numerator = sum_ni2 * (sum_ni2 + n * n) - 2 * n * sum_ni3 - n * n * n
    denominator = n * n * (n - 1)
    var_r = numerator / denominator if denominator > 0 else 0.0

    if var_r > 0:
        z = (r - e_r) / math.sqrt(var_r)
        # Two-sided p-value via normal approximation
        p_2sided = 2.0 * _normal_cdf(-abs(z))
    else:
        z = float("nan")
        p_2sided = float("nan")

    return {
        "runs_observed": float(r),
        "runs_expected": e_r,
        "runs_variance": var_r,
        "runs_zscore": z,
        "runs_pvalue_2sided": p_2sided,
    }


def _normal_cdf(x: float) -> float:
    """Standard normal CDF approximation (Abramowitz & Stegun 26.2.17)."""
    if math.isnan(x):
        return float("nan")
    return 0.5 * math.erfc(-x / math.sqrt(2))


# ═══════════════════════════════════════════════════════════════════════════════
#  Transition Matrix + Chi-Squared Independence Test
# ═══════════════════════════════════════════════════════════════════════════════

def transition_matrix_test(seq: Sequence[str]) -> Dict[str, float]:
    """Test whether transition probabilities depend on previous winner.

    H0: P(winner_t = j | winner_{t-1} = i) = P(winner_t = j) for all i, j
    i.e., the previous winner doesn't influence the next winner.

    Uses chi-squared test of the transition matrix against independence.
    """
    if len(seq) < 3:
        return {"chi2_statistic": float("nan"), "chi2_df": float("nan"),
                "chi2_pvalue": float("nan"), "markov_stickiness": float("nan")}

    trans: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for i in range(1, len(seq)):
        trans[seq[i - 1]][seq[i]] += 1

    states = sorted(set(seq), key=lambda x: (safe_int(x, 10**18), x))
    k = len(states)
    if k < 2:
        return {"chi2_statistic": float("nan"), "chi2_df": float("nan"),
                "chi2_pvalue": float("nan"), "markov_stickiness": float("nan")}

    total_trans = sum(trans[i][j] for i in states for j in states)
    if total_trans == 0:
        return {"chi2_statistic": float("nan"), "chi2_df": float("nan"),
                "chi2_pvalue": float("nan"), "markov_stickiness": float("nan")}

    # Row and column sums
    row_sums = {i: sum(trans[i][j] for j in states) for i in states}
    col_sums = {j: sum(trans[i][j] for i in states) for j in states}

    # Chi-squared statistic
    chi2 = 0.0
    for i in states:
        for j in states:
            observed = trans[i][j]
            expected = (row_sums[i] * col_sums[j]) / total_trans if total_trans > 0 else 0
            if expected > 0:
                chi2 += (observed - expected) ** 2 / expected

    df = (k - 1) * (k - 1)

    # Chi-squared p-value approximation
    p_value = _chi2_survival(chi2, df)

    # Markov stickiness: average diagonal excess over independence
    # (how much more likely is self-transition than expected under independence)
    diag_excess = 0.0
    for s in states:
        if row_sums[s] > 0 and total_trans > 0:
            observed_p = trans[s][s] / row_sums[s]
            expected_p = col_sums[s] / total_trans
            diag_excess += (observed_p - expected_p)
    diag_excess /= k

    return {
        "chi2_statistic": chi2,
        "chi2_df": float(df),
        "chi2_pvalue": p_value,
        "markov_stickiness": diag_excess,
    }


def _chi2_survival(x: float, df: int) -> float:
    """Approximate upper-tail probability for chi-squared distribution.

    Uses the Wilson-Hilferty normal approximation for moderate-to-large df.
    For very small p-values, precision is limited but sufficient for significance testing.
    """
    if math.isnan(x) or df <= 0:
        return float("nan")
    if x <= 0:
        return 1.0
    # Wilson-Hilferty transformation: ((X/df)^(1/3) - (1 - 2/(9*df))) / sqrt(2/(9*df))
    if df >= 1:
        k = df
        z = ((x / k) ** (1.0 / 3.0) - (1.0 - 2.0 / (9.0 * k))) / math.sqrt(2.0 / (9.0 * k))
        return 1.0 - _normal_cdf(z)
    return float("nan")


# ═══════════════════════════════════════════════════════════════════════════════
#  Monte Carlo Permutation Testing
# ═══════════════════════════════════════════════════════════════════════════════

def mc_permutation_test(
    seq: Sequence[str],
    trials: int,
    mc_max_n: int,
    rng: random.Random,
) -> Dict[str, object]:
    """Full Monte Carlo permutation test with confidence intervals.

    Shuffles the sequence (preserving marginal winner counts) and computes
    repeat rate and max run length for each trial.
    """
    n = len(seq)
    counts = Counter(seq)
    exact_mean = exact_repeat_expectation(counts)

    obs_repeat = repeat_rate(seq)
    obs_runs = run_lengths(seq)
    obs_maxrun = max(obs_runs) if obs_runs else 0
    obs_mean_run = statistics.fmean(obs_runs) if obs_runs else 0.0

    result: Dict[str, object] = {
        "observed_repeat_rate": obs_repeat,
        "exact_expected_repeat": exact_mean,
        "observed_max_run": obs_maxrun,
        "observed_mean_run": obs_mean_run,
        "observed_num_runs": len(obs_runs),
        "repeat_effect_pct": (obs_repeat - exact_mean) * 100 if not math.isnan(exact_mean) else float("nan"),
    }

    if trials <= 0 or n > mc_max_n:
        mode = "exact_only_n_too_large" if n > mc_max_n else "exact_only_trials_0"
        result.update({
            "mc_repeat_mean": exact_mean, "mc_repeat_std": float("nan"),
            "mc_repeat_zscore": float("nan"), "mc_repeat_p_ge": float("nan"),
            "mc_repeat_ci_lo": float("nan"), "mc_repeat_ci_hi": float("nan"),
            "mc_maxrun_mean": float("nan"), "mc_maxrun_std": float("nan"),
            "mc_maxrun_zscore": float("nan"), "mc_maxrun_p_ge": float("nan"),
            "mc_maxrun_ci_lo": float("nan"), "mc_maxrun_ci_hi": float("nan"),
            "mc_meanrun_mean": float("nan"), "mc_meanrun_std": float("nan"),
            "mc_meanrun_zscore": float("nan"),
            "baseline_mode": mode,
        })
        return result

    trial_repeats: List[float] = []
    trial_maxruns: List[float] = []
    trial_meanruns: List[float] = []

    work = list(seq)
    for _ in range(trials):
        rng.shuffle(work)
        trial_repeats.append(repeat_rate(work))
        rl = run_lengths(work)
        trial_maxruns.append(float(max(rl)) if rl else 0.0)
        trial_meanruns.append(statistics.fmean(rl) if rl else 0.0)

    def _stats(observed: float, trial_vals: List[float]) -> Dict[str, float]:
        mu = statistics.fmean(trial_vals)
        sd = statistics.pstdev(trial_vals) if len(trial_vals) > 1 else 0.0
        z = (observed - mu) / sd if sd > 0 else float("nan")
        p_ge = sum(v >= observed for v in trial_vals) / len(trial_vals)
        sorted_vals = sorted(trial_vals)
        lo_idx = max(0, int(0.025 * len(sorted_vals)))
        hi_idx = min(len(sorted_vals) - 1, int(0.975 * len(sorted_vals)))
        return {
            "mean": mu, "std": sd, "zscore": z, "p_ge": p_ge,
            "ci_lo": sorted_vals[lo_idx], "ci_hi": sorted_vals[hi_idx],
        }

    rep_s = _stats(obs_repeat, trial_repeats)
    max_s = _stats(float(obs_maxrun), trial_maxruns)
    mean_s = _stats(obs_mean_run, trial_meanruns)

    result.update({
        "mc_repeat_mean": rep_s["mean"], "mc_repeat_std": rep_s["std"],
        "mc_repeat_zscore": rep_s["zscore"], "mc_repeat_p_ge": rep_s["p_ge"],
        "mc_repeat_ci_lo": rep_s["ci_lo"], "mc_repeat_ci_hi": rep_s["ci_hi"],
        "mc_maxrun_mean": max_s["mean"], "mc_maxrun_std": max_s["std"],
        "mc_maxrun_zscore": max_s["zscore"], "mc_maxrun_p_ge": max_s["p_ge"],
        "mc_maxrun_ci_lo": max_s["ci_lo"], "mc_maxrun_ci_hi": max_s["ci_hi"],
        "mc_meanrun_mean": mean_s["mean"], "mc_meanrun_std": mean_s["std"],
        "mc_meanrun_zscore": mean_s["zscore"],
        "baseline_mode": "mc_shuffle",
    })
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  Per-Thread Analysis
# ═══════════════════════════════════════════════════════════════════════════════

def per_thread_metrics(
    seq: Sequence[str],
    n_threads: int,
    trials: int,
    mc_max_n: int,
    rng: random.Random,
) -> List[Dict[str, object]]:
    """Per-thread repeat rates (global and conditional) with MC baselines."""
    if len(seq) < 2:
        return []

    n = len(seq)
    ntrans = n - 1
    counts = Counter(seq)

    # Observed per-thread metrics
    same_counts: Counter = Counter()
    prev_counts: Counter = Counter()
    win_counts = Counter(seq)

    for i in range(1, n):
        prev_counts[seq[i - 1]] += 1
        if seq[i] == seq[i - 1]:
            same_counts[seq[i]] += 1

    # MC baselines for per-thread metrics
    do_mc = 0 < trials and n <= mc_max_n
    thread_global_trials: Dict[str, List[float]] = defaultdict(list)
    thread_cond_trials: Dict[str, List[float]] = defaultdict(list)

    if do_mc:
        work = list(seq)
        for _ in range(trials):
            rng.shuffle(work)
            t_same: Counter = Counter()
            t_prev: Counter = Counter()
            for i in range(1, n):
                t_prev[work[i - 1]] += 1
                if work[i] == work[i - 1]:
                    t_same[work[i]] += 1
            for t in counts:
                thread_global_trials[t].append(t_same[t] / ntrans if ntrans else 0)
                if t_prev[t] > 0:
                    thread_cond_trials[t].append(t_same[t] / t_prev[t])

    results: List[Dict[str, object]] = []
    all_threads = sorted(counts.keys(), key=lambda x: (safe_int(x, 10**18), x))

    for t in all_threads:
        wins = win_counts[t]
        same = same_counts[t]
        prev_n = prev_counts[t]

        global_rr = same / ntrans if ntrans else float("nan")
        cond_rr = same / prev_n if prev_n > 0 else float("nan")
        win_share = wins / n

        row: Dict[str, object] = {
            "thread_id": t,
            "wins": wins,
            "win_share": win_share,
            "prev_count": prev_n,
            "repeat_count": same,
            "repeat_rate_global": global_rr,
            "repeat_rate_given_prev": cond_rr,
        }

        # MC baseline for this thread
        if do_mc and t in thread_global_trials:
            g_trials = thread_global_trials[t]
            c_trials = thread_cond_trials[t]

            if g_trials:
                g_mu = statistics.fmean(g_trials)
                g_sd = statistics.pstdev(g_trials) if len(g_trials) > 1 else 0.0
                row["global_baseline_mean"] = g_mu
                row["global_baseline_std"] = g_sd
                row["global_zscore"] = (global_rr - g_mu) / g_sd if g_sd > 0 else float("nan")
                row["global_p_ge"] = sum(v >= global_rr for v in g_trials) / len(g_trials)
            else:
                row.update({"global_baseline_mean": float("nan"), "global_baseline_std": float("nan"),
                            "global_zscore": float("nan"), "global_p_ge": float("nan")})

            if c_trials:
                c_mu = statistics.fmean(c_trials)
                c_sd = statistics.pstdev(c_trials) if len(c_trials) > 1 else 0.0
                row["cond_baseline_mean"] = c_mu
                row["cond_baseline_std"] = c_sd
                row["cond_zscore"] = (cond_rr - c_mu) / c_sd if c_sd > 0 else float("nan")
                row["cond_p_ge"] = sum(v >= cond_rr for v in c_trials) / len(c_trials)
            else:
                row.update({"cond_baseline_mean": float("nan"), "cond_baseline_std": float("nan"),
                            "cond_zscore": float("nan"), "cond_p_ge": float("nan")})
        else:
            row.update({
                "global_baseline_mean": float("nan"), "global_baseline_std": float("nan"),
                "global_zscore": float("nan"), "global_p_ge": float("nan"),
                "cond_baseline_mean": float("nan"), "cond_baseline_std": float("nan"),
                "cond_zscore": float("nan"), "cond_p_ge": float("nan"),
            })

        results.append(row)

    return results


# ═══════════════════════════════════════════════════════════════════════════════
#  Windowed Analysis
# ═══════════════════════════════════════════════════════════════════════════════

def windowed_analysis(
    seq: Sequence[str],
    window_size: int,
    n_threads: int,
    trials: int,
    mc_max_n: int,
    rng: random.Random,
) -> Tuple[List[Dict[str, object]], List[float]]:
    """Analyse repeat rate, fairness, and dominant share in fixed-size windows.

    Returns (window_rows, z_scores_per_window).
    """
    n = len(seq)
    if n < window_size:
        return [], []

    rows: List[Dict[str, object]] = []
    zscores: List[float] = []

    for widx, start in enumerate(range(0, n - window_size + 1, window_size)):
        wseq = seq[start:start + window_size]
        wn = len(wseq)
        w_counts = Counter(wseq)

        obs_rr = repeat_rate(wseq)
        obs_dom = dominant_share(wseq)
        obs_jfi = jains_fairness(w_counts, n_threads)
        exact_exp = exact_repeat_expectation(w_counts)

        # MC for this window
        if trials > 0 and wn <= mc_max_n:
            work = list(wseq)
            w_trials: List[float] = []
            for _ in range(trials):
                rng.shuffle(work)
                w_trials.append(repeat_rate(work))
            mu = statistics.fmean(w_trials)
            sd = statistics.pstdev(w_trials) if len(w_trials) > 1 else 0.0
            z = (obs_rr - mu) / sd if sd > 0 else float("nan")
            p_ge = sum(v >= obs_rr for v in w_trials) / len(w_trials)
            mode = "mc_shuffle"
        else:
            mu = exact_exp
            sd = float("nan")
            z = float("nan")
            p_ge = float("nan")
            mode = "exact_only"

        zscores.append(z)

        rows.append({
            "window_size": window_size,
            "window_index": widx,
            "window_start": start,
            "window_end_exclusive": start + window_size,
            "window_n_samples": wn,
            "window_repeat_rate": obs_rr,
            "window_expected_repeat": exact_exp,
            "window_repeat_excess_pct": (obs_rr - exact_exp) * 100,
            "window_repeat_baseline_mean": mu,
            "window_repeat_baseline_std": sd,
            "window_repeat_zscore": z,
            "window_repeat_p_ge": p_ge,
            "window_dominant_share": obs_dom,
            "window_dominant_winner": w_counts.most_common(1)[0][0],
            "window_jains_fairness": obs_jfi,
            "window_unique_winners": len(w_counts),
            "baseline_mode": mode,
        })

    return rows, zscores


# ═══════════════════════════════════════════════════════════════════════════════
#  Change-Point Detection (Recursive Binary Segmentation)
# ═══════════════════════════════════════════════════════════════════════════════

def _best_split(values: Sequence[float], min_seg: int) -> Tuple[float, int]:
    """Find split point maximising mean-difference score."""
    n = len(values)
    if n < 2 * min_seg:
        return 0.0, -1

    pooled_std = statistics.pstdev(values)
    if pooled_std <= 0:
        return 0.0, -1

    best_score = 0.0
    best_idx = -1
    for i in range(min_seg, n - min_seg + 1):
        delta = abs(statistics.fmean(values[:i]) - statistics.fmean(values[i:]))
        score = delta / pooled_std
        if score > best_score:
            best_score = score
            best_idx = i
    return best_score, best_idx


def detect_changepoints(
    values: Sequence[float],
    max_cp: int = 5,
    min_seg: int = 3,
    threshold: float = 2.0,
) -> List[Dict[str, object]]:
    """Recursive binary segmentation for multiple change-points.

    Returns list of detected change-points with scores and segment means.
    """
    clean = [v for v in values if v == v]  # drop NaN
    if len(clean) < 2 * min_seg:
        return []

    # Track segments as (start, end) in clean-index space
    segments = [(0, len(clean))]
    changepoints: List[Dict[str, object]] = []

    for _ in range(max_cp):
        best_global_score = 0.0
        best_seg_idx = -1
        best_split_pos = -1

        for seg_idx, (seg_start, seg_end) in enumerate(segments):
            seg = clean[seg_start:seg_end]
            score, rel_idx = _best_split(seg, min_seg)
            if score > best_global_score:
                best_global_score = score
                best_seg_idx = seg_idx
                best_split_pos = seg_start + rel_idx

        if best_global_score < threshold or best_seg_idx < 0:
            break

        seg_start, seg_end = segments[best_seg_idx]
        left_vals = clean[seg_start:best_split_pos]
        right_vals = clean[best_split_pos:seg_end]

        changepoints.append({
            "cp_position": best_split_pos,
            "cp_score": best_global_score,
            "left_mean": statistics.fmean(left_vals),
            "right_mean": statistics.fmean(right_vals),
            "left_n": len(left_vals),
            "right_n": len(right_vals),
            "abs_delta": abs(statistics.fmean(left_vals) - statistics.fmean(right_vals)),
        })

        # Split segment
        segments.pop(best_seg_idx)
        segments.insert(best_seg_idx, (seg_start, best_split_pos))
        segments.insert(best_seg_idx + 1, (best_split_pos, seg_end))

    changepoints.sort(key=lambda c: c["cp_position"])  # type: ignore[arg-type]
    return changepoints


# ═══════════════════════════════════════════════════════════════════════════════
#  Benjamini-Hochberg FDR Correction
# ═══════════════════════════════════════════════════════════════════════════════

def bh_fdr_correction(pvalues: List[float], alpha: float = 0.05) -> List[Tuple[float, float, bool]]:
    """Benjamini-Hochberg FDR correction.

    Returns list of (original_p, adjusted_p, is_significant) in original order.
    """
    n = len(pvalues)
    if n == 0:
        return []

    # Pair each p-value with its original index, skip NaN
    indexed = [(p, i) for i, p in enumerate(pvalues) if p == p]
    indexed.sort(key=lambda x: x[0])

    adjusted = [float("nan")] * n
    significant = [False] * n

    prev_adj = 1.0
    for rank_rev, (p, orig_idx) in enumerate(reversed(indexed)):
        rank = len(indexed) - rank_rev  # 1-based rank
        adj = min(prev_adj, p * len(indexed) / rank)
        adj = min(adj, 1.0)
        adjusted[orig_idx] = adj
        significant[orig_idx] = adj <= alpha
        prev_adj = adj

    # Fill NaN entries
    for i, p in enumerate(pvalues):
        if p != p:  # isnan
            adjusted[i] = float("nan")
            significant[i] = False

    return [(pvalues[i], adjusted[i], significant[i]) for i in range(n)]


# ═══════════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    args = parse_args()
    in_path = Path(args.input)
    out_prefix = Path(args.out_prefix)

    lag_values = [int(x) for x in args.lags.split(",") if x.strip()]
    window_sizes = [int(x) for x in args.window_sizes.split(",") if x.strip()]
    if not window_sizes:
        raise ValueError("--window-sizes must contain at least one value")

    headers, rows = read_rows(in_path)
    if args.winner_col not in headers:
        raise ValueError(f"Missing winner column: {args.winner_col}")

    rep_col = args.rep_col
    if rep_col not in headers:
        if "seq_idx" in headers:
            rep_col = "seq_idx"
            print("INFO: using seq_idx for sequence ordering.")
        else:
            raise ValueError(f"Missing sequence column: {args.rep_col}")

    group_cols = choose_group_columns(headers, args.group_cols)
    grouped: Dict[Tuple[str, ...], List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row.get(c, "") for c in group_cols)].append(row)

    rng = random.Random(args.seed)

    group_summary_rows: List[Dict[str, object]] = []
    window_detail_rows: List[Dict[str, object]] = []
    thread_summary_rows: List[Dict[str, object]] = []
    regime_summary_rows: List[Dict[str, object]] = []

    # Collect all p-values for FDR correction at the end
    all_pvalues: List[Tuple[str, int, str, float]] = []  # (table, row_idx, col_name, pvalue)

    n_groups = len(grouped)
    for g_idx, (key, grows) in enumerate(sorted(grouped.items()), 1):
        grows.sort(key=lambda r: safe_int(r.get(rep_col, "0"), 0))
        seq = [str(r.get(args.winner_col, "")) for r in grows]
        seq = [x for x in seq if x]
        if len(seq) < 2:
            continue

        n = len(seq)
        counts = Counter(seq)
        base_key = {c: key[i] for i, c in enumerate(group_cols)}

        # Determine n_threads
        n_threads = args.n_threads
        if n_threads <= 0:
            tc_val = base_key.get("thread_count", "")
            if tc_val:
                n_threads = safe_int(tc_val, len(counts))
            else:
                n_threads = len(counts)

        if g_idx % 50 == 0 or g_idx == 1:
            print(f"  Processing group {g_idx}/{n_groups} (n={n}, threads={n_threads})")

        # ── 1. Overall MC permutation test ──────────────────────────────────
        mc = mc_permutation_test(seq, args.trials, args.mc_max_n, rng)

        # ── 2. Wald-Wolfowitz runs test ─────────────────────────────────────
        ww = wald_wolfowitz_runs_test(seq)

        # ── 3. Transition matrix chi-squared ────────────────────────────────
        tm = transition_matrix_test(seq)

        # ── 4. Lag-k same rates ─────────────────────────────────────────────
        lag_rates = {f"lag{lag}_same_rate": lag_same_rate(seq, lag) for lag in lag_values}

        # ── 5. Aggregate fairness metrics ───────────────────────────────────
        jfi = jains_fairness(counts, n_threads)
        dom = dominant_share(seq)
        ent = normalized_entropy(counts)

        # ── 6. Multi-scale windowed analysis ────────────────────────────────
        window_z_by_scale: Dict[int, List[float]] = {}
        for ws in window_sizes:
            w_rows, w_zscores = windowed_analysis(seq, ws, n_threads, args.trials, args.mc_max_n, rng)
            window_z_by_scale[ws] = w_zscores

            for wr in w_rows:
                wr.update(base_key)
                window_detail_rows.append(wr)
                # Track p-values
                p_val = wr.get("window_repeat_p_ge")
                if isinstance(p_val, float) and p_val == p_val:
                    all_pvalues.append(("window", len(window_detail_rows) - 1, "window_repeat_p_ge_bh", p_val))

        # ── 7. Change-point detection on z-scores at each scale ─────────────
        for ws in window_sizes:
            zvals = window_z_by_scale.get(ws, [])
            cps = detect_changepoints(zvals, args.max_changepoints, args.cp_min_segment)
            for cp_idx, cp in enumerate(cps):
                regime_summary_rows.append({
                    **base_key,
                    "window_size": ws,
                    "changepoint_index": cp_idx,
                    **cp,
                })

        # ── 8. Per-thread analysis ──────────────────────────────────────────
        thread_rows = per_thread_metrics(seq, n_threads, args.trials, args.mc_max_n, rng)
        for tr in thread_rows:
            tr.update(base_key)
            thread_summary_rows.append(tr)
            # Track p-values
            for col in ["global_p_ge", "cond_p_ge"]:
                p_val = tr.get(col)
                if isinstance(p_val, float) and p_val == p_val:
                    bh_col = col.replace("p_ge", "p_ge_bh")
                    all_pvalues.append(("thread", len(thread_summary_rows) - 1, bh_col, p_val))

        # ── Build summary row ───────────────────────────────────────────────
        # Window-z summary per scale
        wz_summary: Dict[str, object] = {}
        for ws in window_sizes:
            zvals = window_z_by_scale.get(ws, [])
            clean_z = [z for z in zvals if z == z]
            prefix = f"w{ws}"
            wz_summary[f"{prefix}_n_windows"] = len(zvals)
            wz_summary[f"{prefix}_z_mean"] = statistics.fmean(clean_z) if clean_z else float("nan")
            wz_summary[f"{prefix}_z_std"] = statistics.pstdev(clean_z) if len(clean_z) > 1 else float("nan")
            wz_summary[f"{prefix}_z_max"] = max(clean_z) if clean_z else float("nan")
            # Fraction of windows with z > 2 (significantly sticky)
            wz_summary[f"{prefix}_frac_sticky"] = sum(1 for z in clean_z if z > 2.0) / len(clean_z) if clean_z else float("nan")
            # Number of change-points detected
            wz_summary[f"{prefix}_n_changepoints"] = sum(
                1 for r in regime_summary_rows
                if all(r.get(c) == base_key.get(c) for c in group_cols) and r.get("window_size") == ws
            )

        summary_row: Dict[str, object] = {
            **base_key,
            "n_samples": n,
            "n_threads_competing": n_threads,
            "unique_winners": len(counts),
            # Aggregate fairness
            "jains_fairness": jfi,
            "dominant_share": dom,
            "normalized_entropy": ent,
            # MC repeat rate
            "observed_repeat_rate": mc["observed_repeat_rate"],
            "expected_repeat_rate": mc["exact_expected_repeat"],
            "repeat_excess_pct": mc["repeat_effect_pct"],
            "mc_repeat_zscore": mc["mc_repeat_zscore"],
            "mc_repeat_p_ge": mc["mc_repeat_p_ge"],
            "mc_repeat_ci_lo": mc["mc_repeat_ci_lo"],
            "mc_repeat_ci_hi": mc["mc_repeat_ci_hi"],
            # MC max run
            "observed_max_run": mc["observed_max_run"],
            "observed_mean_run": mc["observed_mean_run"],
            "mc_maxrun_zscore": mc["mc_maxrun_zscore"],
            "mc_maxrun_p_ge": mc["mc_maxrun_p_ge"],
            "mc_maxrun_ci_lo": mc["mc_maxrun_ci_lo"],
            "mc_maxrun_ci_hi": mc["mc_maxrun_ci_hi"],
            # MC mean run length
            "mc_meanrun_zscore": mc["mc_meanrun_zscore"],
            # Wald-Wolfowitz
            "ww_runs_observed": ww["runs_observed"],
            "ww_runs_expected": ww["runs_expected"],
            "ww_runs_zscore": ww["runs_zscore"],
            "ww_runs_pvalue": ww["runs_pvalue_2sided"],
            # Transition matrix
            "chi2_statistic": tm["chi2_statistic"],
            "chi2_df": tm["chi2_df"],
            "chi2_pvalue": tm["chi2_pvalue"],
            "markov_stickiness": tm["markov_stickiness"],
            # Baseline mode
            "baseline_mode": mc["baseline_mode"],
        }
        summary_row.update(lag_rates)
        summary_row.update(wz_summary)

        # Track group-level p-values
        for col in ["mc_repeat_p_ge", "mc_maxrun_p_ge", "ww_runs_pvalue", "chi2_pvalue"]:
            p_val = summary_row.get(col)
            if isinstance(p_val, float) and p_val == p_val:
                all_pvalues.append(("group", len(group_summary_rows), f"{col}_bh", p_val))

        group_summary_rows.append(summary_row)

    # ── FDR Correction ──────────────────────────────────────────────────────
    print(f"\nApplying Benjamini-Hochberg FDR correction to {len(all_pvalues)} p-values (alpha={args.fdr_alpha})...")

    raw_pvals = [p for _, _, _, p in all_pvalues]
    corrected = bh_fdr_correction(raw_pvals, args.fdr_alpha)

    for (table, row_idx, col_name, _raw_p), (_orig, adj_p, is_sig) in zip(all_pvalues, corrected):
        if table == "group":
            group_summary_rows[row_idx][col_name] = adj_p
            group_summary_rows[row_idx][col_name.replace("_bh", "_bh_sig")] = int(is_sig)
        elif table == "window":
            window_detail_rows[row_idx][col_name] = adj_p
            window_detail_rows[row_idx][col_name.replace("_bh", "_bh_sig")] = int(is_sig)
        elif table == "thread":
            thread_summary_rows[row_idx][col_name] = adj_p
            thread_summary_rows[row_idx][col_name.replace("_bh", "_bh_sig")] = int(is_sig)

    # ── Sort outputs ────────────────────────────────────────────────────────
    group_summary_rows.sort(
        key=lambda r: (
            -1e18 if isinstance(r.get("mc_repeat_zscore"), float) and r["mc_repeat_zscore"] != r["mc_repeat_zscore"]
            else -r["mc_repeat_zscore"]  # type: ignore[operator]
        )
    )

    # ── Define field orders ─────────────────────────────────────────────────
    group_fields = list(group_cols) + [
        "n_samples", "n_threads_competing", "unique_winners",
        "jains_fairness", "dominant_share", "normalized_entropy",
        "observed_repeat_rate", "expected_repeat_rate", "repeat_excess_pct",
        "mc_repeat_zscore", "mc_repeat_p_ge", "mc_repeat_p_ge_bh", "mc_repeat_p_ge_bh_sig",
        "mc_repeat_ci_lo", "mc_repeat_ci_hi",
        "observed_max_run", "observed_mean_run",
        "mc_maxrun_zscore", "mc_maxrun_p_ge", "mc_maxrun_p_ge_bh", "mc_maxrun_p_ge_bh_sig",
        "mc_maxrun_ci_lo", "mc_maxrun_ci_hi",
        "mc_meanrun_zscore",
        "ww_runs_observed", "ww_runs_expected", "ww_runs_zscore",
        "ww_runs_pvalue", "ww_runs_pvalue_bh", "ww_runs_pvalue_bh_sig",
        "chi2_statistic", "chi2_df",
        "chi2_pvalue", "chi2_pvalue_bh", "chi2_pvalue_bh_sig",
        "markov_stickiness",
        "baseline_mode",
    ]
    group_fields += [f"lag{lag}_same_rate" for lag in lag_values]
    for ws in window_sizes:
        prefix = f"w{ws}"
        group_fields += [
            f"{prefix}_n_windows", f"{prefix}_z_mean", f"{prefix}_z_std",
            f"{prefix}_z_max", f"{prefix}_frac_sticky", f"{prefix}_n_changepoints",
        ]

    window_fields = list(group_cols) + [
        "window_size", "window_index", "window_start", "window_end_exclusive",
        "window_n_samples",
        "window_repeat_rate", "window_expected_repeat", "window_repeat_excess_pct",
        "window_repeat_baseline_mean", "window_repeat_baseline_std",
        "window_repeat_zscore", "window_repeat_p_ge",
        "window_repeat_p_ge_bh", "window_repeat_p_ge_bh_sig",
        "window_dominant_share", "window_dominant_winner",
        "window_jains_fairness", "window_unique_winners",
        "baseline_mode",
    ]

    thread_fields = list(group_cols) + [
        "thread_id", "wins", "win_share",
        "prev_count", "repeat_count",
        "repeat_rate_global", "global_baseline_mean", "global_baseline_std",
        "global_zscore", "global_p_ge", "global_p_ge_bh", "global_p_ge_bh_sig",
        "repeat_rate_given_prev", "cond_baseline_mean", "cond_baseline_std",
        "cond_zscore", "cond_p_ge", "cond_p_ge_bh", "cond_p_ge_bh_sig",
    ]

    regime_fields = list(group_cols) + [
        "window_size", "changepoint_index",
        "cp_position", "cp_score",
        "left_mean", "right_mean", "left_n", "right_n", "abs_delta",
    ]

    # ── Write outputs ───────────────────────────────────────────────────────
    group_out = out_prefix.with_name(out_prefix.name + "_group_summary.csv")
    window_out = out_prefix.with_name(out_prefix.name + "_window_detail.csv")
    thread_out = out_prefix.with_name(out_prefix.name + "_thread_summary.csv")
    regime_out = out_prefix.with_name(out_prefix.name + "_regime_summary.csv")

    write_csv(group_out, group_summary_rows, group_fields)
    write_csv(window_out, window_detail_rows, window_fields)
    write_csv(thread_out, thread_summary_rows, thread_fields)
    write_csv(regime_out, regime_summary_rows, regime_fields)

    print(f"\nWrote {group_out}  ({len(group_summary_rows)} groups)")
    print(f"Wrote {window_out}  ({len(window_detail_rows)} windows)")
    print(f"Wrote {thread_out}  ({len(thread_summary_rows)} thread rows)")
    print(f"Wrote {regime_out}  ({len(regime_summary_rows)} change-points)")

    # ── Print headline results ──────────────────────────────────────────────
    if group_summary_rows:
        top = group_summary_rows[0]
        gdesc = ", ".join(f"{c}={top.get(c, '?')}" for c in group_cols)
        print(f"\nMost sticky group: {gdesc}")
        print(f"  Jain's fairness (aggregate):  {top.get('jains_fairness', '?'):.4f}")
        print(f"  Repeat rate:                  {top.get('observed_repeat_rate', '?'):.4f}  "
              f"(expected: {top.get('expected_repeat_rate', '?'):.4f}, "
              f"excess: {top.get('repeat_excess_pct', '?'):+.2f}%)")
        print(f"  MC repeat z-score:            {top.get('mc_repeat_zscore', '?')}")
        print(f"  Wald-Wolfowitz z-score:       {top.get('ww_runs_zscore', '?')}")
        print(f"  Chi-squared (Markov) p-value: {top.get('chi2_pvalue', '?')}")
        print(f"  Max run length:               {top.get('observed_max_run', '?')}")

        # Count significantly sticky groups
        n_sig = sum(1 for r in group_summary_rows
                    if r.get("mc_repeat_p_ge_bh_sig") == 1)
        print(f"\n  Groups with significant stickiness (FDR {args.fdr_alpha}): "
              f"{n_sig}/{len(group_summary_rows)}")

        # Highlight the contrast: fair aggregate but unfair windows
        for r in group_summary_rows[:5]:
            jfi = r.get("jains_fairness", float("nan"))
            if isinstance(jfi, float) and jfi > 0.9:
                for ws in window_sizes:
                    frac = r.get(f"w{ws}_frac_sticky", 0)
                    if isinstance(frac, float) and frac > 0.1:
                        gdesc = ", ".join(f"{c}={r.get(c, '?')}" for c in group_cols)
                        print(f"\n  Key finding: {gdesc}")
                        print(f"    Aggregate Jain's fairness = {jfi:.4f} (looks fair)")
                        print(f"    But {frac:.0%} of w={ws} windows are significantly sticky (z > 2)")
                        print(f"    This is the hidden unfairness your research targets.")
                        break

    print("\nDone.")


if __name__ == "__main__":
    main()
