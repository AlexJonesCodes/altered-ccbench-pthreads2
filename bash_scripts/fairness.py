# Use Jain's Fairness Index to evaluate the fairness of each atomic operation
# This is measuring fairness between core coherence latency

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# csv format is:
# test,from_core,to_core,avg_latency
CSV = "./results/ccbench_summary.csv"
df = pd.read_csv(
    CSV,
    dtype={
        "test": str,
        "from_core": int,
        "to_core": int
    }
)

def calculate_fairness(latencies):
    n = len(latencies)
    sum_latencies = sum(latencies)
    sum_squared_latencies = sum([latency ** 2 for latency in latencies])

    if sum_squared_latencies == 0:
        fairness_index = 1.0  # All latencies are zero, considered perfectly fair
    else:
        fairness_index = (sum_latencies ** 2) / (n * sum_squared_latencies)
    return fairness_index

def benchmark_tests():
    test_fairness = pd.DataFrame(columns=["test", "fairness"])
    df["avg_latency"] = pd.to_numeric(df["avg_latency"], errors="coerce")

    # run jain's fairness on tests
    unique_tests = df["test"].unique()

    for test in unique_tests:
        test_df = df[df["test"] == test]
        latencies = test_df["avg_latency"].values
        fairness_index = calculate_fairness(latencies)

        test_fairness.loc[len(test_fairness)] = [test, fairness_index]
        print(f"Test: {test}, Jain's Fairness Index: {fairness_index:.4f}")

    # visualise fairness
    plt.figure(figsize=(12, 6))  # width=16 inches, height=8 inches
    test_fairness = test_fairness.sort_values("test")
    bars = plt.bar(test_fairness["test"], test_fairness["fairness"])
    plt.ylabel("Fairness Index")
    plt.xlabel("Test Name")
    plt.ylim(0,1)
    plt.xticks(rotation=90, ha="center")
    plt.tight_layout()
    plt.yticks(np.arange(0, 1.1, 0.1))  # 0 to 1 inclusive
    plt.xlim(-0.5, len(test_fairness["test"]) - 0.5)
    plt.title("Fairness of Different Atomic Operations, Over Core Permutations")
    # annotate values inside each bar
    for bar in bars:
        height = bar.get_height()
        plt.text(
            bar.get_x() + bar.get_width() / 2,  # x position: center of the bar
            height / 2,                          # y position: halfway up the bar
            f"{height:.2f}",                     # text
            ha="center",
            va="center",
            rotation=90,
            color="white"                        # good contrast inside bars
        )
    plt.savefig("./figs/core_placement.png")


def benchmark_core_placement():
    # janes fairness over core comms
    from_to_fairness = pd.DataFrame(columns=["from_core", "to_core", "fairness"])
    from_cores = df["from_core"].unique()
    to_cores = df["to_core"].unique()

    for from_core in from_cores:
        for to_core in to_cores:
            rows = df[(df["from_core"] == from_core) & (df["to_core"] == to_core)]
            if rows.empty:
                from_to_fairness.loc[len(from_to_fairness)] = [from_core, to_core, float("nan")]
                continue
            latencies = rows["avg_latency"].values
            fairness = calculate_fairness(latencies)

            from_to_fairness.loc[len(from_to_fairness)] = [from_core, to_core, fairness]
            print(f"From core: {from_core}, to {to_core}, Jain's Fairness Index: {fairness:.4f}")
    
    # visualise fairness
    grid = from_to_fairness.pivot(index="to_core", columns="from_core", values="fairness")

    # reorder cores: even first, then odd
    cols = list(grid.columns)
    rows = list(grid.index)

    even_cols = [c for c in cols if c % 2 == 0]
    odd_cols  = [c for c in cols if c % 2 == 1]
    new_col_order = even_cols + odd_cols

    even_rows = [r for r in rows if r % 2 == 0]
    odd_rows  = [r for r in rows if r % 2 == 1]
    new_row_order = even_rows + odd_rows

    grid = grid.reindex(index=new_row_order, columns=new_col_order)


    cell_size = 1  # inches per cell
    fig, ax = plt.subplots(figsize=(cell_size * grid.shape[1],
                                    cell_size * grid.shape[0]))
    cax = ax.imshow(grid, cmap="Greens", interpolation="nearest")
    fig.colorbar(cax, label="Fairness Index")

    # annotate values
    if grid.size <= 40:   # 6x6 or smaller, adjust as you like
        for i, to in enumerate(grid.index):
            for j, frm in enumerate(grid.columns):
                val = grid.loc[to, frm]
                ax.text(j, i, f"{val:.2f}", ha="center", va="center", color="white")

    # set ticks at integer positions
    step = 1
    ax.set_xticks(range(0, len(grid.columns), step))
    ax.set_xticklabels([int(x) for x in grid.columns[::step]])

    ax.set_yticks(range(0, len(grid.index), step))
    ax.set_yticklabels([int(y) for y in grid.index[::step]])
    ax.set_xlabel("From Core")
    ax.set_ylabel("To Core")
    ax.tick_params(axis='x', which='both', bottom=True, top=True, labeltop=True, labelbottom=True)
    ax.tick_params(axis='y', which='both', left=True, right=True, labelleft=True, labelright=True)
    ax.set_title("Fairness Between Cores, Over Different Atomic Operations")

    # overlay values in cells
    for i in range(grid.shape[0]):
        for j in range(grid.shape[1]):
            val = grid.iloc[i, j]
            if np.isnan(val):
                continue
            color = "white" if val > 0.5 else "black"
            ax.text(j, i, f"{val:.2f}", ha="center", va="center", color=color, fontsize=6)

    fig.tight_layout()
    plt.savefig("./figs/from_to_fairness.png")


def visualise_latency():
    for test_name in df["test"].unique():
        if test_name != "CAS_ON_SHARED":
            continue  # skip this test
        test_table = df[df["test"] == test_name]

        grid = test_table.pivot(index="to_core", columns="from_core", values="avg_latency")

        # reorder cores: even first, then odd
        cols = list(grid.columns)
        rows = list(grid.index)

        even_cols = [c for c in cols if c % 2 == 0]
        odd_cols  = [c for c in cols if c % 2 == 1]
        new_col_order = even_cols + odd_cols

        even_rows = [r for r in rows if r % 2 == 0]
        odd_rows  = [r for r in rows if r % 2 == 1]
        new_row_order = even_rows + odd_rows

        # apply the new order
        grid = grid.reindex(index=new_row_order, columns=new_col_order)

        # use big figure so 64×64 stays readable
        fig, ax = plt.subplots(figsize=(18, 18))

        cax = ax.imshow(grid, cmap="Blues", aspect="equal")
        fig.colorbar(cax, label="Avg Latency")

        # annotate but tiny font; skip NaN
        for i in range(grid.shape[0]):
            for j in range(grid.shape[1]):
                val = grid.iloc[i, j]
                if np.isnan(val):
                    continue
                ax.text(
                    j, i, f"{val:.0f}",
                    ha="center",
                    va="center",
                    color="black",
                    fontsize=4      # critical for 64×64
                )

        ax.set_xticks(range(len(grid.columns)))
        ax.set_yticks(range(len(grid.index)))
        ax.set_xticklabels(grid.columns, fontsize=6)
        ax.set_yticklabels(grid.index, fontsize=6)

        ax.tick_params(axis='x', bottom=True, top=True, labeltop=True, labelbottom=True)
        ax.tick_params(axis='y', left=True, right=True, labelright=True)

        ax.set_xlabel("From Core")
        ax.set_ylabel("To Core")
        ax.set_title(f"{test_name} Raw Avg Latency")

        fig.tight_layout()
        plt.savefig(f"./figs/spam/{test_name}_raw_latency.png", dpi=600)
        plt.close(fig)
        print(f"{test_name} is done")

def visualise_socket_latency_comparison_normalised():
    rows = []

    for test in df["test"].unique():
        # cas concurrent isnt pinned to cores, bad test
        if test == "CAS_CONCURRENT": 
            continue  # skip this test

        test_df = df[df["test"] == test]

        same_avg = test_df[
            (test_df["from_core"] % 2) == (test_df["to_core"] % 2)
        ]["avg_latency"].mean()

        cross_avg = test_df[
            (test_df["from_core"] % 2) != (test_df["to_core"] % 2)
        ]["avg_latency"].mean()

        if np.isnan(same_avg) or np.isnan(cross_avg):
            continue

        if same_avg >= cross_avg:
            slow_path = "same"
            slow = same_avg
            fast = cross_avg
        else:
            slow_path = "cross"
            slow = cross_avg
            fast = same_avg

        rows.append({
            "test": test,
            "slow_path": slow_path,
            "slow_val": slow,
            "fast_path": "cross" if slow_path == "same" else "same",
            "fast_val": fast,
            "fast_frac": fast / slow
        })

    summary = pd.DataFrame(rows).sort_values("test")
    x = np.arange(len(summary))
    width = 0.7

    plt.figure(figsize=(14, 6))

    # draw slower path (full height = 1.0)
    for i, row in summary.iterrows():
        color = "orange" if row["slow_path"] == "same" else "blue"
        xpos = x[list(summary.index).index(i)]
        plt.bar(
            xpos,
            1.0,
            width=width,
            color=color
        )
        # place slower bar annotation near top of the bar
        plt.text(
            xpos,
            0.95,  # slightly below top
            f"{row['slow_val']:.1f}",
            ha="center",
            va="top",
            rotation=90,
            fontsize=8,
            color="white" if row["slow_val"] / 1.0 > 0.3 else "black"
        )

    # draw faster path (shorter bar)
    for i, row in summary.iterrows():
        color = "blue" if row["fast_path"] == "cross" else "orange"
        xpos = x[list(summary.index).index(i)]
        plt.bar(
            xpos,
            row["fast_frac"],
            width=width,
            color=color
        )
        # place faster bar annotation near bottom of the bar
        plt.text(
            xpos,
            row["fast_frac"] * 0.05 + 0.02,  # small offset above bottom
            f"{row['fast_val']:.1f}",
            ha="center",
            va="bottom",
            rotation=90,
            fontsize=8,
            color="white" if row["fast_frac"] > 0.3 else "black"
        )

    plt.xticks(x, summary["test"], rotation=90)
    plt.ylabel("Latency Factor (Faster / Slower), (Bar Value in Cycles)")
    plt.yticks(np.arange(0, 1.1, 0.1))  # 0, 0.1, 0.2, ..., 1.0
    plt.xlabel("Instruction")
    plt.ylim(0, 1.1)
    plt.title("Same-Socket vs Cross-Socket Average Latency")

    from matplotlib.patches import Patch
    plt.legend(handles=[
        Patch(color="orange", label="Same Socket"),
        Patch(color="blue", label="Cross Socket"),
    ])

    plt.tight_layout()
    plt.savefig("./socket_latency_relative.png")




#benchmark_tests()
print("\n")
#benchmark_core_placement()
visualise_latency()
#visualise_socket_latency_comparison_normalised()
