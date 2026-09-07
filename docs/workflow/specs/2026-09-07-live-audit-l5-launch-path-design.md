# L5's launch path is independent of the install path

Issue [#709](https://github.com/randomparity/hmc-mcp/issues/709).
Decision: [ADR 0128](../../adr/0128-l5-module-entry-point-launch.md).

## Problem

`test_a_failed_sink_leaves_the_denial_unchanged` (L5) is the live suite's only fd-2-closed
launch. It execs the `hmc-mcp` console script under `2>&-`. `uv` writes that script with a
direct `#!` shebang while the venv interpreter path is at most 127 characters and with a
`/bin/sh` trampoline past it, and the trampoline does not survive being exec'd with no
stderr: the child exits 120 before answering, so L5 reports "the server closed stdout without
answering". That reads as the ADR 0040 / ADR 0043 regression L5 exists to disprove, but it is
an install-path artifact. Latent for ordinary checkouts and CI; it bites deep temporary
clones, which agent workflows routinely produce.

## Scope

- `src/hmc_mcp/__main__.py` (new) — calls `hmc_mcp.main`, parses nothing.
- `tests/app/test_authorization_audit_live.py` — a new L5-only fixture returning
  `[sys.executable, "-m", "hmc_mcp"]`, asserting the `hmc_mcp` the child imports resolves
  inside this repository checkout (the guarantee `server_binary` carries for the others). L5's two
  spawns both use it. The module docstring records the constraint beside the POSIX-only note.
- `tests/test_entry_points.py` (new) — `python -m hmc_mcp` and `hmc-mcp` are one program.
- `CHANGELOG.md` — `python -m hmc_mcp` under Unreleased.

Out of scope per the frozen charter: L1–L4 and the `run_a` spawn path; the shared
`server_binary` fixture; console-script generation; Windows support; argument parsing in
`__main__.py`. No deferrals carried.

## Success

1. L5 passes whatever the venv interpreter path length, launching through the module entry
   point rather than the console script.
2. Both L5 spawns use that one mechanism, so the runs it compares differ only in the sink.
3. The L5 fixture fails when `hmc_mcp` resolves outside this repository checkout.
4. `python -m hmc_mcp` runs the same CLI as `hmc-mcp`.
5. The module docstring records the launch constraint.
6. `just verify` and `uv run --no-sync prek run --all-files` are green.

## Validation

- **L5 under a closed sink, module-launched** (success 1, 2). Mode: focused-test. Case
  `tests/app/test_authorization_audit_live.py::test_a_failed_sink_leaves_the_denial_unchanged`.
  Red: with the fixture returning a uv-form `/bin/sh` trampoline the child exits 120,
  reproduced on this branch. Green:
  `uv run --no-sync pytest tests/app/test_authorization_audit_live.py::test_a_failed_sink_leaves_the_denial_unchanged`.
- **The checkout guard** (success 3). Mode: focused-test. Same fixture; red by resolving the
  origin outside the checkout and observing the assertion; green as above.
- **Entry-point equivalence** (success 4). Mode: focused-test.
  `tests/test_entry_points.py::test_module_entry_point_matches_the_console_script`. Red:
  without `__main__.py`, `No module named hmc_mcp.__main__`. Green:
  `uv run --no-sync pytest tests/test_entry_points.py`.
- **Docstring and changelog wording** (success 5). Mode: task-test-not-applicable. Prose with
  no executable consumer; `tests/unit/test_changelog.py` validates only the released-version
  heading, and asserting on wording would test the sentence rather than the behaviour.
- **Repository guardrails** (success 6). Mode: focused-test. Green: `just verify` and
  `uv run --no-sync prek run --all-files`, both exit 0.
