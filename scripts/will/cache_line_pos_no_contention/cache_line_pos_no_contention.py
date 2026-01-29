#!/usr/bin/env python3
"""
Non-contention placement latency sweep (dual metrics).

Runs ccbench with a single worker thread (-x has one core) while sweeping the seed (-b)
across all selected cores, for tests 13 (FAI), 14 (TAS), 15 (SWAP), and 34 (CAS_UNTIL_SUCCESS).

Writes a CSV with both metrics:
  test_id,seed_thread,worker_thread,latency_b4,latency_avg

- latency_b4: from "Common-start latency (B4 -> success), per thread" (thread ID 0 mean cycles)
- latency_avg: from "Cross-core summary" per-core average (Core number 0)

Notes:
- For tests that don’t populate the Common-start latency (e.g., FAI/TAS/SWAP in stock builds),
  latency_b4 may be 0.0 or absent; we record NaN if the section is missing.
- For CAS_UNTIL_SUCCESS, latency_b4 should be meaningful.

Configure paths and core sets below.
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

# Tests to run
TEST_IDS = [13, 14, 15, 34]  # FAI, TAS, SWAP, CAS_UNTIL_SUCCESS

# Repetitions per run (per seed)
REPS = 10000

# Stride
STRIDE = 1

# ccbench flags
DISABLE_NUMA = False  # if True, add -n (usually keep False so -b controls placement)
VERBOSE = True        # add -v
USE_MLOCK = False     # add -K

# Cores to test:
# - workers: list of cores to serve as the single -x worker
# - seeds: list of cores to sweep as -b (seed)
USE_ALL_ONLINE_FOR_WORKERS = True
USE_ALL_ONLINE_FOR_SEEDS   = True

# If not using all online cores, set these manually:
WORKER_CORES = [0, 6, 1, 7]   # example
SEED_CORES   = [0, 1, 2, 3]   # example

# Output
OUT_DIR  = "./r53600"
CSV_FILE = os.path.join(OUT_DIR, "noncontention_latency_dual.csv")
SAVE_LOGS = True
LOG_DIR  = os.path.join(OUT_DIR, "logs")

# ==============================
# Internals
# ==============================

# Common-start line:
LAT_CS_RE = re.compile(r"thread ID\s+(\d+)\s+\(core\s+(\d+)\):\s+mean\s+([0-9.]+)\s+cycles")
# Cross-core summary per-core avg line:
CORE_AVG_RE = re.compile(r"Core number\s+(\d+)\s+is using thread:\s+(\d+)\.\s+with:\s+avg\s+([0-9.]+)\s+cycles")

def read_file(path: str) -> str:
    with open(path, "r") as f:
        return f.read().strip()

def parse_cpu_list(s: str):
    # Parse "0-3,8,10-11" into sorted list of ints
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
        raise RuntimeError("Cannot find /sys/devices/system/cpu/online")
    return parse_cpu_list(read_file(sysfs))

def ensure_dir(d: str):
    os.makedirs(d, exist_ok=True)

def write_csv_header(path: str):
    exists = os.path.exists(path)
    if exists and os.path.getsize(path) > 0:
        return
    with open(path, "w") as f:
        f.write("test_id,seed_thread,worker_thread,latency_b4,latency_avg\n")

def append_csv(path: str, test_id: int, seed_core: int, worker_core: int,
               latency_b4: float, latency_avg: float):
    def fmt(x):
        return f"{x:.1f}" if (x == x) else "nan"  # NaN-safe
    with open(path, "a") as f:
        f.write(f"{test_id},{seed_core},{worker_core},{fmt(latency_b4)},{fmt(latency_avg)}\n")

def run_ccbench(test_id: int, worker_core: int, seed_core: int) -> str:
    # -t and -x for one worker
    t_str = f"[{test_id}]"
    x_str = f"[{worker_core}]"
    args = [CCBENCH_PATH, "-r", str(REPS), "-t", t_str, "-x", x_str, "-b", str(seed_core), "-s", str(STRIDE)]
    if DISABLE_NUMA: args.append("-n")
    if VERBOSE:      args.append("-v")
    if USE_MLOCK:    args.append("-K")

    try:
        proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True)
        return proc.stdout
    except subprocess.CalledProcessError as e:
        out = e.stdout or ""
        if SAVE_LOGS:
            ensure_dir(LOG_DIR)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            with open(os.path.join(LOG_DIR, f"FAIL_test{test_id}_worker{worker_core}_seed{seed_core}_{ts}.log"), "w") as f:
                f.write(out)
        raise

def parse_b4_latency(stdout: str) -> float:
    """
    Parse Common-start mean cycles for thread ID 0.
    Returns float or NaN if not found.
    """
    for line in stdout.splitlines():
        m = LAT_CS_RE.search(line)
        if not m:
            continue
        tid = int(m.group(1))
        mean = float(m.group(3))
        if tid == 0:
            return mean
    return float("nan")

def parse_crosscore_avg(stdout: str) -> float:
    """
    Parse Cross-core summary avg cycles for Core number 0.
    Returns float or NaN if not found.
    """
    for line in stdout.splitlines():
        m = CORE_AVG_RE.search(line)
        if not m:
            continue
        role = int(m.group(1))
        avg  = float(m.group(3))
        if role == 0:
            return avg
    return float("nan")

def main():
    ensure_dir(OUT_DIR)
    if SAVE_LOGS: ensure_dir(LOG_DIR)

    if not os.path.exists(CCBENCH_PATH) or not os.access(CCBENCH_PATH, os.X_OK):
        print(f"Error: ccbench not found or not executable at {CCBENCH_PATH}", file=sys.stderr)
        sys.exit(1)

    online = get_online_cpus()
    workers = online if USE_ALL_ONLINE_FOR_WORKERS else WORKER_CORES
    seeds   = online if USE_ALL_ONLINE_FOR_SEEDS   else SEED_CORES

    if not workers:
        print("No worker cores configured.", file=sys.stderr)
        sys.exit(2)
    if not seeds:
        print("No seed cores configured.", file=sys.stderr)
        sys.exit(3)

    write_csv_header(CSV_FILE)

    print(f"Workers: {workers}")
    print(f"Seeds:   {seeds}")
    print(f"Tests:   {TEST_IDS}  REPS={REPS}  STRIDE={STRIDE}  NUMA {'off' if DISABLE_NUMA else 'on'}  VERBOSE={VERBOSE}")

    for test_id in TEST_IDS:
        for worker in workers:
            for seed in seeds:
                print(f"[RUN] test={test_id} worker={worker} seed={seed} ...")
                out = run_ccbench(test_id, worker, seed)
                if SAVE_LOGS:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    with open(os.path.join(LOG_DIR, f"test{test_id}_worker{worker}_seed{seed}_{ts}.log"), "w") as f:
                        f.write(out)

                lat_b4  = parse_b4_latency(out)     # Common-start (B4->success)
                lat_avg = parse_crosscore_avg(out)  # Cross-core summary avg
                if not (lat_b4 == lat_b4):
                    print(f"  Note: no B4 latency found (test={test_id}, worker={worker}, seed={seed})", file=sys.stderr)
                if not (lat_avg == lat_avg):
                    print(f"  Note: no Cross-core avg found (test={test_id}, worker={worker}, seed={seed})", file=sys.stderr)

                append_csv(CSV_FILE, test_id, seed, worker, lat_b4, lat_avg)
                print(f"  -> B4={lat_b4 if lat_b4==lat_b4 else 'NaN'} cycles, AVG={lat_avg if lat_avg==lat_avg else 'NaN'} cycles; wrote CSV row")

    print(f"\nAll runs complete. CSV: {CSV_FILE}")

if __name__ == "__main__":
    main()
