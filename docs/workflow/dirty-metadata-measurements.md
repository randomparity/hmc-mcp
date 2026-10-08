# Dirty metadata regression measurements

Issue #1432, following baseline issue #1430. The narrowed regression retains
fresh tracked-project copies, locked app environment creation, dirty package
metadata, direct lint and an actual configured lint hook. Production hooks and
CI's independent full-hook invocation are unchanged (ADR 0205).

## Focused comparison

Command, run twice serially for each variant:

```sh
/usr/bin/time -p uv run --no-sync pytest \
  tests/test_ci_pipeline.py::test_dirty_project_commands_do_not_rebuild_editable_metadata \
  --no-cov -q
```

The before test is from `836c533203b0300a1e49d8e1e4513ee8547b8769`.
The measured after test has Git blob
`04390bc6389bd8f89483e7642277b20718ef748c`. The test at implementation commit
`92b5c0e18fdc0f785f5c51624d507538fc77d820` has blob
`89fbf746cfac819650102662adda0c27f04b827b`; removing only the two explanatory
comment lines immediately before `hooks = subprocess.run` reproduces the
measured blob. This relation was verified with `git hash-object --stdin`; no
executable statement changed between measurement and commit. Each invocation creates
a fresh temporary project and environment; the shared uv dependency cache was
warm for both variants. Samples ran without another campaign verification job.
Host: Fedora 44, Linux 7.2.7, x86_64, 48 available CPUs, 251 GiB total RAM;
Python 3.11.15, uv 0.12.19, prek 0.5.0, locked project dependencies.
These observations impose no hardware floor and do not predict other targets.

| Variant | Sample | Pytest elapsed | Command wall |
| --- | --- | ---: | ---: |
| Complete dirty-project hooks | 1 | 87.58 s | 89.14 s |
| Complete dirty-project hooks | 2 | 87.13 s | 87.77 s |
| Representative dirty-project hook | 1 | 1.27 s | 1.92 s |
| Representative dirty-project hook | 2 | 1.16 s | 1.78 s |

Median command wall time fell from 88.46 s to 1.85 s (97.9%) in these two
samples. This is a focused test comparison, not full verification or CI timing.
`--no-cov` is the documented focused-test mode; full verification still uses
the unchanged exact 90.5% branch-coverage gate.

## Controlled faults

Each mutation was isolated and restored before the next one. The integration
probes ran the real pytest test, real environment sync and real commands; the
parent exported `UV_NO_SYNC=1` to exercise isolation from CI's ambient setting.

| Fault | Observed detection |
| --- | --- |
| Remove `--no-sync` from the lint recipe | Exit 1 at the direct-lint rebuild assertion; actual editable build output. |
| Remove `UV_NO_SYNC` from the outer hook launcher | Exit 1 at the hook rebuild assertion; actual editable build output. |
| Make the lint hook run `env UV_NO_SYNC=0 uv run ruff check .` | Hook succeeds but pytest exits 1 at the rebuild assertion; verbose hook output exposes the build on stdout. |
| Remove `--no-sync` from the unselected typecheck recipe | Existing exhaustive recipe guard fails. |
| Replace the unselected typecheck hook entry with `uv run ty check` | Existing exhaustive delegation guard fails. |

The first three failures occurred after successful real commands and specifically
at the `Building hmcpctl` assertion, not at setup or exit-code assertions.
Their command wall times were 2.12 s, 2.17 s and 2.19 s respectively. The last
two probes invoked the unchanged structural guards on temporary copies of both
configuration files; restored input passed both guards. No production suppression
or mock hook execution was introduced.

The affected pipeline module passed: 30 tests in 2.65 s. Both complete installed
hook execution and final full verification are separate shipping requirements;
commit-bound results and native CI results are recorded on the pull request.
