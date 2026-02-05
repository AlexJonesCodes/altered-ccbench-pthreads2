#!/usr/bin/env python3
"""
Contention placement latency sweep (dual metrics).

Runs ccbench with a fixed contending set of threads (-x has many cores) while sweeping the seed (-b)
across all selected seed cores, for tests 13 (FAI), 14 (TAS), 15 (SWAP), and 34 (CAS_UNTIL_SUCCESS).

Writes a wide CSV with both metrics per thread:
  test_id,seed_thread,
  [for each thread ID i in the -x order]
    core_i,b4_mean_i,pfd_avg_i,pfd_min_i,pfd_max_i,pfd_std_i,pfd_absdev_i

- b4_mean_i: from "Common-start latency (B4 -> success), per thread" (thread ID i mean cycles)
- pfd_*_i  : from "Cross-core summary" per-thread stats (role==thread ID i)

Notes:
- This script excludes CPU 0 by default and selects one logical CPU per physical core (no SMT siblings) for cleaner data.
- Set USE_ALL_ONLINE_CPUS=True to run on all online CPUs (noisy, but comprehensive).
"""

import os
import re
import sys
import subprocess
from datetime import datetime
from typing import List, Tuple, Optional, Dict

# ==============================
# Configuration (edit in code)
# ==============================

# Path to ccbench binary
CCBENCH_PATH = "../../../ccbench"

# Tests to run (IDs as understood by your ccbench build)
TEST_IDS = [13, 14, 15, 34]  # FAI, TAS, SWAP, CAS_UNTIL_SUCCESS

# Repetitions per run (per seed core)
REPS = 10000

# Stride for single-line race
STRIDE = 1

# Flags to ccbench
DISABLE_NUMA = False  # if True, add -n
VERBOSE = True        # if True, add -v (and -p 0 to keep output stable for parsing)
USE_MLOCK = False     # if True, add -K

# Core set for contention:
# - By default, pick one logical CPU per physical core (exclude CPU 0)
EXCLUDE_CPUS = {0}
USE_ONE_PER_CORE = True

# - If you want everything (including SMT siblings), set this True:
USE_ALL_ONLINE_CPUS = False

# If neither of the above, set a manual list:
CORE_SET: List[int] = [1, 2, 3, 4, 5, 6, 7]  # only used when USE_ONE_PER_CORE=False and USE_ALL_ONLINE_CPUS=False

# Output
OUT_DIR = "./results/r53600/"
CSV_FILE = os.path.join(OUT_DIR, "placement_latency_contention.csv")

# Save raw stdout of each run (optional)
SAVE_LOGS = False
LOG_DIR = os.path.join(OUT_DIR, "logs")

# ==============================
# Internals
# ==============================

# Parse B4 common-start latencies:
#   "  thread ID 0 (core 16): mean  123.4 cycles, min  120.0, max  140.0"
LAT_B4_RE = re.compile(r"thread ID\s+(\d+)\s+\(core\s+(\d+)\):\s+mean\s+([0-9.]+)\s+cycles", re.IGNORECASE)

# Parse cross-core per-thread stats:
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
    coremap: Dict[tuple, int] = {}

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

    # If chosen rep is excluded (e.g., cpu0), try another sibling
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

def build_core_set() -> List[int]:
    if USE_ONE_PER_CORE:
        return one_thread_per_core(exclude=EXCLUDE_CPUS)
    if USE_ALL_ONLINE_CPUS:
        cores = [c for c in get_online_cpus() if c not in EXCLUDE_CPUS]
        return cores
    return [c for c in CORE_SET if c not in EXCLUDE_CPUS]

def run_ccbench(test_id: int, cores: List[int], seed_core: int) -> str:
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
        args += ["-p", "0"]  # suppress per-sample dumps; keep summary lines stable for parsing
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

def parse_b4_means(stdout: str, nthreads: int) -> List[float]:
    """
    Extract mean cycles per 'thread ID i' from the "Common-start latency" section.
    Returns a list of length nthreads filled with NaN by default.
    """
    lats = [float('nan')] * nthreads
    for line in stdout.splitlines():
        m = LAT_B4_RE.search(line)
        if not m:
            continue
        tid = int(m.group(1))
        mean = float(m.group(3))
        if 0 <= tid < nthreads:
            # Treat zero as missing (consistent with code changes that skip zero entries)
            lats[tid] = mean if mean > 0.0 else float('nan')
    return lats

def parse_cross_core(stdout: str, nthreads: int):
    """
    Parse cross-core summary lines and return per-thread arrays aligned to thread IDs 0..n-1.
    Returns dict with keys: core, pfd_avg, pfd_min, pfd_max, pfd_std, pfd_absdev
    """
    out = {
        "core": [None] * nthreads,               # physical core id per thread index
        "pfd_avg": [float('nan')] * nthreads,
        "pfd_min": [float('nan')] * nthreads,
        "pfd_max": [float('nan')] * nthreads,
        "pfd_std": [float('nan')] * nthreads,
        "pfd_absdev": [float('nan')] * nthreads,
    }
    for line in stdout.splitlines():
        m = CROSS_RE.search(line)
        if not m:
            continue
        role = int(m.group(1))   # thread ID within group
        core = int(m.group(2))   # physical CPU
        avg  = float(m.group(3))
        vmin = float(m.group(4))
        vmax = float(m.group(5))
        std  = float(m.group(6))
        absd = float(m.group(7))
        if 0 <= role < nthreads:
            out["core"][role] = core
            out["pfd_avg"][role] = avg
            out["pfd_min"][role] = vmin
            out["pfd_max"][role] = vmax
            out["pfd_std"][role] = std
            out["pfd_absdev"][role] = absd
    return out

def write_csv_header(path: str, nthreads: int):
    exists = os.path.exists(path)
    if exists and os.path.getsize(path) > 0:
        return
    cols = ["test_id", "seed_thread"]
    for i in range(nthreads):
        cols.append(f"core_{i}")
        cols.append(f"b4_mean_{i}")
        cols.append(f"pfd_avg_{i}")
        cols.append(f"pfd_min_{i}")
        cols.append(f"pfd_max_{i}")
        cols.append(f"pfd_std_{i}")
        cols.append(f"pfd_absdev_{i}")
    with open(path, "w") as f:
        f.write(",".join(cols) + "\n")

def append_csv_row(path: str, test_id: int, seed_core: int,
                   cores_phys: List[Optional[int]],
                   b4_means: List[float],
                   pfd: Dict[str, List[float]]):
    def fnum(x):
        return f"{x:.1f}" if (x == x) else "nan"
    with open(path, "a") as f:
        row = [str(test_id), str(seed_core)]
        n = len(b4_means)
        for i in range(n):
            row.append("" if cores_phys[i] is None else str(cores_phys[i]))
            row.append(fnum(b4_means[i]))
            row.append(fnum(pfd["pfd_avg"][i]))
            row.append(fnum(pfd["pfd_min"][i]))
            row.append(fnum(pfd["pfd_max"][i]))
            row.append(fnum(pfd["pfd_std"][i]))
            row.append(fnum(pfd["pfd_absdev"][i]))
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
        print("Need at least 2 cores in the contention set.", file=sys.stderr)
        sys.exit(2)

    write_csv_header(CSV_FILE, nthreads)

    print(f"Contending cores (order defines thread IDs 0..{nthreads-1}): {cores}")
    print(f"Seeds to sweep: {cores}")
    print(f"Tests: {TEST_IDS} | REPS={REPS} | STRIDE={STRIDE} | NUMA {'off' if DISABLE_NUMA else 'on'} | VERBOSE={VERBOSE}")

    for test_id in TEST_IDS:
        for seed in cores:
            print(f"[RUN] test={test_id} seed={seed} ...")
            out = run_ccbench(test_id, cores, seed)
            if SAVE_LOGS:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                with open(os.path.join(LOG_DIR, f"test{test_id}_seed{seed}_{ts}.log"), "w") as f:
                    f.write(out)

            b4_means = parse_b4_means(out, nthreads)
            pfd = parse_cross_core(out, nthreads)
            missing_b4 = [i for i, v in enumerate(b4_means) if not (v == v)]
            missing_pfd = [i for i, v in enumerate(pfd["pfd_avg"]) if not (v == v)]
            if missing_b4:
                print(f"  Note: missing B4 mean for thread IDs {missing_b4}", file=sys.stderr)
            if missing_pfd:
                print(f"  Note: missing Cross-core summary for thread IDs {missing_pfd}", file=sys.stderr)

            append_csv_row(CSV_FILE, test_id, seed, pfd["core"], b4_means, pfd)
            print(f"  Wrote row to {CSV_FILE}")

    print("All runs complete.")

if __name__ == "__main__":
    main()
