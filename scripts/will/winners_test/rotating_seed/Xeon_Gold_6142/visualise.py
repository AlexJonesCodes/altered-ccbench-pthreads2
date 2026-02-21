import csv
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict
from itertools import combinations
from scipy.stats import ttest_ind, skew, kurtosis

CSV_FILE = "6400_runs_1.6mill_reps_repeat/6400_runs_1.6mill_reps_repeat.csv"

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
        wins = int(row["wins"])
        test_type = row["test_type"]

        core_winners[cpu].append(wins)
        runs.add(run)

runs = sorted(runs)

NUM_CORES = 32
NUM_SOCKETS = 2

# -------------------------------
# Helpers
# -------------------------------
def cpu_to_socket(cpu):
    # Socket 0 = even CPUs, Socket 1 = odd CPUs
    return cpu % 2

def smt_cpus(core_id):
    # Each core has 2 SMT threads: core_id and core_id+32
    return [core_id, core_id + 32]

def socket_color(cpu):
    return "#d9d9d9" if cpu_to_socket(cpu) == 0 else "#bfbfbf"

def compute_centers(group_sizes, positions):
    centers = []
    idx = 0
    for size in group_sizes:
        group = positions[idx:idx+size]
        centers.append((group[0] + group[-1]) / 2)
        idx += size
    return centers

# -------------------------------
# Per-core statistics
# -------------------------------
print(f"\nDetailed Statistics per Core — Test Type: {test_type}\n")
for core_id in range(NUM_CORES):
    data = np.concatenate([core_winners[c] for c in smt_cpus(core_id)])
    mean = np.mean(data)
    median = np.median(data)
    std = np.std(data, ddof=1)
    q1, q3 = np.percentile(data, [25, 75])
    iqr = q3 - q1
    outliers = data[(data < q1 - 1.5*iqr) | (data > q3 + 1.5*iqr)]
    outlier_rate = len(outliers)/len(data)
    sk = skew(data)
    kurt = kurtosis(data)
    print(f"Core {core_id:2d} | Mean: {mean:10.2f} | Median: {median:10.2f} "
          f"| Std: {std:10.2f} | IQR: {iqr:10.2f} "
          f"| Outliers: {len(outliers)} ({outlier_rate*100:.2f}%) "
          f"| Skew: {sk:6.2f} | Kurtosis: {kurt:6.2f}")

# -------------------------------
# Pairwise t-tests
# -------------------------------
print("\nPairwise Welch t-tests (p < 0.05 significant)\n")
for c1, c2 in combinations(range(NUM_CORES), 2):
    data1 = np.concatenate([core_winners[c] for c in smt_cpus(c1)])
    data2 = np.concatenate([core_winners[c] for c in smt_cpus(c2)])
    t_stat, p_val = ttest_ind(data1, data2, equal_var=False)
    sig = "*" if p_val < 0.05 else ""
    print(f"Core {c1} vs Core {c2}: t={t_stat:7.2f}, p={p_val:.5f} {sig}")

# -------------------------------
# Plotting function
# -------------------------------
def make_plots(ordering_name, build_positions_func):
    result = build_positions_func()
    if result is None:
        print(f"Skipping {ordering_name} — no data")
        return

    data_for_plot, cpu_labels, core_labels, positions, group_sizes = result
    if len(data_for_plot) == 0:
        print(f"Skipping {ordering_name} — empty plot data")
        return

    centers = compute_centers(group_sizes, positions)

    # ---------- BAR + LINE ----------
    fig, ax = plt.subplots(figsize=(14, 7))
    means = [np.mean(d) for d in data_for_plot]
    medians = [np.median(d) for d in data_for_plot]

    bars = ax.bar(positions, means, width=0.6, edgecolor="black")
    for i, bar in enumerate(bars):
        bar.set_facecolor(socket_color(int(cpu_labels[i])))

    ax.plot(positions, medians, color="blue", marker="o", linewidth=2)
    ax.set_ylabel("Instructions Executed")
    ax.set_xlabel("Core ID")
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
    ax.set_xticklabels(core_labels)

    plt.tight_layout()
    plt.savefig(CSV_FILE + f"_{ordering_name}_barline.png", dpi=300)
    plt.close()

    # ---------- VIOLIN ----------
    fig, ax = plt.subplots(figsize=(14, 7))
    vp = ax.violinplot(data_for_plot, positions=positions, showmedians=True)
    for i, body in enumerate(vp["bodies"]):
        body.set_facecolor(socket_color(int(cpu_labels[i])))
        body.set_edgecolor("black")
        body.set_alpha(0.6)

    for i, d in enumerate(data_for_plot):
        ax.hlines(np.mean(d), positions[i]-0.3, positions[i]+0.3,
                  colors="red", linestyles="dashed", linewidth=2)

    ax.set_ylabel("Instructions Executed")
    ax.set_xlabel("Core ID")
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
    ax.set_xticklabels(core_labels)

    plt.tight_layout()
    plt.savefig(CSV_FILE + f"_{ordering_name}_violin.png", dpi=300)
    plt.close()

# -------------------------------
# Ordering functions
# -------------------------------
def grouped_by_socket():
    data_for_plot = []
    cpu_labels = []
    core_labels = []
    positions = []
    group_sizes = []
    pos = 1

    for core_id in range(NUM_CORES):
        smt_pair = smt_cpus(core_id)
        for cpu in smt_pair:
            data_for_plot.append(core_winners[cpu])
            cpu_labels.append(str(cpu))
            positions.append(pos)
            pos += 1
        core_labels.append(str(core_id))
        group_sizes.append(len(smt_pair))

    return data_for_plot, cpu_labels, core_labels, positions, group_sizes

def cross_socket_interleaved():
    data_for_plot = []
    cpu_labels = []
    core_labels = []
    positions = []
    group_sizes = []
    pos = 1

    # Interleave SMT threads from both sockets for each core
    for core_id in range(NUM_CORES):
        smt_pair = [core_id, core_id+32]  # socket0, socket1
        smt_pair = sorted(smt_pair)       # keep consistent order
        for cpu in smt_pair:
            data_for_plot.append(core_winners[cpu])
            cpu_labels.append(str(cpu))
            positions.append(pos)
            pos += 1
        core_labels.append(str(core_id))
        group_sizes.append(len(smt_pair))

    return data_for_plot, cpu_labels, core_labels, positions, group_sizes

# -------------------------------
# Socket-level plots
# -------------------------------
def socket_level_plots():
    data_for_plot = []
    cpu_labels = []
    positions = [1,2]

    # Socket 0 = even CPUs
    soc0_cpus = [cpu for cpu in range(64) if cpu_to_socket(cpu)==0]
    soc0_data = [v for cpu in soc0_cpus for v in core_winners[cpu]]
    data_for_plot.append(soc0_data)
    cpu_labels.append("Socket 0")

    # Socket 1 = odd CPUs
    soc1_cpus = [cpu for cpu in range(64) if cpu_to_socket(cpu)==1]
    soc1_data = [v for cpu in soc1_cpus for v in core_winners[cpu]]
    data_for_plot.append(soc1_data)
    cpu_labels.append("Socket 1")

    core_labels = ["Socket 0", "Socket 1"]

    # Bar + Line
    fig, ax = plt.subplots(figsize=(8,7))
    means = [np.mean(d) for d in data_for_plot]
    medians = [np.median(d) for d in data_for_plot]
    ax.bar(positions, means, width=0.6, edgecolor="black", color=["#d9d9d9","#bfbfbf"])
    ax.plot(positions, medians, color="blue", marker="o", linewidth=2)
    ax.set_xticks(positions)
    ax.set_xticklabels(core_labels)
    ax.set_xlabel("Socket")
    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"Socket Comparison: {test_type}")
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
    ax.set_title(f"Socket Comparison: {test_type}")
    plt.tight_layout()
    plt.savefig(CSV_FILE + "_socket_violin.png", dpi=300)
    plt.close()

# -------------------------------
# Per-core totals
# -------------------------------
def per_core_total_plots():
    data_for_plot = []
    cpu_labels = []
    core_labels = []
    positions = []
    group_sizes = []
    pos = 1

    for core_id in range(NUM_CORES):
        smt_pair = smt_cpus(core_id)
        total_per_run = [a+b for a,b in zip(core_winners[smt_pair[0]], core_winners[smt_pair[1]])]
        data_for_plot.append(total_per_run)
        cpu_labels.append(str(core_id))  # one label per core for total
        positions.append(pos)
        pos += 1
        core_labels.append(str(core_id))
        group_sizes.append(1)

    centers = positions  # for per-core totals, label directly on each core

    # Bar + Line
    fig, ax = plt.subplots(figsize=(14,7))
    means = [np.mean(d) for d in data_for_plot]
    medians = [np.median(d) for d in data_for_plot]
    ax.bar(positions, means, width=0.6, edgecolor="black", color="#d9d9d9")
    ax.plot(positions, medians, color="blue", marker="o", linewidth=2)
    ax.set_xticks(centers)
    ax.set_xticklabels(core_labels)
    ax.set_xlabel("Core ID")
    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"Per-Core Total Wins: {test_type}")
    plt.tight_layout()
    plt.savefig(CSV_FILE + "_per_core_total_barline.png", dpi=300)
    plt.close()

    # Violin
    fig, ax = plt.subplots(figsize=(14,7))
    vp = ax.violinplot(data_for_plot, positions=positions, showmedians=True)
    for body in vp["bodies"]:
        body.set_facecolor("#d9d9d9")
        body.set_edgecolor("black")
        body.set_alpha(0.6)
    for i, d in enumerate(data_for_plot):
        ax.hlines(np.mean(d), positions[i]-0.3, positions[i]+0.3, colors="red", linestyles="dashed", linewidth=2)
    ax.set_xticks(centers)
    ax.set_xticklabels(core_labels)
    ax.set_xlabel("Core ID")
    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"Per-Core Total Wins: {test_type}")
    plt.tight_layout()
    plt.savefig(CSV_FILE + "_per_core_total_violin.png", dpi=300)
    plt.close()

# -------------------------------
# Generate all plots
# -------------------------------
make_plots("Grouped_by_Socket", grouped_by_socket)
make_plots("Cross_Socket", cross_socket_interleaved)
socket_level_plots()
per_core_total_plots()

print("\nAll 8 plots generated successfully.\n")