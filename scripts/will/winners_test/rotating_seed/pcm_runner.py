import subprocess
import csv
import re
import os

NUM_RUNS = 4000
CSV_FILE = "4000_runs_1mill_reps_repeat.csv"

# Regex to parse lines like:
CPU_LINE_RE = re.compile(
    r"CPU\s+(\d+)\s+ran\s+(\S+)\s+\|\s+"
    r"wins:\s+(\d+)\s+\|\s+"
    r"attempts:\s+(\d+)\s+\|\s+"
    r"successes:\s+(\d+)\s+\|\s+"
    r"failures:\s+(\d+)"
)

X_ARRAY_STR = str(list(range(0,40)))  # example
X_ARRAY = [int(x.strip()) for x in X_ARRAY_STR.strip("[]").split(",")]
NUM_CPUS = len(X_ARRAY)

CMD_BASE = [
    "../../../../ccbench",
    "-r", "1" + "000" + "000",
    "-x", X_ARRAY_STR,
    "-t", "[0]",
    "-R"
]

# Ensure pcm folder exists
os.makedirs("pcm", exist_ok=True)

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
        b_value = X_ARRAY[run % NUM_CPUS]
        cmd = CMD_BASE + ["-b", str(b_value)]

        print(f"Starting run {run + 1} with -b {b_value}...")

        # PCM CSV filename for this run
        pcm_csv_file = f"pcm/{run+1}.csv"

        # Run PCM with CCbench in one command
        pcm_cmd = [
            "sudo", "pcm", "0.2", f"-csv={pcm_csv_file}", "--"
        ] + cmd

        result = subprocess.run(
            pcm_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True
        )

        # Parse the CCbench stdout as before
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

        print(f"Run {run + 1} completed. PCM saved to {pcm_csv_file}")

print(f"All runs completed. Results saved to {CSV_FILE}")