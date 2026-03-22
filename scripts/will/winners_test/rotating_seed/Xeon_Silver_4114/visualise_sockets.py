import csv
import os
import numpy as np
import matplotlib.pyplot as plt
from collections import defaultdict
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

# -------------------------------
# PLOT TOGGLES
# -------------------------------
RUN_ORIGINAL_PLOTS = False
RUN_GINI_PLOTS = True
RUN_DOMINANCE_PLOTS = False
RUN_SCATTER_PLOTS = False
RUN_CV_PLOTS = False
RUN_IMBALANCE_PLOTS = True

plt.rcParams.update({
    'font.size': 18,
    'axes.titlesize': 20,
    'axes.labelsize': 18,
    'xtick.labelsize': 16,
    'ytick.labelsize': 16,
    'legend.fontsize': 16,
    'figure.titlesize': 22
})

# -------------------------------
# INPUT CSV FILES
# -------------------------------
CSV_FILES = [

"4kruns_1_000_000_reps2/4000_runs_1mill_reps2.csv",
"cas_4kruns_1_000_000_reps/4000_runs_1mill_reps_cas.csv",
"fai_4kruns_1_000_000_reps/4000_runs_1mill_reps_fai_rep.csv",
"load_on_modified_4kruns_1_000_000_reps/load_on_modified_4000_runs_1mill_reps.csv",
"swap_4kruns_1_000_000_reps/4000_runs_1mill_reps_swap.csv",
"tas_4kruns_1_000_000_reps/1/4000_runs_1mill_reps_tas.csv",

"../Xeon_Gold_6142/6400_runs_1.6mill_reps_repeat/6400_runs_1.6mill_reps_repeat.csv",
"../Xeon_Gold_6142/cas_6400_runs_1.6mill_reps_repeat/cas_6400_runs_1.6mill_reps.csv",
"../Xeon_Gold_6142/fai_6400_runs_1.6mill_reps_repeat/fai_6400_runs_1.6mill_reps.csv",
"../Xeon_Gold_6142/load_on_modified_6400_runs_1.6mill_reps_repeat/6400_runs_1.6mill_reps_load_on_modified.csv",
"../Xeon_Gold_6142/swap_6400_runs_1.6mill_reps_repeat/swap_6400_runs_1.6mill_reps.csv",
"../Xeon_Gold_6142/tas_6400_runs_1.6mill_reps/1/6400_runs_1.6mill_reps_tas.csv"
]

NUM_SILVER = 6
NUM_TOTAL = len(CSV_FILES)
is_gold_list = [False]*NUM_SILVER + [True]*(NUM_TOTAL - NUM_SILVER)

OUTPUT_DIR = "socket_plots/fairness/"
os.makedirs(OUTPUT_DIR, exist_ok=True)

LABEL_MAP = {
"STORE_ON_MODIFIED": "STORE",
"LOAD_FROM_MODIFIED": "LOAD"
}

# -------------------------------
# Socket mapping
# -------------------------------
def get_socket_mapping(is_gold, total_cpus):

    if is_gold:
        return {
            0: [cpu for cpu in range(total_cpus) if cpu % 2 == 0],
            1: [cpu for cpu in range(total_cpus) if cpu % 2 == 1],
        }

    return {
        0: list(range(0,10)) + list(range(20,30)),
        1: list(range(10,20)) + list(range(30,40)),
    }

# -------------------------------
# Color helper
# -------------------------------
def cpu_color_per_test(is_gold, socket):

    if is_gold:
        return "#d4af37" if socket == 0 else "#b8962e"
    else:
        return "#d9d9d9" if socket == 0 else "#a6a6a6"

# -------------------------------
# Load CSV
# -------------------------------
def load_test(csv_file):

    core_winners = defaultdict(lambda: defaultdict(int))
    test_type = None

    with open(csv_file, newline="") as f:

        reader = csv.DictReader(f)

        for row in reader:

            run = int(row["run"])
            cpu = int(row["cpu"])
            wins = int(row["wins"])
            test_type = row["test_type"]

            core_winners[run][cpu] += wins

    return core_winners, test_type

# -------------------------------
# Helper totals
# -------------------------------
def total_executions_per_run(core_winners):

    totals = {}

    for run, cpu_dict in core_winners.items():
        totals[run] = sum(cpu_dict.values())

    return totals

# -------------------------------
# Compute socket data
# -------------------------------
def compute_socket_data(core_winners, SOCKETS, mode="totals"):

    socket_data = defaultdict(list)
    totals_per_run = total_executions_per_run(core_winners)

    for run in core_winners:

        total_cpus = sum(map(len, SOCKETS.values()))
        fair_share = totals_per_run[run] / total_cpus

        for socket, cpus in SOCKETS.items():

            socket_sum = sum(core_winners[run].get(cpu,0) for cpu in cpus)

            if mode == "totals":
                socket_data[socket].append(socket_sum / (fair_share * len(cpus)))
            else:
                for cpu in cpus:
                    wins = core_winners[run].get(cpu,0)
                    socket_data[socket].append(wins / fair_share)

    return socket_data

# -------------------------------
# Gini coefficient
# -------------------------------
def gini(values):

    values = np.array(values, dtype=float)

    if np.sum(values) == 0:
        return 0

    values = np.sort(values)
    n = len(values)
    index = np.arange(1,n+1)

    return (2*np.sum(index*values)/(n*np.sum(values))) - (n+1)/(n-1)

# -------------------------------
# Coefficient of Variation
# -------------------------------
def coefficient_of_variation(values):

    values = np.array(values, dtype=float)

    mean = np.mean(values)

    if mean == 0:
        return 0

    return np.std(values) / mean

# -------------------------------
# Imbalance ratio
# -------------------------------
def imbalance_ratio(s0, s1):

    total = s0 + s1

    if total == 0:
        return 0

    return abs(s0 - s1) / total

# -------------------------------
# Dominance share per run
# -------------------------------
def dominance_per_run(core_winners, SOCKETS):

    shares = []

    for run, cpu_dict in core_winners.items():

        s0 = sum(cpu_dict.get(cpu,0) for cpu in SOCKETS[0])
        s1 = sum(cpu_dict.get(cpu,0) for cpu in SOCKETS[1])

        total = s0 + s1

        if total == 0:
            continue

        shares.append(max(s0/total, s1/total))

    return shares


# -------------------------------
# Socket share per run
# -------------------------------
def socket_share_per_run(core_winners, SOCKETS):

    shares = []

    for run, cpu_dict in core_winners.items():

        s0 = sum(cpu_dict.get(cpu,0) for cpu in SOCKETS[0])
        s1 = sum(cpu_dict.get(cpu,0) for cpu in SOCKETS[1])

        total = s0 + s1

        if total == 0:
            continue

        shares.append(s0 / total)

    return shares

# -------------------------------
# Axis helpers
# -------------------------------
def format_axes(ax, positions, socket_labels, ylabel, fair_value_real, title):

    ax.set_xticks(positions)
    ax.set_xticklabels(socket_labels)
    ax.set_xlabel("Socket and Operation", labelpad=40)
    ax.set_ylabel(f"{ylabel} (Fair = {fair_value_real})")
    ax.set_ylim(bottom=0)
    ax.set_yticks(np.arange(0, ax.get_ylim()[1] + 0.1, 0.1))
    ax.set_title(title)

def add_test_separators(ax, positions):

    for i in range(1, len(positions)-1, 2):

        boundary = (positions[i] + positions[i+1]) / 2
        ax.axvline(boundary, color="black", linewidth=1, alpha=0.4)

def add_group_labels(ax, positions, labels):

    for i in range(0, len(positions), 2):

        mid = (positions[i] + positions[i+1]) / 2

        ax.text(
            mid,
            -0.10,
            labels[i],
            ha='center',
            va='top',
            transform=ax.get_xaxis_transform(),
            fontsize=16
        )

# -------------------------------
# Load tests
# -------------------------------
tests = []

for csv_file, is_gold in zip(CSV_FILES, is_gold_list):

    core_winners, test_type = load_test(csv_file)

    total_cpus = max(core_winners[next(iter(core_winners))].keys()) + 1
    SOCKETS = get_socket_mapping(is_gold, total_cpus)

    dominance_vals = dominance_per_run(core_winners, SOCKETS)
    share_vals = socket_share_per_run(core_winners, SOCKETS)

    socket_totals = []
    gini_runs = []
    imbalance_runs = []

    for run, cpu_dict in core_winners.items():

        s0 = sum(cpu_dict.get(cpu,0) for cpu in SOCKETS[0])
        s1 = sum(cpu_dict.get(cpu,0) for cpu in SOCKETS[1])

        socket_totals.extend([s0, s1])

        gini_runs.append(gini([s0, s1]))
        imbalance_runs.append(imbalance_ratio(s0, s1))

    tests.append({
        "type": test_type,
        "totals": compute_socket_data(core_winners, SOCKETS, "totals"),
        "individual": compute_socket_data(core_winners, SOCKETS, "individual"),
        "gini_total": gini(socket_totals),
        "gini_runs": gini_runs,
        "dominance": dominance_vals,
        "shares": share_vals,
        "cv": coefficient_of_variation(dominance_vals),
        "imbalance": np.mean(imbalance_runs)
    })

# -------------------------------
# ORIGINAL PLOT FUNCTION
# -------------------------------
def make_plots(dataset, ylabel, title, filename):

    data_for_plot = []
    positions = []
    socket_labels = []
    test_labels = []

    pos = 1

    # -------------------------------
    # Compute fair value per CPU
    # -------------------------------
    with open(CSV_FILES[0], newline="") as f:

        reader = csv.DictReader(f)

        first_run_rows = [row for row in reader if int(row["run"]) == 1]

        total_executions = sum(int(row["wins"]) for row in first_run_rows)

        total_cpus = len(first_run_rows)

    if dataset == "totals":
        fair_value_real = total_executions / 2
    else:
        fair_value_real = total_executions / total_cpus

    for test_idx, test in enumerate(tests):
        for socket in [0,1]:

            data_for_plot.append(test[dataset][socket])
            positions.append(pos)
            socket_labels.append(str(socket))
            test_labels.append(LABEL_MAP.get(test["type"], test["type"]))

            pos += 1

    if not data_for_plot:
        print(f"Warning: no data to plot for {filename}")
        return

    # -------------------------------
    # BAR plot
    # -------------------------------
    fig, ax = plt.subplots(figsize=(14,7))

    means = [np.mean(d) for d in data_for_plot]
    medians = [np.median(d) for d in data_for_plot]

    bars = ax.bar(positions, means, edgecolor="black")

    for i, bar in enumerate(bars):

        test_idx = i // 2
        socket = int(socket_labels[i])

        bar.set_facecolor(cpu_color_per_test(is_gold_list[test_idx], socket))

    ax.plot(positions, medians, marker="o", color="deepskyblue")

    ax.axhline(1.0, color='gray', linestyle='--', label='Fair Value')

    legend_handles = [
        Patch(facecolor="#d9d9d9", edgecolor="black", label="Socket 0"),
        Patch(facecolor="#a6a6a6", edgecolor="black", label="Socket 1"),
        Line2D([0],[0],color="deepskyblue",marker="o",label="Median"),
        Line2D([0],[0],color='gray',linestyle='--',label='Fair Value')
    ]

    format_axes(ax, positions, socket_labels, ylabel, fair_value_real, title)

    add_group_labels(ax, positions, test_labels)
    add_test_separators(ax, positions)

    ax.legend(handles=legend_handles, loc='lower right')

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, filename + "_bar.png"), dpi=300)
    plt.close()

    # -------------------------------
    # VIOLIN plot
    # -------------------------------
    fig, ax = plt.subplots(figsize=(14,7))

    vp = ax.violinplot(data_for_plot, positions=positions, showmedians=True, bw_method=0.2)

    for i, body in enumerate(vp["bodies"]):

        test_idx = i // 2
        socket = int(socket_labels[i])

        body.set_facecolor(cpu_color_per_test(is_gold_list[test_idx], socket))
        body.set_edgecolor("black")
        body.set_alpha(0.6)

    for i, d in enumerate(data_for_plot):
        ax.hlines(np.mean(d), positions[i]-0.3, positions[i]+0.3,
                  colors="red", linestyles="dashed", linewidth=2)

    ax.axhline(1.0, color='gray', linestyle='--', label='Fair Share')

    legend_handles = [
        Line2D([0],[0],color="black",lw=2,label="Median"),
        Line2D([0],[0],color="red",lw=2,linestyle="--",label="Mean"),
        Line2D([0],[0],color='gray',linestyle='--',label='Fair Share')
    ]

    format_axes(ax, positions, socket_labels, ylabel, fair_value_real, title)

    add_group_labels(ax, positions, test_labels)
    add_test_separators(ax, positions)

    ax.legend(handles=legend_handles, loc='lower right')

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, filename + "_violin.png"), dpi=300)
    plt.close()

# -------------------------------
# GINI PLOT
# -------------------------------
def make_gini_plot():

    totals = [t["gini_total"] for t in tests]
    runs = [t["gini_runs"] for t in tests]
    labels = [LABEL_MAP.get(t["type"], t["type"]) for t in tests]

    pos = np.arange(len(totals)) + 1

    fig, ax = plt.subplots(figsize=(14,7))

    # Bar plots (total gini)
    bars = ax.bar(pos, totals, width=0.9, edgecolor="black", zorder=3)

    for i, bar in enumerate(bars):

        if is_gold_list[i]:
            bar.set_facecolor("#d4af37")
        else:
            bar.set_facecolor("#d9d9d9")

    ax.set_xticks(pos)
    ax.set_xticklabels(labels)

    ax.set_ylabel("Gini Coefficient")
    ax.set_title("Socket Execution Inequality")
    ax.set_yticks(np.arange(0, 0.12, 0.01))

    legend_handles = [
        Patch(facecolor="#d9d9d9", edgecolor="black", label="Silver CPU"),
        Patch(facecolor="#d4af37", edgecolor="black", label="Gold CPU"),
    ]

    ax.legend(handles=legend_handles, loc="upper right")

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR,"gini_coefficient.png"), dpi=300)
    plt.close()

# -------------------------------
# DOMINANCE PLOT
# -------------------------------
def make_dominance_plot():

    data = [t["dominance"] for t in tests]
    labels = [LABEL_MAP.get(t["type"], t["type"]) for t in tests]

    pos = np.arange(len(data)) + 1

    fig, ax = plt.subplots(figsize=(14,7))

    vp = ax.violinplot(data, positions=pos, showmedians=True)

    for body in vp["bodies"]:
        body.set_facecolor("#c27ba0")
        body.set_edgecolor("black")
        body.set_alpha(0.6)

    ax.set_xticks(pos)
    ax.set_xticklabels(labels)

    ax.set_ylabel("Dominant Socket Share")
    ax.set_title("Socket Dominance per Run")

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR,"dominance_violin.png"), dpi=300)
    plt.close()


# -------------------------------
# SOCKET SHARE SCATTER
# -------------------------------

def make_scatter_plots():

    fig, ax = plt.subplots(figsize=(14,7))

    offset = 0

    for i, test in enumerate(tests):

        shares = test["shares"]
        runs = np.arange(len(shares)) + offset

        color = "#d4af37" if is_gold_list[i] else "#a6a6a6"

        ax.scatter(runs, shares, s=8, alpha=0.35, color=color)

        offset += len(shares) + 200

    ax.axhline(0.5, linestyle="--", color="black")

    ax.set_ylabel("Socket 0 Share")
    ax.set_xlabel("Run")
    ax.set_title("Socket Share Per Run")

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR,"socket_share_scatter.png"), dpi=300)
    plt.close()

# -------------------------------
# COEFFICIENT OF VARIATION PLOT
# -------------------------------
def make_cv_plot():

    values = [t["cv"] for t in tests]
    labels = [LABEL_MAP.get(t["type"], t["type"]) for t in tests]

    pos = np.arange(len(values)) + 1

    fig, ax = plt.subplots(figsize=(14,7))

    bars = ax.bar(pos, values, edgecolor="black")

    for i, bar in enumerate(bars):

        if is_gold_list[i]:
            bar.set_facecolor("#d4af37")
        else:
            bar.set_facecolor("#d9d9d9")

    ax.set_xticks(pos)
    ax.set_xticklabels(labels)

    ax.set_ylabel("Coefficient of Variation")
    ax.set_title("Run to Run Fairness Variability")

    legend_handles = [
        Patch(facecolor="#d9d9d9", edgecolor="black", label="Silver CPU"),
        Patch(facecolor="#d4af37", edgecolor="black", label="Gold CPU")
    ]

    ax.legend(handles=legend_handles, loc="upper right")

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR,"coefficient_of_variation.png"), dpi=300)
    plt.close()

# -------------------------------
# IMBALANCE RATIO PLOT
# -------------------------------
def make_imbalance_plot():

    values = [t["imbalance"] for t in tests]
    labels = [LABEL_MAP.get(t["type"], t["type"]) for t in tests]

    pos = np.arange(len(values)) + 1

    fig, ax = plt.subplots(figsize=(14,7))

    bars = ax.bar(pos, values, edgecolor="black")

    for i, bar in enumerate(bars):

        if is_gold_list[i]:
            bar.set_facecolor("#d4af37")
        else:
            bar.set_facecolor("#d9d9d9")

    ax.set_xticks(pos)
    ax.set_xticklabels(labels)

    ax.set_ylabel("Imbalance Ratio")
    ax.set_title("Socket Execution Unfairnes")

    ax.set_yticks(np.arange(0, 0.30, 0.025))

    legend_handles = [
        Patch(facecolor="#d9d9d9", edgecolor="black", label="Silver CPU"),
        Patch(facecolor="#d4af37", edgecolor="black", label="Gold CPU")
    ]

    ax.legend(handles=legend_handles, loc="upper right")

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR,"imbalance_ratio.png"), dpi=300)
    plt.close()

# -------------------------------
# RUN PLOTS
# -------------------------------
if RUN_ORIGINAL_PLOTS:

    make_plots("totals", "Normalized Executions", "Socket Total Executions", "socket_total")
    make_plots("individual", "Normalized Executions per CPU", "Typical Executions per CPU by Socket", "socket_individual")

if RUN_GINI_PLOTS:
    make_gini_plot()

if RUN_DOMINANCE_PLOTS:
    make_dominance_plot()

if RUN_SCATTER_PLOTS:
    make_scatter_plots()

if RUN_CV_PLOTS:
    make_cv_plot()

if RUN_IMBALANCE_PLOTS:
    make_imbalance_plot()

print("Plots generated successfully.")