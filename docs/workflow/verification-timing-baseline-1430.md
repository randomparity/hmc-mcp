# Verification timing baseline — issue #1430

## Measurement scope and provenance

Local verification ran on clean commit `cd005939a85897308d96e5351a9d2e7e2ad948b8`
(implementation of timing presentation only), based on
`0abbbd86168726268ceb3707119eb748f93edac7`. Three serial full
`just verify-timings` runs were selected before execution. Each executes the
canonical `just verify` graph, including static checks, package-wide branch
coverage, MCP/CLI smoke, build and distribution validation. No gate was removed.

Warm means an existing venv after `just setup`, focused tests and commit hooks,
with existing uv/download and OS caches retained. No cache eviction or extra
full-suite warm-up was performed. No other benchmark ran concurrently.
`time.monotonic()` measured end-to-end command elapsed time; native `just --time`
measured recipe bodies. A parent recipe excludes its dependencies, so the
`static` and `verify` rows below are not aggregate durations.

Historical CI is a separate successful run at a different commit:
[`37795020469`](https://github.com/randomparity/hmc-mcp/actions/runs/37795020469),
`19f8597c978b86907891ccb1e96ade41e6787b44`. Local and hosted durations are not
before/after speedup evidence. Final PR verification and CI are separate checks,
not additional baseline samples.

## Local environment and full runs

Python 3.11.15; Fedora Linux 44 (Workstation Edition); x86_64;
48 logical CPUs visible and 48 available through scheduler affinity;
250.92 GiB physical memory.
Local just 1.57.0 and uv 0.12.19; locked pytest 9.1.1, pytest-cov 7.1.0
and coverage 7.15.4. CPU quota and cgroup memory-limit files were unavailable
in this environment; affinity/host available memory do not prove an unlimited
container allocation. No new hardware floor is inferred.

| Run | Overall seconds | Pytest recipe seconds | Available memory GiB at start | Load 1/5/15 min before → after |
| --- | ---: | ---: | ---: | --- |
| 1 | 639.277 | 549.083 | 53.54 | 1.55/1.33/1.63 → 1.76/1.66/1.64 |
| 2 | 639.986 | 549.535 | 51.50 | 1.76/1.66/1.64 → 2.57/2.42/2.06 |
| 3 | 650.780 | 556.667 | 50.88 | 2.57/2.42/2.06 → 1.86/3.72/3.58 |

Overall median 639.986s; min–max 639.277–650.780s;
sample standard deviation 6.447s (n=3).
Each run exited 0: 8,544 tests passed, measured coverage 95.31%,
configured floor 90.5% with branch measurement retained.

### Recipe-body wall times

| Recipe | Run 1 s | Run 2 s | Run 3 s |
| --- | ---: | ---: | ---: |
| `lint` | 0.020 | 0.021 | 0.022 |
| `format-check` | 0.020 | 0.020 | 0.021 |
| `typecheck` | 0.134 | 0.134 | 0.139 |
| `secrets` | 64.137 | 64.376 | 66.711 |
| `workflow-security` | 0.054 | 0.053 | 0.053 |
| `env-vars` | 0.140 | 0.142 | 0.143 |
| `nicknames` | 0.134 | 0.134 | 0.148 |
| `test-layout` | 0.045 | 0.047 | 0.047 |
| `capability-inventory` | 10.829 | 10.866 | 11.752 |
| `tool-docs-check` | 1.810 | 1.799 | 1.919 |
| `adr-numbering` | 0.040 | 0.039 | 0.039 |
| `doc-freshness` | 1.947 | 1.907 | 2.047 |
| `live-vocabulary` | 3.355 | 3.359 | 3.571 |
| `scenario-gap` | 2.038 | 2.020 | 2.168 |
| `static` | 0.000 | 0.000 | 0.000 |
| `test` | 549.083 | 549.535 | 556.667 |
| `smoke` | 1.774 | 1.779 | 1.704 |
| `build` | 0.815 | 0.800 | 0.782 |
| `verify-artifacts` | 0.133 | 0.140 | 0.134 |
| `verify` | 2.764 | 2.809 | 2.708 |

### Slow test phases

The diagnostic mode retains pytest’s combined top 30 setup/call/teardown
durations, with no minimum cutoff. The ten largest observed entries follow;
a dash means that phase did not appear in that run’s top 30, not zero time.

| Phase and test node | Run 1 s | Run 2 s | Run 3 s |
| --- | ---: | ---: | ---: |
| `call tests/test_ci_pipeline.py::test_dirty_project_commands_do_not_rebuild_editable_metadata` | 86.57 | 89.94 | 90.23 |
| `call tests/unit/test_i_record_grammar.py::test_the_scan_finds_every_known_site` | 13.59 | 13.71 | 13.00 |
| `call tests/unit/test_i_record_grammar.py::test_prose_docstrings_are_excluded_from_selection` | 11.67 | 11.63 | 11.47 |
| `call tests/scripts/test_check_live_vocabulary.py::test_passes_on_the_committed_tree` | 11.35 | 11.42 | 11.60 |
| `call tests/unit/test_i_record_grammar.py::test_no_command_literal_lives_outside_a_function[-a]` | 7.35 | 6.18 | 8.13 |
| `call tests/unit/test_i_record_grammar.py::test_no_command_literal_lives_outside_a_function[-i]` | 7.16 | 7.31 | 6.01 |
| `call tests/unit/test_i_record_grammar.py::test_no_command_literal_lives_outside_a_function[--filter]` | 7.24 | 7.13 | 5.99 |
| `call tests/unit/test_i_record_grammar.py::test_every_site_is_built_by_its_shared_builder[--filter]` | 4.53 | 4.68 | 4.52 |
| `call tests/unit/test_i_record_grammar.py::test_every_site_is_built_by_its_shared_builder[-i]` | 4.60 | 4.62 | 4.49 |
| `call tests/unit/test_i_record_grammar.py::test_every_site_is_built_by_its_shared_builder[-a]` | 4.53 | 4.59 | 4.50 |

## Separate component observations

- Fresh environment `just setup`: **0.785s**, exit 0, at
  `0abbbd86168726268ceb3707119eb748f93edac7`. The venv was absent; uv and OS caches were retained.
  This is not a cold-cache measurement. Python 3.11.15 installed 98 packages.
- Dirty-metadata regression alone: **87.972s** end-to-end;
  pytest reported **86.83s call, 0.39s setup, 0.00s teardown** (rounded).
  Command: `uv run --no-sync pytest --no-cov
  tests/test_ci_pipeline.py::test_dirty_project_commands_do_not_rebuild_editable_metadata
  -q --durations=0 --durations-min=0`. It exercises the full nested hook stack.
  This focused observation had no package-wide coverage; the full runs above did.
  It used the implementation file contents before their commit, with HEAD
  `c07588434ad5683c074999b99742bc98e6b5a35e`; those contents were committed unchanged
  in `cd005939a85897308d96e5351a9d2e7e2ad948b8`. An earlier 86.338s probe failed because
  its copied checkout caught three missing `check=False` arguments in new tests.
  The exact lint findings were fixed before this passing observation; the failed
  attempt is excluded from the baseline and is not a nondeterministic retry.
- Independent `just capability-inventory`: **10.686s**, exit 0.
- Independent `just verification-report`: **20.093s**, exit 0.
  Both ran serially after the three full samples at `cd005939a85897308d96e5351a9d2e7e2ad948b8`
  with the same environment and retained caches. These are one-off observations,
  not distributions or a measured optimization benefit.

| Separate sample | Available memory GiB at start | Load 1/5/15 min before → after |
| --- | ---: | --- |
| Fresh setup | 53.68 | 1.14/1.52/2.23 → not sampled |
| Dirty-metadata | 53.75 | 1.43/1.15/1.67 → 1.28/1.19/1.63 |
| Capability gate | 51.33 | 1.57/3.60/3.54 → 1.48/3.51/3.51 |
| Capability report | 51.31 | 1.48/3.51/3.51 → 1.81/3.45/3.49 |

## Actual native CI baseline

GitHub reports success, 19 executed jobs and one skipped support-drift job.
Run creation to last executed job completion: **948s** (15m48s).
Initial executed-job queue delay: **3s**. Sum of per-job
`started_at − created_at` queue delays: **78s**.
Sum of executed-job `completed_at − started_at` runner time: **6,475s**;
the eight native verification legs account for **6,228s**.
The skipped job’s timestamps are not execution evidence and are excluded.
Dependency waiting is reflected in end-to-end time, not reclassified as queue
delay before a dependent job is created. Parallel job durations must not be
added to obtain end-to-end latency.

| Native leg | Queue s | Job elapsed s | Static interval s | Pytest interval s | Hook step s | Capability interval s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| amd64 / Python 3.11 | 3 | 622 | 67.70 | 460.59 | 69 | 12.64 |
| amd64 / Python 3.12 | 2 | 808 | 87.72 | 604.46 | 87 | 12.49 |
| amd64 / Python 3.13 | 3 | 854 | 104.36 | 615.40 | 103 | 13.58 |
| amd64 / Python 3.14 | 3 | 708 | 105.76 | 464.56 | 105 | 13.39 |
| arm64 / Python 3.11 | 5 | 840 | 88.50 | 636.11 | 88 | 12.48 |
| arm64 / Python 3.12 | 5 | 909 | 92.37 | 699.67 | 92 | 13.61 |
| arm64 / Python 3.13 | 4 | 839 | 88.52 | 634.56 | 88 | 12.71 |
| arm64 / Python 3.14 | 5 | 648 | 89.57 | 438.81 | 90 | 12.73 |

Both architectures used Ubuntu 24.04 runner images; installed CPython versions
were 3.11.16, 3.12.3, 3.13.15 and 3.14.7. CI pinned just 1.58.0 and uv
0.12.10, with setup-uv caching enabled. CPU/memory/load and actual cache-hit
state were not measured in this historical run; no equivalence with local
resources or caches is assumed.

Queue and job elapsed values come from the run/jobs API; hook values from
step timestamps. Static, pytest and capability intervals come from timestamped
command boundaries in the retained successful job logs, so include command-launch
and logging overhead. All eight named release-wheel artifacts were present,
unexpired, and associated with the successful run; each native installed-wheel
smoke leg passed, as did library-wheel smoke and dependency-floor checks.

## Attribution and follow-up boundaries

The dirty-metadata regression, secrets scan and capability gate are observed
costs, not promises of removable work. Their optimization owners remain
#1432, #1431/#1435 and #1433; parallel pytest evaluation remains #1434.
The i-record grammar scan cases also appear among the largest calls.
`tests/unit/test_i_record_grammar.py:772` reparses the source tree for callers
at lines 780, 820, 849 and 915. Investigate that cost separately while preserving
its security recurrence guards and negative controls; no optimization or new
issue is included here. Sample size three and one historical CI run cannot
establish a speedup or performance threshold.
