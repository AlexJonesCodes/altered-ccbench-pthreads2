import csv
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict
from itertools import combinations
from scipy.stats import ttest_ind, skew, kurtosis
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

<<<<<<< HEAD
CSV_FILE = "tas_4kruns_1_000_000_reps/3/4000_runs_1mill_reps_tas.csv"
=======
CSV_FILE = "tas_4kruns_1_000_000_reps/4000_runs_1mill_reps_tas.csv"
>>>>>>> a117d36 (create multi-test runner)

# -------------------------------
# Read data
# -------------------------------
core_winners = defaultdict(list)
runs = set()
test_type = None
force_top_yticks = False

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

print("\nDetected CPUs:", cores)

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
    q1 = np.percentile(data, 25)
    q3 = np.percentile(data, 75)
    iqr = q3 - q1

    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr
    outliers = data[(data < lower_bound) | (data > upper_bound)]
    outlier_rate = len(outliers) / len(data)

    p5 = np.percentile(data, 5)
    p95 = np.percentile(data, 95)

    sk = skew(data)
    kurt = kurtosis(data)

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
# Compute core centers safely
# -------------------------------
def compute_centers(group_sizes, positions):
    centers = []
    idx = 0
    for size in group_sizes:
        group = positions[idx:idx+size]
        centers.append((group[0] + group[-1]) / 2)
        idx += size
    return centers

# -------------------------------
# Plot builder
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

    bars = ax.bar(positions, means, width=0.6, edgecolor="black", facecolor="#d9d9d9")
    for i, bar in enumerate(bars):
        bar.set_facecolor(socket_color(int(cpu_labels[i])))

    median_line, = ax.plot(positions, medians, color="deepskyblue", marker="o", linewidth=2)

    # legend (bar = mean, line = median)
    legend_handles = [
        Patch(facecolor="#d9d9d9", edgecolor="black", label="Mean"),
        Line2D([0], [0], color=median_line.get_color(), marker="o", label="Median")
    ]
    ax.legend(handles=legend_handles)

    ax.set_ylabel("Instructions Executed")
    ax.set_xlabel("Core Number")
    ax.set_title(f"{ordering_name}: {test_type}")
    ax.set_ylim(bottom=0 if not force_top_yticks else 0,
                top=45000 if force_top_yticks else None)

    ax_top = ax.twiny()
    ax_top.set_xlim(ax.get_xlim())
    ax_top.set_xticks(positions)
    ax_top.set_xticklabels(cpu_labels)
    ax_top.set_xlabel("CPU Number")

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

    # legend (solid = median, dashed = mean)
    legend_handles = [
        Line2D([0], [0], color="black", lw=2, label="Median"),
        Line2D([0], [0], color="red", lw=2, linestyle="--", label="Mean")
    ]
    ax.legend(handles=legend_handles)

    ax.set_ylabel("Instructions Executed")
    ax.set_xlabel("Core Number")
    ax.set_title(f"{ordering_name}: {test_type}")
    ax.set_ylim(bottom=0 if not force_top_yticks else 0,
                top=45000 if force_top_yticks else None)

    ax_top = ax.twiny()
    ax_top.set_xlim(ax.get_xlim())
    ax_top.set_xticks(positions)
    ax_top.set_xticklabels(cpu_labels)
    ax_top.set_xlabel("CPU Number")

    ax.set_xticks(centers)
    ax.set_xticklabels(core_labels)

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
    group_sizes = []
    pos = 1

    logical_cores = sorted(set(c % 20 for c in cores))

    for lc in logical_cores:
        group = []
        for cpu in [lc, lc + 20]:
            if cpu in core_winners:
                data_for_plot.append(core_winners[cpu])
                cpu_labels.append(str(cpu))
                positions.append(pos)
                group.append(cpu)
                pos += 1
        if group:
            core_labels.append(str(lc))
            group_sizes.append(len(group))

    if not data_for_plot:
        return None

    return data_for_plot, cpu_labels, core_labels, positions, group_sizes

# -------------------------------
# Socket-level plots
# -------------------------------
def socket_level_plots():
    data_for_plot = []
    labels = []
    positions = []

    sockets = {
        "Socket 0": list(range(0,10)) + list(range(20,30)),
        "Socket 1": list(range(10,20)) + list(range(30,40))
    }

    pos = 1
    for name, cpus in sockets.items():
        vals = [v for cpu in cpus if cpu in core_winners for v in core_winners[cpu]]
        if len(vals) == 0:
            continue
        data_for_plot.append(vals)
        labels.append(name)
        positions.append(pos)
        pos += 1

    if len(data_for_plot) == 0:
        print("Skipping socket plots — no socket data")
        return

    # BAR
    fig, ax = plt.subplots(figsize=(8,7))
    means = [np.mean(d) for d in data_for_plot]
    medians = [np.median(d) for d in data_for_plot]

    bars = ax.bar(positions, means, edgecolor="black", facecolor="#d9d9d9")
    median_line, = ax.plot(positions, medians, marker="o", color="deepskyblue")

    legend_handles = [
        Patch(facecolor="#d9d9d9", edgecolor="black", label="Mean"),
        Line2D([0], [0], color=median_line.get_color(), marker="o", label="Median")
    ]
    ax.legend(handles=legend_handles)

    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.set_title(f"Socket Comparison: {test_type}")
    ax.set_ylabel("Instructions Executed")
    ax.set_ylim(bottom=0 if not force_top_yticks else 0,
                top=45000 if force_top_yticks else None)

    plt.tight_layout()
    plt.savefig(CSV_FILE + "_socket_barline.png", dpi=300)
    plt.close()

    # VIOLIN
    fig, ax = plt.subplots(figsize=(8,7))
    vp = ax.violinplot(data_for_plot, positions=positions, showmedians=True)

    for body in vp["bodies"]:
        body.set_edgecolor("black")
        body.set_facecolor("#d9d9d9")
        body.set_alpha(0.6)

    for i, d in enumerate(data_for_plot):
        ax.hlines(np.mean(d), positions[i]-0.3, positions[i]+0.3,
                  colors="red", linestyles="dashed", linewidth=2)

    legend_handles = [
        Line2D([0], [0], color="black", lw=2, label="Median"),
        Line2D([0], [0], color="red", lw=2, linestyle="--", label="Mean")
    ]
    ax.legend(handles=legend_handles)

    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"Socket Comparison: {test_type}")
    ax.set_ylim(bottom=0 if not force_top_yticks else 0,
                top=45000 if force_top_yticks else None)

    plt.tight_layout()
    plt.savefig(CSV_FILE + "_socket_violin.png", dpi=300)
    plt.close()

# -------------------------------
# Per-core SMT totals
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

    if len(per_core_data) == 0:
        print("Skipping per-core SMT totals — no SMT pairs present")
        return

    data_for_plot = []
    positions = []
    labels = []
    pos = 1

    for core in sorted(per_core_data.keys()):
        data_for_plot.append(per_core_data[core])
        positions.append(pos)
        labels.append(str(core))
        pos += 1

    # BAR + LINE
    fig, ax = plt.subplots(figsize=(14,7))
    means = [np.mean(d) for d in data_for_plot]
    medians = [np.median(d) for d in data_for_plot]

    bars = ax.bar(positions, means, edgecolor="black", facecolor="#d9d9d9")
    median_line, = ax.plot(positions, medians, marker="o", color="deepskyblue")

    legend_handles = [
        Patch(facecolor="#d9d9d9", edgecolor="black", label="Mean"),
        Line2D([0], [0], color=median_line.get_color(), marker="o", label="Median")
    ]
    ax.legend(handles=legend_handles)

    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Instructions Executed")
    ax.set_ylim(bottom=0 if not force_top_yticks else 0,
                top=80000 if force_top_yticks else None)
    ax.set_title(f"Per-Core Total Wins: {test_type}")

    plt.tight_layout()
    plt.savefig(CSV_FILE + "_per_core_total_barline.png", dpi=300)
    plt.close()

    # VIOLIN
    fig, ax = plt.subplots(figsize=(14,7))
    vp = ax.violinplot(data_for_plot, positions=positions, showmedians=True)

    for body in vp["bodies"]:
        body.set_facecolor("#d9d9d9")
        body.set_edgecolor("black")
        body.set_alpha(0.6)

    for i, d in enumerate(data_for_plot):
        ax.hlines(np.mean(d), positions[i]-0.3, positions[i]+0.3,
                  colors="red", linestyles="dashed", linewidth=2)

    legend_handles = [
        Line2D([0], [0], color="black", lw=2, label="Median"),
        Line2D([0], [0], color="red", lw=2, linestyle="--", label="Mean")
    ]
    ax.legend(handles=legend_handles)

    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Instructions Executed")
    ax.set_ylim(bottom=0 if not force_top_yticks else 0,
                top=80000 if force_top_yticks else None)
    ax.set_title(f"Per-Core Total Wins: {test_type}")

    plt.tight_layout()
    plt.savefig(CSV_FILE + "_per_core_total_violin.png", dpi=300)
    plt.close()

# -------------------------------
# Generate plots
# -------------------------------
make_plots("Grouped by Socket", grouped_by_socket)
socket_level_plots()
per_core_total_plots()

print("\nAll plots generated successfully.\n")