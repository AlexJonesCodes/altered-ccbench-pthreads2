#!/usr/bin/env python3
"""Windowed temporal-locality analysis without transition/Markov outputs.

Computes per-group/per-window repeat-rate behavior, Monte Carlo shuffle baselines,
dominant winner share, Jain fairness index, change-point scores, and optional
spatial/topology-aware stickiness metrics (same-HT/L2/socket/cross-socket).
"""

from __future__ import annotations

import argparse
import csv
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

TOPO_CLASSES = ["same_core", "same_ht", "same_l2", "same_socket", "cross_socket", "unknown"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", help="Input CSV/TSV path.")
    p.add_argument(
        "--out-prefix",
        default="winner_window_locality",
        help=(
            "Output prefix: writes <prefix>_summary.csv, <prefix>_window_summary.csv, "
            "and topology CSV when topology maps are provided"
        ),
    )
    p.add_argument("--winner-col", default="winner_thread_id", help="Winner id column.")
    p.add_argument("--winner-core-col", default="winner_core", help="Winner core column (for topology analysis).")
    p.add_argument("--rep-col", default="rep", help="Sequence order column; falls back to seq_idx.")
    p.add_argument(
        "--group-cols",
        default="",
        help=(
            "Comma-separated grouping columns. If empty, uses standard run/op context "
            "(run_id,op,op_id,core_set_id,thread_count,seed_core when present)."
        ),
    )
    p.add_argument("--trials", type=int, default=300, help="Monte Carlo shuffle trials per group/window.")
    p.add_argument("--mc-max-n", type=int, default=200_000, help="Skip MC if group/window length exceeds this.")
    p.add_argument("--seed", type=int, default=7, help="RNG seed for reproducibility.")
    p.add_argument("--window-size", type=int, default=1000, help="Window size in samples.")
    p.add_argument("--window-step", type=int, default=0, help="Window step; <=0 means use window-size.")
    p.add_argument("--cp-threshold", type=float, default=2.0, help="Change-point score threshold for cp_flag.")
    p.add_argument(
        "--socket-map",
        default="",
        help='Socket map, e.g. "0:10,11,12;1:20,21,22"',
    )
    p.add_argument(
        "--l2-map",
        default="",
        help='L2-sharing groups, e.g. "0:10,11;1:12,13;2:20,21"',
    )
    p.add_argument(
        "--ht-pairs",
        default="",
        help='Hyperthread sibling pairs, e.g. "5-6;7-8;5-10"',
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
    return sum(1 for i in range(1, len(seq)) if seq[i] == seq[i - 1]) / (len(seq) - 1)


def dominant_share(seq: Sequence[str]) -> float:
    if not seq:
        return float("nan")
    c = Counter(seq)
    return c.most_common(1)[0][1] / len(seq)


def jains_fairness_index(seq: Sequence[str]) -> float:
    if not seq:
        return float("nan")
    vals = list(Counter(seq).values())
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
        out = [c for c in cols if c in headers]
        if not out:
            raise ValueError("None of --group-cols exist in input headers")
        return out
    preferred = ["run_id", "op", "op_id", "core_set_id", "thread_count", "seed_core"]
    picked = [c for c in preferred if c in headers]
    if picked:
        return picked
    exclude = {"rep", "seq_idx", "winner_thread_id", "winner_core"}
    return [h for h in headers if h not in exclude]


def write_csv(path: Path, rows: Iterable[Dict[str, object]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def metric_baseline(observed: float, trials: Sequence[float], mode: str) -> Dict[str, float]:
    if not trials:
        return {
            "observed": observed,
            "baseline_mean": float("nan"),
            "baseline_std": float("nan"),
            "zscore": float("nan"),
            "p_ge": float("nan"),
            "baseline_mode": mode,
        }
    mu = statistics.fmean(trials)
    sd = statistics.pstdev(trials) if len(trials) > 1 else 0.0
    z = (observed - mu) / sd if sd > 0 else float("nan")
    p_ge = sum(v >= observed for v in trials) / len(trials)
    return {
        "observed": observed,
        "baseline_mean": mu,
        "baseline_std": sd,
        "zscore": z,
        "p_ge": p_ge,
        "baseline_mode": mode,
    }


def detect_change_point(values: Sequence[float], min_seg: int = 2) -> Tuple[float, int, float, float, float]:
    clean = [v for v in values if not (v != v)]
    if len(clean) < 2 * min_seg:
        return float("nan"), -1, float("nan"), float("nan"), float("nan")
    pooled = statistics.pstdev(clean)
    best = (float("-inf"), -1, float("nan"), float("nan"), float("nan"))
    for i in range(min_seg, len(clean) - min_seg + 1):
        lm = statistics.fmean(clean[:i])
        rm = statistics.fmean(clean[i:])
        delta = abs(lm - rm)
        score = (delta / pooled) if pooled > 0 else 0.0
        if score > best[0]:
            best = (score, i, lm, rm, delta)
    return best


def parse_group_map(raw: str) -> Dict[int, int]:
    out: Dict[int, int] = {}
    if not raw.strip():
        return out
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        gid_s, cores_s = chunk.split(":", 1)
        gid = safe_int(gid_s.strip(), -1)
        if gid < 0:
            raise ValueError(f"Invalid group id in mapping: {gid_s}")
        for c in cores_s.split(","):
            c = c.strip()
            if not c:
                continue
            core = safe_int(c, -1)
            if core < 0:
                raise ValueError(f"Invalid core id in mapping: {c}")
            out[core] = gid
    return out


def parse_ht_pairs(raw: str) -> Dict[int, set]:
    pairs: Dict[int, set] = defaultdict(set)
    if not raw.strip():
        return pairs
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" not in chunk:
            raise ValueError(f"Invalid HT pair format: {chunk}")
        a_s, b_s = chunk.split("-", 1)
        a = safe_int(a_s.strip(), -1)
        b = safe_int(b_s.strip(), -1)
        if a < 0 or b < 0:
            raise ValueError(f"Invalid HT pair cores: {chunk}")
        pairs[a].add(b)
        pairs[b].add(a)
    return pairs


def classify_topology(a: int, b: int, socket_map: Dict[int, int], l2_map: Dict[int, int], ht_pairs: Dict[int, set]) -> str:
    if a == b:
        return "same_core"
    if b in ht_pairs.get(a, set()):
        return "same_ht"
    if a in l2_map and b in l2_map and l2_map[a] == l2_map[b]:
        return "same_l2"
    if a in socket_map and b in socket_map:
        if socket_map[a] == socket_map[b]:
            return "same_socket"
        return "cross_socket"
    return "unknown"


def topology_rates(core_seq: Sequence[int], socket_map: Dict[int, int], l2_map: Dict[int, int], ht_pairs: Dict[int, set]) -> Dict[str, float]:
    if len(core_seq) < 2:
        return {k: float("nan") for k in TOPO_CLASSES}
    c = Counter()
    for i in range(1, len(core_seq)):
        cls = classify_topology(core_seq[i - 1], core_seq[i], socket_map, l2_map, ht_pairs)
        c[cls] += 1
    denom = len(core_seq) - 1
    return {k: c.get(k, 0) / denom for k in TOPO_CLASSES}


def main() -> None:
    args = parse_args()
    in_path = Path(args.input)
    out_prefix = Path(args.out_prefix)

    if args.window_size <= 0:
        raise ValueError("--window-size must be > 0")
    window_step = args.window_step if args.window_step > 0 else args.window_size

    socket_map = parse_group_map(args.socket_map)
    l2_map = parse_group_map(args.l2_map)
    ht_pairs = parse_ht_pairs(args.ht_pairs)
    topo_enabled = bool(socket_map or l2_map or ht_pairs)

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

    core_col = args.winner_core_col if args.winner_core_col in headers else args.winner_col
    if core_col != args.winner_core_col:
        print(f"INFO: {args.winner_core_col} not found; using {args.winner_col} for topology ids.")

    group_cols = choose_group_columns(headers, args.group_cols)
    grouped: Dict[Tuple[str, ...], List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row.get(c, "") for c in group_cols)].append(row)

    rng = random.Random(args.seed)
    summary_rows: List[Dict[str, object]] = []
    window_rows: List[Dict[str, object]] = []
    topo_rows: List[Dict[str, object]] = []

    for key, grows in grouped.items():
        grows.sort(key=lambda r: safe_int(r.get(rep_col, "0"), 0))
        seq = [str(r.get(args.winner_col, "")) for r in grows]
        seq = [x for x in seq if x != ""]
        core_seq = [safe_int(r.get(core_col, ""), -1) for r in grows]
        core_seq = [c for c in core_seq if c >= 0]
        if len(seq) < 2:
            continue

        n = len(seq)
        base_key = {c: key[i] for i, c in enumerate(group_cols)}
        zvals: List[float] = []

        for widx, start in enumerate(range(0, n - args.window_size + 1, window_step)):
            wseq = seq[start:start + args.window_size]
            wcore = core_seq[start:start + args.window_size] if len(core_seq) >= start + args.window_size else []
            obs = repeat_rate(wseq)
            dom = dominant_share(wseq)
            jfi = jains_fairness_index(wseq)
            wn = len(wseq)

            if args.trials <= 0 or wn > args.mc_max_n:
                mode = "exact_repeat_only_n_too_large" if wn > args.mc_max_n else "exact_repeat_only_trials_0"
                res = metric_baseline(obs, [], mode)
                topo_trials = {k: [] for k in TOPO_CLASSES}
            else:
                mode = "mc_shuffle"
                work = list(wseq)
                tvals: List[float] = []
                topo_trials = {k: [] for k in TOPO_CLASSES}
                wcore_work = list(wcore)
                for _ in range(args.trials):
                    rng.shuffle(work)
                    tvals.append(repeat_rate(work))
                    if topo_enabled and len(wcore_work) == wn:
                        rng.shuffle(wcore_work)
                        tr = topology_rates(wcore_work, socket_map, l2_map, ht_pairs)
                        for k in TOPO_CLASSES:
                            topo_trials[k].append(tr[k])
                res = metric_baseline(obs, tvals, mode)

            zvals.append(res["zscore"])
            window_rows.append(
                {
                    **base_key,
                    "window_index": widx,
                    "window_start": start,
                    "window_end_exclusive": start + args.window_size,
                    "window_n_samples": wn,
                    "window_repeat_rate": res["observed"],
                    "window_repeat_baseline_mean": res["baseline_mean"],
                    "window_repeat_baseline_std": res["baseline_std"],
                    "window_repeat_zscore": res["zscore"],
                    "window_repeat_p_ge": res["p_ge"],
                    "window_dominant_share": dom,
                    "window_jains_fairness": jfi,
                    "baseline_mode": res["baseline_mode"],
                }
            )

            if topo_enabled and len(wcore) == wn:
                obs_topo = topology_rates(wcore, socket_map, l2_map, ht_pairs)
                for klass in TOPO_CLASSES:
                    b = metric_baseline(obs_topo[klass], topo_trials[klass], mode)
                    topo_rows.append(
                        {
                            **base_key,
                            "window_index": widx,
                            "window_start": start,
                            "window_end_exclusive": start + args.window_size,
                            "window_n_samples": wn,
                            "topology_class": klass,
                            "probability": b["observed"],
                            "baseline_mean": b["baseline_mean"],
                            "baseline_std": b["baseline_std"],
                            "zscore": b["zscore"],
                            "p_ge": b["p_ge"],
                            "baseline_mode": b["baseline_mode"],
                        }
                    )

        cp_score, cp_idx, cp_l, cp_r, cp_delta = detect_change_point(zvals)
        clean = [z for z in zvals if not (z != z)]
        summary_rows.append(
            {
                **base_key,
                "n_samples": n,
                "window_size": args.window_size,
                "window_step": window_step,
                "n_windows": len(zvals),
                "window_repeat_zscore_mean": statistics.fmean(clean) if clean else float("nan"),
                "window_repeat_zscore_std": statistics.pstdev(clean) if len(clean) > 1 else float("nan"),
                "cp_score": cp_score,
                "cp_index": cp_idx,
                "cp_left_mean_z": cp_l,
                "cp_right_mean_z": cp_r,
                "cp_abs_delta_z": cp_delta,
                "cp_flag": int(cp_score == cp_score and cp_score >= args.cp_threshold),
            }
        )

    summary_rows.sort(key=lambda r: str(tuple(r.get(c, "") for c in group_cols)))
    window_rows.sort(key=lambda r: (str(tuple(r.get(c, "") for c in group_cols)), safe_int(str(r.get("window_index", "0")), 0)))
    topo_rows.sort(
        key=lambda r: (
            str(tuple(r.get(c, "") for c in group_cols)),
            safe_int(str(r.get("window_index", "0")), 0),
            str(r.get("topology_class", "")),
        )
    )

    summary_fields = list(group_cols) + [
        "n_samples",
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

    summary_out = out_prefix.with_name(out_prefix.name + "_summary.csv")
    window_out = out_prefix.with_name(out_prefix.name + "_window_summary.csv")
    write_csv(summary_out, summary_rows, summary_fields)
    write_csv(window_out, window_rows, window_fields)
    print(f"Wrote {summary_out}")
    print(f"Wrote {window_out}")

    if topo_enabled:
        topo_fields = list(group_cols) + [
            "window_index",
            "window_start",
            "window_end_exclusive",
            "window_n_samples",
            "topology_class",
            "probability",
            "baseline_mean",
            "baseline_std",
            "zscore",
            "p_ge",
            "baseline_mode",
        ]
        topo_out = out_prefix.with_name(out_prefix.name + "_window_topology_summary.csv")
        write_csv(topo_out, topo_rows, topo_fields)
        print(f"Wrote {topo_out}")


if __name__ == "__main__":
    main()
