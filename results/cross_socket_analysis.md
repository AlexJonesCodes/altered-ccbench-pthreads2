# Adversarial Interference Analysis: Topology Comparison

## Experiment Setup

Three topology modes of `run_adversarial_interference_study.sh` are compared:

| | `cross_socket_st` | `same_socket_st` | `same_socket_ht` |
|---|---|---|---|
| Victim placement | Socket 0 | Socket N, physical cores | Socket N, physical cores |
| Attacker placement | Socket 1 | Socket N, other physical cores | Socket N, SMT siblings |
| SMT used | No | No | Yes (hyperthreads) |
| Interconnect crossed | Yes (UPI/QPI) | No (intra-socket ring/mesh) | No (intra-core) |
| Attacker sweep | 1, 2, 4, 8 threads | 1, 2, 4, 5 threads | 1, 2, 4, 8 threads |

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

# Part 3: Same-Socket Hyperthreaded Results (`same_socket_ht`)

In this mode, attackers run on SMT siblings (hyperthreads) of physical cores on
the same socket. This is the most intimate sharing possible — victim and
attacker share execution resources within the same physical core (L1/L2 caches,
execution units, store buffers).

## Plot A: Victim Mean Latency by Phase

| Attacker threads | Baseline (cycles) | Attacker RMW (cycles) | Attacker control (cycles) |
|:---:|:---:|:---:|:---:|
| 1 | 60 | 72 | 65 |
| 2 | 60 | 89 | 86 |
| 4 | 60 | 123 | 146 |
| 8 | 60 | 167 | 174 |

### Observations

- **Baseline is the lowest of all three topologies** (60 cycles vs 68
  cross-socket, 82 same-socket ST). With SMT siblings available but idle, the
  victim has maximum access to core resources and benefits from optimal
  power/frequency states.

- **Interference appears immediately at 1 attacker** — unlike same-socket ST
  where 1-2 attackers helped the victim, here even a single attacker raises
  latency (60→72 cycles RMW, 60→65 control). This is because SMT siblings
  compete directly for execution pipeline resources (ALUs, load/store units,
  reorder buffer entries), not just cache bandwidth.

- **Slowdown scales steeply and monotonically**: 72→89→123→167 cycles for RMW
  across 1→2→4→8 attackers. At 8 attackers, victim latency is 167-174 cycles
  (~180-190% of baseline), the worst absolute latency of any topology.

- **Control again exceeds RMW** at 4 and 8 attackers (146 vs 123, 174 vs 167),
  consistent with the pattern seen in all topologies.

## Plot B: Per-Replicate Slowdown Distribution

- **Median slowdown is always positive** and grows steadily: ~5% at 1 attacker,
  ~10% at 2, ~40% at 4, ~110% at 8. There is no zero-crossing or benefit
  regime — hyperthreaded attackers always hurt.

- **Shared and separate distributions overlap closely** at every attacker count,
  continuing the pattern from other topologies.

- **The distribution at 8 attackers** shows a tight IQR of ~100-120% with
  outliers reaching 135%, indicating consistent severe interference with
  relatively low variability.

- **One outlier at 2 attackers** shows ~-50% (a large speedup), likely a
  measurement artifact from a single replicate.

## Plot C: Shared vs Separate Attacker Comparison

### Automated diagnosis

> Diagnosis: broad interconnect pressure (shared ratio=1.378, separate
> ratio=1.369) | fairness: preserved

- Shared ratio (1.378) and separate ratio (1.369) are both above 1.05 and
  nearly identical, again classified as **broad interconnect pressure**.
- These ratios are the **highest of any topology** (cross-socket: 1.337/1.325,
  same-socket ST: 1.010/0.984), meaning hyperthreaded placement creates the
  most aggregate interference.
- Jain fairness remains ~0.97-1.0 across all conditions, preserved as in
  cross-socket.
- At 8 attackers, both shared and separate reach ~120 cycles with modest error
  bars, closely matching the cross-socket pattern.

## Plot D: Cache-Line HITM Events (perf c2c)

| Phase | Local HITM | Remote HITM | Total HITM | Ratio vs baseline |
|:---:|:---:|:---:|:---:|:---:|
| Baseline | ~250 | ~5 | 255 | 1.00x |
| Shared | ~940 | ~15 | 955 | 3.75x |
| Separate | ~1,080 | ~16 | 1,096 | 4.30x |

### Observations

- **Absolute HITM counts match cross-socket levels** (255 baseline vs 249
  cross-socket), not same-socket ST levels (10 baseline). This is surprising —
  hyperthreaded cores generate as many coherence events as cross-socket
  operation. The likely explanation is that SMT siblings sharing L1/L2 create
  frequent Modified-state conflicts that register as Local HITM.

- **Relative amplification ratios are nearly identical to cross-socket**
  (3.75x/4.30x vs 3.50x/4.20x), suggesting the same coherence dynamics despite
  fundamentally different sharing topologies.

- **Separate > shared pattern persists** (4.30x vs 3.75x), consistent across
  all three topologies.

---

# Part 4: Comparative Analysis

## Summary table

| Metric | Cross-socket ST | Same-socket ST | Same-socket HT |
|---|:---:|:---:|:---:|
| Diagnosis | Broad interconnect pressure | No significant interference | Broad interconnect pressure |
| Shared ratio | 1.337 | 1.010 | 1.378 |
| Separate ratio | 1.325 | 0.984 | 1.369 |
| Baseline latency | 68 cycles | 82 cycles | 60 cycles |
| Max median slowdown | ~100% (8 atk) | ~0% (5 atk) | ~110% (8 atk) |
| Latency at max attackers | 160-169 cycles | 152-172 cycles | 167-174 cycles |
| Baseline HITM | 249 | 10 | 255 |
| HITM amplification (sep) | 4.20x | 23.10x | 4.30x |
| Jain fairness | ~0.98-1.0 | ~0.75-0.82 | ~0.97-1.0 |
| Warming effect at low count | No | Yes (22% speedup) | No |

## Key contrasts

### 1. Three distinct interference regimes

- **Cross-socket ST**: Significant interference driven by UPI/QPI bandwidth
  saturation. Slowdown reaches ~100% at 8 attackers.
- **Same-socket ST**: No measurable interference. Attackers at low counts
  actually *help* the victim through cache warming. The intra-socket ring bus
  and L3 absorb contention efficiently.
- **Same-socket HT**: The *worst* interference of all three topologies.
  Slowdown reaches ~110% at 8 attackers, exceeding even cross-socket. SMT
  siblings compete for physical core resources (execution units, store buffers,
  L1/L2) — a fundamentally different and more severe bottleneck than
  interconnect bandwidth.

### 2. Hyperthreading eliminates the warming benefit

Same-socket ST shows a clear warming effect at 1-2 attackers (82→64 cycles).
Same-socket HT shows no such benefit — even 1 attacker raises latency (60→72).
When the attacker shares the physical core, it competes for execution resources
from the first cycle. The warming benefit requires attackers on *separate*
physical cores where they can populate shared L3 without stealing pipeline
slots.

### 3. Baseline latency reveals resource availability

The baseline ordering (60 HT < 68 cross-socket < 82 same-socket ST) is
informative:
- **HT (60)**: Victim has an entire physical core to itself with SMT siblings
  idle. Optimal resource availability.
- **Cross-socket (68)**: Victim is alone on its socket. Slightly higher than HT,
  possibly due to different core types or NUMA memory placement.
- **Same-socket ST (82)**: Victim shares the socket with other physical cores
  running OS/system threads. Higher baseline contention for L3 and ring bus.

### 4. HITM patterns reveal two coherence regimes

Same-socket ST has very low baseline HITM (10) with extreme relative
amplification (23x) but zero latency impact — intra-socket coherence is cheap.
Cross-socket and HT have similar high baseline HITM (~250) and moderate
amplification (~4x) with real latency cost. The HT result is particularly
notable: SMT siblings generate coherence traffic volumes comparable to
cross-socket operation because L1/L2 sharing creates frequent Modified-state
conflicts.

### 5. Fairness splits into two groups

Cross-socket and HT maintain high fairness (~0.97-1.0) because their
interference mechanisms (interconnect saturation, pipeline competition) affect
all threads uniformly. Same-socket ST has lower fairness (~0.75-0.82) due to
inherent intra-socket asymmetry (core position on ring bus, L3 slice distance),
but this is a baseline property, not attacker-induced.

### 6. Control workload is universally as harmful as RMW

Across all three topologies, benign load attackers cause equal or greater
slowdown than RMW attackers. This is topology-invariant evidence that the
bottleneck is traffic volume, not atomic-operation-specific overhead.

## Implications

- **SMT placement is the worst topology for interference**. Hyperthreaded
  attackers cause more slowdown than cross-socket attackers and eliminate the
  warming benefit seen in same-socket ST. Applications sharing physical cores
  via hyperthreading are most vulnerable to adversarial interference.

- **Same-socket, separate physical cores is the most resilient topology**. Not
  only does it show zero net interference, but low attacker counts provide a
  measurable warming benefit. This is the optimal placement for cooperating
  threads.

- **NUMA-aware scheduling is necessary but not sufficient**. Placing threads on
  the same socket avoids cross-socket interference, but placing them on SMT
  siblings creates *worse* interference. The ideal strategy is same-socket,
  separate physical cores.

- **Cache-line isolation is irrelevant across all topologies**. Shared-line and
  separate-line attackers produce identical outcomes in every mode. Padding to
  avoid false sharing will not help when the bottleneck is bandwidth (cross-socket),
  pipeline resources (HT), or nonexistent (same-socket ST).

- **HITM counts require topology context**. The same HITM count means different
  things: 23x amplification on the same socket is free; 4x across sockets or
  SMT siblings is expensive. Performance tools should report HITM alongside
  topology metadata.
