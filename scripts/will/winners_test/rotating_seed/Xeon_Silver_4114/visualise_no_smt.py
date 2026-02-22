import csv
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict
from itertools import combinations
from scipy.stats import ttest_ind, skew, kurtosis

CSV_FILE = "1cpu_per_core_4kruns_1_000_000_reps/4000_runs_1mill_reps_one_cpu_per_core.csv"

# -------------------------------------------------
# SOCKET TOPOLOGY (YOUR REAL HARDWARE)
# -------------------------------------------------
SOCKET_CPU_MAP = {
    0: set(list(range(0, 10)) + list(range(20, 30))),
    1: set(list(range(10, 20)) + list(range(30, 40))),
}

def get_socket(cpu):
    for s, cpuset in SOCKET_CPU_MAP.items():
        if cpu in cpuset:
            return s
    return None

def socket_color(cpu):
    s = get_socket(cpu)
    return "#d9d9d9" if s == 0 else "#bfbfbf"

# -------------------------------------------------
# READ DATA
# -------------------------------------------------
cpu_winners = defaultdict(list)
runs = set()
test_type = None

with open(CSV_FILE, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        run = int(row["run"])
        cpu = int(row["cpu"])
        wins = int(row["wins"])
        test_type = row["test_type"]

        cpu_winners[cpu].append(wins)
        runs.add(run)

cpus = sorted(cpu_winners.keys())
runs = sorted(runs)

print("\nDetected CPUs:", cpus)

# detect which sockets actually exist in data
sockets_present = sorted({
    get_socket(c) for c in cpus if get_socket(c) is not None
})
print("Detected sockets:", sockets_present)

# -------------------------------------------------
# EXTENDED STATS
# -------------------------------------------------
print(f"\nDetailed Statistics per Logical CPU — Test Type: {test_type}\n")

outlier_summary = []

for cpu in cpus:
    data = np.array(cpu_winners[cpu])

    mean = np.mean(data)
    median = np.median(data)
    std = np.std(data, ddof=1)
    q1 = np.percentile(data, 25)
    q3 = np.percentile(data, 75)
    iqr = q3 - q1

    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    outliers = data[(data < lower) | (data > upper)]
    outlier_rate = len(outliers) / len(data)

    p5 = np.percentile(data, 5)
    p95 = np.percentile(data, 95)

    sk = skew(data)
    kurt = kurtosis(data)

    outlier_summary.append(outlier_rate)

    print(
        f"CPU {cpu:2d} | Runs {len(data)} "
        f"| Mean {mean:10.2f} | Median {median:10.2f} "
        f"| Std {std:10.2f} | IQR {iqr:10.2f} "
        f"| 5th {p5:10.2f} | 95th {p95:10.2f} "
        f"| Outliers {len(outliers):4d} ({outlier_rate*100:5.2f}%) "
        f"| Skew {sk:6.2f} | Kurt {kurt:6.2f}"
    )

print("\nOutlier Rate Summary:")
print(f"Mean {np.mean(outlier_summary)*100:.2f}%")
print(f"Max {np.max(outlier_summary)*100:.2f}%")
print(f"Min {np.min(outlier_summary)*100:.2f}%")

# -------------------------------------------------
# PAIRWISE T TESTS
# -------------------------------------------------
print("\nPairwise Welch t-tests (p < 0.05 significant)\n")
for c1, c2 in combinations(cpus, 2):
    t, p = ttest_ind(cpu_winners[c1], cpu_winners[c2], equal_var=False)
    sig = "*" if p < 0.05 else ""
    print(f"CPU {c1} vs CPU {c2}: t={t:7.2f}, p={p:.5f} {sig}")

# -------------------------------------------------
# PER CPU PLOTS
# -------------------------------------------------
def per_cpu_plots():

    data = [cpu_winners[c] for c in cpus]
    positions = list(range(1, len(cpus)+1))
    labels = [str(c) for c in cpus]

    # BAR
    fig, ax = plt.subplots(figsize=(14,7))
    means = [np.mean(d) for d in data]
    medians = [np.median(d) for d in data]

    bars = ax.bar(positions, means, edgecolor="black")
    for i, bar in enumerate(bars):
        bar.set_facecolor(socket_color(cpus[i]))

    ax.plot(positions, medians, marker="o")
    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Logical CPU")
    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"Per CPU Performance: {test_type}")

    plt.tight_layout()
    plt.savefig(CSV_FILE + "_per_cpu_barline.png", dpi=300)
    plt.close()

    # VIOLIN
    fig, ax = plt.subplots(figsize=(14,7))
    vp = ax.violinplot(data, positions=positions, showmedians=True)

    for i, body in enumerate(vp["bodies"]):
        body.set_facecolor(socket_color(cpus[i]))
        body.set_edgecolor("black")
        body.set_alpha(0.6)

    for i, d in enumerate(data):
        ax.hlines(np.mean(d), positions[i]-0.3, positions[i]+0.3,
                  linestyles="dashed")

    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Logical CPU")
    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"Per CPU Performance: {test_type}")

    plt.tight_layout()
    plt.savefig(CSV_FILE + "_per_cpu_violin.png", dpi=300)
    plt.close()

# -------------------------------------------------
# SOCKET COMPARISON (FIXED)
# -------------------------------------------------
def socket_plots():

    if not sockets_present:
        print("No sockets detected — skipping socket plots")
        return

    data = []
    labels = []
    positions = []

    for i, s in enumerate(sockets_present):
        vals = []
        for cpu in cpus:
            if get_socket(cpu) == s:
                vals.extend(cpu_winners[cpu])

        if len(vals) == 0:
            continue

        data.append(vals)
        labels.append(f"Socket {s}")
        positions.append(len(positions)+1)

    if len(data) == 0:
        print("No socket data — skipping")
        return

    # BAR
    fig, ax = plt.subplots(figsize=(8,7))
    means = [np.mean(d) for d in data]
    medians = [np.median(d) for d in data]

    ax.bar(positions, means, edgecolor="black")
    ax.plot(positions, medians, marker="o")

    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Socket")
    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"Socket Comparison: {test_type}")

    plt.tight_layout()
    plt.savefig(CSV_FILE + "_socket_barline.png", dpi=300)
    plt.close()

    # VIOLIN
    fig, ax = plt.subplots(figsize=(8,7))
    vp = ax.violinplot(data, positions=positions, showmedians=True)

    for i, body in enumerate(vp["bodies"]):
        body.set_facecolor("#d9d9d9" if i == 0 else "#bfbfbf")
        body.set_edgecolor("black")
        body.set_alpha(0.6)

    for i, d in enumerate(data):
        ax.hlines(np.mean(d), positions[i]-0.3, positions[i]+0.3,
                  linestyles="dashed")

    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Socket")
    ax.set_ylabel("Instructions Executed")
    ax.set_title(f"Socket Comparison: {test_type}")

    plt.tight_layout()
    plt.savefig(CSV_FILE + "_socket_violin.png", dpi=300)
    plt.close()

# -------------------------------------------------
# RUN
# -------------------------------------------------
per_cpu_plots()
socket_plots()

print("\nAll plots generated successfully.\n")