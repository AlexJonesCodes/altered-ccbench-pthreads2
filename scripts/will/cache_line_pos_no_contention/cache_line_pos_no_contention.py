#!/usr/bin/env python3
"""
Non-contention placement latency sweep (dual metrics) with robust one-thread-per-core selection.

Runs ccbench with a single worker thread (-x has one core) while sweeping the seed (-b)
across all selected cores, for tests 13 (FAI), 14 (TAS), 15 (SWAP), and 34 (CAS_UNTIL_SUCCESS).

Writes a CSV with both metrics:
  test_id,seed_thread,worker_thread,latency_b4,pfd_avg,pfd_min,pfd_max,pfd_std,pfd_absdev

- latency_b4: from "Common-start latency (B4 -> success), per thread" (thread ID 0 mean cycles)
- pfd_*    : from "Cross-core summary" per-core stats (Core number 0)

Notes:
- For tests that don’t populate the Common-start latency (in older builds), latency_b4 may be missing; we record NaN.
- For CAS_UNTIL_SUCCESS and with the code changes applied, latency_b4 should be meaningful.
- This script excludes CPU 0 and 1 by default and selects one logical CPU per physical core (no SMT siblings)
  using kernel-reported topology and the current process CPU affinity.
"""

import os
import re
import sys
import subprocess
from datetime import datetime
from typing import List, Tuple, Optional, Set, Dict

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
# - Exclude these CPUs everywhere (skip housekeeping; add more if needed)
EXCLUDE_CPUS: Set[int] = {}

# - Use one logical CPU per physical core (SMT off) for workers and seeds
USE_ONE_PER_CORE_FOR_WORKERS = True
USE_ONE_PER_CORE_FOR_SEEDS   = True

# - If not using one-per-core, choose sets via these toggles/lists:
USE_ALL_ONLINE_FOR_WORKERS = False
USE_ALL_ONLINE_FOR_SEEDS   = False

# If not using "all online" or "one per core", set these manually:
WORKER_CORES = [2, 3, 4]   # example; ignored if USE_ONE_PER_CORE_FOR_WORKERS or USE_ALL_ONLINE_FOR_WORKERS is True
SEED_CORES   = [5, 6, 7]   # example; ignored if USE_ONE_PER_CORE_FOR_SEEDS   or USE_ALL_ONLINE_FOR_SEEDS   is True

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

# ---------- CPU selection helpers (robust one-per-core) ----------

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
    if os.path.exists(sysfs):
        return parse_cpu_list(read_file(sysfs))
    # Fallback: enumerate cpu directories
    cpus = []
    base = "/sys/devices/system/cpu"
    if os.path.isdir(base):
        for name in os.listdir(base):
            if name.startswith("cpu") and name[3:].isdigit():
                cpus.append(int(name[3:]))
    return sorted(cpus)

def get_affinity_cpus() -> List[int]:
    # CPUs the current process is allowed to run on
    try:
        return sorted(os.sched_getaffinity(0))
    except AttributeError:
        return get_online_cpus()

def get_allowed_cpus(exclude: Set[int]) -> List[int]:
    online = set(get_online_cpus())
    aff = set(get_affinity_cpus())
    allowed = (online & aff) - set(exclude)
    return sorted(allowed)

def read_topology_ids(cpu: int) -> Optional[Tuple[int, int]]:
    """Return (physical_package_id, core_id) for a cpu, or None if unavailable."""
    base = f"/sys/devices/system/cpu/cpu{cpu}/topology"
    try:
        pkg = int(read_file(os.path.join(base, "physical_package_id")))
        cid = int(read_file(os.path.join(base, "core_id")))
        return (pkg, cid)
    except Exception:
        return None

def read_thread_siblings(cpu: int) -> Optional[frozenset]:
    """Return the full sibling set for this core as a frozenset of CPU IDs, or None."""
    path = f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list"
    try:
        return frozenset(parse_cpu_list(read_file(path)))
    except Exception:
        return None

def one_thread_per_core(exclude: Set[int]) -> List[int]:
    """
    Return one logical CPU per physical core, excluding any in 'exclude',
    and respecting the process's CPU affinity. Stable ordering by CPU ID.
    """
    allowed = get_allowed_cpus(exclude)
    if not allowed:
        return []

    # First try grouping by (package, core_id)
    groups: Dict[Tuple[int,int], List[int]] = {}
    missing_topology = False
    for cpu in allowed:
        key = read_topology_ids(cpu)
        if key is None:
            missing_topology = True
            break
        groups.setdefault(key, []).append(cpu)

    reps: Set[int] = set()
    if not missing_topology and groups:
        for _, lst in groups.items():
            reps.add(min(lst))
    else:
        # Fallback: use thread_siblings_list
        core_groups: Dict[frozenset, List[int]] = {}
        for cpu in allowed:
            sibs = read_thread_siblings(cpu)
            if sibs is None:
                core_groups.setdefault(frozenset([cpu]), []).append(cpu)
            else:
                # intersect with allowed to avoid picking excluded siblings
                sibs_allowed = frozenset(s for s in sibs if s in allowed)
                core_groups.setdefault(sibs_allowed if sibs_allowed else frozenset([cpu]), []).append(cpu)
        for key, lst in core_groups.items():
            # pick the lowest CPU id present in this group
            reps.add(min(key) if key else min(lst))

    result = sorted(reps)
    # Sanity: ensure excluded CPUs not present
    assert not (set(result) & set(exclude)), f"Excluded CPUs leaked into selection: {set(result)&set(exclude)}"
    return result

def ensure_dir(d: str):
    os.makedirs(d, exist_ok=True)

def build_worker_set() -> List[int]:
    if USE_ONE_PER_CORE_FOR_WORKERS:
        return one_thread_per_core(exclude=EXCLUDE_CPUS)
    if USE_ALL_ONLINE_FOR_WORKERS:
        cores = [c for c in get_online_cpus() if c not in EXCLUDE_CPUS and c in set(get_affinity_cpus())]
        return cores
    return [c for c in WORKER_CORES if c not in EXCLUDE_CPUS]

def build_seed_set() -> List[int]:
    if USE_ONE_PER_CORE_FOR_SEEDS:
        return one_thread_per_core(exclude=EXCLUDE_CPUS)
    if USE_ALL_ONLINE_FOR_SEEDS:
        cores = [c for c in get_online_cpus() if c not in EXCLUDE_CPUS and c in set(get_affinity_cpus())]
        return cores
    return [c for c in SEED_CORES if c not in EXCLUDE_CPUS]

# ---------- ccbench run and parsers ----------

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
            return mean if mean > 0.0 else float("nan")
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

# ==============================
# Main
# ==============================

def main():
    ensure_dir(OUT_DIR)
    if SAVE_LOGS:
        ensure_dir(LOG_DIR)

    if not os.path.exists(CCBENCH_PATH) or not os.access(CCBENCH_PATH, os.X_OK):
        print(f"Error: ccbench not found or not executable at {CCBENCH_PATH}", file=sys.stderr)
        sys.exit(1)

    # Build sets and print diagnostics
    online = get_online_cpus()
    affinity = get_affinity_cpus()
    workers = build_worker_set()
    seeds   = build_seed_set()

    if not workers:
        print("No worker cores configured.", file=sys.stderr)
        sys.exit(2)
    if not seeds:
        print("No seed cores configured.", file=sys.stderr)
        sys.exit(3)

    # Guards to ensure exclusions are respected
    assert set(workers).isdisjoint(EXCLUDE_CPUS), f"Excluded CPUs in workers: {set(workers)&EXCLUDE_CPUS}"
    assert set(seeds).isdisjoint(EXCLUDE_CPUS), f"Excluded CPUs in seeds: {set(seeds)&EXCLUDE_CPUS}"

    write_csv_header(CSV_FILE)

    print(f"Online CPUs:   {online}")
    print(f"Affinity CPUs: {affinity}")
    print(f"Workers (one per core, excluding {sorted(EXCLUDE_CPUS)}): {workers}")
    print(f"Seeds   (one per core, excluding {sorted(EXCLUDE_CPUS)}): {seeds}")
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
