import pandas as pd
import numpy as np
import sys

# --------------------------------------------------
# USER SETTINGS — adjust if needed
# --------------------------------------------------

CSV_PATH = "6400_runs_1.6mill_reps_repeat/6400_runs_1.6mill_reps_repeat.csv"  

RUN_COLUMN = "run"           # column identifying run number
CPU_COLUMN = "cpu"           # column identifying cpu id
VALUE_COLUMN = "wins"       # column with measurement

# socket layout (edit if your topology differs)
SOCKET0_CPUS = list(range(0, 10)) + list(range(20, 30))
SOCKET1_CPUS = list(range(10, 20)) + list(range(30, 40))


# --------------------------------------------------
# LOAD DATA
# --------------------------------------------------

try:
    df = pd.read_csv(CSV_PATH)
except Exception as e:
    print("Failed to read CSV:", e)
    sys.exit(1)

required = [RUN_COLUMN, CPU_COLUMN, VALUE_COLUMN]
for col in required:
    if col not in df.columns:
        print(f"Missing column '{col}' in CSV")
        sys.exit(1)


# --------------------------------------------------
# ORGANISE DATA
# --------------------------------------------------

runs = sorted(df[RUN_COLUMN].unique())

socket0_means = []
socket1_means = []
winners = []


# --------------------------------------------------
# PER RUN SOCKET MEANS
# --------------------------------------------------

print("\nPer-run socket dominance\n")

for run in runs:
    run_df = df[df[RUN_COLUMN] == run]

    vals0 = run_df[run_df[CPU_COLUMN].isin(SOCKET0_CPUS)][VALUE_COLUMN]
    vals1 = run_df[run_df[CPU_COLUMN].isin(SOCKET1_CPUS)][VALUE_COLUMN]

    if len(vals0) == 0 or len(vals1) == 0:
        socket0_means.append(None)
        socket1_means.append(None)
        winners.append(None)
        print(f"Run {run:4d} | missing data")
        continue

    m0 = vals0.mean()
    m1 = vals1.mean()

    socket0_means.append(m0)
    socket1_means.append(m1)

    if m0 > m1:
        winner = 0
    elif m1 > m0:
        winner = 1
    else:
        winner = "tie"

    winners.append(winner)

    print(
        f"Run {run:4d} | "
        f"Socket0 mean={m0:12.3f} | "
        f"Socket1 mean={m1:12.3f} | "
        f"Winner={winner}"
    )


# --------------------------------------------------
# DETECT FLIPS
# --------------------------------------------------

# --------------------------------------------------
# DETECT FLIPS
# --------------------------------------------------

print("\nSocket dominance flips\n")

prev = None
flip_count = 0
flip_differences = []

for i, w in enumerate(winners):
    if w is None or w == "tie":
        continue

    if prev is None:
        prev = w
        continue

    if w != prev:
        diff = abs(socket0_means[i] - socket1_means[i])
        flip_differences.append(diff)

        print(
            f"FLIP at run {runs[i]} : "
            f"Socket {prev} -> Socket {w} | "
            f"Mean difference = {diff:.3f}"
        )

        flip_count += 1
        prev = w

print(f"\nTotal flips detected: {flip_count}")

if flip_differences:
    avg_flip_diff = np.mean(flip_differences)
    print(f"Average dominance difference at flip: {avg_flip_diff:.3f}")
else:
    print("No flips detected — no average difference to report.")