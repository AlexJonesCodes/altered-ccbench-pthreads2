import subprocess
import csv
import re

NUM_RUNS = 400
CSV_FILE = "400_runs_1mill_reps_random_addr_moving_seed.csv.csv"

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




print(f"Using -x array: {X_ARRAY_STR}")
# Convert to actual list of integers
X_ARRAY = [int(x.strip()) for x in X_ARRAY_STR.strip("[]").split(",")]
NUM_CPUS = len(X_ARRAY)

CMD_BASE = [
    "../../../../ccbench",
    "-r", "1" + "000" + "000" ,
    "-x", X_ARRAY_STR,
    "-t", "[0]",
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

        print(f"Starting run {run + 1} with -b {b_value}...")

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

print(f"All runs completed. Results saved to {CSV_FILE}")
