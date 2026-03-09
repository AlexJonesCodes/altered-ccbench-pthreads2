import csv
from collections import Counter
import ast


VALUES = [0, 7, 12, 13, 14, 15]
EPSILON = 0.02  # tolerance for deviations


CSV_FILE = "random_test_inc_tas/generated_t_arrays.csv"
uniform_t_arrays = []

with open(CSV_FILE, newline='') as f:
    reader = csv.DictReader(f)
    for row in reader:

        t_array = ast.literal_eval(row["t_array"])
        uniform_t_arrays.append(t_array)

NUM_SAMPLES = len(uniform_t_arrays)


pos_counts_soc0 = [Counter() for _ in range(16)]
pos_counts_soc1 = [Counter() for _ in range(16)]


pattern_violations = []

for run_index, arr in enumerate(uniform_t_arrays, start=1):

    soc0_full = arr[::2]  # even indices
    soc1_full = arr[1::2] # odd indices


    if soc0_full[:16] != soc0_full[16:]:
        pattern_violations.append((run_index, 0, soc0_full[:16], soc0_full[16:]))
    if soc1_full[:16] != soc1_full[16:]:
        pattern_violations.append((run_index, 1, soc1_full[:16], soc1_full[16:]))


    for i, val in enumerate(soc0_full[:16]):
        pos_counts_soc0[i][val] += 1
    for i, val in enumerate(soc1_full[:16]):
        pos_counts_soc1[i][val] += 1


if pattern_violations:
    print("Double-segment pattern violations detected:")
    for run_idx, soc, first16, last16 in pattern_violations:
        print(f"Run {run_idx}, Socket {soc}: first16 != last16")
else:
    print("All runs respect the double-segment pattern.")


def report_pos_freq(pos_counts, socket_id):
    print(f"\nFrequencies per position (first 16-element block) for Socket {socket_id}:")
    expected_freq = 1 / len(VALUES)
    violations = []
    for i, counter in enumerate(pos_counts):
        freqs = {val: f"{counter[val]/NUM_SAMPLES:.3f}" for val in VALUES}
        print(f"Position {i}: {freqs}")
        for val in VALUES:
            freq = counter[val] / NUM_SAMPLES
            if abs(freq - expected_freq) > EPSILON:
                violations.append((i, val, freq))
    if violations:
        print(f"\nPositions/values violating tolerance for Socket {socket_id}:")
        for pos, val, freq in violations:
            print(f"Position {pos}, Value {val}: freq = {freq:.4f}, expected ≈ {expected_freq:.4f}")
    else:
        print(f"All positions are within ±{EPSILON:.3f} of expected frequency for Socket {socket_id}.")

report_pos_freq(pos_counts_soc0, 0)
report_pos_freq(pos_counts_soc1, 1)