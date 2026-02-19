import csv
from collections import defaultdict, Counter
import numpy as np
from scipy.stats import skew, kurtosis, ttest_rel

CSV_FILE = "Xeon_Silver_4114/4kruns_1_000_000_reps8/4000_runs_1mill_reps.csv"

# --------------------------------------------------
# CONFIGURATION (EDIT IF TOPOLOGY DIFFERENT)
# --------------------------------------------------

SMT_OFFSET = 20   # logical sibling distance
SOCKET_CPUS = {
    0: list(range(0, 10)) + list(range(20, 30)),
    1: list(range(10, 20)) + list(range(30, 40))
}

# --------------------------------------------------
# LOAD DATA
# --------------------------------------------------

data = defaultdict(dict)      # data[run][cpu] = wins
b_map = {}                    # run -> seed cpu
cpu_set = set()
test_type = None

with open(CSV_FILE, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        run = int(row["run"])
        cpu = int(row["cpu"])
        wins = int(row["wins"])
        b_val = int(row["b_value"])

        data[run][cpu] = wins
        b_map[run] = b_val
        cpu_set.add(cpu)
        test_type = row["test_type"]

runs = sorted(data.keys())
cpu_list = sorted(cpu_set)
num_runs = len(runs)
num_cpus = len(cpu_list)

print(f"Loaded {num_runs} runs, {num_cpus} CPUs, test_type={test_type}")

# --------------------------------------------------
# PER-RUN METRICS
# --------------------------------------------------

fairness = {}
seed_adv = {}
seed_vs_smt = {}
socket_totals = {0: {}, 1: {}}
per_cpu_wins = defaultdict(list)

for run in runs:
    wins = data[run]

    # ordered array
    arr = np.array([wins[c] for c in cpu_list])

    # Jain fairness
    J = (arr.sum() ** 2) / (num_cpus * np.sum(arr ** 2))
    fairness[run] = J

    # seed advantage
    seed = b_map[run]
    seed_win = wins[seed]
    others = [wins[c] for c in cpu_list if c != seed]
    seed_adv[run] = seed_win - np.mean(others)

    # seed vs SMT sibling
    sib = seed + SMT_OFFSET if seed < SMT_OFFSET else seed - SMT_OFFSET
    seed_vs_smt[run] = seed_win - wins.get(sib, np.nan)

    # socket totals
    for s in SOCKET_CPUS:
        socket_totals[s][run] = sum(wins[c] for c in SOCKET_CPUS[s])

    # store per cpu
    for c in cpu_list:
        per_cpu_wins[c].append(wins[c])

# --------------------------------------------------
# SUMMARY FUNCTION
# --------------------------------------------------

def summarize(arr, name):
    arr = np.array(arr, dtype=float)
    arr = arr[~np.isnan(arr)]
    print(f"{name}:")
    print(f"  mean={arr.mean():.4f}  std={arr.std(ddof=1):.4f}")
    print(f"  min={arr.min():.4f}  max={arr.max():.4f}")
    print()

print("\n===== GLOBAL SUMMARY =====")
summarize(list(fairness.values()), "Jain fairness")
summarize(list(seed_adv.values()), "Seed advantage")
summarize(list(seed_vs_smt.values()), "Seed vs SMT diff")

# --------------------------------------------------
# SOCKET COMPARISON
# --------------------------------------------------

s0 = np.array([socket_totals[0][r] for r in runs])
s1 = np.array([socket_totals[1][r] for r in runs])
delta = s0 - s1

print("\n===== SOCKET COMPARISON =====")
print(f"Mean difference (S0-S1): {delta.mean():.2f}")
print(f"Std difference: {delta.std(ddof=1):.2f}")

t, p = ttest_rel(s0, s1)
print(f"Paired t-test: t={t:.3f}, p={p:.6f}")

# effect size (Cohen d paired)
d = delta.mean() / delta.std(ddof=1)
print(f"Cohen's d: {d:.3f}")

# --------------------------------------------------
# SOCKET FAIRNESS (2 participants)
# --------------------------------------------------

socket_J = []
for r in runs:
    w = np.array([socket_totals[0][r], socket_totals[1][r]])
    socket_J.append((w.sum()**2)/(2*np.sum(w**2)))

summarize(socket_J, "Socket Jain fairness")

# --------------------------------------------------
# PER-CPU ANALYSIS
# --------------------------------------------------

print("\n===== PER CPU PERFORMANCE =====")

cpu_mean = {}
for c in cpu_list:
    arr = np.array(per_cpu_wins[c])
    cpu_mean[c] = arr.mean()
    print(f"CPU {c:2d}: mean wins = {arr.mean():.2f}, std = {arr.std(ddof=1):.2f}")

# --------------------------------------------------
# DOMINANCE (who wins most runs)
# --------------------------------------------------

run_winner = []
for r in runs:
    winner = max(data[r].items(), key=lambda x: x[1])[0]
    run_winner.append(winner)

counts = Counter(run_winner)

print("\n===== RUN WINNER FREQUENCY =====")
for c, n in counts.most_common():
    print(f"CPU {c:2d}: wins {n} runs ({n/num_runs*100:.2f}%)")

# --------------------------------------------------
# RANK STABILITY
# --------------------------------------------------

print("\n===== RANK STABILITY (std of rank) =====")

rank_history = defaultdict(list)

for r in runs:
    ordered = sorted(data[r].items(), key=lambda x: x[1], reverse=True)
    for rank, (cpu, _) in enumerate(ordered):
        rank_history[cpu].append(rank)

for c in cpu_list:
    print(f"CPU {c:2d}: rank std = {np.std(rank_history[c]):.2f}")

# --------------------------------------------------
# MOMENTUM (WIN STREAKS)
# --------------------------------------------------

print("\n===== MOMENTUM (WIN STREAK LENGTHS) =====")

streaks = []
current = run_winner[0]
length = 1

for w in run_winner[1:]:
    if w == current:
        length += 1
    else:
        streaks.append(length)
        current = w
        length = 1
streaks.append(length)

summarize(streaks, "Win streak length")

# --------------------------------------------------
# DISTRIBUTION SHAPE
# --------------------------------------------------

print("\n===== GLOBAL WIN DISTRIBUTION =====")

all_wins = np.concatenate([per_cpu_wins[c] for c in cpu_list])

print(f"Skew: {skew(all_wins):.3f}")
print(f"Kurtosis: {kurtosis(all_wins):.3f}")

q1, q3 = np.percentile(all_wins, [25, 75])
iqr = q3 - q1
outliers = all_wins[(all_wins < q1-1.5*iqr) | (all_wins > q3+1.5*iqr)]

print(f"Outlier rate: {len(outliers)/len(all_wins)*100:.2f}%")


# ============================================================
# CROSS-SOCKET CONTENTION ANALYSIS
# ============================================================

import pandas as pd

# ------------------------------------------------------------
# BUILD DATAFRAME FOR SOCKET ANALYSIS
# ------------------------------------------------------------

rows = []

with open(CSV_FILE, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        rows.append({
            "run": int(row["run"]),
            "cpu": int(row["cpu"]),
            "wins": int(row["wins"]),
            "attempts": int(row["attempts"]),
            "successes": int(row["successes"]),
            "failures": int(row["failures"]),
            "test_type": row["test_type"]
        })

df = pd.DataFrame(rows)

# map cpu -> socket using your topology
def cpu_to_socket(cpu):
    if cpu in SOCKET_CPUS[0]:
        return 0
    elif cpu in SOCKET_CPUS[1]:
        return 1
    else:
        raise ValueError(cpu)

df["socket"] = df["cpu"].apply(cpu_to_socket)

# logical core id (collapse SMT siblings)
df["core"] = df["cpu"] % SMT_OFFSET


print("\n" + "="*60)
print("CROSS-SOCKET CONTENTION INTENSITY")
print("="*60)

# ------------------------------------------------------------
# 1. How much work each socket attempts vs wins
# ------------------------------------------------------------
socket_efficiency = (
    df.groupby("socket")[["attempts", "wins"]]
      .sum()
)

socket_efficiency["success_rate"] = (
    socket_efficiency["wins"] / socket_efficiency["attempts"]
)

print("\nSocket execution efficiency:")
print(socket_efficiency)

# ------------------------------------------------------------
# 2. Socket competition per run
# ------------------------------------------------------------
run_socket = (
    df.groupby(["run", "socket"])[["wins", "attempts"]]
      .sum()
)

run_socket["efficiency"] = (
    run_socket["wins"] / run_socket["attempts"]
)

run_socket = run_socket.unstack()

# Who wins more per run?
run_winner = (
    df.groupby(["run", "socket"])["wins"]
      .sum()
      .unstack()
      .idxmax(axis=1)
)

print("\nRun winner distribution:")
print(run_winner.value_counts())

# ------------------------------------------------------------
# 3. Inter-socket imbalance per run
# ------------------------------------------------------------
run_share = (
    df.groupby(["run", "socket"])["wins"]
      .sum()
      .groupby(level=0)
      .apply(lambda x: x / x.sum())
      .unstack()
)

run_share["imbalance"] = abs(run_share[0] - run_share[1])

print("\nImbalance statistics (0 = perfectly balanced):")
print(run_share["imbalance"].describe())

# ------------------------------------------------------------
# 4. Hyperthread vs core competition (important!)
# ------------------------------------------------------------
# collapse logical siblings to core level
df["core"] = df["cpu"] % 20   # works for your topology

core_totals = (
    df.groupby(["socket", "core"])["wins"]
      .sum()
)

print("\nPer-core distribution inside each socket:")
print(core_totals.groupby(level=0).describe())

# ------------------------------------------------------------
# 5. Test-type sensitivity to cross-socket contention
# ------------------------------------------------------------
test_socket = (
    df.groupby(["test_type", "socket"])["wins"]
      .sum()
      .unstack()
)

test_socket_share = test_socket.div(test_socket.sum(axis=1), axis=0)

print("\nSocket dominance by test type:")
print(test_socket_share)

# ------------------------------------------------------------
# 6. Detect systematic socket advantage
# ------------------------------------------------------------
advantage = run_share.mean()

print("\nAverage win share per socket:")
print(advantage)


print("\nCross-socket contention analysis complete.")
