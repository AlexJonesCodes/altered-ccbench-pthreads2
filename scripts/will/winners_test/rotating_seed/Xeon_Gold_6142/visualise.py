import csv
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict
from itertools import combinations
from scipy.stats import ttest_ind, skew, kurtosis

CSV_FILE = "3200_runs_1_000_000_reps/3200_runs_1mill_reps1.csv"

# -------------------------------
# Read data
# -------------------------------
core_winners = defaultdict(list)
runs = set()
test_type = None

with open(CSV_FILE, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        run = int(row["run"])
        cpu = int(row["cpu"])
        winners = int(row["wins"])
        test_type = row["test_type"]

        core_winners[cpu].append(winners)
        runs.add(run)

cores = sorted(set(cpu % 32 for cpu in core_winners))
runs = sorted(runs)

# -------------------------------
# Extended Statistics per core
# -------------------------------
print(f"\nDetailed Statistics per Core — Test Type: {test_type}\n")
outlier_summary = []

for core in cores:
    # Aggregate both SMT threads for stats
    data = np.array(core_winners[core] + core_winners[core + 32])
    mean = np.mean(data)
    median = np.median(data)
    std = np.std(data, ddof=1)
    q1, q3 = np.percentile(data, [25, 75])
    iqr = q3 - q1
    outliers = data[(data < q1 - 1.5*iqr) | (data > q3 + 1.5*iqr)]
    outlier_rate = len(outliers)/len(data)
    p5, p95 = np.percentile(data, [5, 95])
    sk = skew(data)
    kurt = kurtosis(data)
    outlier_summary.append(outlier_rate)
    print(f"Core {core:2d} | Mean: {mean:.2f} | Median: {median:.2f} | Std: {std:.2f} | "
          f"IQR: {iqr:.2f} | 5th: {p5:.2f} | 95th: {p95:.2f} | Outliers: {len(outliers)} "
          f"({outlier_rate*100:.2f}%) | Skew: {sk:.2f} | Kurtosis: {kurt:.2f}")

print(f"\nMean outlier rate across cores: {np.mean(outlier_summary)*100:.2f}%")
print(f"Max outlier rate: {np.max(outlier_summary)*100:.2f}%")
print(f"Min outlier rate: {np.min(outlier_summary)*100:.2f}%")

# -------------------------------
# Pairwise t-tests
# -------------------------------
print("\nPairwise Welch t-tests (p < 0.05 significant)\n")
for core1, core2 in combinations(cores, 2):
    data1 = core_winners[core1] + core_winners[core1 + 32]
    data2 = core_winners[core2] + core_winners[core2 + 32]
    t_stat, p_val = ttest_ind(data1, data2, equal_var=False)
    sig = "*" if p_val < 0.05 else ""
    print(f"Core {core1} vs Core {core2}: t={t_stat:.2f}, p={p_val:.5f} {sig}")

# -------------------------------
# Helpers
# -------------------------------
def socket_color(cpu):
    # CPU 0-31 Socket 0/1 SMT mapping
    return "#d9d9d9" if cpu % 32 < 16 else "#bfbfbf"

def plot_barline_violin(data_for_plot, cpu_labels, cpu_to_core_map, title_prefix):
    positions = np.arange(1, len(data_for_plot)+1)
    core_groups = defaultdict(list)
    for pos, core in enumerate(cpu_to_core_map):
        core_groups[core].append(pos)
    centers = [np.mean(core_groups[core]) + 1 for core in sorted(core_groups)]
    core_labels = [str(core) for core in sorted(core_groups)]

    # --- BAR + LINE ---
    fig, ax = plt.subplots(figsize=(14,7))
    means = [np.mean(d) for d in data_for_plot]
    medians = [np.median(d) for d in data_for_plot]

    # Add label for mean
    bars = ax.bar(positions, means, width=0.6, edgecolor="black", label="Mean per CPU")
    for i, bar in enumerate(bars):
        bar.set_facecolor(socket_color(int(cpu_labels[i])))

    ax.plot(positions, medians, color="blue", marker="o", linewidth=2, label="Median per CPU")

    ax.set_xticks(centers)
    ax.set_xticklabels(core_labels)
    ax.set_xlabel("Core Number")
    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"Xeon Gold 6142: {title_prefix}: {test_type}")
    ax.legend(loc="upper right")
    plt.tight_layout()
    plt.savefig(CSV_FILE + f"_{title_prefix}_barline.png", dpi=300)
    plt.close()

    # --- VIOLIN ---
    fig, ax = plt.subplots(figsize=(14,7))
    vp = ax.violinplot(data_for_plot, positions=positions, showmedians=True)
    for i, body in enumerate(vp["bodies"]):
        body.set_facecolor(socket_color(int(cpu_labels[i])))
        body.set_edgecolor("black")
        body.set_alpha(0.6)
    for i, d in enumerate(data_for_plot):
        ax.hlines(np.mean(d), positions[i]-0.3, positions[i]+0.3, colors="red", linestyles="dashed", linewidth=2)
    ax.set_xticks(centers)
    ax.set_xticklabels(core_labels)
    ax.set_xlabel("Core Number")
    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"Xeon Gold 6142: {title_prefix}: {test_type}")
    plt.tight_layout()
    plt.savefig(CSV_FILE + f"_{title_prefix}_violin.png", dpi=300)
    plt.close()

# -------------------------------
# Generate plots
# -------------------------------
# 1. Grouped by Socket
data_for_plot = []
cpu_labels = []
cpu_to_core_map = []
for core in range(16):
    for cpu in [core, core+16, core+32, core+48]:  # SMT pair across sockets
        data_for_plot.append(core_winners[cpu])
        cpu_labels.append(str(cpu))
        cpu_to_core_map.append(core)
plot_barline_violin(data_for_plot, cpu_labels, cpu_to_core_map, "Grouped_by_Socket")

# 2. Cross-socket (interleaved cores)
data_for_plot = []
cpu_labels = []
cpu_to_core_map = []
for core in range(16):
    for cpu in [core, core+32, core+16, core+48]:
        data_for_plot.append(core_winners[cpu])
        cpu_labels.append(str(cpu))
        cpu_to_core_map.append(core)
plot_barline_violin(data_for_plot, cpu_labels, cpu_to_core_map, "Cross_Socket")

# 3. Socket-level plots
def socket_level_plots():
    data_for_plot = []
    cpu_labels = []
    positions = [1, 2]

    soc0_cpus = list(range(0,16)) + list(range(32,48))
    soc0_data = [val for cpu in soc0_cpus for val in core_winners[cpu]]
    data_for_plot.append(soc0_data)
    cpu_labels.append("Socket 0")

    soc1_cpus = list(range(16,32)) + list(range(48,64))
    soc1_data = [val for cpu in soc1_cpus for val in core_winners[cpu]]
    data_for_plot.append(soc1_data)
    cpu_labels.append("Socket 1")

    core_labels = ["Socket 0", "Socket 1"]

    # BAR+LINE
    fig, ax = plt.subplots(figsize=(8,7))
    means = [np.mean(d) for d in data_for_plot]
    medians = [np.median(d) for d in data_for_plot]
    bars = ax.bar(positions, means, width=0.6, edgecolor="black", color=["#d9d9d9","#bfbfbf"])
    ax.plot(positions, medians, color="blue", marker="o", linewidth=2)
    ax.set_xticks(positions)
    ax.set_xticklabels(core_labels)
    ax.set_xlabel("Socket")
    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"Xeon Gold 6142: Socket Comparison: {test_type}")
    plt.tight_layout()
    plt.savefig(CSV_FILE + "_socket_barline.png", dpi=300)
    plt.close()

    # Violin
    fig, ax = plt.subplots(figsize=(8,7))
    vp = ax.violinplot(data_for_plot, positions=positions, showmedians=True)
    for i, body in enumerate(vp["bodies"]):
        body.set_facecolor(["#d9d9d9","#bfbfbf"][i])
        body.set_edgecolor("black")
        body.set_alpha(0.6)
    for i, d in enumerate(data_for_plot):
        ax.hlines(np.mean(d), positions[i]-0.3, positions[i]+0.3, colors="red", linestyles="dashed", linewidth=2)
    ax.set_xticks(positions)
    ax.set_xticklabels(core_labels)
    ax.set_xlabel("Socket")
    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"Xeon Gold 6142: Socket Comparison: {test_type}")
    plt.tight_layout()
    plt.savefig(CSV_FILE + "_socket_violin.png", dpi=300)
    plt.close()

socket_level_plots()

# 4. Per-core totals
data_for_plot = []
cpu_labels = []
cpu_to_core_map = []
for core in range(32):
    # sum SMT threads
    total_per_run = [a+b for a,b in zip(core_winners[core], core_winners[core+32])]
    data_for_plot.append(total_per_run)
    cpu_labels.append(str(core))
    cpu_to_core_map.append(core)
plot_barline_violin(data_for_plot, cpu_labels, cpu_to_core_map, "Per_Core_Total")

print("\nAll 8 plots generated successfully.\n")
