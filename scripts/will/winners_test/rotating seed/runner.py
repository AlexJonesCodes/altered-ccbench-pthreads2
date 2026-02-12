import subprocess
import csv
import re

NUM_RUNS = 10
CSV_FILE = "ccbench_results.csv"

# Regex to parse lines like:
# CPU 0 ran STORE_ON_MODIFIED | wins: 1014746 | attempts: 1014748 | successes: 1014748 | failures: 0
CPU_LINE_RE = re.compile(
    r"CPU\s+(\d+)\s+ran\s+(\S+)\s+\|\s+"
    r"wins:\s+(\d+)\s+\|\s+"
    r"attempts:\s+(\d+)\s+\|\s+"
    r"successes:\s+(\d+)\s+\|\s+"
    r"failures:\s+(\d+)"
)

# CPUs in -x array
CPU_ARRAY = list(range(40))  # 0..39
NUM_CPUS = len(CPU_ARRAY)

CMD_BASE = [
    "../../../../ccbench",
    "-r", "10000000",
    "-x", "[0,...,39]",
    "-t", "[0]",
    "-R",
    "-Z", "static"
]

with open(CSV_FILE, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow([
        "run",
        "cpu",
        "test_type",
        "wins",
        "attempts",
        "successes",
        "failures"
    ])

    for run in range(NUM_RUNS):
        # Pick -b value cycling through CPU_ARRAY
        b_value = CPU_ARRAY[run % NUM_CPUS]

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
                    run + 1,  # 1-based run number
                    cpu,
                    test_type,
                    wins,
                    attempts,
                    successes,
                    failures
                ])

        print(f"Run {run + 1} completed.")

print(f"All runs completed. Results saved to {CSV_FILE}")
