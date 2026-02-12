import csv
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict
from itertools import combinations
from scipy.stats import ttest_ind

CSV_FILE = "multi_socket_fixed_addr_seed_10.csv"

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
# Function to shade boxes by socket
# -------------------------------
def shade_boxes_by_socket(bp, cpu_labels):
    for i, box in enumerate(bp["boxes"]):
        cpu_num = int(cpu_labels[i])
        # Socket 0: CPUs 0-19, Socket 1: CPUs 20-39
        if cpu_num < 20:
            box.set(facecolor="#d9d9d9", edgecolor="black")  # light grey
        else:
            box.set(facecolor="#bfbfbf", edgecolor="black")  # slightly darker grey
    # Whiskers and caps
    for whisker in bp["whiskers"]:
        whisker.set(color="black", linewidth=1)
    for cap in bp["caps"]:
        cap.set(color="black", linewidth=1)

# -------------------------------
# Original plot (SMT pairs grouped per socket)
# -------------------------------
data_for_plot = []
cpu_labels = []
core_labels = []
positions = []

pos = 1
for core in range(int(len(cores)/2)):
    # First thread of pair
    if core in core_winners:
        data_for_plot.append(core_winners[core])
        cpu_labels.append(str(core))
        positions.append(pos)
        pos += 1
    # SMT sibling
    sibling = core + 20
    if sibling in core_winners:
        data_for_plot.append(core_winners[sibling])
        cpu_labels.append(str(sibling))
        positions.append(pos)
        pos += 1
    # Bottom label for the pair: core number (same for both threads)
    core_labels.append(str(core))

fig1, ax = plt.subplots(figsize=(14, 7))

bp = ax.boxplot(
    data_for_plot,
    positions=positions,
    widths=0.6,
    patch_artist=True,
    showmeans=True,
    meanline=True,
    meanprops=dict(color="blue", linewidth=2),
    medianprops=dict(color="black", linewidth=1.5),
    flierprops=dict(marker="o", markerfacecolor="none", markeredgecolor="black", alpha=0.4)
)

# Shade boxes by socket
shade_boxes_by_socket(bp, cpu_labels)

ax.set_ylabel("Winners")
ax.set_title(f"Boxplot of Winners per Core — Test Type: {test_type}")

# Top x-axis: CPU/thread number
ax_top = ax.twiny()
ax_top.set_xlim(ax.get_xlim())
ax_top.set_xticks(positions)
ax_top.set_xticklabels(cpu_labels, rotation=0)
ax_top.set_xlabel("CPU Number")

# Bottom x-axis: Core number (centered between pairs)
pair_centers = [(positions[i] + positions[i + 1]) / 2 for i in range(0, len(positions), 2)]
ax.set_xticks(pair_centers)
ax.set_xticklabels(core_labels)
ax.set_xlabel("Core number (SMT pairs)")

fig1_path = CSV_FILE + "_grouped_socket.png"
plt.tight_layout()
plt.savefig(fig1_path, dpi=300)

# -------------------------------
# Cross-socket plot (cores alternating)
# -------------------------------
data_for_plot = []
cpu_labels = []
core_labels = []
positions = []

pos = 1
for core_idx in range(10):
    for soc_base in [0, 10]:  # socket 0 then socket 1
        cpu1 = core_idx + soc_base
        if cpu1 in core_winners:
            data_for_plot.append(core_winners[cpu1])
            cpu_labels.append(str(cpu1))
            positions.append(pos)
            pos += 1
        cpu2 = cpu1 + 20  # SMT sibling
        if cpu2 in core_winners:
            data_for_plot.append(core_winners[cpu2])
            cpu_labels.append(str(cpu2))
            positions.append(pos)
            pos += 1
    core_labels.append(str(core_idx))

fig2, ax = plt.subplots(figsize=(14, 7))

bp = ax.boxplot(
    data_for_plot,
    positions=positions,
    widths=0.6,
    patch_artist=True,
    showmeans=True,
    meanline=True,
    meanprops=dict(color="blue", linewidth=2),
    medianprops=dict(color="black", linewidth=1.5),
    flierprops=dict(marker="o", markerfacecolor="none", markeredgecolor="black", alpha=0.4)
)

# Shade boxes by socket
shade_boxes_by_socket(bp, cpu_labels)

ax.set_ylabel("Winners")
ax.set_title(f"Boxplot of Winners per Core — Test Type: {test_type} (Cross-Socket Ordering)")

# Top x-axis: CPU/thread number
ax_top = ax.twiny()
ax_top.set_xlim(ax.get_xlim())
ax_top.set_xticks(positions)
ax_top.set_xticklabels(cpu_labels, rotation=0)
ax_top.set_xlabel("CPU Number")

# Bottom x-axis: Core number (centered over 4 boxes per core)
pair_centers = [(positions[i] + positions[i + 3]) / 2 for i in range(0, len(positions), 4)]
ax.set_xticks(pair_centers)
ax.set_xticklabels(core_labels)
ax.set_xlabel("Core number (SMT pairs across sockets)")

fig2_path = CSV_FILE + "_grouped_core.png"
plt.tight_layout()
plt.savefig(fig2_path, dpi=300)
