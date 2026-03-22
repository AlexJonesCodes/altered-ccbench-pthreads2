import csv
from matplotlib.colors import LinearSegmentedColormap
import numpy as np
import matplotlib.pyplot as plt
from collections import defaultdict
from pathlib import Path

# -------------------------------
# CONFIGURATION
# -------------------------------

OUTPUT_DIR = Path("./mixed_chip_plots")

ENABLE_GLOBAL_VIOLIN = False
ENABLE_PER_CORE_VIOLIN = False
ENABLE_PER_CHIP_HEATMAP = False
ENABLE_CROSS_CHIP_HEATMAP = True
COLOR_MODE = "delta"  # options: "raw", "normalized", "delta"

# directories containing benchmark results
CHIPS = {
    # "Ryzen 5 3600": "./r53600/",
    "Xeon Gold 6142": "./gold_6142/",
    "Xeon Silver 4114": "./silver_4114/",

    "Xeon E5-2683 v3": "./E52683v3/",
    "Xeon E5-2660 v3": "./E52660v3/",
    "Xeon E5-2630 v3": "./E52630v3/",
    "Xeon E5-2680 v3": "./E52680v3/",

    "Xeon E5-2660 v2": "./2660v2/",

    "Xeon E5-2450": "./E52450/",

    "Xeon E5530": "./E5530/",
}

TESTS = [0,7,12,13,14,15]

TEST_NAMES = {
    0: "STORE",
    7: "LOAD",
    12: "CAS",
    13: "FAI",
    14: "TAS",
    15: "SWAP",
    34: "Repeat CAS",
}

# -------------------------------
# Plot styling
# -------------------------------

plt.rcParams.update({
    "font.size": 16,
    "axes.titlesize": 20,
    "axes.labelsize": 18,
    "xtick.labelsize": 14,
    "ytick.labelsize": 14
})

# -------------------------------
# Output directories
# -------------------------------

GLOBAL_DIR = OUTPUT_DIR / "global_violins"
CORE_DIR = OUTPUT_DIR / "per_core_violins"
PER_CHIP_HEATMAP_DIR = OUTPUT_DIR / "chip_heatmaps"
CROSS_CHIP_HEATMAP_DIR = OUTPUT_DIR / "cross_chip_heatmaps"

for d in [
    OUTPUT_DIR,
    GLOBAL_DIR,
    CORE_DIR,
    PER_CHIP_HEATMAP_DIR,
    CROSS_CHIP_HEATMAP_DIR
]:
    d.mkdir(parents=True, exist_ok=True)

# -------------------------------
# Data containers
# -------------------------------

chip_global_data = {}
chip_per_core_data = {}
chip_baseline = {}

# -------------------------------
# Load all CSVs
# -------------------------------

for chip_name, base_dir in CHIPS.items():

    csv_file = Path(base_dir) / "ccbench_results.csv"

    global_data = defaultdict(list)
    per_core_data = defaultdict(lambda: defaultdict(list))
    baseline_global = defaultdict(list)

    with open(csv_file) as f:

        reader = csv.DictReader(f)

        for r in reader:

            cpu1 = int(r["cpu1"])
            cpu2 = int(r["cpu2"])

            t1 = int(r["test1"])
            t2 = int(r["test2"])

            lat1 = float(r["core0_avg_cycles"])
            lat2 = float(r["core1_avg_cycles"])

            if cpu2 == -1 and t2 == -1:

                baseline_global[t1].append(lat1)
                continue

            global_data[(t1,t2)].append(lat1)
            global_data[(t2,t1)].append(lat2)

            core_key = (cpu1,cpu2)

            per_core_data[core_key][(t1,t2)].append(lat1)
            per_core_data[core_key][(t2,t1)].append(lat2)

    chip_global_data[chip_name] = global_data
    chip_per_core_data[chip_name] = per_core_data
    chip_baseline[chip_name] = baseline_global

# -------------------------------
# Dataset builder
# -------------------------------

def build_dataset(source, baseline, primary):

    datasets = []
    labels = []

    datasets.append(baseline[primary])
    labels.append("None")

    datasets.append(source[(primary,primary)])
    labels.append(TEST_NAMES[primary])

    for other in TESTS:

        if other == primary:
            continue

        datasets.append(source[(primary,other)])
        labels.append(TEST_NAMES[other])

    return datasets, labels

# -------------------------------
# Violin plot function
# -------------------------------

def make_violin(datasets, labels, title, outfile):

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

    plt.tight_layout()
    plt.savefig(outfile, dpi=300)
    plt.close()

# -------------------------------
# Global violin plots (per chip)
# -------------------------------

if ENABLE_GLOBAL_VIOLIN:

    for chip in CHIPS:

        global_data = chip_global_data[chip]
        baseline = chip_baseline[chip]

        for inst in TESTS:

            datasets, labels = build_dataset(
                global_data,
                baseline,
                inst
            )

            outfile = GLOBAL_DIR / f"{chip}_{TEST_NAMES[inst]}.png"

            title = f"{chip}: {TEST_NAMES[inst]} latency vs other instructions"

            make_violin(datasets, labels, title, outfile)

# -------------------------------
# Per-core violin plots
# -------------------------------

if ENABLE_PER_CORE_VIOLIN:

    for chip in CHIPS:

        per_core_data = chip_per_core_data[chip]
        baseline = chip_baseline[chip]

        for core, core_data in per_core_data.items():

            core_folder = CORE_DIR / f"{chip}_{core[0]}_{core[1]}"
            core_folder.mkdir(exist_ok=True)

            for inst in TESTS:

                datasets, labels = build_dataset(
                    core_data,
                    baseline,
                    inst
                )

                outfile = core_folder / f"{TEST_NAMES[inst]}.png"

                title = f"{chip} cores {core[0]}-{core[1]}: {TEST_NAMES[inst]} latency"

                make_violin(datasets, labels, title, outfile)

# -------------------------------
# Per-chip heatmaps (original)
# -------------------------------

if ENABLE_PER_CHIP_HEATMAP:

    cmap = LinearSegmentedColormap.from_list(
        "grey_white",
        ["#005499", "#ffffff"]
    )

    for chip in CHIPS:

        global_data = chip_global_data[chip]
        baseline = chip_baseline[chip]

        heatmap = np.zeros((len(TESTS)+1, len(TESTS)))

        for xi, focus in enumerate(TESTS):

            vals = baseline[focus]
            heatmap[0,xi] = np.median(vals) if vals else np.nan

        for yi, introduced in enumerate(TESTS, start=1):
            for xi, focus in enumerate(TESTS):

                vals = global_data[(focus,introduced)]
                heatmap[yi,xi] = np.median(vals) if vals else np.nan

        fig, ax = plt.subplots(figsize=(10,8))

        im = ax.imshow(heatmap, cmap=cmap)

        ax.set_xticks(range(len(TESTS)))
        ax.set_yticks(range(len(TESTS)+1))

        ax.set_xticklabels([TEST_NAMES[t] for t in TESTS])
        ax.set_yticklabels(["None"] + [TEST_NAMES[t] for t in TESTS])

        # INVERT Y-AXIS
        ax.invert_yaxis()

        ax.set_xlabel("Focus Instruction")
        ax.set_ylabel("Interfering Instruction")

        ax.set_title(f"{chip} Instruction Contention")

        fig.colorbar(im, ax=ax, label="Latency (cycles)")

        plt.tight_layout()

        plt.savefig(
            PER_CHIP_HEATMAP_DIR /
            f"{chip}_instruction_interference_heatmap.png",
            dpi=300
        )

        plt.close()

# -------------------------------
# Microarchitecture mapping for vertical lines
# -------------------------------
MICROARCHS = {
    "Xeon Gold 6142": "skylake",
    "Xeon Silver 4114": "skylake",
    "Xeon E5-2683 v3": "haswell-ep",
    "Xeon E5-2660 v3": "haswell-ep",
    "Xeon E5-2630 v3": "haswell-ep",
    "Xeon E5-2680 v3": "haswell-ep",
    "Xeon E5-2660 v2": "ivy-bridge-ep",
    "Xeon E5-2450": "sandy-bridge-en",
    "Xeon E5530": "nehalem-ep"
}

# -------------------------------
# Cross-chip heatmaps with architecture separators
# -------------------------------
chip_names = list(CHIPS.keys())

for focus in TESTS:

    heatmap = np.zeros((len(TESTS)+1, len(chip_names)))
    color_data = np.zeros_like(heatmap)  # values used for coloring

    for xi, chip in enumerate(chip_names):

        global_data = chip_global_data[chip]
        baseline = chip_baseline[chip]

        baseline_val = np.median(baseline[focus]) if baseline[focus] else np.nan

        # baseline row
        val = np.median(baseline[focus]) if baseline[focus] else np.nan
        heatmap[0,xi] = val

        # coloring for baseline row
        if COLOR_MODE == "raw":
            color_data[0,xi] = val
        elif COLOR_MODE == "normalized":
            color_data[0,xi] = 1.0
        elif COLOR_MODE == "delta":
            color_data[0,xi] = 0.0

        # other rows
        for yi, introduced in enumerate(TESTS, start=1):

            val = np.median(global_data[(focus,introduced)]) if global_data[(focus,introduced)] else np.nan
            heatmap[yi,xi] = val

            if np.isnan(val) or baseline_val is None:
                color_val = np.nan
            else:
                if COLOR_MODE == "raw":
                    color_val = val
                elif COLOR_MODE == "normalized":
                    color_val = val / baseline_val
                elif COLOR_MODE == "delta":
                    color_val = val - baseline_val

            color_data[yi,xi] = color_val

    # create figure
    fig, ax = plt.subplots(figsize=(12,8))

    # define colormap for this heatmap
    cmap = LinearSegmentedColormap.from_list(
        "grey_white",
        ["#005499", "#ffffff"]
    )

    im = ax.imshow(color_data, cmap=cmap)
    im = ax.imshow(color_data, cmap=cmap)

    ax.set_xticks(range(len(chip_names)))
    ax.set_xticklabels(chip_names, rotation=45, ha="right")

    ax.set_yticks(range(len(TESTS)+1))
    ax.set_yticklabels(["None"] + [TEST_NAMES[t] for t in TESTS])
    ax.invert_yaxis()

    ax.set_xlabel("Processor")
    ax.set_ylabel("Interfering Instruction")
    ax.set_title(f"{TEST_NAMES[focus]} Latency Under SMT Contention (values are raw latency)")

    # annotate with actual latency
    for y in range(len(TESTS)+1):
        for x in range(len(chip_names)):
            val = heatmap[y,x]
            if not np.isnan(val):
                ax.text(x, y, f"{val:.1f}", ha="center", va="center")

    # -------------------------------
    # Draw vertical lines between microarchitectures
    # -------------------------------
    last_arch = MICROARCHS[chip_names[0]]
    for xi in range(1, len(chip_names)):
        arch = MICROARCHS[chip_names[xi]]
        if arch != last_arch:
            ax.axvline(x=xi-0.5, color='black', linestyle='dashed', linewidth=2)
        last_arch = arch

    # colorbar label
    if COLOR_MODE == "raw":
        cbar_label = "Latency (cycles)"
    elif COLOR_MODE == "normalized":
        cbar_label = "Normalized latency (baseline=1)"
    elif COLOR_MODE == "delta":
        cbar_label = "Delta latency (cycles)"

    fig.colorbar(im, ax=ax, label=cbar_label)

    plt.tight_layout()
    outfile = CROSS_CHIP_HEATMAP_DIR / f"{TEST_NAMES[focus]}_chip_comparison_{COLOR_MODE}.png"
    plt.savefig(outfile, dpi=300)
    plt.close()

print("All plots generated.")