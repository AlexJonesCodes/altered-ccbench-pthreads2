altered-ccbench
=======

This is an altered version of ccbench, the code of which was written by:

* Website             : http://lpd.epfl.ch/site/ccbench
* Author              : Vasileios Trigonakis <vasileios.trigonakis@epfl.ch>
* Related Publications: ccbench is a part of the SSYNC synchronization suite
  (http://lpd.epfl.ch/site/ssync):  
  Everything You Always Wanted to Know about Synchronization but Were Afraid to Ask,   
  Tudor David, Rachid Guerraoui, Vasileios Trigonakis (alphabetical order),   
  SOSP '13 - Proceeding of the 24th ACM Symposium on Operating Systems Principles

## Wins tracking notes (CAS_UNTIL_SUCCESS)

The benchmark reports per-thread "wins" via the `race_try_win` helper, which
increments a per-thread counter the first time a repetition is claimed. For
`CAS_UNTIL_SUCCESS` (test ID 33), that claim happens only after a thread
successfully completes its CAS loop. If no thread executes the CAS-until-success
path, or the winner-claim is never reached, the reported wins will remain zero.

Common reasons to see zero wins in the output/logs include:

* The test ID is not actually `CAS_UNTIL_SUCCESS` on the threads you expect
  (e.g., a `-t`/`-x` shape mismatch, or using a single test ID with many threads
  where only role 0/1 run the CAS-until-success path).
* The seed core and thread layout do not put contenders into the CAS-until-success
  loop (e.g., seed core excludes all intended workers, or the run uses a test
  mode that bypasses the CAS-until-success switch entirely).
* Log parsing expects the exact "Group … wins" lines; if those lines are absent,
  downstream CSVs will show zero wins even though no wins were recorded.

## Retry dominance sweep helper

For experiments that rotate the seed (pinned) core and per-thread backoff levels,
use `scripts/retry_dominance_sweep.sh`. The script accepts a core list, rotates
the seed core across runs, assigns different backoff maxima per thread, and
outputs per-run logs plus a summary CSV of average wins per backoff level.

This uses the new `--backoff-array` option (e.g., `-A "[1,2,4,8]"`) to supply
per-thread backoff caps. The array length must match the total thread count.


## Seed rotation protocol

The seed core (`-b`) determines which thread holds the cache line in modified
state at the start of each repetition. By default, the seed rotation script
(`scripts/run_seed_rotation_wins.sh`) cycles the seed across every core in the
contending set, producing one complete run per seed position.

Rotating the seed matters because initial cache-line placement can bias results.
The thread whose core already holds the line in modified state may enjoy a
latency advantage over the others, skewing win counts, fairness metrics, or
latency distributions in ways that look like a real architectural effect but are
actually an artifact of the starting condition. By repeating the experiment with
each core as the seed, you can separate effects that track the seed (and
therefore reflect cache-line ownership) from effects that are stable across seed
positions (and therefore reflect interconnect topology, protocol arbitration, or
other structural properties of the machine).

The dedicated collection script extracts per-thread win counts and Jain fairness
index (both including and excluding the seed thread) for each seed position,
enabling direct comparison of the seed thread's win share against the baseline
average.

## Adversary test: attackers on separate addresses

To test whether unfairness/slowdown still appears when attackers do **not**
share a cache line, use `scripts/run_adversarial_separate_attacker_addrs.sh`.

The script runs three phases with the same victim setup:

1. `victim_baseline` (no attackers),
2. `victim_plus_shared` (each attacker core runs in its own process, all
   processes hammer the same fixed line),
3. `victim_plus_separate` (each attacker core runs in its own process on a
   distinct fixed address).

This isolates same-line coherence pressure from broader interference effects.

This keeps process count constant across shared/separate attacker phases,
so differences are less likely to be caused by process-model artifacts.
If the separate-address phase still slows the victim or hurts fairness,
interference is broader than a single cache-line hotspot.

Example:

```bash
scripts/run_adversarial_separate_attacker_addrs.sh \
  --victim-cores "0,2,4,6" \
  --attacker-cores "8,10,12,14" \
  --victim-test CAS \
  --attacker-test FAI
```

A ready-to-run example wrapper is also provided at
`scripts/run_adversarial_separate_attacker_addrs_example.sh`.


For deeper studies (seed rotation + adversary intensity trend), use:
`scripts/run_adversarial_separate_attacker_addrs_sweep.sh`.

Example:

```bash
scripts/run_adversarial_separate_attacker_addrs_sweep.sh \
  --victim-cores "0,2,4,6" \
  --attacker-cores "8,10,12,14,16,18,20,22" \
  --attacker-core-sweep "1,2,4,6,8" \
  --seed-cores "0,2,4,6" \
  --replicates 3 \
  --victim-test CAS \
  --attacker-test FAI \
  --output-dir results/adversarial_separate_attacker_addrs_sweep
```

This produces:

* `raw_phase_results.csv` (one row per run/phase),
* `summary_by_attacker_threads.csv` (averages over seeds/replicates), and
* `trend_separate_minus_shared.csv` (difference trend as adversary cores increase).

Results are written to `results/adversarial_separate_attacker_addrs/summary.csv`.
Run metadata (including auto-fallback decisions) is written to
`results/adversarial_separate_attacker_addrs/run_meta.txt`.

If you have seen victim segfaults with static addresses, try:

```bash
scripts/run_adversarial_separate_attacker_addrs.sh \
  --victim-cores "0,2,4,6" \
  --attacker-cores "8,10,12,14" \
  --victim-test CAS \
  --attacker-test FAI \
  --victim-fallback-addr 0x700000200000 \
  --fail-stats
```

The script now probes the victim setup and automatically falls back from
`--fixed-victim-addr static` → `--victim-fallback-addr` → `none` on crash.
The script prints `INFO: phase start ...` / `INFO: phase done ...` messages, so
a long run after fallback warnings is expected and not treated as a hang.
Interpretation:

* `latency_ratio_vs_baseline > 1.0` means higher latency (slower), while `< 1.0` means lower latency (faster).
* Lower `jain_fairness` means victim thread progress became less fair.
* `latency_delta_pct_vs_baseline` is the percent change in mean latency vs baseline (positive = slower, negative = faster).
* `effect_vs_baseline` is a categorical summary (`slower`, `faster`, `neutral`).
* If `victim_plus_separate` remains slow/unfair, interference is broader than
  a single shared-line hotspot.

## Adversarial atomic-vs-atomic experiment helper

Use `scripts/run_adversarial_lock_vs_fai.sh` to model an adversarial setup with:

* **Victim group**: threads running a victim atomic primitive
  (default `CAS`).
* **Attacker group (RMW)**: threads running heavy atomic `FAI` on a **different
  fixed cache line**.
* **Control attacker**: weaker atomic control workload (default `LOAD_FROM_L1`)
  to help separate heavy-RMW coherence effects from generic execution interference.

The script now includes:

* synchronized victim/attacker starts (FIFO barrier, no `sleep`-based start),
* single long-running attacker run per victim phase (avoids burst gaps),
* attacker intensity sweeps via `--attacker-thread-sweep`,
* optional SMT sibling safety check (`--enforce-no-smt-siblings`), and
* automatic `--fail-stats` preflight fallback: if a 1-rep probe crashes with
  SIGSEGV, the script disables `--fail-stats` for the run and records that in
  `run_meta.txt`, and
* automatic victim-address fallback: if the victim preflight still segfaults
  with `--fixed-victim-addr static`, the script retries using
  `--victim-fallback-addr` (default `0x700000200000`) and records the fallback
  in `run_meta.txt`; if that still segfaults, victim fixed-address mode is
  auto-disabled for the run, and
* a `summary.csv` with per-phase victim metrics (mean/fairness/success), and
* an optional flat CSV export path via `--results-csv` (default `results/adversarial_lock_vs_fai_results.csv`).

Example:

```bash
scripts/run_adversarial_lock_vs_fai.sh \
  --victim-cores "0,2,4,6" \
  --attacker-cores "8,10,12,14" \
  --attacker-thread-sweep "1,2,4" \
  --victim-test CAS \
  --attacker-test FAI \
  --control-test LOAD_FROM_L1
```
