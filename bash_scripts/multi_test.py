#!/usr/bin/env python3

import subprocess
import itertools
import csv
import sys
import re
import time
from pathlib import Path
from test_nums_to_name import *


# ---------- timing start ----------

START_TS = time.perf_counter()

# ---------- configuration ----------

CCBENCH = "../ccbench"
REPS = 100
CORES = list(range(0, 10))
TEST_ID = 14
OUTFILE = Path("results/ccbench_crosscore.csv")

# ---------- load test mapping ----------

with open("test_nums_to_name.sh") as f:
    for line in f:
        if line.startswith("TARGET_CORE"):
            # example: TARGET_CORE[14]=0
            k, v = line.strip().split("=")
            idx = int(k[k.find("[") + 1 : k.find("]")])
            TARGET_CORE[idx] = int(v)

CARE_CORE = TARGET_CORE[TEST_ID]

# ---------- helpers ----------

TEST_RE = re.compile(r"^Test number (\d+)")
CORE_RE = re.compile(
    r"Core number (\d+).*avg\s+([0-9.]+)\s+cycles"
)

def run_ccbench(core_pairs):
    cmd = [
        CCBENCH,
        "-r", str(REPS),
        "-t", f"[[{TEST_ID},{TEST_ID}]]",
        "-x", str(core_pairs).replace(" ", "")
    ]
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True
    )
    return result.stdout

def parse_crosscore(log):
    """
    Returns:
      { test_idx : avg_latency }
    """
    current_test = None
    results = {}

    for line in log.splitlines():
        m = TEST_RE.match(line)
        if m:
            current_test = int(m.group(1))
            continue

        m = CORE_RE.search(line)
        if m and current_test is not None:
            core_idx = int(m.group(1))
            avg = float(m.group(2))

            if core_idx == CARE_CORE:
                results[current_test] = avg

    return results

# ---------- main ----------

OUTFILE.parent.mkdir(exist_ok=True)

with OUTFILE.open("w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow([
        "test0","test0_from_core","test0_to_core","test0_avg_latency",
        "test1","test1_from_core","test1_to_core","test1_avg_latency"
    ])

    # generate disjoint core-pair permutations
    for (a, b, c, d) in itertools.permutations(CORES, 4):
        if len({a, b, c, d}) != 4:
            continue
        if a > b or c > d:
            continue  # normalize inside pairs

        pair0 = [a, b]
        pair1 = [c, d]

        log = run_ccbench([pair0, pair1])
        parsed = parse_crosscore(log)

        if 0 not in parsed or 1 not in parsed:
            print("ERROR: missing data for", pair0, pair1, file=sys.stderr)
            sys.exit(1)

        writer.writerow([
            TEST_ID, a, b, parsed[0],
            TEST_ID, c, d, parsed[1]
        ])
        print(f"Completed: {pair0} and {pair1}")

# ---------- timing end ----------

END_TS = time.perf_counter()
ELAPSED = END_TS - START_TS

print(f"Total execution time: {ELAPSED:.6f} seconds")
