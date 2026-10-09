# Bounded pytest evaluation (#1434)

## Problem and authority

Pytest is serial after the preceding runtime optimizations. Issue #1434 and epic
#1429 require a measured adoption decision, including a valid measured NO-GO.
The external charter is [WORK:SCOPE](https://github.com/randomparity/hmc-mcp/issues/1434#issuecomment-6072269590).
The campaign approved the unchanged exclusions and temporary native measurement
surface. Complexity is M, fixed denominator 250; this is the full-spec lane.
ADR [0208](../../adr/0208-bounded-pytest-evaluation.md) records the experiment decision.

## Approach

Evaluate pytest-xdist 3.8.0, the current stable release checked on
[PyPI](https://pypi.org/project/pytest-xdist/) on 2026-10-08, with exactly two
workers and `--dist=loadfile --max-worker-restart=0`. The existing
[pytest-cov integration](https://pytest-cov.readthedocs.io/en/latest/xdist.html)
combines worker data; verify that behavior against actual totals and a split
coverage fixture before trusting it. Keeping files together minimizes changes
to module fixture semantics. Serial pytest remains the reference and fallback.
No automatic CPU-count selection or production parallel default is introduced
by this experiment. No existing ownership transition is needed.

Use one temporary stdlib measurement harness for local and native hosted runs.
It launches the supplied command with filtered coverage/pytest overrides,
retains complete logs, records monotonic wall time and samples aggregate RSS
across its descendant forest every 100 ms. Linux subreaper ownership retains
orphaned descendants for cleanup, including nested sessions. RSS is a sampled
high-water observation, includes shared pages in each process, and can miss
short peaks; it is neither maximum individual RSS nor unique physical memory.
Record sample period and peak process count. An isolated local cgroup additionally
bounds memory and CPU; do not describe physical RAM as its effective allowance.
Timeout and interruption terminate/reap the owned forest, with TERM then KILL.
Harness errors or surviving processes invalidate the sample rather than passing.
Record candidate survivors before containment cleanup; containment does not prove
production runner cleanup. Defer additional SIGINT during bounded teardown.
Reap adopted children while the measured command is still active, excluding its
direct Popen child so that only Popen consumes that command’s exit status. Invoke
the harness through `uv run --no-sync` to preserve the installed console-script PATH.

## Execution and success

1. Bootstrap with `just setup`. Pin xdist as a dev dependency during evaluation
   (`uv add --dev --no-sync 'pytest-xdist==3.8.0'`, then `just setup`); record lock
   identity. Both candidate modes use that identical environment. Inspect nested
   subprocess tests before enabling the plugin; preserve app extras.
2. Prove the harness reports a failing command, timeout and interrupt and cleans
   nested descendants. Prove sum-of-process RSS with two resident allocations.
   Run existing runner/coverage-gate tests under two workers with `--no-cov`
   only for this focused check, plus explicit port/file/environment and split
   coverage probes. These synthetic checks are fitness evidence, not suite proof.
3. Before full comparisons, sort the existing six sharing-mode parameters in
   `tests/unit/test_documents.py`; the campaign approved this exact cause fix after
   an actual worker collection mismatch. Prove identical identities/count across
   different hash seeds and stable ordering after the correction. This test-only
   correction may remain even when parallel execution is rejected.
4. Compare the complete configured suite serial and two-worker modes on the
   integrated source. Keep JUnit test identities/counts and coverage JSON per run;
   compare statement and branch denominators, covered totals, statuses and skips.
   A failed run is a result, never a successful timing. No exclusions, retries of
   failed tests, floor changes or denominator changes make a candidate eligible.
5. Profile normal local resources and a real 1 CPU / 2 GiB / no-swap cgroup,
   then native amd64 and arm64 Ubuntu 24.04/Python 3.11. Start with one pair per
   profile; where eligible take a second pair in reversed order. Two pairs is
   the initial limit. A third needs named variance uncertainty and root review.
   Stage fitness first: a reproducible disqualifying failure ends good-path
   repetitions, but still obtain measured native evidence and disclose omitted
   comparisons. Never repeat the old #1430 baseline.
6. The temporary PR-only workflow is restricted to the owned branch, uses
   contents:read, no secrets and existing pinned setup actions. Its explicit
   checkout selects the PR head SHA so local/native samples share one candidate
   tree; ordinary production CI retains its existing checkout. It changes no
   ordinary native verify/wheel leg. Its logs and summary carry tested SHA,
   versions, resource context and sample results. Always retain normalized JSON
with hashed test identities/statuses and hashed per-file coverage summaries,
including on failure. Retrieve that artifact for final comparisons; do not upload
raw private logs, JUnit or coverage reports.
7. Publish the measured decision and limits. Adoption requires stable complete
   runs, preserved gate/isolation/lifecycle and repeatable wall-time benefit at
   adequate resources. If that holds, propose the minimal production runner
   implementation and review that concrete design before landing parallelism.
   Otherwise retain serial operation and record a NO-GO, not a universal claim
   that parallel pytest cannot work. Remove evaluation-only workflow, harness,
   tests and dependency in a separate commit before final delivery.

## Failure model

Actors are repository contributors and trusted native CI jobs executing the
configured offline test suite. Assets are gate integrity, test completeness,
resources and cleanup of owned test processes. No live HMC is contacted.
The experiment handles nonzero commands, coverage failures, worker crashes,
TERM-resistant children, nested sessions, timeout and SIGINT. SIGKILL of the
measurement supervisor cannot emit a report; an interrupted/missing sample is
invalid and its enclosing cgroup/job is responsible for final OS cleanup.
Sampling uncertainty and hosted cache/queue variance are accepted measurement
limits and are reported, not interpreted as exact memory or guaranteed speedup.
Other optimizations belong to #1430/#1431/#1432/#1433/#1435; unrelated test
remediation requires the campaign owner's reassessment. Product API, weaker
coverage/security, target/version removal and hardware floors remain excluded.

## Validation and delivery

Every executable experiment contract has focused tests and a controlled fault
that makes its assertion fail. Full suite samples retain the exact 90.5% combined
coverage floor, branch denominator and all collected tests. Local code publication
requires `just verify` and separate pinned all-files hooks; final prose-only publication uses relevant
document guards. Ordinary actual CI still covers eight native verification legs,
eight installed-wheel legs and two library jobs. The final evidence report lists
commands, SHAs, environment, cache conditions, measurement limits and all failures.
No fresh test asserts report prose. No claimed aggregate saving sums components.
