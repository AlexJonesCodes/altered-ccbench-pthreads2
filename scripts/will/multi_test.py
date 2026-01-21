#!/usr/bin/env python3
import json
import subprocess
from pathlib import Path
import time
import sys
import shutil

# =========================
# Easy-to-change parameters
# =========================
CCBENCH = "../../ccbench"    # Path to ccbench binary
THREAD_COUNT = 11         # Number of threads (length of -t and -x)
REPS = 10000             # -r repetitions
VALUE_A = 34             # initial value in -t
VALUE_B = 13             # replacement value in -t
BIND_TO_NODE = 0         # -b
SAMPLE = 1               # -s
PIN_ONE_BASED = True     # True -> -x [1..THREAD_COUNT], False -> [0..THREAD_COUNT-1]
OUT_DIR = Path("results/multi_test")  # Directory to store logs
SLEEP_BETWEEN_RUNS_SEC = 0.0  # e.g., 0.25 to add a small delay between runs
# =========================

def build_thread_ids(tcount: int, one_based: bool):
    if one_based:
        return list(range(1, tcount + 1))
    return list(range(0, tcount))

def run_once(test_vec, thread_ids, tag):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUT_DIR / f"ccbench_{tag}.log"

    cmd = [
        CCBENCH,
        "-r", str(REPS),
        "-t", json.dumps(test_vec),      # e.g., [32,32,13,13]
        "-x", json.dumps(thread_ids),    # e.g., [1,2,3,4]
        "-b", str(BIND_TO_NODE),
        "-s", str(SAMPLE),
    ]

    print(f"Running: {' '.join(cmd)}")
    t0 = time.perf_counter()
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        print(f"ERROR: ccbench binary not found at '{CCBENCH}'.", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        # Save whatever output we have for debugging
        log_path.write_text(e.stdout or "")
        print(f"ERROR: ccbench exited with {e.returncode}. Log written to {log_path}", file=sys.stderr)
        sys.exit(e.returncode)

    elapsed = time.perf_counter() - t0
    log_path.write_text(result.stdout)
    print(f"Completed in {elapsed:.3f}s. Log written to {log_path}")

def main():
    # Optional sanity check for binary path
    if shutil.which(CCBENCH) is None:
        print(f"WARNING: ccbench may not be executable or on PATH: {CCBENCH}", file=sys.stderr)

    # Build thread pinning once
    thread_ids = build_thread_ids(THREAD_COUNT, PIN_ONE_BASED)

    # Perform exactly THREAD_COUNT runs:
    # k = 1..THREAD_COUNT, with last k entries replaced by VALUE_B
    # Example (T=4): [32,32,32,13], [32,32,13,13], [32,13,13,13], [13,13,13,13]
    for k in range(1, THREAD_COUNT + 1):
        test_vec = [VALUE_A] * (THREAD_COUNT - k) + [VALUE_B] * k
        tag = f"T{THREAD_COUNT}_k{k}_{VALUE_A}x{THREAD_COUNT-k}_{VALUE_B}x{k}"
        run_once(test_vec, thread_ids, tag)

        if SLEEP_BETWEEN_RUNS_SEC > 0:
            time.sleep(SLEEP_BETWEEN_RUNS_SEC)

    print("All runs completed.")

if __name__ == "__main__":
    main()
