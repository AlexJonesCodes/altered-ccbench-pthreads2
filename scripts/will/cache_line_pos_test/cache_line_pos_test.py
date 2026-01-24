#!/usr/bin/env python3
"""
Run ccbench with a fixed contending set of threads while moving the seed (-b) across all threads,
and write a CSV with common-start mean latencies per thread.

CSV columns:
  test_id, pinned_thread, latency_0, latency_1, ..., latency_{N-1}
where latency_i is the mean cycles reported for thread ID i in the "Common-start latency (B4 -> success)" section.

You can configure ccbench path, tests to run, repetitions, stride, and which cores to include.
"""

import os
import re
import sys
import subprocess
from datetime import datetime

# ==============================
# Configuration (edit in code)
# ==============================

# Path to ccbench binary
CCBENCH_PATH = "../../../ccbench"

# Tests to run (IDs as understood by your ccbench build)
TEST_IDS = [13, 14, 15, 34]  # e.g., [13, 14, 15, 34]

# Repetitions per run (per seed core)
REPS = 10000

# Stride for single-line race
STRIDE = 1

# Flags to ccbench
DISABLE_NUMA = False  # if True, add -n
VERBOSE = True        # if True, add -v
USE_MLOCK = False     # if True, add -K

# Core set to contend:
# Option A: use all online CPUs (be careful, can be very heavy)
USE_ALL_ONLINE_CPUS = True

# Output
OUT_DIR = "./results/r53600"
CSV_FILE = os.path.join(OUT_DIR, "placement_latency.csv")

# Save raw stdout of each run (optional)
SAVE_LOGS = True
LOG_DIR = os.path.join(OUT_DIR, "logs")

# ==============================
# Internals
# ==============================

# Parse lines like:
#   "  thread ID 0 (core 0): mean    0.0 cycles, min    0.0, max    0.0"
LAT_RE = re.compile(r"thread ID\s+(\d+)\s+\(core\s+(\d+)\):\s+mean\s+([0-9.]+)\s+cycles")

def read_file(path: str) -> str:
    with open(path, "r") as f:
        return f.read().strip()

def parse_cpu_list(s: str):
    # Parse "0-3,8,10-11" into a sorted list of ints
    out = set()
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(out)

def get_online_cpus():
    sysfs = "/sys/devices/system/cpu/online"
    if not os.path.exists(sysfs):
        raise RuntimeError("Cannot find /sys/devices/system/cpu/online to discover online CPUs.")
    return parse_cpu_list(read_file(sysfs))

def ensure_dir(d: str):
    os.makedirs(d, exist_ok=True)

def build_core_set():
    if USE_ALL_ONLINE_CPUS:
        return get_online_cpus()
    return list(CORE_SET)

def run_ccbench(test_id: int, cores: list[int], seed_core: int) -> str:
    # Build -t and -x strings
    t_str = "[" + ",".join(str(test_id) for _ in cores) + "]"
    x_str = "[" + ",".join(str(c) for c in cores) + "]"

    args = [CCBENCH_PATH,
            "-r", str(REPS),
            "-t", t_str,
            "-x", x_str,
            "-b", str(seed_core),
            "-s", str(STRIDE)]
    if DISABLE_NUMA:
        args.append("-n")
    if VERBOSE:
        args.append("-v")
    if USE_MLOCK:
        args.append("-K")

    try:
        proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True)
        return proc.stdout
    except subprocess.CalledProcessError as e:
        out = e.stdout or ""
        if SAVE_LOGS:
            ensure_dir(LOG_DIR)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            with open(os.path.join(LOG_DIR, f"FAIL_test{test_id}_seed{seed_core}_{ts}.log"), "w") as f:
                f.write(out)
        raise

def parse_latencies(stdout: str, nthreads: int) -> list[float]:
    """
    Extract mean cycles per 'thread ID i' from ccbench output. Returns a list of length nthreads.
    Missing indices are filled with NaN.
    """
    lats = [float('nan')] * nthreads
    for line in stdout.splitlines():
        m = LAT_RE.search(line)
        if not m:
            continue
        tid = int(m.group(1))
        mean = float(m.group(3))
        if 0 <= tid < nthreads:
            lats[tid] = mean
    return lats

def write_csv_header(path: str, nthreads: int):
    exists = os.path.exists(path)
    if exists and os.path.getsize(path) > 0:
        return
    with open(path, "w") as f:
        cols = ["test_id", "pinned_thread"] + [f"latency_{i}" for i in range(nthreads)]
        f.write(",".join(cols) + "\n")

def append_csv_row(path: str, test_id: int, seed_core: int, lats: list[float]):
    with open(path, "a") as f:
        row = [str(test_id), str(seed_core)] + [f"{x:.1f}" if (x == x) else "nan" for x in lats]  # x==x filters NaN
        f.write(",".join(row) + "\n")

def main():
    ensure_dir(OUT_DIR)
    if SAVE_LOGS:
        ensure_dir(LOG_DIR)

    if not os.path.exists(CCBENCH_PATH) or not os.access(CCBENCH_PATH, os.X_OK):
        print(f"Error: ccbench not found or not executable at {CCBENCH_PATH}", file=sys.stderr)
        sys.exit(1)

    cores = build_core_set()
    nthreads = len(cores)
    if nthreads < 2:
        print("Need at least 2 cores in CORE_SET to create contention.", file=sys.stderr)
        sys.exit(2)

    print(f"Contending cores (order defines thread IDs 0..{nthreads-1}): {cores}")
    print(f"Seeds to sweep: {cores}")
    print(f"Tests: {TEST_IDS} | REPS={REPS} | STRIDE={STRIDE} | NUMA {'off' if DISABLE_NUMA else 'on'} | VERBOSE={VERBOSE}")

    write_csv_header(CSV_FILE, nthreads)

    for test_id in TEST_IDS:
        for seed in cores:
            print(f"[RUN] test={test_id} seed={seed} ...")
            out = run_ccbench(test_id, cores, seed)
            if SAVE_LOGS:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                with open(os.path.join(LOG_DIR, f"test{test_id}_seed{seed}_{ts}.log"), "w") as f:
                    f.write(out)

            lats = parse_latencies(out, nthreads)
            # Basic sanity: warn if some thread IDs missing
            missing = [i for i, v in enumerate(lats) if not (v == v)]
            if missing:
                print(f"  Warning: missing latency for thread IDs {missing}", file=sys.stderr)
            append_csv_row(CSV_FILE, test_id, seed, lats)
            print(f"  Wrote row to {CSV_FILE}")

    print("All runs complete.")

if __name__ == "__main__":
    main()
