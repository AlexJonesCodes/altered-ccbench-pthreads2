import csv
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict
from itertools import combinations
from scipy.stats import ttest_ind, skew, kurtosis
import math
from matplotlib.patches import Patch
from matplotlib.lines import Line2D



'''

CSV_FILES = [
    "Xeon_Gold_6142/6400_runs_1.6mill_reps_repeat/6400_runs_1.6mill_reps_repeat.csv",
    "Xeon_Gold_6142/cas_6400_runs_1.6mill_reps_repeat/cas_6400_runs_1.6mill_reps.csv",
    "Xeon_Gold_6142/fai_6400_runs_1.6mill_reps_repeat/fai_6400_runs_1.6mill_reps.csv",
    "Xeon_Gold_6142/load_on_modified_6400_runs_1.6mill_reps_repeat/6400_runs_1.6mill_reps_load_on_modified.csv",
    "Xeon_Gold_6142/swap_6400_runs_1.6mill_reps_repeat/swap_6400_runs_1.6mill_reps.csv",
    "Xeon_Gold_6142/tas_6400_runs_1.6mill_reps/1/6400_runs_1.6mill_reps_tas.csv"
]

IS_GOLD = True


'''


CSV_FILES = [
    "Xeon_Silver_4114/cas_4kruns_1_000_000_reps/4000_runs_1mill_reps_cas.csv",
    "Xeon_Silver_4114/4kruns_1_000_000_reps2/4000_runs_1mill_reps2.csv",
    "Xeon_Silver_4114/fai_4kruns_1_000_000_reps/4000_runs_1mill_reps_fai_rep.csv",
    "Xeon_Silver_4114/load_on_modified_4kruns_1_000_000_reps/load_on_modified_4000_runs_1mill_reps.csv",
    "Xeon_Silver_4114/swap_4kruns_1_000_000_reps/4000_runs_1mill_reps_swap.csv",
    "Xeon_Silver_4114/tas_4kruns_1_000_000_reps/1/4000_runs_1mill_reps_tas.csv"
]

IS_GOLD = False


plt.rcParams.update({
    'font.size': 20,
    'axes.titlesize': 26,
    'axes.labelsize': 20,
    'xtick.labelsize': 13,
    'ytick.labelsize': 16,
    'legend.fontsize': 16,
    'figure.titlesize': 26
})

for CSV_FILE in CSV_FILES:
    print(f"\nProcessing CSV: {CSV_FILE}\n")
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

    NUM_SOCKETS = 2

    if IS_GOLD:
        CHIP_NAME = "Xeon Gold 6142"
        NUM_CORES = 32
        NUM_CPUS = 64
        COLOR_DARK = "#d4af37"
        COLOR_LIGHT = "#f0c85a"
    else:
        CHIP_NAME = "Xeon Silver 4114"
        NUM_CORES = 20
        NUM_CPUS = 40
        COLOR_DARK = "#d9d9d9"
        COLOR_LIGHT = "#eeeeee"
        
    socket_boundary = NUM_CORES//2 + 0.5

    # -------------------------------
    # Helpers
    # -------------------------------
    def set_yaxis(ax, values):
        # Flatten if nested lists are passed
        if isinstance(values[0], (list, np.ndarray)):
            values = [v for sub in values for v in sub]

        max_val = max(values)
        y_max = math.ceil(max_val / 5000) * 5000

        ax.set_ylim(0, y_max)
        ax.set_yticks(np.arange(0, y_max + 1, 5000))

    def cpu_to_socket(cpu):

        if IS_GOLD:
            # Gold 6142
            # even CPUs → socket 0
            # odd CPUs  → socket 1
            return cpu % 2

        else:
            # Silver 4114
            if cpu < 10 or (20 <= cpu < 30):
                return 0
            else:
                return 1

    def smt_cpus(core_id):

        if IS_GOLD:
            # Gold: SMT pair separated by 32
            return [core_id, core_id + 32]

        else:
            # Silver: SMT pair separated by 20
            return [core_id, core_id + 20]

    def compute_centers(group_sizes, positions):
        centers = []
        idx = 0
        for size in group_sizes:
            group = positions[idx:idx+size]
            centers.append((group[0] + group[-1]) / 2)
            idx += size
        return centers

    legend_handles = [
        Patch(facecolor=COLOR_DARK, edgecolor="black", label="Mean"),
        Line2D([0], [0], color="blue", marker="o", linewidth=2, label="Median")
    ]


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
        fig, ax = plt.subplots(figsize=(20, 7))
        means = [np.mean(d) for d in data_for_plot]
        medians = [np.median(d) for d in data_for_plot]

        bars = ax.bar(positions, means, width=0.6, edgecolor="black")
        for i, bar in enumerate(bars):
            # First half = Socket 0, second half = Socket 1
            if i < len(bars)//2:
                bar.set_facecolor(COLOR_LIGHT)  # lighter gold for Socket 0
            else:
                bar.set_facecolor(COLOR_DARK)  # darker gold for Socket 1
        
        ax.plot(positions, medians, color="blue", marker="o", linewidth=2)
        ax.set_ylabel("Instructions Executed")
        ax.set_title(f"{CHIP_NAME}: {ordering_name}: {test_type}")
        set_yaxis(ax, means + medians)

        # CPU numbers on the main ticks
        ax.set_xticks(positions)
        ax.set_xticklabels(cpu_labels)

        # Draw core labels centered between SMT pairs
        for i, center in enumerate(centers):
            ax.text(center, -0.06, core_labels[i],  # move further down
                    transform=ax.get_xaxis_transform(),
                    ha="center", va="top", fontsize=16)
    

        ax.set_xlabel("Cores and CPUs", labelpad=20)

        ax.legend(handles=legend_handles, loc="lower right")
        plt.tight_layout()
        ax.axvline(socket_boundary * 2 - 0.5, color="black", linestyle="--", linewidth=1)

        # Create a top x-axis for socket labels
        ax_top = ax.twiny()  # twin x-axis
        ax_top.set_xlim(ax.get_xlim())
        ax_top.set_xticks([(min(positions) + (socket_boundary * 2 -0.5))/2, ((socket_boundary * 2 -0.5) + max(positions))/2])
        ax_top.set_xticklabels(["Socket 0", "Socket 1"], fontsize=14)
        ax_top.tick_params(length=0)   # hide tick marks
        ax_top.set_xlabel("")          # optional: no label

        # Create a top x-axis for socket labels
        ax_top = ax.twiny()  # twin x-axis
        ax_top.set_xlim(ax.get_xlim())
        ax_top.set_xticks([(min(positions) + (socket_boundary * 2 -0.5))/2, ((socket_boundary * 2 -0.5) + max(positions))/2])
        ax_top.set_xticklabels(["Socket 0", "Socket 1"], fontsize=14)
        ax_top.tick_params(length=0)   # hide tick marks
        ax_top.set_xlabel("")          # optional: no label


        ax.set_xlim(min(positions)-0.8, max(positions)+0.8)
        plt.savefig(CSV_FILE + f"_{ordering_name}_barline.png", dpi=300)
        plt.close()

        # ---------- VIOLIN ----------
        fig, ax = plt.subplots(figsize=(20, 7))
        vp = ax.violinplot(data_for_plot, positions=positions, showmedians=True)
        for i, body in enumerate(vp["bodies"]):
            if i < len(vp["bodies"])//2:
                body.set_facecolor(COLOR_LIGHT)
            else:
                body.set_facecolor(COLOR_DARK)
            body.set_edgecolor("black")
            body.set_alpha(0.6)

        for i, d in enumerate(data_for_plot):
            ax.hlines(np.mean(d), positions[i]-0.3, positions[i]+0.3,
                    colors="red", linestyles="dashed", linewidth=2)

        ax.set_ylabel("Instructions Executed")
        ax.set_xlabel("Cores and CPUs", labelpad=20)
        ax.set_title(f"{CHIP_NAME}: {ordering_name}: {test_type}")

        # Top axis = CPU numbers
        # CPU numbers on the main ticks
        ax.set_xticks(positions)
        ax.set_xticklabels(cpu_labels)

        # Draw core labels centered between SMT pairs
        for i, center in enumerate(centers):
            ax.text(center, -0.06, core_labels[i],  # move further down
                    transform=ax.get_xaxis_transform(),
                    ha="center", va="top", fontsize=16)
            ax.set_xlabel("Cores and CPUs")
        set_yaxis(ax, data_for_plot)

        plt.tight_layout()
        ax.axvline(socket_boundary * 2 - 0.5, color="black", linestyle="--", linewidth=1)
        # Create a top x-axis for socket labels
        ax_top = ax.twiny()  # twin x-axis
        ax_top.set_xlim(ax.get_xlim())
        ax_top.set_xticks([(min(positions) + (socket_boundary * 2 -0.5))/2, ((socket_boundary * 2 -0.5) + max(positions))/2])
        ax_top.set_xticklabels(["Socket 0", "Socket 1"], fontsize=14)
        ax_top.tick_params(length=0)   # hide tick marks
        ax_top.set_xlabel("")          # optional: no label

        ax.set_xlim(min(positions)-0.8, max(positions)+0.8)
        plt.savefig(CSV_FILE + f"_{ordering_name}_violin.png", dpi=300)
        plt.close()

    # -------------------------------
    # Ordering functions
    # -------------------------------

    def socket_grouped_core_order():
        if IS_GOLD:
            # Gold 6142: even cores → socket 0, odd → socket 1
            return list(range(0, NUM_CORES, 2)) + list(range(1, NUM_CORES, 2))
        else:
            # Silver 4114: first half cores → socket 0, second half → socket 1
            half = NUM_CORES // NUM_SOCKETS
            return list(range(0, half)) + list(range(half, NUM_CORES))

    def grouped_by_socket():
        data_for_plot = []
        cpu_labels = []
        core_labels = []
        positions = []
        group_sizes = []
        pos = 1

        for core_id in socket_grouped_core_order():
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
        for core_id in socket_grouped_core_order():
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
        soc0_cpus = [cpu for cpu in range(NUM_CPUS) if cpu_to_socket(cpu)==0]
        soc0_data = [v for cpu in soc0_cpus for v in core_winners[cpu]]
        data_for_plot.append(soc0_data)
        cpu_labels.append("Socket 0")

        # Socket 1 = odd CPUs
        soc1_cpus = [cpu for cpu in range(NUM_CPUS) if cpu_to_socket(cpu)==1]
        soc1_data = [v for cpu in soc1_cpus for v in core_winners[cpu]]
        data_for_plot.append(soc1_data)
        cpu_labels.append("Socket 1")

        core_labels = ["Socket 0", "Socket 1"]

        # Bar + Line
        fig, ax = plt.subplots(figsize=(8,7))
        means = [np.mean(d) for d in data_for_plot]
        medians = [np.median(d) for d in data_for_plot]
        ax.bar(positions, means, width=0.6, edgecolor="black", color=COLOR_DARK)
        ax.plot(positions, medians, color="blue", marker="o", linewidth=2)
        ax.axvline(socket_boundary, color="black", linestyle="--", linewidth=1)
        # Top x-axis for sockets
        ax_top = ax.twiny()
        ax_top.set_xlim(ax.get_xlim())
        ax_top.set_xticks([positions[0], positions[1]])  # Socket 0 on left, Socket 1 on right
        ax_top.set_xticklabels(["Socket 0", "Socket 1"], fontsize=14)
        ax_top.tick_params(length=0)
        ax_top.set_xlabel("")  # optional
        ax.set_xticks(positions)
        ax.legend(handles=legend_handles, loc="lower right")
        ax.set_xticklabels(core_labels)
        ax.set_xlabel("Socket")
        ax.set_ylabel("Instructions Executed")
        ax.set_title(f"{CHIP_NAME}: Socket Comparison: {test_type}")
        plt.tight_layout()
        set_yaxis(ax, means + medians)
        ax.set_xlim(min(positions)-0.8, max(positions)+0.8)
        plt.savefig(CSV_FILE + "_socket_barline.png", dpi=300)
        plt.close()

        # Violin
        fig, ax = plt.subplots(figsize=(8,7))
        vp = ax.violinplot(data_for_plot, positions=positions, showmedians=True)
        for i, body in enumerate(vp["bodies"]):
            body.set_facecolor(COLOR_DARK)
            body.set_edgecolor("black")
            body.set_alpha(0.6)
        for i, d in enumerate(data_for_plot):
            ax.hlines(np.mean(d), positions[i]-0.3, positions[i]+0.3, colors="red", linestyles="dashed", linewidth=2)
        ax.set_xticks(positions)
        ax.set_xticklabels(core_labels)
        ax.set_xlabel("Socket")
        ax.set_ylabel("Instructions Executed")
        ax.set_title(f"{CHIP_NAME}: Socket Comparison: {test_type}")
        set_yaxis(ax, data_for_plot)
        plt.tight_layout()
        ax.set_xlim(min(positions)-0.8, max(positions)+0.8)
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

        for core_id in socket_grouped_core_order():
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
        ax.bar(positions, means, width=0.6, edgecolor="black", color=COLOR_DARK)
        ax.plot(positions, medians, color="blue", marker="o", linewidth=2)
        ax.axvline(socket_boundary, color="black", linestyle="--", linewidth=1)
        ax.set_xticks(centers)
        ax.legend(handles=legend_handles, loc="lower right")
        ax.set_xticklabels(core_labels)
        ax.set_xlabel("Core ID")
        ax.set_ylabel("Instructions Executed")
        ax.set_title(f"{CHIP_NAME}: Per Core Total Wins: {test_type}")
        set_yaxis(ax, means + medians)
        plt.tight_layout()

        # Top x-axis for sockets
        ax_top = ax.twiny()
        ax_top.set_xlim(ax.get_xlim())
        ax_top.set_xticks([(min(positions) + socket_boundary)/2, (socket_boundary + max(positions))/2])
        ax_top.set_xticklabels(["Socket 0", "Socket 1"], fontsize=14)
        ax_top.tick_params(length=0)
        ax_top.set_xlabel("")
        
        ax.set_xlim(min(positions)-0.8, max(positions)+0.8)
        plt.savefig(CSV_FILE + "_per_core_total_barline.png", dpi=300)
        plt.close()

        # Violin
        fig, ax = plt.subplots(figsize=(14,7))
        vp = ax.violinplot(data_for_plot, positions=positions, showmedians=True)
        for body in vp["bodies"]:
            body.set_facecolor(COLOR_DARK)
            body.set_edgecolor("black")
            body.set_alpha(0.6)
        for i, d in enumerate(data_for_plot):
            ax.hlines(np.mean(d), positions[i]-0.3, positions[i]+0.3, colors="red", linestyles="dashed", linewidth=2)
        ax.set_xticks(centers)
        ax.set_xticklabels(core_labels)
        ax.set_xlabel("Cores and CPUs")
        ax.set_ylabel("Instructions Executed")
        ax.set_title(f"{CHIP_NAME}: Per-Core Total Wins: {test_type}")
        set_yaxis(ax, data_for_plot)
        ax.axvline(socket_boundary, color="black", linestyle="--", linewidth=1)
        plt.tight_layout()

        # Top x-axis for sockets
        ax_top = ax.twiny()
        ax_top.set_xlim(ax.get_xlim())
        ax_top.set_xticks([(min(positions) + socket_boundary)/2, (socket_boundary + max(positions))/2])
        ax_top.set_xticklabels(["Socket 0", "Socket 1"], fontsize=14)
        ax_top.tick_params(length=0)
        ax_top.set_xlabel("")

        ax.set_xlim(min(positions)-0.8, max(positions)+0.8)
        plt.savefig(CSV_FILE + "_per_core_total_violin.png", dpi=300)
        plt.close()

    def per_core_execution_plots():
        # Get the socket-grouped order: 0,2,4,...,30,1,3,5,...,31
        core_order = socket_grouped_core_order()

        data_for_plot = []
        positions = list(range(1, NUM_CORES + 1))  # x-axis positions
        core_labels = [str(c) for c in core_order]

        # Collect combined data for each core (both SMT threads)
        for core_id in core_order:
            smt_pair = smt_cpus(core_id)
            combined = core_winners[smt_pair[0]] + core_winners[smt_pair[1]]
            data_for_plot.append(combined)

        # Vertical line to separate the two sockets (16 cores each)

        # ---------- BAR + LINE ----------
        fig, ax = plt.subplots(figsize=(14,7))
        means = [np.mean(d) for d in data_for_plot]
        medians = [np.median(d) for d in data_for_plot]

        bars = ax.bar(positions, means, width=0.6, edgecolor="black", color=COLOR_DARK)
        ax.plot(positions, medians, color="blue", marker="o", linewidth=2)

        ax.legend(handles=legend_handles, loc="lower right")
        ax.axvline(socket_boundary, color="black", linestyle="--", linewidth=1)

        # Top x-axis for sockets
        ax_top = ax.twiny()
        ax_top.set_xlim(ax.get_xlim())
        ax_top.set_xticks([(min(positions) + socket_boundary)/2, (socket_boundary + max(positions))/2])
        ax_top.set_xticklabels(["Socket 0", "Socket 1"], fontsize=14)
        ax_top.tick_params(length=0)
        ax_top.set_xlabel("")

        ax.set_xticks(positions)
        ax.set_xticklabels(core_labels)

        ax.set_xlabel("Core")
        ax.set_ylabel("Instructions Executed")
        ax.set_title(f"{CHIP_NAME}: Per Core Executions: {test_type}")
        set_yaxis(ax, means + medians)
        plt.tight_layout()
        ax.set_xlim(min(positions)-0.8, max(positions)+0.8)
        plt.savefig(CSV_FILE + "_per_core_exec_barline.png", dpi=300)
        plt.close()

        # ---------- VIOLIN ----------
        fig, ax = plt.subplots(figsize=(14,7))
        vp = ax.violinplot(data_for_plot, positions=positions, showmedians=True)

        for body in vp["bodies"]:
            body.set_facecolor(COLOR_DARK)
            body.set_edgecolor("black")
            body.set_alpha(0.6)

        for i, d in enumerate(data_for_plot):
            ax.hlines(np.mean(d), positions[i]-0.3, positions[i]+0.3,
                    colors="red", linestyles="dashed", linewidth=2)

        # Top x-axis for sockets
        ax_top = ax.twiny()
        ax_top.set_xlim(ax.get_xlim())
        ax_top.set_xticks([(min(positions) + socket_boundary)/2, (socket_boundary + max(positions))/2])
        ax_top.set_xticklabels(["Socket 0", "Socket 1"], fontsize=14)
        ax_top.tick_params(length=0)
        ax_top.set_xlabel("")
        ax.axvline(socket_boundary, color="black", linestyle="--", linewidth=1)

        ax.set_xticks(positions)
        ax.set_xticklabels(core_labels)

        ax.set_xlabel("Core")
        ax.set_ylabel("Instructions Executed")
        ax.set_title(f"{CHIP_NAME}: Per-Core Executions: {test_type}")
        set_yaxis(ax, data_for_plot)
        plt.tight_layout()
        ax.set_xlim(min(positions)-0.8, max(positions)+0.8)
        plt.savefig(CSV_FILE + "_per_core_exec_violin.png", dpi=300)
        plt.close()

    def per_core_imbalance_plot():
        core_order = socket_grouped_core_order()

        imbalance_values = []
        positions = list(range(1, NUM_CORES + 1))
        core_labels = [str(c) for c in core_order]

        for core_id in core_order:
            cpu0, cpu1 = smt_cpus(core_id)

            s0 = sum(core_winners[cpu0])
            s1 = sum(core_winners[cpu1])

            if s0 + s1 == 0:
                imbalance = 0
            else:
                imbalance = abs(s0 - s1) / (s0 + s1)

            imbalance_values.append(imbalance)

        fig, ax = plt.subplots(figsize=(14,7))

        bars = ax.bar(positions, imbalance_values, width=0.6,
                    edgecolor="black", color=COLOR_DARK)

        ax.axvline(socket_boundary, color="black", linestyle="--", linewidth=1)

        # Top x-axis for sockets
        ax_top = ax.twiny()
        ax_top.set_xlim(ax.get_xlim())
        ax_top.set_xticks([(min(positions) + socket_boundary)/2, (socket_boundary + max(positions))/2])
        ax_top.set_xticklabels(["Socket 0", "Socket 1"], fontsize=14)
        ax_top.tick_params(length=0)
        ax_top.set_xlabel("")

        ax.set_xticks(positions)
        ax.set_xticklabels(core_labels)

        ax.set_xlabel("Core")
        ax.set_ylabel("Imbalance Ratio")
        ax.set_title(f"{CHIP_NAME}: SMT Execution Imbalance per Core: {test_type}")

        ax.set_ylim(0)

        plt.tight_layout()
        ax.set_xlim(min(positions)-0.8, max(positions)+0.8)

        plt.savefig(CSV_FILE + "_per_core_imbalance.png", dpi=300)
        plt.close()

    # -------------------------------
    # Generate all plots
    # -------------------------------
    make_plots(str(CHIP_NAME + ": Executions for Each CPU Using Operation"), grouped_by_socket)
    socket_level_plots()
    per_core_total_plots()
    per_core_execution_plots()
    per_core_imbalance_plot()

print("\nAll 8 plots generated successfully.\n")