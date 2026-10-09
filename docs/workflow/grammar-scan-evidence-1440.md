# Grammar scan evidence — issue #1440

The candidate preserves the scanner's two roots, three flag categories and
existing assertions. It passes invocation-owned parsed modules to its consumers
and shares one AST node list among literal-selection exclusions.

## Comparable focused measurements

Before source: `157238ba10bcc6198596dca07e79b9c2658ec280`.
After source: the test-module blob `ec98e819328a8dbab99ad800834f01a39ede0618`.
Both use the same source inventory (263 Python files), locked environment,
CPython 3.11.15, Linux x86_64 and 48 scheduler-affinity CPUs. Other full local
verification was paused throughout these serial samples. Existing venv,
uv/download and OS caches were retained; there was no cache eviction or extra
warm-up. CPU quota, memory limits and load were not measured; these are focused
local observations, not a guaranteed speedup or new hardware requirement.

Each sample ran the complete module with the identical command:

```sh
uv run --no-sync pytest tests/unit/test_i_record_grammar.py -q --no-cov
```

A monotonic timer surrounded the command, including launch and collection.
The before module had 75 cases; the after module includes 18 additional proof
cases (93 total). All six commands exited 0. Coverage was disabled equally for
this focused comparison; the unchanged package coverage gate remains in
`just verify`. These samples are not comparable to the coverage-enabled whole
suite's eight call durations reported by #1430.

| Sample | Before seconds | After seconds |
| --- | ---: | ---: |
| 1 | 22.728 | 13.838 |
| 2 | 21.549 | 13.614 |
| 3 | 21.895 | 13.872 |
| Median | 21.895 | 13.838 |

The measured median fell by 8.058 seconds (36.8%). This supports retaining the
change in this setting; it does not establish full-suite or hosted savings.

## Repeated-work instrumentation

Separate untimed runs wrapped `Path.read_text`, `ast.parse` and `ast.walk`
without changing their return values or exceptions. Read/parse counts include
only paths beneath the two declared roots. Walk counts include yielded nodes.
Wrappers cover setup through teardown of each of the eight original scan cases,
so fixture work is included. Instrumentation was absent from the timing runs.
Both instrumented module runs passed, before 75 cases and after 93.

| Work across eight original scan cases | Before | After |
| --- | ---: | ---: |
| Source reads | 2,630 | 2,104 |
| Source parses | 2,630 | 2,104 |
| AST walk calls | 114,972 | 31,341 |
| Yielded AST nodes | 24,187,378 | 9,402,154 |

The known-site test reads/parses each source once instead of once per category
(789 → 263). The seven other cases still obtain fresh snapshots individually.
Each literal selection walks its subtree once instead of four times. Remaining
walks and the actual selection/payload predicates are retained.

## Fault and freshness proof

The new tests use temporary Python source roots and invoke the actual recurrence
assertions. All three flag categories reject a builder bypass, an uninspected
literal ending at the flag, and a hoisted command literal. A source set missing
known sites fails the existing equality assertion. Module/class/function prose,
f-string fragments and exact diagnostic labels remain excluded.

Two parametrized fixture invocations use distinct temporary roots and require
fresh reads from both; repeated consumers within one invocation share their
snapshot. Independent scans observe changed source and changed roots. Malformed
source raises `SyntaxError`; an unreadable read boundary, stubbed with
`PermissionError`, propagates that exception. Corrected files are read afresh.

The new traversal proof first failed against the original implementation for
all three flags (`4 != 1` walks), then passed. A controlled change to module
fixture scope caused the second invocation to fail (`0 != 2` fresh reads).
Restoring function scope returned the focused proof set to green. No production
source or hardware was changed for these faults.

## Provenance and boundaries

This is the operator-approved #1440 grammar-scan follow-up from #1430's baseline,
in campaign `87e4772566c1-6a015497-d377-4262-a9b5-eca0113ddfc0`.
The earlier report is immutable at PR #1437 commit
`9d1fef221fba85a1f1cb46434a7d58461502161c`,
`docs/workflow/verification-timing-baseline-1430.md`; its measured source was
`cd005939a85897308d96e5351a9d2e7e2ad948b8`. It motivated this investigation and
was not reused as the comparable before sample.

ADR 0045/0061's bounded literal analysis remains unchanged: element-wise command
assembly remains outside its reach. A source edit during one invocation becomes
visible on the next invocation. Production grammar/API/live hardware and the
independent #1431–#1435 optimization scopes remain excluded. Native amd64/arm64
and Python 3.11–3.14 targets, inventory and coverage requirements are unchanged.
