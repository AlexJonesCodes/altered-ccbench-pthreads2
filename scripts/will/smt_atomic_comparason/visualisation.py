import csv
from matplotlib.colors import LinearSegmentedColormap
import numpy as np
import matplotlib.pyplot as plt
from collections import defaultdict
from pathlib import Path

# matplotlib style toggles
plt.rcParams.update({
    "font.size": 16,
    "axes.titlesize": 20,
    "axes.labelsize": 18,
    "xtick.labelsize": 14,
    "ytick.labelsize": 14
})


OUTPUT_DIR = Path("./mixed_chip_plots/delete")
# toggles for different graphs
COLORING = "delta"  # options are: "raw", "normalized", "delta"

# directories containing benchmark results
CHIPS = {
    # "Ryzen 5 3600": "./r53600/", local ryzen for testing workings code
    "Gold 6142": "./gold_6142/",
    "Silver 4114": "./silver_4114/",

    "E5-2660 v3": "./E52660v3/",
    "E5-2630 v3": "./E52630v3/",
    "E5-2680 v3": "./E52680v3/",

    "E5-2660 v2": "./2660v2/",

    "E5-2450": "./E52450/",

    "E5530": "./E5530/",
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

# output dirs
CROSS_CHIP_HEATMAP_DIR = OUTPUT_DIR / "cross_chip_heatmaps"

for d in [
    OUTPUT_DIR,
    CROSS_CHIP_HEATMAP_DIR
]:
    d.mkdir(parents=True, exist_ok=True)

chip_global_data = {}
chip_per_core_data = {}
chip_baseline = {}

# loop through and load each csv
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

    chip_global_data[chip_name] = global_data
    chip_baseline[chip_name] = baseline_global

def make_violin(datasets, labels, title, outfile):
    positions = np.arange(1, len(datasets)+1)
    fig, ax = plt.subplots(figsize=(14,7)) # TODO: adjust size to fit page

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


# translates chip to arch
MICROARCHS = {
    "Gold 6142": "skylake",
    "Silver 4114": "skylake",
    "E5-2683 v3": "haswell-ep",
    "E5-2660 v3": "haswell-ep",
    "E5-2630 v3": "haswell-ep",
    "E5-2680 v3": "haswell-ep",
    "E5-2660 v2": "ivy-bridge-ep",
    "E5-2450": "sandy-bridge-en",
    "E5530": "nehalem-ep"
}


# cross chips heatmap gen
chip_names = list(CHIPS.keys())

for focus in TESTS:
    heatmap = np.zeros((len(TESTS)+1, len(chip_names)))
    colour = np.zeros_like(heatmap)  # values used for coloring
    for xi, chip in enumerate(chip_names):
        global_data = chip_global_data[chip]
        baseline = chip_baseline[chip]
        baseline_val = np.median(baseline[focus]) if baseline[focus] else np.nan

        # baseline row
        val = np.median(baseline[focus]) if baseline[focus] else np.nan
        heatmap[0,xi] = val

        # coloring for baseline row
        if COLORING == "raw":
            colour[0,xi] = val
        elif COLORING == "normalized":
            colour[0,xi] = 1.0
        elif COLORING == "delta":
            colour[0,xi] = 0.0

        # other rows
        for yi, introduced in enumerate(TESTS, start=1):
            val = np.median(global_data[(focus,introduced)]) if global_data[(focus,introduced)] else np.nan
            heatmap[yi,xi] = val
            if np.isnan(val) or baseline_val is None:
                color_val = np.nan
            else:
                if COLORING == "raw":
                    color_val = val
                elif COLORING == "normalized":
                    color_val = val / baseline_val
                elif COLORING == "delta":
                    color_val = val - baseline_val
            colour[yi,xi] = color_val

    fig, ax = plt.subplots(figsize=(9,8))
    cmap = LinearSegmentedColormap.from_list(
        "grey_white",
        ["#005499", "#ffffff"]
    )

    im = ax.imshow(colour, cmap=cmap)
    im = ax.imshow(colour, cmap=cmap)
    ax.set_xticks(range(len(chip_names)))
    ax.set_xticklabels(chip_names, rotation=45, ha="right")
    ax.set_yticks(range(len(TESTS)+1))
    ax.set_yticklabels(["None"] + [TEST_NAMES[t] for t in TESTS])
    ax.invert_yaxis()
    ax.set_xlabel("Chip (values are raw latency in cycles)")
    ax.set_ylabel("Interfering Operation")
    ax.set_title(f"{TEST_NAMES[focus]} Latency Under SMT Contention")

    # annotate with actual latency
    for y in range(len(TESTS)+1):
        for x in range(len(chip_names)):
            val = heatmap[y,x]
            if not np.isnan(val):
                ax.text(x, y, f"{val:.1f}", ha="center", va="center")

    # make vert linest between archs
    last_arch = MICROARCHS[chip_names[0]]
    for xi in range(1, len(chip_names)):
        arch = MICROARCHS[chip_names[xi]]
        if arch != last_arch:
            ax.axvline(x=xi-0.5, color='black', linestyle='dashed', linewidth=2)
        last_arch = arch

    # colorbar labels
    if COLORING == "raw":
        cbar_label = "Latency (cycles)"
    elif COLORING == "normalized":
        cbar_label = "Normalized latency (baseline=1)"
    elif COLORING == "delta":
        cbar_label = "Colour is Delta Latency From None"

    fig.colorbar(im, ax=ax, label=cbar_label, shrink=0.7, pad=0.01)
    plt.tight_layout()
    outfile = CROSS_CHIP_HEATMAP_DIR / f"{TEST_NAMES[focus]}_chip_comparison_{COLORING}.png"
    plt.savefig(outfile, dpi=300)
    plt.close()

print("all plots done.")