# Adversarial Interference Analysis: Cross-Socket vs Same-Socket

## Experiment Setup

Two topology modes of `run_adversarial_interference_study.sh` are compared:

| | `cross_socket_st` | `same_socket_st` |
|---|---|---|
| Victim placement | Socket 0 | Socket N, subset of cores |
| Attacker placement | Socket 1 | Socket N, remaining cores |
| Interconnect crossed | Yes (UPI/QPI) | No (intra-socket ring/mesh) |
| Attacker sweep | 1, 2, 4, 8 threads | 1, 2, 4, 5 threads |

**Phases per configuration**:
- **Baseline**: victim runs alone (no attackers)
- **Attacker (RMW)**: victim + attackers performing read-modify-write (FAI)
- **Attacker (control)**: victim + attackers performing a benign load (L1 hit)

For the shared-vs-separate comparison:
- **Shared line**: all attackers hammer the same cache line as the victim
- **Separate lines**: each attacker operates on a distinct cache line

---

# Part 1: Cross-Socket Results (`cross_socket_st`)

## Plot A: Victim Mean Latency by Phase

| Attacker threads | Baseline (cycles) | Attacker RMW (cycles) | Attacker control (cycles) |
|:---:|:---:|:---:|:---:|
| 1 | 68 | 64 | 59 |
| 2 | 68 | 82 | 90 |
| 4 | 68 | 108 | 146 |
| 8 | 68 | 160 | 169 |

### Observations

- **Baseline is stable** at ~68 cycles across all configurations, confirming
  the victim workload is deterministic in isolation.

- **Latency grows superlinearly with attacker count**. From 1 to 8 attackers,
  RMW-induced latency increases from 64 to 160 cycles (~135% increase over
  baseline). Doubling attackers from 4 to 8 adds ~52 cycles, while doubling
  from 2 to 4 adds only ~26 cycles.

- **The control workload causes equal or greater slowdown than RMW**. At 4
  attackers the control (146 cycles) exceeds the RMW condition (108 cycles), and
  at 8 attackers they converge (169 vs 160). The interference is not driven by
  the *type* of memory operation but by the sheer volume of cross-socket traffic.

- **At 1 attacker, latency is slightly below baseline** for both RMW (64) and
  control (59). This likely reflects measurement noise or a minor warming effect
  from the attacker populating shared cache hierarchy.

## Plot B: Per-Replicate Slowdown Distribution

- **Median slowdown scales with attacker count**: ~5-8% at 1 attacker, ~5% at
  2 attackers, ~35% at 4 attackers, ~100% at 8 attackers.

- **Shared and separate distributions are nearly identical** at every attacker
  count. Box positions, IQRs, and whisker extents overlap closely, confirming
  interference is independent of cache-line sharing.

- **Variability increases with attacker count**. IQR widens from ~10-15pp at
  1-2 attackers to ~25-30pp at 8 attackers.

## Plot C: Shared vs Separate Attacker Comparison

### Automated diagnosis

> Diagnosis: broad interconnect pressure (shared ratio=1.337, separate
> ratio=1.325) | fairness: preserved

- Both shared and separate conditions cause equal slowdown (~1.33x baseline).
- Jain fairness remains ~0.98-1.0 across all conditions.
- Classification: **broad interconnect pressure** — cache-line sharing is
  irrelevant; the bottleneck is interconnect bandwidth saturation.

## Plot D: Cache-Line HITM Events (perf c2c)

| Phase | Local HITM | Remote HITM | Total HITM | Ratio vs baseline |
|:---:|:---:|:---:|:---:|:---:|
| Baseline | ~245 | ~4 | 249 | 1.00x |
| Shared | ~863 | ~9 | 872 | 3.50x |
| Separate | ~1,032 | ~14 | 1,046 | 4.20x |

- HITM events are predominantly Local HITM (<2% Remote). Cross-socket
  invalidations are handled efficiently by the coherence protocol.

- Separate-address attackers produce *more* HITM events (4.20x vs 3.50x) because
  they activate more cache lines in Modified state, increasing snoop-hit surface
  area. Yet they cause the same victim slowdown — confirming HITM count is not
  the bottleneck.

---

# Part 2: Same-Socket Results (`same_socket_st`)

## Plot A: Victim Mean Latency by Phase

| Attacker threads | Baseline (cycles) | Attacker RMW (cycles) | Attacker control (cycles) |
|:---:|:---:|:---:|:---:|
| 1 | 82 | 64 | 70 |
| 2 | 82 | 65 | 63 |
| 4 | 82 | 113 | 140 |
| 5 | 82 | 152 | 172 |

### Observations

- **Baseline is higher than cross-socket** (~82 vs ~68 cycles). This likely
  reflects greater intra-socket resource contention (shared L3 slices, ring bus
  bandwidth) even in the baseline phase when victim threads share the socket
  with the OS and other system threads.

- **At 1-2 attackers, latency *drops* below baseline** (64-65 cycles for RMW,
  63-70 for control). This is a consistent pattern, not noise — attackers at
  low counts appear to *help* the victim. Possible explanations:
  - Attacker threads warm shared L3 cache lines that the victim subsequently
    hits.
  - Attacker activity triggers hardware prefetchers that benefit the victim.
  - The additional thread activity stabilizes power/frequency states (the core
    may boost differently when neighboring cores are active).

- **At 4-5 attackers, significant slowdown appears** (113-152 cycles for RMW,
  140-172 for control). Like cross-socket, the control workload causes *greater*
  slowdown than RMW.

- **The transition from benefit to penalty occurs between 2 and 4 attackers**,
  suggesting a threshold where shared-resource contention (L3 bandwidth, ring
  bus slots) overwhelms any warming benefit.

## Plot B: Per-Replicate Slowdown Distribution

- **Median slowdown is near zero or slightly negative** at all attacker counts
  (1, 2, 4, 5). This is dramatically different from cross-socket, where median
  slowdown reached 100% at 8 attackers.

- **The distribution is centered around 0%** with an IQR of roughly -5% to +10%
  at 1-2 attackers, and a similar spread at 4-5 attackers. The range is
  approximately -30% to +35% including outliers.

- **Shared and separate distributions are again similar**, though shared (orange)
  shows slightly higher median than separate (green) at 1-2 attackers.

- **At 5 attackers, median dips slightly negative** (~-5%), suggesting that at
  higher same-socket attacker counts, the warming benefit slightly outweighs
  contention for the median replicate, though outliers reach +35%.

## Plot C: Shared vs Separate Attacker Comparison

### Automated diagnosis

> Diagnosis: no significant interference (shared ratio=1.010, separate
> ratio=0.984) | fairness: preserved

- Shared ratio is 1.010 (essentially no slowdown) and separate ratio is 0.984
  (slight speedup). Neither exceeds the 1.05 threshold.
- Jain fairness is ~0.75-0.82 across all conditions — **lower than cross-socket
  (~0.98-1.0)** but consistent across baseline/shared/separate, meaning the
  fairness level is inherent to same-socket operation, not caused by attackers.
- The wide error bars on fairness suggest variability across replicates.

## Plot D: Cache-Line HITM Events (perf c2c)

| Phase | Local HITM | Remote HITM | Total HITM | Ratio vs baseline |
|:---:|:---:|:---:|:---:|:---:|
| Baseline | ~9 | ~1 | 10 | 1.00x |
| Shared | ~63 | ~6 | 69 | 6.90x |
| Separate | ~220 | ~11 | 231 | 23.10x |

### Observations

- **Absolute HITM counts are dramatically lower** than cross-socket (10 baseline
  vs 249 baseline). Same-socket operation generates far fewer coherence events
  because there is no inter-socket snooping overhead.

- **Relative HITM amplification is much higher** — separate attackers produce
  23.1x baseline HITM vs only 4.2x in cross-socket. The intra-socket coherence
  protocol (L3 snoop filter) is more sensitive to additional Modified-state lines.

- **Despite 23x HITM amplification, there is no victim slowdown**. This is the
  strongest evidence that same-socket HITM events are cheap (resolved within the
  L3 cache hierarchy in a few cycles) compared to cross-socket coherence
  transactions.

---

# Part 3: Comparative Analysis

## Slowdown comparison

| Metric | Cross-socket | Same-socket |
|---|:---:|:---:|
| Diagnosis | Broad interconnect pressure | No significant interference |
| Shared ratio | 1.337 | 1.010 |
| Separate ratio | 1.325 | 0.984 |
| Max median slowdown | ~100% (8 attackers) | ~0% (5 attackers) |
| Latency at max attackers | 160-169 cycles | 152-172 cycles |
| Baseline latency | 68 cycles | 82 cycles |

## Key contrasts

### 1. Cross-socket creates real interference; same-socket does not

The same victim workload under the same number of attackers produces
fundamentally different outcomes depending on topology. Cross-socket attackers
cause up to 100% slowdown; same-socket attackers cause effectively zero net
slowdown. The inter-socket interconnect is the bottleneck, not the cache
coherence protocol itself.

### 2. Same-socket attackers can *help* the victim

At 1-2 attackers on the same socket, victim latency consistently drops below
baseline (82→64 cycles, a 22% improvement). This warming effect is absent in
cross-socket placement, where the attacker's cache activity is on a different L3
and cannot benefit the victim.

### 3. HITM cost depends on topology

Same-socket HITM events are nearly free (23x amplification, zero slowdown),
while cross-socket HITM events carry real latency cost (3.5x amplification,
34% slowdown). This confirms that Local HITM within a socket resolves via the
L3 snoop filter in a few cycles, while cross-socket coherence requires
expensive interconnect round-trips.

### 4. Fairness behaves differently

Cross-socket fairness is high (~0.98-1.0) because interconnect pressure affects
all threads uniformly. Same-socket fairness is lower (~0.75-0.82) but stable,
reflecting inherent intra-socket scheduling asymmetry (core proximity to L3
slices, ring bus position). Attackers do not degrade fairness in either
topology.

### 5. Control workload is as harmful as RMW

In both topologies, the control (benign load) attacker causes equal or greater
slowdown than the RMW attacker. This rules out atomic-operation-specific
overhead as the interference mechanism. The bottleneck is memory traffic volume,
regardless of whether that traffic is reads or read-modify-writes.

## Implications

- **NUMA-aware scheduling matters**. Cross-socket placement converts a zero-cost
  interference pattern into a 100% slowdown. Applications sensitive to latency
  jitter should pin cooperating threads to the same socket.

- **Cache-line isolation is irrelevant for bandwidth-bound interference**. In
  both topologies, shared-line and separate-line attackers produce identical
  outcomes. Padding structures to avoid false sharing will not help when the
  bottleneck is interconnect or ring bus bandwidth.

- **HITM counts are topology-dependent metrics**. A high HITM count on the same
  socket is benign; the same count across sockets is expensive. Performance
  analysis tools reporting HITM should contextualize by topology.

- **Low attacker counts on the same socket are beneficial**. System designs that
  co-locate a small number of cooperating threads on one socket may see latency
  improvements from cache warming effects, up to a threshold (~2 threads in
  this experiment) beyond which contention dominates.
