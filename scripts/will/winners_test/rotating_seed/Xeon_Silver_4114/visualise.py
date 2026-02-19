import csv
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict
from itertools import combinations
from scipy.stats import ttest_ind, skew, kurtosis

CSV_FILE = "4kruns_1_000_000_reps19/4000_runs_1mill_reps.csv"

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
        core = int(row["cpu"])
        winners = int(row["wins"])
        test_type = row["test_type"]

        core_winners[core].append(winners)
        runs.add(run)

cores = sorted(core_winners.keys())
runs = sorted(runs)

# -------------------------------
# Extended Statistics per core
# -------------------------------
print(f"\nDetailed Statistics per Core — Test Type: {test_type}\n")

outlier_summary = []

for core in cores:
    data = np.array(core_winners[core])

    mean = np.mean(data)
    median = np.median(data)
    std = np.std(data, ddof=1)
    min_v = np.min(data)
    q1 = np.percentile(data, 25)
    q3 = np.percentile(data, 75)
    max_v = np.max(data)

    iqr = q3 - q1
    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr
    outliers = data[(data < lower_bound) | (data > upper_bound)]
    outlier_rate = len(outliers) / len(data)

    p5 = np.percentile(data, 5)
    p95 = np.percentile(data, 95)

    sk = skew(data)
    kurt = kurtosis(data)  # excess kurtosis

    outlier_summary.append(outlier_rate)

    print(
        f"Core {core:2d} | Runs: {len(data)} "
        f"| Mean: {mean:10.2f} | Median: {median:10.2f} "
        f"| Std: {std:10.2f} | IQR: {iqr:10.2f} "
        f"| 5th: {p5:10.2f} | 95th: {p95:10.2f} "
        f"| Outliers: {len(outliers):4d} ({outlier_rate*100:5.2f}%) "
        f"| Skew: {sk:6.2f} | Kurtosis: {kurt:6.2f}"
    )

print("\nOutlier Rate Summary:")
print(f"Mean outlier rate across cores: {np.mean(outlier_summary)*100:.2f}%")
print(f"Max outlier rate: {np.max(outlier_summary)*100:.2f}%")
print(f"Min outlier rate: {np.min(outlier_summary)*100:.2f}%")

# -------------------------------
# Pairwise t-tests
# -------------------------------
print("\nPairwise Welch t-tests (p < 0.05 significant)\n")
for core1, core2 in combinations(cores, 2):
    data1 = core_winners[core1]
    data2 = core_winners[core2]
    t_stat, p_val = ttest_ind(data1, data2, equal_var=False)
    sig = "*" if p_val < 0.05 else ""
    print(f"Core {core1} vs Core {core2}: t={t_stat:7.2f}, p={p_val:.5f} {sig}")

# -------------------------------
# Helper: socket shading
# -------------------------------
def socket_color(cpu):
    return "#d9d9d9" if cpu < 20 else "#bfbfbf"

# -------------------------------
# Compute core centers for labels
# -------------------------------
def compute_centers_and_labels(positions, ordering_name):
    centers = []
    labels = []

    if "Grouped" in ordering_name:
        for i in range(0, len(positions), 2):
            centers.append((positions[i] + positions[i+1]) / 2)
            labels.append(str(i // 2))
    else:
        for i in range(0, len(positions), 4):
            centers.append((positions[i] + positions[i+3]) / 2)
            labels.append(str(i // 4))

    return centers, labels

# -------------------------------
# Plot builder (bar/line + violin with mean)
# -------------------------------
def make_plots(ordering_name, build_positions_func):

    data_for_plot, cpu_labels, core_labels, positions = build_positions_func()

    centers, labels = compute_centers_and_labels(positions, ordering_name)

    # ---------- BAR + LINE ----------
    fig, ax = plt.subplots(figsize=(14, 7))

    means = [np.mean(d) for d in data_for_plot]
    medians = [np.median(d) for d in data_for_plot]

    bars = ax.bar(positions, means, width=0.6, edgecolor="black", label="Mean per CPU")
    for i, bar in enumerate(bars):
        cpu_num = int(cpu_labels[i])
        bar.set_facecolor(socket_color(cpu_num))

    ax.plot(positions, medians, color="blue", marker="o", linewidth=2, label="Median per CPU")

    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"{ordering_name}: {test_type}")
    ax.set_ylim(bottom=0)

    # Top axis = CPU numbers
    ax_top = ax.twiny()
    ax_top.set_xlim(ax.get_xlim())
    ax_top.set_xticks(positions)
    ax_top.set_xticklabels(cpu_labels)
    ax_top.set_xlabel("CPU Number")

    # Bottom axis = Core centers
    ax.set_xticks(centers)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Core Number")

    ax.legend(loc="upper right", fontsize=12, frameon=True)

    plt.tight_layout()
    plt.savefig(CSV_FILE + f"_{ordering_name}_barline.png", dpi=300)
    plt.close()

    # ---------- VIOLIN PLOT + MEAN LINE + LEGEND ----------
    fig, ax = plt.subplots(figsize=(14, 7))
    vp = ax.violinplot(data_for_plot, positions=positions, showmedians=True)

    for i, body in enumerate(vp["bodies"]):
        cpu_num = int(cpu_labels[i])
        body.set_facecolor(socket_color(cpu_num))
        body.set_edgecolor("black")
        body.set_alpha(0.6)

    # Mean dashed line
    for i, d in enumerate(data_for_plot):
        mean_val = np.mean(d)
        ax.hlines(mean_val, positions[i]-0.3, positions[i]+0.3, colors="red", linestyles="dashed", linewidth=2)

    # Add legend manually for violin plot
    ax.plot([], [], color="red", linestyle="dashed", linewidth=2, label="Mean")
    ax.plot([], [], color="black", linestyle="-", linewidth=1.5, label="Median")

    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"{ordering_name}: {test_type}")
    ax.set_ylim(bottom=0)
    ax_top = ax.twiny()
    ax_top.set_xlim(ax.get_xlim())
    ax_top.set_xticks(positions)
    ax_top.set_xticklabels(cpu_labels)
    ax_top.set_xlabel("CPU Number")

    ax.set_xticks(centers)
    ax.set_xticklabels(core_labels)
    ax.set_xlabel("Core Number")

    ax.legend(loc="upper right", fontsize=12, frameon=True)

    plt.tight_layout()
    plt.savefig(CSV_FILE + f"_{ordering_name}_violin.png", dpi=300)
    plt.close()

# -------------------------------
# Ordering builders
# -------------------------------
def grouped_by_socket():
    data_for_plot = []
    cpu_labels = []
    core_labels = []
    positions = []
    pos = 1

    for core in range(int(len(cores)/2)):
        for cpu in [core, core + 20]:
            if cpu in core_winners:
                data_for_plot.append(core_winners[cpu])
                cpu_labels.append(str(cpu))
                positions.append(pos)
                pos += 1
        core_labels.append(str(core))

    return data_for_plot, cpu_labels, core_labels, positions

def cross_socket():
    data_for_plot = []
    cpu_labels = []
    core_labels = []
    positions = []
    pos = 1

    for core_idx in range(10):
        for soc_base in [0, 10]:
            for cpu in [core_idx + soc_base, core_idx + soc_base + 20]:
                if cpu in core_winners:
                    data_for_plot.append(core_winners[cpu])
                    cpu_labels.append(str(cpu))
                    positions.append(pos)
                    pos += 1
        core_labels.append(str(core_idx))

    return data_for_plot, cpu_labels, core_labels, positions

# -------------------------------
# Socket-level plots
# -------------------------------
def socket_level_plots():
    data_for_plot = []
    cpu_labels = []
    positions = [1, 2]

    soc0_cpus = list(range(0,10)) + list(range(20,30))
    soc0_data = [item for cpu in soc0_cpus if cpu in core_winners for item in core_winners[cpu]]
    data_for_plot.append(soc0_data)
    cpu_labels.append("Socket 0")

    soc1_cpus = list(range(10,20)) + list(range(30,40))
    soc1_data = [item for cpu in soc1_cpus if cpu in core_winners for item in core_winners[cpu]]
    data_for_plot.append(soc1_data)
    cpu_labels.append("Socket 1")

    core_labels = ["Socket 0", "Socket 1"]

    # ---------- BAR + LINE ----------
    fig, ax = plt.subplots(figsize=(8, 7))
    means = [np.mean(d) for d in data_for_plot]
    medians = [np.median(d) for d in data_for_plot]

    bars = ax.bar(positions, means, width=0.6, edgecolor="black", label="Mean per Socket")
    for i, bar in enumerate(bars):
        bar.set_facecolor("#d9d9d9" if i==0 else "#bfbfbf")

    ax.plot(positions, medians, color="blue", marker="o", linewidth=2, label="Median per Socket")
    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"Socket Comparison: {test_type}")
    ax.set_ylim(bottom=0)
    ax.set_xticks(positions)
    ax.set_xticklabels(core_labels)
    ax.set_xlabel("Socket")
    ax.legend(loc="upper right", fontsize=12, frameon=True)
    plt.tight_layout()
    plt.savefig(CSV_FILE + "_socket_barline.png", dpi=300)
    plt.close()

    # ---------- VIOLIN ----------
    fig, ax = plt.subplots(figsize=(8, 7))
    vp = ax.violinplot(data_for_plot, positions=positions, showmedians=True)
    for i, body in enumerate(vp["bodies"]):
        body.set_facecolor("#d9d9d9" if i==0 else "#bfbfbf")
        body.set_edgecolor("black")
        body.set_alpha(0.6)

    # Mean dashed line
    for i, d in enumerate(data_for_plot):
        mean_val = np.mean(d)
        ax.hlines(mean_val, positions[i]-0.3, positions[i]+0.3, colors="red", linestyles="dashed", linewidth=2)

    # Legend
    ax.plot([], [], color="red", linestyle="dashed", linewidth=2, label="Mean")
    ax.plot([], [], color="black", linestyle="-", linewidth=1.5, label="Median")

    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"Socket Comparison: {test_type}")
    ax.set_ylim(bottom=0)
    ax.set_xticks(positions)
    ax.set_xticklabels(core_labels)
    ax.set_xlabel("Socket")
    ax.legend(loc="upper right", fontsize=12, frameon=True)
    plt.tight_layout()
    plt.savefig(CSV_FILE + "_socket_violin.png", dpi=300)
    plt.close()

# -------------------------------
# Per-core (SMT-paired) total wins plots
# -------------------------------
def per_core_total_plots():
    per_core_data = defaultdict(list)
    num_runs = len(next(iter(core_winners.values())))

    for run_idx in range(num_runs):
        for core in range(20):
            cpu0 = core
            cpu1 = core + 20
            if cpu0 in core_winners and cpu1 in core_winners:
                total = core_winners[cpu0][run_idx] + core_winners[cpu1][run_idx]
                per_core_data[core].append(total)

    data_for_plot = []
    positions = []
    labels = []
    pos = 1
    for core in sorted(per_core_data.keys()):
        data_for_plot.append(per_core_data[core])
        positions.append(pos)
        labels.append(str(core))
        pos += 1

    # ---------- BAR + LINE ----------
    fig, ax = plt.subplots(figsize=(14, 7))
    means = [np.mean(d) for d in data_for_plot]
    medians = [np.median(d) for d in data_for_plot]

    bars = ax.bar(positions, means, width=0.6, edgecolor="black", label="Mean per Core")
    for i, bar in enumerate(bars):
        bar.set_facecolor("#d9d9d9")

    ax.plot(positions, medians, color="blue", marker="o", linewidth=2, label="Median per Core")
    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"Per-Core Total Wins: {test_type}")
    ax.set_ylim(bottom=0)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Core Number")
    ax.legend(loc="upper right", fontsize=12, frameon=True)
    plt.tight_layout()
    plt.savefig(CSV_FILE + "_per_core_total_barline.png", dpi=300)
    plt.close()

    # ---------- VIOLIN ----------
    fig, ax = plt.subplots(figsize=(14, 7))
    vp = ax.violinplot(data_for_plot, positions=positions, showmedians=True)
    for body in vp["bodies"]:
        body.set_facecolor("#d9d9d9")
        body.set_edgecolor("black")
        body.set_alpha(0.6)

    # Mean dashed line
    for i, d in enumerate(data_for_plot):
        mean_val = np.mean(d)
        ax.hlines(mean_val, positions[i]-0.3, positions[i]+0.3, colors="red", linestyles="dashed", linewidth=2)

    # Legend
    ax.plot([], [], color="red", linestyle="dashed", linewidth=2, label="Mean")
    ax.plot([], [], color="black", linestyle="-", linewidth=1.5, label="Median")

    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"Per-Core Total Wins: {test_type}")
    ax.set_ylim(bottom=0)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Core Number")
    ax.legend(loc="upper right", fontsize=12, frameon=True)
    plt.tight_layout()
    plt.savefig(CSV_FILE + "_per_core_total_violin.png", dpi=300)
    plt.close()

# -------------------------------
# Generate all plots
# -------------------------------
make_plots("Grouped by Socket", grouped_by_socket)
make_plots("cross_socket", cross_socket)
socket_level_plots()
per_core_total_plots()

print("\nAll 8 plots generated successfully.\n")
