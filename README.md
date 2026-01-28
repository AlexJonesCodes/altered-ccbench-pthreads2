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
