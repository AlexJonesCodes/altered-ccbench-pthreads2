#!/usr/bin/env python3
"""
Non-contention placement latency sweep (dual metrics).

Runs ccbench with a single worker thread (-x has one core) while sweeping the seed (-b)
across all selected cores, for tests 13 (FAI), 14 (TAS), 15 (SWAP), and 34 (CAS_UNTIL_SUCCESS).

Writes a CSV with both metrics:
  test_id,seed_thread,worker_thread,latency_b4,pfd_avg,pfd_min,pfd_max,pfd_std,pfd_absdev

- latency_b4: from "Common-start latency (B4 -> success), per thread" (thread ID 0 mean cycles)
- pfd_*    : from "Cross-core summary" per-core stats (Core number 0)

Notes:
- For tests that don’t populate the Common-start latency (in older builds), latency_b4 may be missing; we record NaN.
- For CAS_UNTIL_SUCCESS and with the code changes applied, latency_b4 should be meaningful.
- This script excludes CPU 0 by default and selects one logical CPU per physical core (no SMT siblings) for cleaner data.
"""

import os
import re
import sys
import subprocess
from datetime import datetime
from typing import List, Tuple, Optional

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
VERBOSE = True        # add -v (and -p 0 to keep output parse-stable)
USE_MLOCK = False     # add -K

# Core selection:
# - Exclude these CPUs everywhere (CPU 0 by default to avoid housekeeping noise)
EXCLUDE_CPUS = {0}

# - Use one logical CPU per physical core (SMT off) for workers and seeds
USE_ONE_PER_CORE_FOR_WORKERS = True
USE_ONE_PER_CORE_FOR_SEEDS   = True

# - If not using one-per-core, choose sets via these toggles/lists:
USE_ALL_ONLINE_FOR_WORKERS = False
USE_ALL_ONLINE_FOR_SEEDS   = False

# If not using "all online" or "one per core", set these manually:
WORKER_CORES = [1, 2, 3]   # example; ignored if USE_ONE_PER_CORE_FOR_WORKERS or USE_ALL_ONLINE_FOR_WORKERS is True
SEED_CORES   = [4, 5, 6]   # example; ignored if USE_ONE_PER_CORE_FOR_SEEDS   or USE_ALL_ONLINE_FOR_SEEDS   is True

# Output
OUT_DIR  = "./r53600/"
CSV_FILE = os.path.join(OUT_DIR, "noncontention_latency.csv")
SAVE_LOGS = False
LOG_DIR  = os.path.join(OUT_DIR, "logs")

# ==============================
# Internals
# ==============================

# Common-start line, e.g.:
#   "  thread ID 0 (core 16): mean  123.4 cycles, min  120.0, max  140.0"
LAT_CS_RE = re.compile(r"thread ID\s+(\d+)\s+\(core\s+(\d+)\):\s+mean\s+([0-9.]+)\s+cycles", re.IGNORECASE)

# Cross-core summary line, e.g.:
#   "Core number 0 is using thread: 16. with: avg 123.4 cycles (min 100.0 | max 200.0), std dev: 10.2, abs dev: 8.7"
CROSS_RE = re.compile(
    r"Core number\s+(\d+)\s+is using thread:\s+(\d+)\.\s+with:\s+avg\s+([0-9.]+)\s+cycles\s+\(min\s+([0-9.]+)\s+\|\s+max\s+([0-9.]+)\),\s+std dev:\s+([0-9.]+),\s+abs dev:\s+([0-9.]+)",
    re.IGNORECASE
)

def read_file(path: str) -> str:
    with open(path, "r") as f:
        return f.read().strip()

def parse_cpu_list(s: str) -> List[int]:
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

def get_online_cpus() -> List[int]:
    sysfs = "/sys/devices/system/cpu/online"
    if not os.path.exists(sysfs):
        raise RuntimeError("Cannot find /sys/devices/system/cpu/online")
    return parse_cpu_list(read_file(sysfs))

def one_thread_per_core(exclude=set()) -> List[int]:
    """
    Return a sorted list of logical CPUs: one per physical core,
    excluding any in 'exclude'. Robust across sockets; prefers the
    lowest logical CPU id in each core that's not excluded.
    """
    online = set(get_online_cpus())
    # Map (package_id, core_id) -> chosen logical cpu
    coremap = {}

    def topo_read(path) -> Optional[int]:
        try:
            with open(path) as f:
                return int(f.read().strip())
        except FileNotFoundError:
            return None

    for cpu in online:
        if cpu in exclude:
            continue
        pkg = topo_read(f"/sys/devices/system/cpu/cpu{cpu}/topology/physical_package_id")
        cid = topo_read(f"/sys/devices/system/cpu/cpu{cpu}/topology/core_id")
        if pkg is None or cid is None:
            # Fallback: group by thread_siblings_list
            sib_path = f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list"
            try:
                with open(sib_path) as f:
                    sibs = parse_cpu_list(f.read().strip())
                rep = next((s for s in sorted(sibs) if s not in exclude), None)
                if rep is not None:
                    coremap[frozenset(sibs)] = rep
                continue
            except FileNotFoundError:
                continue

        key = (pkg, cid)
        prev = coremap.get(key)
        if prev is None or cpu < prev:
            coremap[key] = cpu

    # If the chosen rep for a core is excluded (e.g., cpu0), pick another sibling
    for key, chosen in list(coremap.items()):
        if chosen in exclude:
            sibs_file = f"/sys/devices/system/cpu/cpu{chosen}/topology/thread_siblings_list"
            try:
                with open(sibs_file) as f:
                    sibs = parse_cpu_list(f.read().strip())
                repl = next((s for s in sibs if s not in exclude), None)
                if repl is not None:
                    coremap[key] = repl
                else:
                    del coremap[key]
            except FileNotFoundError:
                del coremap[key]

    return sorted(set(coremap.values()))

def ensure_dir(d: str):
    os.makedirs(d, exist_ok=True)

def build_worker_set() -> List[int]:
    if USE_ONE_PER_CORE_FOR_WORKERS:
        return one_thread_per_core(exclude=EXCLUDE_CPUS)
    if USE_ALL_ONLINE_FOR_WORKERS:
        cores = [c for c in get_online_cpus() if c not in EXCLUDE_CPUS]
        return cores
    return [c for c in WORKER_CORES if c not in EXCLUDE_CPUS]

def build_seed_set() -> List[int]:
    if USE_ONE_PER_CORE_FOR_SEEDS:
        return one_thread_per_core(exclude=EXCLUDE_CPUS)
    if USE_ALL_ONLINE_FOR_SEEDS:
        cores = [c for c in get_online_cpus() if c not in EXCLUDE_CPUS]
        return cores
    return [c for c in SEED_CORES if c not in EXCLUDE_CPUS]

def run_ccbench(test_id: int, worker_core: int, seed_core: int) -> str:
    # -t and -x for one worker
    t_str = f"[{test_id}]"
    x_str = f"[{worker_core}]"
    args = [CCBENCH_PATH, "-r", str(REPS), "-t", t_str, "-x", x_str, "-b", str(seed_core), "-s", str(STRIDE)]
    if DISABLE_NUMA:
        args.append("-n")
    if VERBOSE:
        args.append("-v")
        args += ["-p", "0"]  # keep output parse-stable
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
            # In your ccbench.c we now skip zero entries; still, guard here
            return mean if mean != 0.0 else float("nan")
    return float("nan")

def parse_crosscore_stats(stdout: str) -> Tuple[float, float, float, float, float]:
    """
    Parse Cross-core summary stats (avg, min, max, std, absdev) for Core number 0.
    Returns a 5-tuple of floats or NaN for missing values.
    """
    for line in stdout.splitlines():
        m = CROSS_RE.search(line)
        if not m:
            continue
        role = int(m.group(1))
        if role != 0:
            continue
        avg  = float(m.group(3))
        vmin = float(m.group(4))
        vmax = float(m.group(5))
        std  = float(m.group(6))
        absd = float(m.group(7))
        return avg, vmin, vmax, std, absd
    return (float("nan"),) * 5

def write_csv_header(path: str):
    exists = os.path.exists(path)
    if exists and os.path.getsize(path) > 0:
        return
    with open(path, "w") as f:
        f.write("test_id,seed_thread,worker_thread,latency_b4,pfd_avg,pfd_min,pfd_max,pfd_std,pfd_absdev\n")

def append_csv(path: str, test_id: int, seed_core: int, worker_core: int,
               latency_b4: float, pfd_avg: float, pfd_min: float,
               pfd_max: float, pfd_std: float, pfd_absdev: float):
    def fmt(x):
        return f"{x:.1f}" if (x == x) else "nan"  # NaN-safe
    with open(path, "a") as f:
        f.write(",".join([
            str(test_id),
            str(seed_core),
            str(worker_core),
            fmt(latency_b4),
            fmt(pfd_avg),
            fmt(pfd_min),
            fmt(pfd_max),
            fmt(pfd_std),
            fmt(pfd_absdev),
        ]) + "\n")

def main():
    ensure_dir(OUT_DIR)
    if SAVE_LOGS:
        ensure_dir(LOG_DIR)

    if not os.path.exists(CCBENCH_PATH) or not os.access(CCBENCH_PATH, os.X_OK):
        print(f"Error: ccbench not found or not executable at {CCBENCH_PATH}", file=sys.stderr)
        sys.exit(1)

    workers = build_worker_set()
    seeds   = build_seed_set()

    if not workers:
        print("No worker cores configured.", file=sys.stderr)
        sys.exit(2)
    if not seeds:
        print("No seed cores configured.", file=sys.stderr)
        sys.exit(3)

    write_csv_header(CSV_FILE)

    print(f"Workers (one per core, excluding {sorted(EXCLUDE_CPUS)} if configured): {workers}")
    print(f"Seeds   (one per core, excluding {sorted(EXCLUDE_CPUS)} if configured): {seeds}")
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

                lat_b4 = parse_b4_latency(out)                 # Common-start (B4->success)
                avg, vmin, vmax, std, absdev = parse_crosscore_stats(out)  # PFD summary

                if not (lat_b4 == lat_b4):
                    print(f"  Note: no B4 latency found (test={test_id}, worker={worker}, seed={seed})", file=sys.stderr)
                if not (avg == avg):
                    print(f"  Note: no Cross-core summary found (test={test_id}, worker={worker}, seed={seed})", file=sys.stderr)

                append_csv(CSV_FILE, test_id, seed, worker, lat_b4, avg, vmin, vmax, std, absdev)
                print(f"  -> B4={lat_b4 if lat_b4==lat_b4 else 'NaN'} cycles, "
                      f"AVG={avg if avg==avg else 'NaN'} cycles; wrote CSV row")

    print(f"\nAll runs complete. CSV: {CSV_FILE}")

if __name__ == "__main__":
    main()
