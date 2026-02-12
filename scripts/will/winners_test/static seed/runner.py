import subprocess
import csv
import re
import sys


NUM_RUNS = 20
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

CMD = [
    "../../../ccbench",
    "-r", "10000000",
    "-x", "[0,...,9]",
    "-t", "[0]",
    "-b", "0",
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

    for run in range(1, NUM_RUNS + 1):
        print(f"Starting run {run}...")

        result = subprocess.run(
            CMD,
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
                    run,
                    cpu,
                    test_type,
                    wins,
                    attempts,
                    successes,
                    failures
                ])

        print(f"Run {run} completed.")

print(f"All runs completed. Results saved to {CSV_FILE}")
