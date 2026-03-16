import csv
from matplotlib.colors import LinearSegmentedColormap
import numpy as np
import matplotlib.pyplot as plt
from collections import defaultdict
from pathlib import Path

# BASE_DIR = "./r53600/"
# BASE_DIR = "./silver_4114/"
# BASE_DIR = "./gold_6142/"
# BASE_DIR = "./E52450/"
# BASE_DIR = "./2660v2/"
# BASE_DIR = "./E52630/"
# BASE_DIR = "./E52683v3/"
# BASE_DIR = "./E52660v3/"
BASE_DIR = "./E52680v3/"


CSV_FILE = BASE_DIR + "ccbench_results.csv"

# CHIP_NAME = "Ryzen 5 3600: "
# CHIP_NAME = "Xeon Silver 4114: "
# CHIP_NAME = "Xeon Gold 6142: "
# CHIP_NAME = "Xeon E5-2450: "
# CHIP_NAME = "Xeon E5-2660 v2: "
# CHIP_NAME = "Xeon E5-2630: "
# CHIP_NAME = "Xeon E5-2683 v3: "
# CHIP_NAME = "Xeon E5-2660 v3: "
CHIP_NAME = "Xeon E5-2680 v3: "

# TESTS = [0,7,13,14,15,34]
TESTS = [0,7,12,13,14,15]

TEST_NAMES = {
    0: "STORE",
    7: "LOAD",
    12: "CAS",
    13: "FAI",
    14: "TAS",
    15: "SWAP",
    34: "Repeat CAS"
}

OUTDIR = Path(BASE_DIR + "violin_plots")
CORE_DIR = OUTDIR / "per_core"

OUTDIR.mkdir(exist_ok=True)
CORE_DIR.mkdir(exist_ok=True)

# -------------------------------
# Your plotting style
# -------------------------------

plt.rcParams.update({
    "font.size": 16,
    "axes.titlesize": 20,
    "axes.labelsize": 18,
    "xtick.labelsize": 16,
    "ytick.labelsize": 16
})

# -------------------------------
# Data containers
# -------------------------------

global_data = defaultdict(list)
per_core_data = defaultdict(lambda: defaultdict(list))

# baseline data (no contention)
baseline_global = defaultdict(list)
baseline_per_core = defaultdict(lambda: defaultdict(list))

# -------------------------------
# Read CSV
# -------------------------------

with open(CSV_FILE) as f:

    reader = csv.DictReader(f)

    for r in reader:

        cpu1 = int(r["cpu1"])
        cpu2 = int(r["cpu2"])

        t1 = int(r["test1"])
        t2 = int(r["test2"])

        lat1 = float(r["core0_avg_cycles"])
        lat2 = float(r["core1_avg_cycles"])

        # baseline (single CPU runs)
        if cpu2 == -1 and t2 == -1:

            baseline_global[t1].append(lat1)
            baseline_per_core[(cpu1)][t1].append(lat1)
            continue

        # store latency for each instruction perspective
        global_data[(t1,t2)].append(lat1)
        global_data[(t2,t1)].append(lat2)

        core_key = (cpu1,cpu2)

        per_core_data[core_key][(t1,t2)].append(lat1)
        per_core_data[core_key][(t2,t1)].append(lat2)

# -------------------------------
# Compute Y limits per instruction
# -------------------------------

instruction_max = defaultdict(float)

# check global data
for (inst, other), vals in global_data.items():

    if vals:
        instruction_max[inst] = max(instruction_max[inst], max(vals))

# check baseline
for inst, vals in baseline_global.items():

    if vals:
        instruction_max[inst] = max(instruction_max[inst], max(vals))

# check per-core data
for core_data in per_core_data.values():

    for (inst, other), vals in core_data.items():

        if vals:
            instruction_max[inst] = max(instruction_max[inst], max(vals))

# small padding
for inst in instruction_max:
    instruction_max[inst] *= 1.05

# -------------------------------
# Dataset builder
# -------------------------------

def build_dataset(source, primary):

    datasets = []
    labels = []

    # None baseline first
    datasets.append(baseline_global[primary])
    labels.append("None")

    # self first
    datasets.append(source[(primary,primary)])
    labels.append(TEST_NAMES[primary])

    for other in TESTS:

        if other == primary:
            continue

        datasets.append(source[(primary,other)])
        labels.append(TEST_NAMES[other])

    return datasets, labels

# -------------------------------
# Plot function
# -------------------------------

def make_violin(datasets, labels, title, outfile, ymax):

    positions = np.arange(1, len(datasets)+1)

    fig, ax = plt.subplots(figsize=(14,7))

    vp = ax.violinplot(
        datasets,
        positions=positions,
        showmedians=True
    )

    for body in vp["bodies"]:
        body.set_facecolor("#d9d9d9")
        body.set_edgecolor("black")
        body.set_alpha(0.7)

    for i,d in enumerate(datasets):

        if not d:
            continue

        mean = np.mean(d)

        ax.hlines(
            mean,
            positions[i]-0.3,
            positions[i]+0.3,
            colors="red",
            linestyles="dashed",
            linewidth=2
        )

    ax.set_xticks(positions)
    ax.set_xticklabels(labels)

    ax.set_ylabel("Latency (cycles)")
    ax.set_xlabel("Interacting Instruction")

    ax.set_title(title)

    ax.set_ylim(0, ymax)

    plt.tight_layout()
    plt.savefig(outfile, dpi=300)
    plt.close()

# -------------------------------
# Global plots
# -------------------------------

for inst in TESTS:

    datasets, labels = build_dataset(global_data, inst)

    title = f"Latency of {TEST_NAMES[inst]} vs other instructions"

    outfile = OUTDIR / f"{TEST_NAMES[inst]}.png"

    make_violin(
        datasets,
        labels,
        title,
        outfile,
        instruction_max[inst]
    )

# -------------------------------
# Per-core plots
# -------------------------------

for core, core_data in per_core_data.items():

    core_folder = CORE_DIR / f"{core[0]}_{core[1]}"
    core_folder.mkdir(exist_ok=True)

    for inst in TESTS:

        datasets, labels = build_dataset(core_data, inst)

        title = f"{TEST_NAMES[inst]} latency (cores {core[0]}, {core[1]})"

        outfile = core_folder / f"{TEST_NAMES[inst]}.png"

        make_violin(
            datasets,
            labels,
            title,
            outfile,
            instruction_max[inst]
        )


# -------------------------------
# Heatmap generation
# -------------------------------

import matplotlib.pyplot as plt

heatmap = np.zeros((len(TESTS)+1, len(TESTS)))

# baseline row
for xi, focus in enumerate(TESTS):

    vals = baseline_global[focus]

    if vals:
        heatmap[0, xi] = np.median(vals)
    else:
        heatmap[0, xi] = np.nan

# normal rows
for yi, introduced in enumerate(TESTS, start=1):
    for xi, focus in enumerate(TESTS):

        vals = global_data[(focus, introduced)]

        if vals:
            heatmap[yi, xi] = np.median(vals)
        else:
            heatmap[yi, xi] = np.nan

fig, ax = plt.subplots(figsize=(10,8))

grey_white = LinearSegmentedColormap.from_list(
    "grey_white",
    ["#005499", "#ffffff"]
)

im = ax.imshow(heatmap, cmap=grey_white)

# axis labels
ax.set_xticks(range(len(TESTS)))
ax.set_yticks(range(len(TESTS)+1))

ax.set_xticklabels([TEST_NAMES[t] for t in TESTS])
ax.set_yticklabels(["None"] + [TEST_NAMES[t] for t in TESTS])

ax.invert_yaxis()

ax.set_xlabel("Focus Instruction (latency measured)")
ax.set_ylabel("Introduced Instruction")

ax.set_title(CHIP_NAME + "Latency for Operations Under Contention")

# annotate cells
for y in range(len(TESTS)+1):
    for x in range(len(TESTS)):
        val = heatmap[y,x]
        if not np.isnan(val):
            ax.text(x, y, f"{val:.1f}",
                    ha="center",
                    va="center",
                    color="black")

fig.colorbar(im, ax=ax, label="Latency (cycles)")

plt.tight_layout()
plt.savefig(OUTDIR / "instruction_interference_heatmap.png", dpi=300)
plt.close()

print("All violin plots generated")