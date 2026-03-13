import subprocess
import csv
import re

# --------------------------------------------------
# Configuration
# --------------------------------------------------

CCBENCH = "../../../ccbench"
OUTPUT_CSV = "ccbench_results.csv"

REPEATS = 20

TESTS = [0, 7, 13, 14, 15, 34]


# --------------------------------------------------
# Detect SMT sibling CPU pairs
# --------------------------------------------------

def detect_cpu_pairs():

    output = subprocess.check_output(
        ["lscpu", "-p=CPU,CORE"],
        text=True
    )

    core_map = {}

    for line in output.splitlines():

        if line.startswith("#"):
            continue

        cpu, core = map(int, line.split(","))

        core_map.setdefault(core, []).append(cpu)

    pairs = []

    for core in sorted(core_map.keys()):

        cpus = sorted(core_map[core])

        if len(cpus) >= 2:
            pairs.append((cpus[0], cpus[1]))

    return pairs


# --------------------------------------------------
# Generate full 6x6 test matrix
# --------------------------------------------------

def generate_test_matrix():

    tests = []

    for a in TESTS:
        for b in TESTS:
            tests.append((a, b))

    return tests


# --------------------------------------------------
# Parse Cross-core summary
# --------------------------------------------------

summary_regex = re.compile(
    r"Core number 0.*avg\s+([0-9.]+).*?\n"
    r"Core number 1.*avg\s+([0-9.]+)",
    re.MULTILINE
)


def parse_output(output):

    match = summary_regex.search(output)

    if not match:
        return None, None

    return float(match.group(1)), float(match.group(2))


# --------------------------------------------------
# Run one benchmark
# --------------------------------------------------

def run_test(cpu1, cpu2, test1, test2):

    cmd = [
        CCBENCH,
        "-x", f"[{cpu1},{cpu2}]",
        "-t", f"[{test1},{test2}]",
        "-b", str(cpu1)
    ]

    print("Running:", " ".join(cmd))

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    return parse_output(proc.stdout)


# --------------------------------------------------
# Main experiment
# --------------------------------------------------

def main():

    cpu_pairs = detect_cpu_pairs()

    print("Detected SMT pairs:")
    for p in cpu_pairs:
        print(p)

    test_matrix = generate_test_matrix()

    with open(OUTPUT_CSV, "w", newline="") as f:

        writer = csv.writer(f)

        writer.writerow([
            "repeat",
            "cpu1",
            "cpu2",
            "test1",
            "test2",
            "core0_avg_cycles",
            "core1_avg_cycles"
        ])

        for repeat in range(REPEATS):

            print(f"\n===== Repeat {repeat} =====\n")

            for cpu1, cpu2 in cpu_pairs:

                for test1, test2 in test_matrix:

                    c0, c1 = run_test(cpu1, cpu2, test1, test2)

                    if c0 is None:
                        print("Failed to parse output")
                        continue

                    writer.writerow([
                        repeat,
                        cpu1,
                        cpu2,
                        test1,
                        test2,
                        c0,
                        c1
                    ])

                    f.flush()


if __name__ == "__main__":
    main()