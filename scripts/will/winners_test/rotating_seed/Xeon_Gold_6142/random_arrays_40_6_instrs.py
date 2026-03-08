import random
from collections import Counter

# === Config ===
VALUES = [0, 7, 12, 13, 14, 15]  # 6 values
NUM_SAMPLES = 6400
EPSILON = 0.015

# --- Function to generate a randomized 16-element segment ---
def generate_segment_16(values):
    # Start with two of each instruction (12 slots)
    segment = values * 2

    # Add 4 additional UNIQUE instructions (no repeats)
    segment += random.sample(values, 4)

    # Shuffle the segment
    random.shuffle(segment)

    return segment

# --- Generator ---
def random_doubled_array():
    seg1 = generate_segment_16(VALUES)
    seg2 = generate_segment_16(VALUES)

    block32 = seg1 + seg2
    return block32 + block32  # 64-element array

# --- Track per-position counts ---
pos_counts = [Counter() for _ in range(32)]  # first 32-element block only

for _ in range(NUM_SAMPLES):
    arr = random_doubled_array()
    block = arr[:32]

    for i, val in enumerate(block):
        pos_counts[i][val] += 1

# --- Print frequencies per position ---
print("Frequencies per position (first 32-element block):")
for i, counter in enumerate(pos_counts):
    freqs = {val: f"{counter[val]/NUM_SAMPLES:.3f}" for val in VALUES}
    print(f"Position {i}: {freqs}")

# --- Report violations ---
expected_freq = 1 / len(VALUES)  # approximate expectation per value
violations = []

for i, counter in enumerate(pos_counts):
    for val in VALUES:
        freq = counter[val] / NUM_SAMPLES
        if abs(freq - expected_freq) > EPSILON:
            violations.append((i, val, freq))

print("\nPositions/values violating tolerance:")
if violations:
    for pos, val, freq in violations:
        print(f"Position {pos}, Value {val}: freq = {freq:.4f}, expected ≈ {expected_freq:.4f}")
else:
    print(f"All positions are within ±{EPSILON:.3f} of expected frequency {expected_freq:.3f}.")