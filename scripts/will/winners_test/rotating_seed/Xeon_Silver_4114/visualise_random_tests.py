import csv
import os
import matplotlib.pyplot as plt
from collections import defaultdict
import numpy as np
from matplotlib.lines import Line2D

# -------------------------------
# INPUT CSV FILE
# -------------------------------
CSV_FILE = "rand_test_with_tas_4kruns_1_000_000_reps/2/4000_runs_1mill_reps_random_test_including_tas.csv"
OUTPUT_DIR = "socket_plots/silver_rand_tests"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# -------------------------------
# CPU TOPOLOGY
# -------------------------------
IS_GOLD_CPU = False   # False = Silver, True = Gold

# -------------------------------
# Label mapping for cleaner axis
# -------------------------------
LABEL_MAP = {
    "STORE_ON_MODIFIED": "STORE",
    "LOAD_FROM_MODIFIED": "LOAD"
}

# -------------------------------
# Instruction order
# -------------------------------
INSTRUCTION_ORDER = [
    "STORE_ON_MODIFIED",
    "LOAD_FROM_MODIFIED",
    "TAS",
    "SWAP",
    "CAS",
    "FAI"
]

# -------------------------------
# CPU -> SOCKET
# -------------------------------
def get_socket(cpu):
    if IS_GOLD_CPU:
        return 1 if cpu % 2 == 1 else 0
    else:
        return 0 if (0 <= cpu <= 9 or 20 <= cpu <= 29) else 1

# -------------------------------
# Load CSV data
# -------------------------------
def load_data(csv_file):
    """
    Returns:
    {(instruction, socket): [normalized wins]}, fair value
    """
    per_run = defaultdict(list)

    with open(csv_file, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            run = int(row["run"])
            cpu = int(row["cpu"])
            instr = row["test_type"]
            wins = int(row["wins"])
            per_run[run].append((cpu, instr, wins))

    data = defaultdict(list)
    total_fair = None

    for run, entries in per_run.items():
        total_exec = sum(w for _, _, w in entries)   # e
        n_cpus = len(entries)                        # n
        fair = total_exec / n_cpus                   # e/n
        total_fair = fair

        for cpu, instr, wins in entries:
            socket = get_socket(cpu)
            normalized = wins / fair
            data[(instr, socket)].append(normalized)

    return data, total_fair

# -------------------------------
# Plot violin
# -------------------------------
def plot_violins(data, fair, filename):
    data_for_plot = []
    labels = []

    # socket-first order
    for socket in [0, 1]:
        for instr in INSTRUCTION_ORDER:
            key = (instr, socket)
            data_for_plot.append(data.get(key, []))
            labels.append(f"{LABEL_MAP.get(instr, instr)}\nS{socket}")

    # -------------------------------
    # Plot styling
    # -------------------------------
    plt.rcParams['font.size'] = 14
    plt.rcParams['axes.titlesize'] = 16
    plt.rcParams['axes.labelsize'] = 14
    plt.rcParams['xtick.labelsize'] = 12
    plt.rcParams['ytick.labelsize'] = 12

    fig, ax = plt.subplots(figsize=(14,6))
    vp = ax.violinplot(data_for_plot, showmedians=True)

    colors = ["#d9d9d9","#a6cee3","#1f78b4","#b2df8a","#33a02c","#fb9a99"]
    for i, body in enumerate(vp["bodies"]):
        color = colors[i % len(colors)]
        body.set_facecolor(color)
        body.set_edgecolor("black")
        body.set_alpha(0.6)

    # plot mean per violin
    for i, d in enumerate(data_for_plot):
        if len(d) == 0:
            continue
        ax.hlines(
            np.mean(d),
            i+1-0.3, i+1+0.3,
            colors="red",
            linestyles="dashed",
            linewidth=2
        )

    # fair share line at 1
    ax.axhline(1.0, linestyle="--", color="gray", linewidth=2)

    # -------------------------------
    # Labels & layout
    # -------------------------------
    ax.set_xticks(range(1, len(labels)+1))
    ax.set_xticklabels(labels)
    ax.set_ylabel(f"Normalized Executions (1 is fair, fair is {fair:.0f})")
    ax.set_ylim(bottom=0)
    y_max = max(2.0, max(max(d) if d else 0 for d in data_for_plot)+0.2)
    ax.set_yticks(np.arange(0, y_max + 0.2, 0.2))

    ax.set_title("Normalized Wins per Instruction Type by Socket")

    # Legend
    legend_handles = [
        Line2D([0],[0], color="deepskyblue", lw=2, label="Median", linestyle="-"),
        Line2D([0],[0],color="red", linestyle="--", lw=2, label="Mean"),
        Line2D([0],[0],color="gray", linestyle="--", lw=2, label="Fair Share")
    ]
    ax.legend(handles=legend_handles, loc="lower right")

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, filename + "_violin.png"), dpi=300)
    plt.close()

# -------------------------------
# Main
# -------------------------------
data, fair = load_data(CSV_FILE)
plot_violins(data, fair, "wins_per_instruction_socket_normalized")

print("Violin plot generated successfully!")