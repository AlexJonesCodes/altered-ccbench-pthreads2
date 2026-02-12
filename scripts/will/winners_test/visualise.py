import csv
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict

CSV_FILE = "ccbench_results.csv"

# Data structures
# core -> list of (run, winners)
core_winners = defaultdict(list)
# run -> color
runs = set()
# Assume single test type (as per your setup)
test_type = None

# Read CSV
with open(CSV_FILE, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        run = int(row["run"])
        core = int(row["cpu"])
        winners = int(row["wins"])
        test_type = row["test_type"]

        core_winners[core].append((run, winners))
        runs.add(run)

cores = sorted(core_winners.keys())
runs = sorted(runs)

# Assign colors per run
cmap = plt.get_cmap("tab10")
run_colors = {run: cmap((run - 1) % 10) for run in runs}

# Prepare plot
fig, ax = plt.subplots(figsize=(12, 6))

bar_width = 0.6
max_winner = 0

for core in cores:
    data = core_winners[core]

    # Sort by winners DESC so largest is drawn first (at back)
    data_sorted = sorted(data, key=lambda x: x[1], reverse=True)

    for run, winners in data_sorted:
        ax.bar(
            core,
            winners,
            width=bar_width,
            color=run_colors[run],
            alpha=0.85,
            edgecolor="black",
            linewidth=0.3
        )
        max_winner = max(max_winner, winners)

    # Average winners (dot)
    avg = np.mean([w for _, w in data])
    ax.plot(core, avg, marker="o", color="black", markersize=7, zorder=10)

# Axes formatting
ax.set_xlabel("CPU Core")
ax.set_ylabel("Winners (in hundreds of thousands)")
ax.set_xticks(cores)
ax.set_ylim(0, max_winner * 1.05)
ax.set_title(f"ccbench Winners per Core — Test Type: {test_type}")

# Legend (one entry per run)
legend_handles = [
    plt.Line2D(
        [0],
        [0],
        marker="s",
        linestyle="",
        color=run_colors[run],
        label=f"Run {run}"
    )
    for run in runs
]

legend_handles.append(
    plt.Line2D(
        [0],
        [0],
        marker="o",
        linestyle="",
        color="black",
        label="Average"
    )
)

ax.legend(
    handles=legend_handles,
    title="Repetitions",
    bbox_to_anchor=(1.02, 1),
    loc="upper left"
)

plt.tight_layout()
plt.savefig("ccbench_winners_per_core.png", dpi=300)
