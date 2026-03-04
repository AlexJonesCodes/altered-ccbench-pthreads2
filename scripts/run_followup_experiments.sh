#!/usr/bin/env bash
set -euo pipefail

# Follow-up adversarial experiments based on initial FAI-victim / CAS-attacker results.
#
# Key goals:
#   1) Higher statistical power (more reps & replicates)
#   2) More sensitive victim primitives (CAS, TAS)
#   3) Stronger attacker primitives (FAI)
#   4) Reproduce the 5-attacker shared spike
#
# Usage:
#   scripts/run_followup_experiments.sh [--dry-run]

SWEEP=scripts/run_adversarial_separate_attacker_addrs_sweep.sh
OUTBASE=results/followup_adversary
DRY_RUN=""
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN="--dry-run"

VICTIM_CORES="0,1,2"
ATTACKER_CORES="3,4,5,6,7,8"
SEED_CORES="0,1,2"

# Common: higher victim-reps and more replicates for tighter CIs
VICTIM_REPS=200000
ATTACKER_REPS=20000000
REPLICATES=10

echo "=========================================="
echo "  Experiment 1: Original (FAI victim, CAS attacker) — higher power"
echo "=========================================="
$SWEEP \
  --victim-cores "$VICTIM_CORES" \
  --attacker-cores "$ATTACKER_CORES" \
  --attacker-core-sweep "1,2,3,4,5,6" \
  --seed-cores "$SEED_CORES" \
  --replicates "$REPLICATES" \
  --victim-test FAI \
  --attacker-test CAS \
  --victim-reps "$VICTIM_REPS" \
  --attacker-reps "$ATTACKER_REPS" \
  --output-dir "$OUTBASE/fai_victim_cas_attacker" \
  $DRY_RUN

echo ""
echo "=========================================="
echo "  Experiment 2: CAS victim, FAI attacker — contended victim + strong attacker"
echo "=========================================="
$SWEEP \
  --victim-cores "$VICTIM_CORES" \
  --attacker-cores "$ATTACKER_CORES" \
  --attacker-core-sweep "1,2,3,4,5,6" \
  --seed-cores "$SEED_CORES" \
  --replicates "$REPLICATES" \
  --victim-test CAS \
  --attacker-test FAI \
  --victim-reps "$VICTIM_REPS" \
  --attacker-reps "$ATTACKER_REPS" \
  --output-dir "$OUTBASE/cas_victim_fai_attacker" \
  $DRY_RUN

echo ""
echo "=========================================="
echo "  Experiment 3: TAS victim, FAI attacker — TAS is most contention-sensitive"
echo "=========================================="
$SWEEP \
  --victim-cores "$VICTIM_CORES" \
  --attacker-cores "$ATTACKER_CORES" \
  --attacker-core-sweep "1,2,3,4,5,6" \
  --seed-cores "$SEED_CORES" \
  --replicates "$REPLICATES" \
  --victim-test TAS \
  --attacker-test FAI \
  --victim-reps "$VICTIM_REPS" \
  --attacker-reps "$ATTACKER_REPS" \
  --output-dir "$OUTBASE/tas_victim_fai_attacker" \
  $DRY_RUN

echo ""
echo "=========================================="
echo "  Experiment 4: Reproduce 5-attacker spike — 20 replicates, shared focus"
echo "=========================================="
$SWEEP \
  --victim-cores "$VICTIM_CORES" \
  --attacker-cores "$ATTACKER_CORES" \
  --attacker-core-sweep "4,5,6" \
  --seed-cores "0" \
  --replicates 20 \
  --victim-test FAI \
  --attacker-test CAS \
  --victim-reps "$VICTIM_REPS" \
  --attacker-reps "$ATTACKER_REPS" \
  --output-dir "$OUTBASE/spike_reproduce_4_5_6_attackers" \
  $DRY_RUN

echo ""
echo "=========================================="
echo "  Experiment 5: L2-sibling attackers — cores sharing L2 should show more interference"
echo "=========================================="
# On most Intel/AMD: cores 0&1 share L2, 2&3 share L2, etc.
# Put victims on 0,2,4 (each on a different L2) and attackers on their
# L2-siblings 1,3,5 plus non-siblings 6,7,8 for comparison.
$SWEEP \
  --victim-cores "0,2,4" \
  --attacker-cores "1,3,5,6,7,8" \
  --attacker-core-sweep "1,2,3,4,5,6" \
  --seed-cores "0" \
  --replicates "$REPLICATES" \
  --victim-test FAI \
  --attacker-test CAS \
  --victim-reps "$VICTIM_REPS" \
  --attacker-reps "$ATTACKER_REPS" \
  --output-dir "$OUTBASE/l2_sibling_topology" \
  $DRY_RUN

echo ""
echo "All experiments complete. Results in: $OUTBASE/"
echo ""
echo "To aggregate CSVs across experiments:"
echo "  head -1 $OUTBASE/fai_victim_cas_attacker/summary_by_attacker_threads.csv"
echo "  for d in $OUTBASE/*/; do tail -n+2 \"\$d/summary_by_attacker_threads.csv\"; done"
