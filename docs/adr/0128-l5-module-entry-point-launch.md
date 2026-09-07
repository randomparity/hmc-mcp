# ADR 0128: L5 launches the live server through the module entry point

## Status

Accepted

## Context

`tests/app/test_authorization_audit_live.py::test_a_failed_sink_leaves_the_denial_unchanged`
(L5) is the live suite's only fd-2-closed launch. It execs the server under `2>&-` to prove
that losing the audit sink costs the record and nothing else (ADR 0040, ADR 0043).

It launches the `hmc-mcp` console script that `uv sync` generates. `uv` writes that script
with a direct `#!<interpreter>` shebang while the venv interpreter path is at most 127
characters, and with a `/bin/sh` polyglot trampoline past that, because a longer `#!` line is
not portable. The trampoline interposes a shell between the redirection and the interpreter,
and that shell does not survive being exec'd with no stderr.

Reproduced on this branch, with a uv-form trampoline written over this venv's own
console-script body: `sh -c 'exec <trampoline> --version 2>&-'` exits 120 — CPython's "could
not flush a standard stream at finalization" — while `sh -c 'exec <venv>/bin/python -c
"print(1)" 2>&-'` exits 0 and the direct-shebang console script exits with the command's own
status. The child dies before emitting a JSON-RPC frame, so L5 fails as "the server closed
stdout without answering (exit=120, no stderr captured)".

That reads as the server refusing to start under a closed sink — exactly the production
behaviour L5 exists to disprove — rather than as an install-path artifact. It is latent for
ordinary checkouts and for CI, and bites automated clones into deep temporary directories,
which this repository's agent workflows routinely produce (issue #709).

## Decision

**L5 alone launches the server as `[sys.executable, "-m", "hmc_mcp"]`**, through a new
`src/hmc_mcp/__main__.py` whose only content is a call to the existing `hmc_mcp.main`. The
redirection then applies to the interpreter directly, with no shell in between, at any
install path.

Both of L5's spawns use it — the reference run that keeps stderr open and the blinded run —
so the two runs the test compares still differ only in the sink.

The other four live tests keep the shared `server_binary` console-script fixture unchanged, so
the installed console script stays covered by the live suite. L5 gets its own fixture, which
carries `server_binary`'s "*this* checkout, not a foreign build" guarantee forward in the form
the module route admits: it asserts the `hmc_mcp` the child would import resolves inside this
checkout.

`__main__.py` parses no arguments. Delegation is its whole body, so `python -m hmc_mcp` and
`hmc-mcp` cannot diverge.

## Consequences

- L5 no longer proves the installed console script survives a closed sink; it proves the
  interpreter and the server do. L1–L4 and
  `test_an_audit_level_of_warning_suppresses_permits_but_keeps_denials` still launch the
  console script, so what is lost is only the intersection — console script *and* closed
  sink — which no install path could exercise reliably anyway.
- `python -m hmc_mcp` becomes a supported public invocation, recorded in `CHANGELOG.md`. It
  must stay equivalent to `hmc-mcp`; `tests/test_entry_points.py` holds that equivalence.
- Typer reports the program name as `__main__` under `python -m`, so `--help` differs in that
  one string. L5 never reads it, and the equivalence test normalises it.
- `tests/test_package_version.py::test_wheel_metadata_and_package_contents` already asserts
  every `src/hmc_mcp/*.py` appears in the wheel, so the shim cannot be dropped from a build
  without that test failing.

## Considered & rejected

- **Skip L5 when the console script is a trampoline** (issue #709's option 2: read the first
  line, `pytest.skip` naming the path length). verified: the trampoline appears exactly when
  the interpreter path exceeds 127 characters, and issue #709 records that this repository's
  agent workflows routinely clone into deep temporary directories — so the skip would fire
  precisely where the proof is most likely to run. judgment: a green run that silently proved
  nothing is a worse failure mode than the misleading red it replaces.
- **Parse the trampoline and exec the interpreter it names.** verified: the generated script's
  `exec` line does name this venv's interpreter, so it is recoverable. judgment: a bespoke
  parser for another tool's generated output, over a format `uv` does not document as stable,
  to reach the interpreter `sys.executable` already names.
- **Change how the console script is generated** — a shorter install path, or a different
  install mode. verified: `justfile:15` generates it via
  `uv sync --locked --extra app --link-mode copy`; the trampoline is `uv`'s own portability
  fallback, not a project setting. Excluded from this change by the operator, and it trades a
  test-harness constraint for a build-tooling one.
- **Do nothing.** verified: the reproduction in Context. The failure is deterministic past the
  threshold and misattributes a harness artifact to the server.
