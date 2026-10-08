# Live-harness typecheck boundary (#1416)

## Problem
The configured ty include checks only `src/hmcpctl`. At main `7061511b`,
explicit live-harness files produce twelve diagnostics: capture wrappers (2),
inventory fields (4), provisioning resources (1), and vmedia values (5).
The nullable provisioning selector diagnostic was resolved by #1388/#1436.
## Scope
Authority: [WORK:SCOPE q1416-4995ad54](https://github.com/randomparity/hmc-mcp/issues/1416#issuecomment-6066005008).
One implementation unit adds only `scripts/live_test` to the existing include,
updates `tests/test_ci_pipeline.py`'s exact boundary, and repairs the four
reported modules. Existing result helpers and scenario owners remain; no
ownership transition, caller migration, or compatibility path is needed.
Capture wrappers preserve their input callable type through a callable-bound
TypeVar and a localized cast of the existing spy. Preserve forwarding,
recording, redaction, exceptions and restoration. At existing numeric/identity
uses, apply localized erased casts expressing the pre-existing field contract;
retain `int()` conversion, truthy fallback order, defaults and error behavior.
Do not replace conversions with `int(str(...))`, coerce identifiers, or add
validation. Make the existing media-name exclusion of `None` visible to ty;
widen the boot-token container annotation or type its fallback string to avoid
invariance without changing splitting or order. No diagnostic suppressions.
Top-level scripts typing and tests typing are excluded (owner: none), as
approved by the operator on 2026-10-07. Matching structural test edits and
affected behavioral tests remain permitted. No new setting, dependency,
public interface, scenario, maturity claim or numbered decision is needed.
The operator selected the include boundary; runtime mechanisms stay intact.
### Failure model

- Actors and deployments: local developers and the existing two-architecture,
  four-Python CI matrix; harness behavior is exercised with offline fakes.
- Invariants and assets: existing request forwarding, capture privacy, result
  selection/conversion, media ownership and boot cleanup behavior remain intact.
- Accepted failure classes: malformed numeric fields retain existing conversion
  exceptions; dynamic response values are not newly validated in this typing repair.
- Covered elsewhere: live hardware outcomes belong to existing arm owners;
  top-level scripts and test typing remain outside this approved boundary.

## Success
The configured include equals `["src/hmcpctl", "scripts/live_test"]`.
The twelve current diagnostics disappear without disabling rules. Existing
capture, baseline/provisioning and vmedia behavior remains unchanged.
Verification is offline; no HMC contact occurs.

## Validation
- Boundary: Mode: focused-test. Update
  `tests/test_ci_pipeline.py::test_quality_tools_are_pinned_with_a_strict_type_boundary`;
  red: its new include assertion rejects current configuration; green:
  `uv run --no-sync pytest tests/test_ci_pipeline.py --no-cov -q`.
- Type repairs: Mode: focused-test. `just typecheck` after widening the include
  must first report the twelve diagnostics, then pass after repairs; retain
  timed first-run findings and demonstrate the widened gate rejects a controlled
  live-harness type violation before restoring it.
- Runtime preservation: Mode: focused-test. Existing forwarding/restoration,
  conversion/fallback, owned-media and boot-order cases run through
  `uv run --no-sync pytest tests/scripts/test_capture.py tests/test_live_runner.py tests/test_live_vmedia_arm.py --no-cov -q`;
  add a focused missing edge case only where needed, proving it bites.
  Finish with `just verify`, `uv run --no-sync prek run --all-files`, and the
  required CI matrix and wheel checks before merge handoff.
