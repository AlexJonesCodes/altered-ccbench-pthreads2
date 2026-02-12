import csv
from collections import defaultdict
import numpy as np

CSV_FILE = "your_data.csv"  # replace with your CSV path

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
