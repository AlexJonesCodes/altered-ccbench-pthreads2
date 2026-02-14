import csv
import os
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict

CSV_FILE = "../../static_addr/4000_runs_1mill_reps_static_addr_moving_seed.csv"

# Save outputs in current directory using CSV filename only
outname = os.path.splitext(os.path.basename(CSV_FILE))[0]


# -------------------------------------------------
# Read data grouped by -b value and CPU
# -------------------------------------------------
data_by_b = defaultdict(lambda: defaultdict(list))
test_type = None

with open(CSV_FILE, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        b_val = int(row["b_value"])
        cpu = int(row["cpu"])
        wins = int(row["wins"])
        test_type = row["test_type"]

        data_by_b[b_val][cpu].append(wins)

# -------------------------------------------------
# Socket color helper
# -------------------------------------------------
def socket_color(cpu):
    return "#d9d9d9" if cpu < 20 else "#bfbfbf"

# -------------------------------------------------
# Build ordering: 0,20,1,21,2,22,...
# -------------------------------------------------
def build_smt_pair_order(core_winners):
    cpus = sorted(core_winners.keys())

    # Determine core indices dynamically
    base_cores = sorted(set(cpu % 20 for cpu in cpus))

    data_for_plot = []
    cpu_labels = []
    core_labels = []
    positions = []

    pos = 1

    for core in base_cores:
        pair_positions = []

        for cpu in [core, core + 20]:
            if cpu in core_winners:
                data_for_plot.append(core_winners[cpu])
                cpu_labels.append(str(cpu))
                positions.append(pos)
                pair_positions.append(pos)
                pos += 1

        if len(pair_positions) == 2:
            core_labels.append(str(core))

    return data_for_plot, cpu_labels, core_labels, positions

# -------------------------------------------------
# Plot generator (box + violin)
# -------------------------------------------------
def generate_plots_for_b(b_val, core_winners):

    data_for_plot, cpu_labels, core_labels, positions = \
        build_smt_pair_order(core_winners)

    if not data_for_plot:
        return

    # =========================
    # BOX PLOT
    # =========================
    fig, ax = plt.subplots(figsize=(14, 7))

    bp = ax.boxplot(
        data_for_plot,
        positions=positions,
        widths=0.6,
        patch_artist=True,
        showmeans=True,
        meanline=True,
        meanprops=dict(color="blue", linewidth=2),
        medianprops=dict(color="black", linewidth=1.5),
        flierprops=dict(marker="o",
                        markerfacecolor="none",
                        markeredgecolor="black",
                        alpha=0.4)
    )

    # Shade by socket
    for i, box in enumerate(bp["boxes"]):
        cpu = int(cpu_labels[i])
        box.set_facecolor(socket_color(cpu))
        box.set_edgecolor("black")

    ax.set_ylabel("Winners")
    ax.set_title(f"Boxplot — b={b_val} — {test_type}")
    ax.set_ylim(bottom=0)

    # Top axis (CPU numbers)
    ax_top = ax.twiny()
    ax_top.set_xlim(ax.get_xlim())
    ax_top.set_xticks(positions)
    ax_top.set_xticklabels(cpu_labels)
    ax_top.set_xlabel("CPU Number")

    # Bottom axis (core number centered between pair)
    centers = [(positions[i] + positions[i + 1]) / 2
               for i in range(0, len(positions), 2)]

    ax.set_xticks(centers)
    ax.set_xticklabels(core_labels)
    ax.set_xlabel("Core Number (SMT pairs)")

    plt.tight_layout()
    plt.savefig(f"{outname}_b{b_val}_box.png", dpi=300)
    plt.close()

    # =========================
    # VIOLIN PLOT
    # =========================
    fig, ax = plt.subplots(figsize=(14, 7))

    vp = ax.violinplot(
        data_for_plot,
        positions=positions,
        showmedians=True
    )

    for i, body in enumerate(vp["bodies"]):
        cpu = int(cpu_labels[i])
        body.set_facecolor(socket_color(cpu))
        body.set_edgecolor("black")
        body.set_alpha(0.6)

    ax.set_ylabel("Winners")
    ax.set_title(f"Violin Plot — b={b_val} — {test_type}")
    ax.set_ylim(bottom=0)

    ax_top = ax.twiny()
    ax_top.set_xlim(ax.get_xlim())
    ax_top.set_xticks(positions)
    ax_top.set_xticklabels(cpu_labels)
    ax_top.set_xlabel("CPU Number")

    ax.set_xticks(centers)
    ax.set_xticklabels(core_labels)
    ax.set_xlabel("Core Number (SMT pairs)")

    plt.tight_layout()
    plt.savefig(f"{outname}_b{b_val}_violin.png", dpi=300)
    plt.close()

# -------------------------------------------------
# Generate plots for each -b value
# -------------------------------------------------
for b_val in sorted(data_by_b.keys()):
    print(f"Generating plots for b={b_val}")
    generate_plots_for_b(b_val, data_by_b[b_val])

print("\nAll plots generated.\n")
