#!/usr/bin/env python3
import os
import re
import sys
import subprocess
from datetime import datetime

# ==============================
# Configuration (edit in code)
# ==============================

# Path to your ccbench binary
CCBENCH_PATH = "../../ccbench"

# Tests to run on each SMT pair. Example: [13, 14, 15, 34]
TEST_IDS = [13, 14, 15, 34]

# Repetitions per run (per seed core)
REPS = 1000

# Stride for single-line race
STRIDE = 1

# Pass these flags to ccbench
DISABLE_NUMA = False  # add -n if True
VERBOSE = True        # add -v if True
USE_MLOCK = False     # add -K if True

# Only 2-way SMT groups
ONLY_PAIRS = True

# Output files
test_dir = "./results/smt_fairness/"
CSV_FILE = os.path.join(test_dir, "smt_fairness_simple.csv")  # test_num,pinned_thread,thread1,thread2,thread1_wins,thread2_wins
LOG_FILE = os.path.join(test_dir, "ccbench_all.log")          # a single long log file (append-only)

# ==============================
# Internals
# ==============================

# Example line in ccbench output (printed by rank==0):
#   "  Group 0 role 0 on thread 3 (thread ID 0): 52341 wins"
WIN_LINE_RE = re.compile(
    r"^\s*Group\s+\d+\s+role\s+\d+\s+on\s+thread\s+(\d+)\s+\(thread ID\s+\d+\):\s+(\d+)\s+wins\b"
)

def read_file(path):
    with open(path, "r") as f:
        return f.read().strip()

def parse_cpu_list(s):
    # Parse strings like "0-5,8,10-11"
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
    return out

def get_online_cpus():
    return sorted(parse_cpu_list(read_file("/sys/devices/system/cpu/online")))

def get_smt_pairs():
    """
    Discover SMT sibling groups from Linux /sys and return only 2-way pairs as sorted tuples.
    """
    online = set(get_online_cpus())
    groups = {}
    for cpu in online:
        path = f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list"
        if not os.path.exists(path):
            continue
        sibs = tuple(sorted(parse_cpu_list(read_file(path)) & online))
        if len(sibs) <= 1:
            continue
        groups[sibs] = True
    pairs = [g for g in groups.keys() if len(g) == 2] if ONLY_PAIRS else list(groups.keys())
    return sorted(pairs)

def append_log(header, text):
    """
    Append a run header and the raw ccbench stdout to the single long log file.
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write("\n" + "="*80 + "\n")
        f.write(f"[{ts}] {header}\n")
        f.write("="*80 + "\n")
        f.write(text)
        f.write("\n")

def ensure_csv_with_header(path):
    """
    Open the CSV file in append mode and write the header if the file is empty or missing.
    Returns an open file handle ready for appending rows.
    """
    exists = os.path.exists(path)
    f = open(path, "a")
    if not exists or os.stat(path).st_size == 0:
        f.write("test_num,pinned_thread,thread1,thread2,thread1_wins,thread2_wins\n")
        f.flush()
    return f

def run_ccbench_once(test_id, thread1, thread2, seed_core):
    """
    Run ccbench for a given pair and seed core.
    Returns (wins_by_core_dict, raw_stdout).
    """
    # -t and -x for two threads
    t_str = f"[{test_id},{test_id}]"
    x_str = f"[{thread1},{thread2}]"

    args = [CCBENCH_PATH, "-r", str(REPS), "-t", t_str, "-x", x_str, "-b", str(seed_core), "-s", str(STRIDE)]
    if DISABLE_NUMA:
        args.append("-n")
    if VERBOSE:
        args.append("-v")
    if USE_MLOCK:
        args.append("-K")

    try:
        proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True)
        out = proc.stdout
    except subprocess.CalledProcessError as e:
        out = e.stdout or ""
        append_log(f"ccbench FAILED: test={test_id} pair=({thread1},{thread2}) seed={seed_core}", out)
        print("ccbench failed; see log for details.", file=sys.stderr)
        raise

    # Parse wins section
    wins_by_core = {}
    for line in out.splitlines():
        m = WIN_LINE_RE.match(line)
        if m:
            core = int(m.group(1))
            wins = int(m.group(2))
            wins_by_core[core] = wins

    return wins_by_core, out

def main():
    # Ensure output directory exists
    os.makedirs(test_dir, exist_ok=True)

    # Sanity checks
    if not os.path.exists(CCBENCH_PATH) or not os.access(CCBENCH_PATH, os.X_OK):
        print(f"Error: ccbench not found or not executable at {CCBENCH_PATH}", file=sys.stderr)
        sys.exit(1)

    pairs = get_smt_pairs()
    if not pairs:
        print("No SMT pairs discovered (or SMT disabled).", file=sys.stderr)
        sys.exit(1)

    # Build seed set from all online CPUs
    seeds = get_online_cpus()

    print(f"Discovered SMT pairs: {pairs}")
    print(f"Seeding on all online CPUs: {seeds}")
    print(f"Config: TEST_IDS={TEST_IDS}, REPS={REPS}, STRIDE={STRIDE}, flags: "
          f"{'-n ' if DISABLE_NUMA else ''}{'-v ' if VERBOSE else ''}{'-K' if USE_MLOCK else ''}")
    print(f"Appending raw output to: {LOG_FILE}")
    print(f"Appending CSV rows to:   {CSV_FILE}")

    # Open CSV once (append mode, write header if new)
    csv_f = ensure_csv_with_header(CSV_FILE)

    try:
        for test_id in TEST_IDS:
            for (a, b) in pairs:
                # Keep thread1 < thread2 for consistent CSV ordering
                thread1, thread2 = sorted((a, b))

                # For each seed core across the machine
                for seed in seeds:
                    header = (f"ccbench run: test={test_id} pair=({thread1},{thread2}) "
                              f"seed={seed} reps={REPS} stride={STRIDE}")
                    wins_by_core, raw = run_ccbench_once(test_id, thread1, thread2, seed)
                    append_log(header, raw)

                    # Map wins to the two thread cores; default to 0 if not present
                    w1 = wins_by_core.get(thread1, 0)
                    w2 = wins_by_core.get(thread2, 0)

                    # Append CSV row
                    row = f"{test_id},{seed},{thread1},{thread2},{w1},{w2}\n"
                    csv_f.write(row)
                    csv_f.flush()

                    print(f"Run complete: test={test_id} pair=({thread1},{thread2}) seed={seed} "
                          f"-> wins {thread1}:{w1}, {thread2}:{w2}")

    finally:
        csv_f.close()

    print("All runs complete.")

if __name__ == "__main__":
    main()
