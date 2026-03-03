#!/usr/bin/env bash
set -euo pipefail

# Example launcher for:
#   scripts/run_adversarial_separate_attacker_addrs.sh
#
# Goal:
#   Compare victim performance/fairness when attackers either:
#     (a) all touch one shared cache line, or
#     (b) each touch a separate cache line.
#
# Adjust core lists to match your machine topology.
# Keep victim and attacker core sets disjoint.

scripts/run_adversarial_separate_attacker_addrs.sh \
  --victim-cores "0,2,4,6" \
  --attacker-cores "8,10,12,14" \
  --victim-test CAS \
  --attacker-test FAI \
  --victim-reps 20000 \
  --attacker-reps 200000000 \
  --fixed-victim-addr static \
  --shared-attacker-addr 0x700000100000 \
  --separate-attacker-base 0x700000300000 \
  --separate-attacker-step 0x1000 \
  --output-dir results/adversarial_separate_attacker_addrs_example

cat <<'MSG'

Done. See:
  results/adversarial_separate_attacker_addrs_example/summary.csv

How to read summary.csv:
  - victim_baseline: victim alone.
  - victim_plus_shared: attackers active, all on one line.
  - victim_plus_separate: attackers active, each on distinct lines.

  latency_ratio_vs_baseline > 1.0 means higher latency (slower), while < 1.0 means lower latency (faster).
  Lower jain_fairness means less fair distribution across victim threads.

If victim_plus_separate is still bad, interference is broader than
single-line coherence hotspotting.
MSG
