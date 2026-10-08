# Capability fingerprint measurements (#1433)

These measurements cover the capability gate and report on 2026-10-08. They
attribute this invocation-local optimization, not overall verification or CI time.

## Inputs and method

Baseline commit: `8e3d29970f03e549b279978a9998d65fd0498f22`.
Baseline validator blob: `8e672874217a7cec222d340b040bfe66c1175011`.
Candidate measurements used the same checkout after design-only commit
`cb73bd7aeaec4b0ef31815b4f539facd9b1b9f78`, with validator blob
`28143444ae67be0155a717268cc4f1f92e78c986` and test blob
`ee8f1d2d93746b28c877ea38349e92f7bdab5c37` as uncommitted inputs.
Maturity blob `c110af662cb750ca3be547e302afa2853e8864df` and packaged projection
blob `e32de9854727304750e124ec6f99126760866f42` were unchanged.

Environment: Linux x86_64, CPython 3.11.15, 48 logical CPUs available to the
process, approximately 251 GiB RAM, uv 0.12.19 and just 1.57.0. Dependencies came
from `just setup` and the unchanged lock. Each sample launched the actual recipe
as a fresh subprocess, timed with `time.monotonic`; stdout and stderr were
captured and compared. Samples ran serially in the campaign's exclusive local
measurement slot: three gate runs, then three report runs, before and after.
Filesystem and interpreter bytecode caches were not flushed. This is a warm
checkout comparison, without a cold-cache or hosted-runner claim.

## Results

| Recipe | Before samples (seconds) | After samples (seconds) | Median before → after |
| --- | --- | --- | --- |
| `just capability-inventory` | 12.005, 10.678, 10.698 | 3.435, 3.610, 3.640 | 10.698 → 3.610 |
| `just verification-report` | 20.422, 20.329, 20.683 | 3.705, 3.481, 3.429 | 20.422 → 3.481 |

Every sample exited zero; the corresponding stdout and stderr were identical.
The gate median fell 66.3%; the report median fell 83.0%. Three samples establish
this local observation, not a minimum improvement on other hardware.

Separate instrumentation of the actual report counted 214 fingerprint calls
before and 27 after, for the same 27 handler modules. The baseline cProfile run
spent 29.024 of 30.792 profiled seconds in fingerprints; these instrumented
numbers are excluded from the timing table. Parsed-import caching was unnecessary
for this bounded first change and was not added.

At fixed time `2026-10-08T22:00:00Z`, the old and new implementations produced
identical states for 163 registry operations and identical 28,574-byte runtime
projections, SHA-256
`e029dda519e2db4edc11288d66cccb48c5f84c9850012a0bbfa5b7cac72ff3ec`.

## Behavioral proof and limits

The focused script suite passed 154 tests. New work-count tests first failed on
the original implementation's two calls for a shared module and two report
phases. Tests cover root isolation, source/import/dependency changes between
invocations, missing modules, unreadable files, report-only age expiry, exact
report diagnostics and stale exit status. Four temporary faults were rejected:
a process-global cache (four failures), swallowed file reads (one), projection
age expiry (one), and ignored stale exit status (one). Faults were removed and
the focused suite passed again.

The closure walker, digest and metadata are unchanged. There is no live HMC run
or evidence promotion. Full verification, hooks and native CI are separate
shipping requirements; these measurements do not substitute for them.
