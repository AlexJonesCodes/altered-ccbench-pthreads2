import csv
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict
from itertools import combinations
from scipy.stats import ttest_ind

CSV_FILE = "smt_100_reps_fixed_address.csv"

# -------------------------------
# Read data
# -------------------------------
core_winners = defaultdict(list)  # core -> list of wins
runs = set()
test_type = None

with open(CSV_FILE, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        run = int(row["run"])
        core = int(row["cpu"])
        winners = int(row["wins"])
        test_type = row["test_type"]

        core_winners[core].append(winners)
        runs.add(run)

cores = sorted(core_winners.keys())
runs = sorted(runs)

# -------------------------------
# Statistics per core
# -------------------------------
print(f"Statistics per core — Test Type: {test_type}\n")
for core in cores:
    data = core_winners[core]
    mean = np.mean(data)
    median = np.median(data)
    std = np.std(data, ddof=1)
    min_v = np.min(data)
    q1 = np.percentile(data, 25)
    q3 = np.percentile(data, 75)
    max_v = np.max(data)
    print(
        f"Core {core:2d} | Runs: {len(data)} | Mean: {mean:8.2f} | Median: {median:8.2f} | "
        f"Std: {std:8.2f} | Min: {min_v:6d} | Q1: {q1:8.2f} | Q3: {q3:8.2f} | Max: {max_v:6d}"
    )

# -------------------------------
# Pairwise t-tests
# -------------------------------
print("\nPairwise t-tests (p < 0.05 = significant difference)\n")
for core1, core2 in combinations(cores, 2):
    data1 = core_winners[core1]
    data2 = core_winners[core2]
    t_stat, p_val = ttest_ind(data1, data2, equal_var=False)  # Welch’s t-test
    sig = "*" if p_val < 0.05 else ""
    print(f"Core {core1} vs Core {core2}: t={t_stat:6.2f}, p={p_val:.4f} {sig}")

# -------------------------------
# Boxplot with SMT pairs next to each other
# -------------------------------
data_for_plot = []
labels = []

# SMT pairs: cores 0&20, 1&21, ..., 9&29
for i in range(10):
    if i in core_winners:
        data_for_plot.append(core_winners[i])
        labels.append(str(i))
    if i + 20 in core_winners:
        data_for_plot.append(core_winners[i + 20])
        labels.append(str(i + 20))

fig, ax = plt.subplots(figsize=(14, 7))

bp = ax.boxplot(
    data_for_plot,
    labels=labels,
    patch_artist=True,
    showmeans=True,
    meanline=True,
    meanprops=dict(color="blue", linewidth=2),  # solid mean line
    medianprops=dict(color="black", linewidth=1.5),  # solid median line
    flierprops=dict(marker="o", markerfacecolor="none", markeredgecolor="black", alpha=0.4)
)

# Set grey boxes
for box in bp["boxes"]:
    box.set(facecolor="lightgrey", edgecolor="black")

# Whiskers and caps
for whisker in bp["whiskers"]:
    whisker.set(color="black", linewidth=1)
for cap in bp["caps"]:
    cap.set(color="black", linewidth=1)

ax.set_xlabel("CPU Core (SMT pairs grouped)")
ax.set_ylabel("Winners")
ax.set_title(f"Boxplot of Winners per Core — Test Type: {test_type}")

plt.tight_layout()
plt.savefig(CSV_FILE + ".png", dpi=300)