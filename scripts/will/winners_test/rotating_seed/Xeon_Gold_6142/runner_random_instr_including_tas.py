from collections import Counter
import random
import subprocess
import csv
import re

NUM_RUNS = 6400
CSV_FILE = "6400_runs_1.6mill_reps_random_test.csv"


CPU_LINE_RE = re.compile(
    r"CPU\s+(\d+)\s+ran\s+(\S+)\s+\|\s+"
    r"wins:\s+(\d+)\s+\|\s+"
    r"attempts:\s+(\d+)\s+\|\s+"
    r"successes:\s+(\d+)\s+\|\s+"
    r"failures:\s+(\d+)"
)

# Topology for the Gold cpu
sock0_smt0 = list(range(0, 32, 2))
sock1_smt0 = list(range(1, 32, 2))
sock0_smt1 = list(range(32, 64, 2))
sock1_smt1 = list(range(33, 64, 2))

TOPO_ORDER = sock0_smt0 + sock1_smt0 + sock0_smt1 + sock1_smt1

X_ARRAY_STR = str(list(range(0,64)))

uniform_t_array = []


TEST_INDEXES = [0, 7, 12, 13, 14, 15]


def generate_segment_16(values):

    # two of each instruction
    segment = values * 2  # 12 elements

    # add 4 unique extra instructions
    segment += random.sample(values, 4)

    random.shuffle(segment)

    return segment



def random_doubled_array():

    seg1 = generate_segment_16(TEST_INDEXES)
    seg2 = generate_segment_16(TEST_INDEXES)

    final_arr = []
    for loop in range(len(seg1)):
        final_arr.append(seg1[loop])
        final_arr.append(seg2[loop])

    final_arr += final_arr

    return final_arr


print(f"Using -x array: {X_ARRAY_STR}")

X_ARRAY = [int(x.strip()) for x in X_ARRAY_STR.strip("[]").split(",")]
NUM_CPUS = len(X_ARRAY)

CMD_BASE = [
    "../../../../../ccbench",
    "-r", "1" + "600" + "000",
    "-x", X_ARRAY_STR,
    "-R"
]

with open(CSV_FILE, "w", newline="") as f:

    writer = csv.writer(f)

    writer.writerow([
        "run",
        "cpu",
        "b_value",
        "test_type",
        "wins",
        "attempts",
        "successes",
        "failures"
    ])

    for run in range(NUM_RUNS):

        # rotate -b value
        b_value = X_ARRAY[run % NUM_CPUS]

        cmd = CMD_BASE + ["-b", str(b_value)]

        T_ARRAY = random_doubled_array()

        uniform_t_array.append(T_ARRAY)

        cmd = cmd + ["-t", str(T_ARRAY)]

        print(f"Starting run {run + 1} with -b {b_value} and -t {T_ARRAY}...")

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True
        )

        for line in result.stdout.splitlines():

            match = CPU_LINE_RE.search(line)

            if match:
                cpu, test_type, wins, attempts, successes, failures = match.groups()

                writer.writerow([
                    run + 1,
                    cpu,
                    b_value,
                    test_type,
                    wins,
                    attempts,
                    successes,
                    failures
                ])

        print(f"Run {run + 1} completed.")



EPSILON = 0.15
T_ARRAY_FILE = "generated_t_arrays.csv"
ANALYSIS_FILE = "t_array_frequency_analysis.txt"


with open(T_ARRAY_FILE, "w", newline="") as f:

    writer = csv.writer(f)
    writer.writerow(["run", "t_array"])

    for run_index, t_array in enumerate(uniform_t_array, start=1):
        writer.writerow([run_index, str(t_array)])

print(f"All generated -t arrays saved to {T_ARRAY_FILE}")


NUM_SAMPLES = len(uniform_t_array)

pos_counts = [Counter() for _ in range(32)]  # first 32 positions

for t_array in uniform_t_array:

    block = t_array[:32]

    for i, val in enumerate(block):
        pos_counts[i][val] += 1


with open(ANALYSIS_FILE, "w") as f:

    f.write("Frequencies per position (first 32-element block):\n")

    for i, counter in enumerate(pos_counts):

        freqs = {val: f"{counter[val]/NUM_SAMPLES:.3f}" for val in TEST_INDEXES}

        f.write(f"Position {i}: {freqs}\n")


    f.write("\nPositions/values violating tolerance:\n")

    expected_freq = 1 / len(TEST_INDEXES)

    violations = []

    for i, counter in enumerate(pos_counts):

        for val in TEST_INDEXES:

            freq = counter[val] / NUM_SAMPLES

            if abs(freq - expected_freq) > EPSILON:
                violations.append((i, val, freq))


    if violations:
        for pos, val, freq in violations:
            f.write(
                f"Position {pos}, Value {val}: freq = {freq:.4f}, expected ≈ {expected_freq:.4f}\n"
            )
    else:
        f.write(
            f"All positions are within ±{EPSILON:.3f} of expected frequency {expected_freq:.3f}.\n"
        )

print(f"Frequency analysis saved to {ANALYSIS_FILE}")

print(f"All runs completed. Results saved to {CSV_FILE}")