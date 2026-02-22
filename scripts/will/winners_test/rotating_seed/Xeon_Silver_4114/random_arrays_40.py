import random
from collections import Counter

# === Config ===
VALUES = [0, 7, 12, 13, 15]
REPEATS_PER_HALF = 2
NUM_SAMPLES = 8_000
EPSILON = 0.015  # tolerance

# === Generator ===
def random_doubled_array():
    half = [v for v in VALUES for _ in range(REPEATS_PER_HALF)]
    random.shuffle(half)
    other_half = half.copy()
    random.shuffle(other_half)
    block20 = half + other_half
    return block20 + block20

# === Track per-position counts ===
pos_counts = [Counter() for _ in range(20)]  # first 20-element block

for _ in range(NUM_SAMPLES):
    arr = random_doubled_array()
    block = arr[:20]
    for i, val in enumerate(block):
        pos_counts[i][val] += 1

# === Print frequencies per position ===
print("Frequencies per position (first 20-element block):")
for i, counter in enumerate(pos_counts):
    freqs = {val: f"{counter[val]/NUM_SAMPLES:.3f}" for val in VALUES}
    print(f"Position {i}: {freqs}")

# === Report violations ===
expected_freq = 1 / len(VALUES)
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