# L5's launch path is independent of the install path

Issue [#709](https://github.com/randomparity/hmc-mcp/issues/709).
Decision: [ADR 0128](../../adr/0128-l5-module-entry-point-launch.md).

## Problem

`test_a_failed_sink_leaves_the_denial_unchanged` (L5) is the live suite's only fd-2-closed
launch: `/bin/sh -c "exec <console script> … 2>&-"`. Past `uv`'s shebang threshold that
script is a `/bin/sh` trampoline, and `sh` opens it to read it — which with fd 2 closed lands
the script file on fd 2. The interpreter inherits an unwritable stderr rather than none, so
the flush at finalization fails: exit 120, no frame. L5 reports "the server closed stdout
without answering" — the ADR 0040 / ADR 0043 regression it exists to disprove.

## Scope

- `src/hmc_mcp/__main__.py` (new) — calls `hmc_mcp.main`, parses nothing.
- `tests/app/test_authorization_audit_live.py` — a new L5-only fixture returning
  `[sys.executable, "-P", "-m", "hmc_mcp"]`, asserting (by asking that interpreter) that the
  child's `hmc_mcp` resolves inside this checkout. L5 binds one `command` list from it and
  uses it at both spawns. The blinded spawn keeps `/bin/sh -c "exec … 2>&-"`; only the prefix
  changes. The module docstring records the constraint beside the POSIX-only note.
- `tests/app/test_fail_closed_startup.py` — its L1 docstring argues from "there is no
  `__main__.py`"; the shim falsifies that, so the sentence is corrected. Nothing else changes.
- `CHANGELOG.md` — `python -m hmc_mcp` under Unreleased.

L5 gives up the console script *under a closed sink* — exercised below the threshold on all
eight `ci` legs, unreachable above it. Accepted: a closed stderr is not a supported
production configuration, and L1–L4 still cover the console script, sink open.

Out of scope per the frozen charter: L1–L4 and `run_a`; the shared `server_binary` fixture;
console-script generation; Windows support; parsing in `__main__.py`. No deferrals carried.

## Success

1. L5's launch carries no `#!` line and opens no script file, so install-path length cannot
   reach it.
2. Both L5 spawns consume one `command` list, so the runs compared differ only in the sink.
3. The L5 fixture fails when the child's `hmc_mcp` resolves outside this checkout.
4. `python -m hmc_mcp` runs the CLI; L5 exercises it twice on every run.
5. The module docstring records the constraint, `CHANGELOG.md` the invocation, and
   `test_fail_closed_startup.py` no longer asserts there is no `__main__.py`.
6. `just verify` and `uv run --no-sync prek run --all-files` are green.

## Validation

- **L5 under a closed sink** (success 1, 3, 4). Mode: focused-test. Case
  `…live.py::test_a_failed_sink_leaves_the_denial_unchanged`. Red: a uv-form trampoline
  substituted for the fixture's command exits 120; an `hmc_mcp` outside the checkout trips
  the fixture. Green: `uv run --no-sync pytest tests/app/test_authorization_audit_live.py`.
- **One mechanism at both spawns** (success 2). Mode: task-test-not-applicable. Both spawns
  consume one `command` list bound once in the test body, so a second mechanism needs a
  second list — an edit no run distinguishes, since the reference spawn's stderr goes to a
  file and never reaches the closed-fd path.
- **Docstring and changelog wording** (success 5). Mode: task-test-not-applicable. Prose with
  no executable consumer; `tests/unit/test_changelog.py` checks only the released-version
  heading, and asserting on wording tests the sentence, not the behaviour.
