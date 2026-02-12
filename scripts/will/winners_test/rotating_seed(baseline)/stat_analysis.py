import csv
from collections import defaultdict
import numpy as np
from scipy.stats import skew, kurtosis

CSV_FILE = "4000_runs_1mill_reps_random_addr_moving_seed.csv"  # replace with your CSV path

# -------------------------------
# Read CSV and structure data
# -------------------------------
# data[run][cpu] = wins
data = defaultdict(dict)
b_values = set()
test_type = None
cpu_set = set()

with open(CSV_FILE, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        run = int(row["run"])
        cpu = int(row["cpu"])
        b_val = int(row["b_value"])
        wins = int(row["wins"])
        test_type = row["test_type"]

        data[run][cpu] = wins
        b_values.add(b_val)
        cpu_set.add(cpu)

cpu_list = sorted(cpu_set)
b_values = sorted(b_values)
num_cpus = len(cpu_list)

print(f"CSV loaded: {len(data)} runs, {num_cpus} CPUs, {len(b_values)} b_values, test_type={test_type}")

# -------------------------------
# Derived metrics per run
# -------------------------------
fairness_per_run = []
seed_advantage_per_run = []
seed_vs_smt_diff_per_run = []

for run, cpu_wins in sorted(data.items()):
    missing = [cpu for cpu in cpu_list if cpu not in cpu_wins]
    if missing:
        print(f"\nDATASET ERROR: Run {run} is missing CPUs: {missing}")
        print(f"CPUs present in run {run}: {sorted(cpu_wins.keys())}")
        raise ValueError(f"Run {run} missing CPU data")

    wins_array = np.array([cpu_wins[cpu] for cpu in cpu_list])
    total_wins = np.sum(wins_array)

    # -------------------------------
    # Fairness (Jain's index)
    J = (np.sum(wins_array) ** 2) / (num_cpus * np.sum(wins_array ** 2))
    fairness_per_run.append(J)

    # -------------------------------
    # Seed advantage (b_value for this run)
    b_val = list(set([cpu_wins[c] for c in cpu_list if c == c]))[0]  # placeholder, will fix below
    # We get b_value from any row, actually
    # In this dataset, all runs have same b_value? We'll pull from cpu_wins
    # Actually easier: pull from CSV row? For now:
    seed_cpu = None
    for cpu in cpu_list:
        if cpu_wins[cpu] == cpu_wins.get(cpu, cpu_wins[cpu]):
            pass  # placeholder
    # Better: iterate CSV to map run -> b_value
    # We'll rebuild a map:
b_map = {}
with open(CSV_FILE, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        run = int(row["run"])
        b_val = int(row["b_value"])
        b_map[run] = b_val

# Now compute metrics correctly
for run, cpu_wins in sorted(data.items()):
    wins_array = np.array([cpu_wins[cpu] for cpu in cpu_list])
    total_wins = np.sum(wins_array)

    # Fairness
    J = (np.sum(wins_array) ** 2) / (num_cpus * np.sum(wins_array ** 2))
    fairness_per_run.append(J)

    # Seed advantage
    seed_cpu = b_map[run]
    seed_win = cpu_wins[seed_cpu]
    other_wins = [cpu_wins[c] for c in cpu_list if c != seed_cpu]
    seed_adv = seed_win - np.mean(other_wins)
    seed_advantage_per_run.append(seed_adv)

    # Seed vs SMT sibling
    smt_sibling = seed_cpu + 20 if seed_cpu < 20 else seed_cpu - 20
    smt_win = cpu_wins.get(smt_sibling, None)
    if smt_win is not None:
        seed_vs_smt_diff_per_run.append(seed_win - smt_win)
    else:
        seed_vs_smt_diff_per_run.append(None)

# -------------------------------
# Summary statistics
# -------------------------------
print("\n=== Summary ===")
print(f"Runs: {len(data)}")
print(f"CPUs: {num_cpus}")
print(f"b_values: {b_values}")

def summarize(array, name):
    arr = np.array([x for x in array if x is not None])
    print(f"{name}: mean={np.mean(arr):.2f}, std={np.std(arr, ddof=1):.2f}, min={np.min(arr)}, max={np.max(arr)}")

summarize(fairness_per_run, "Jain fairness index")
summarize(seed_advantage_per_run, "Seed advantage (wins)")
summarize(seed_vs_smt_diff_per_run, "Seed vs SMT sibling difference")

# -------------------------------
# Optional per-b-value statistics
# -------------------------------
print("\nPer-b_value statistics:")
for b_val in b_values:
    runs_for_b = [run for run, bv in b_map.items() if bv == b_val]
    if not runs_for_b:
        continue
    advs = [seed_advantage_per_run[run-1] for run in runs_for_b]  # run indices start at 1
    J_vals = [fairness_per_run[run-1] for run in runs_for_b]
    print(f"b={b_val}: seed advantage mean={np.mean(advs):.2f}, fairness mean={np.mean(J_vals):.4f}")


# -------------------------------
# Socket-level analysis
# -------------------------------
core_winners = defaultdict(list)
for run in sorted(data):
    for cpu, wins in data[run].items():
        core_winners[cpu].append(wins)

socket_cpus = {
    0: list(range(0, 10)) + list(range(20, 30)),  # Socket 0
    1: list(range(10, 20)) + list(range(30, 40))  # Socket 1
}

socket_data = {}
for soc, cpus in socket_cpus.items():
    # Flatten wins across all runs for all CPUs in this socket
    wins_all = [win for cpu in cpus if cpu in core_winners for win in core_winners[cpu]]
    socket_data[soc] = np.array(wins_all)

# Compute descriptive statistics
for soc, data in socket_data.items():
    mean = np.mean(data)
    median = np.median(data)
    print(f"Median wins per socket: {median:.2f}")

    std = np.std(data, ddof=1)
    min_v = np.min(data)
    max_v = np.max(data)
    q1 = np.percentile(data, 25)
    q3 = np.percentile(data, 75)
    iqr = q3 - q1
    sk = skew(data)
    kurt = kurtosis(data)
    outliers = data[(data < q1 - 1.5*iqr) | (data > q3 + 1.5*iqr)]
    outlier_rate = len(outliers) / len(data)

    print(f"\nSocket {soc} statistics:")
    print(f"Total wins: {np.sum(data):,}")
    print(f"Mean: {mean:.2f}, Median: {median:.2f}, Std: {std:.2f}")
    print(f"Min: {min_v}, Max: {max_v}, IQR: {iqr:.2f}")
    print(f"Skew: {sk:.2f}, Kurtosis: {kurt:.2f}")
    print(f"Outliers: {len(outliers)} ({outlier_rate*100:.2f}%)")

# -------------------------------
# Per-run socket comparison
# -------------------------------
# Compute per-run totals for Socket0 and Socket1
runs = sorted({run for cpu_data in core_winners.values() for run in range(len(cpu_data))})
socket0_per_run = []
socket1_per_run = []

for run_idx in range(len(core_winners[0])):  # Assuming all CPUs have same run count
    s0 = sum(core_winners[cpu][run_idx] for cpu in socket_cpus[0] if cpu in core_winners)
    s1 = sum(core_winners[cpu][run_idx] for cpu in socket_cpus[1] if cpu in core_winners)
    socket0_per_run.append(s0)
    socket1_per_run.append(s1)

socket0_per_run = np.array(socket0_per_run)
socket1_per_run = np.array(socket1_per_run)
delta = socket0_per_run - socket1_per_run

print("\nSocket comparison per run (Socket0 - Socket1):")
print(f"Mean difference: {np.mean(delta):.2f}")
print(f"Std difference: {np.std(delta, ddof=1):.2f}")
print(f"Min difference: {np.min(delta)}, Max difference: {np.max(delta)}")

# Optional: paired t-test for statistical significance
from scipy.stats import ttest_rel
t_stat, p_val = ttest_rel(socket0_per_run, socket1_per_run)
sig = "significant" if p_val < 0.05 else "not significant"
print(f"Paired t-test: t={t_stat:.2f}, p={p_val:.5f} ({sig})")

# -------------------------------
# Socket-level Jain's Fairness Index
# -------------------------------
# Treat Socket 0 and Socket 1 as "participants" per run
socket_wins_array = np.vstack([socket0_per_run, socket1_per_run]).T  # shape: (runs, 2)
jain_socket_per_run = []

for wins in socket_wins_array:
    J = (np.sum(wins) ** 2) / (2 * np.sum(wins ** 2))  # 2 sockets
    jain_socket_per_run.append(J)

jain_socket_per_run = np.array(jain_socket_per_run)

print("\nSocket-level Jain Fairness Index:")
print(f"Mean: {np.mean(jain_socket_per_run):.4f}")
print(f"Std:  {np.std(jain_socket_per_run, ddof=1):.4f}")
print(f"Min:  {np.min(jain_socket_per_run):.4f}, Max: {np.max(jain_socket_per_run):.4f}")
