from collections import Counter
import random
import subprocess
import csv
import re

NUM_RUNS = 4000
CSV_FILE = "4000_runs_1mill_reps_random_test.csv"

# Regex to parse lines like:
# CPU 0 ran STORE_ON_MODIFIED | wins: 1014746 | attempts: 1014748 | successes: 1014748 | failures: 0
CPU_LINE_RE = re.compile(
    r"CPU\s+(\d+)\s+ran\s+(\S+)\s+\|\s+"
    r"wins:\s+(\d+)\s+\|\s+"
    r"attempts:\s+(\d+)\s+\|\s+"
    r"successes:\s+(\d+)\s+\|\s+"
    r"failures:\s+(\d+)"
)

# The -x array string — could be out-of-order, gaps, etc.
X_ARRAY_STR = str(list(range(0,40)))  # example, replace with your -x

uniform_t_array = []  # to track generated -t arrays for uniformity checks
# === Generator (6-value, randomized 10-element halves) ===
VALUES = [0, 7, 12, 13, 15, 18]  # new 6 values

def generate_half_10(values):
    # Start with 1 of each value
    half = values.copy()
    # Add remaining 4 slots randomly
    half += random.choices(values, k=10 - len(values))
    random.shuffle(half)
    return half

def random_doubled_array():
    first_half = generate_half_10(VALUES)
    second_half = generate_half_10(VALUES)
    block20 = first_half + second_half
    return block20 + block20  # final 40-element array

print(f"Using -x array: {X_ARRAY_STR}")
# Convert to actual list of integers
X_ARRAY = [int(x.strip()) for x in X_ARRAY_STR.strip("[]").split(",")]
NUM_CPUS = len(X_ARRAY)

CMD_BASE = [
    "../../../../../ccbench",
    "-r", "1" + "000" + "000" ,
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
        # Rotate -b through the actual X_ARRAY values
        b_value = X_ARRAY[run % NUM_CPUS]

        cmd = CMD_BASE + ["-b", str(b_value)]
        T_ARRAY_STR = random_doubled_array()

        uniform_t_array.append(T_ARRAY_STR) 
        cmd = cmd + ["-t", str(T_ARRAY_STR)]  # T_ARRAY_STR is already a Python list        
        print(f"Starting run {run + 1} with -b {b_value} and -t {T_ARRAY_STR}...")
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

    
# --- Config for analysis ---
EPSILON = 0.02 # tolerance for frequency violations
T_ARRAY_FILE = "generated_t_arrays.csv"
ANALYSIS_FILE = "t_array_frequency_analysis.txt"

# --- 1. Save all generated -t arrays ---
with open(T_ARRAY_FILE, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["run", "t_array"])  # header
    for run_index, t_array in enumerate(uniform_t_array, start=1):
        writer.writerow([run_index, str(t_array)])
print(f"All generated -t arrays saved to {T_ARRAY_FILE}")

# --- 2. Per-position frequency analysis ---
NUM_SAMPLES = len(uniform_t_array)
pos_counts = [Counter() for _ in range(20)]  # first 20-element block only

for t_array in uniform_t_array:
    block = t_array[:20]
    for i, val in enumerate(block):
        pos_counts[i][val] += 1

# --- 3. Write analysis report to TXT ---
with open(ANALYSIS_FILE, "w") as f:
    f.write("Frequencies per position (first 20-element block):\n")
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
            f.write(f"Position {pos}, Value {val}: freq = {freq:.4f}, expected ≈ {expected_freq:.4f}\n")
    else:
        f.write(f"All positions are within ±{EPSILON:.3f} of expected frequency {expected_freq:.3f}.\n")

print(f"Frequency analysis saved to {ANALYSIS_FILE}")

print(f"All runs completed. Results saved to {CSV_FILE}")
