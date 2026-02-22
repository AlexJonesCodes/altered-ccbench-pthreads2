import random
from collections import Counter

# === Config ===
VALUES = [0, 7, 12, 13, 14, 15]  # 6 values
NUM_SAMPLES = 4_000
EPSILON = 0.02  # slightly higher tolerance due to uneven counts

# --- Function to generate a randomized 10-element half ---
def generate_half_10(values):
    # Start with 1 of each (6 slots)
    half = values.copy()
    # Add 4 additional slots randomly to reach 10
    half += random.choices(values, k=10 - len(values))
    random.shuffle(half)
    return half

# --- Generator ---
def random_doubled_array():
    first_half = generate_half_10(VALUES)
    second_half = generate_half_10(VALUES)
    block20 = first_half + second_half
    return block20 + block20  # 40-element array

# --- Track per-position counts ---
pos_counts = [Counter() for _ in range(20)]  # first 20-element block only

for _ in range(NUM_SAMPLES):
    arr = random_doubled_array()
    block = arr[:20]
    for i, val in enumerate(block):
        pos_counts[i][val] += 1

# --- Print frequencies per position ---
print("Frequencies per position (first 20-element block):")
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