# ADR 0128: L5 launches the live server through the module entry point

## Status

Accepted

## Context

`tests/app/test_authorization_audit_live.py::test_a_failed_sink_leaves_the_denial_unchanged`
(L5) is the live suite's only fd-2-closed launch. It runs
`/bin/sh -c "exec <console script> serve … 2>&-"` to prove that losing the audit sink costs
the record and nothing else (ADR 0040, ADR 0043).

`uv` writes that console script with a direct `#!<interpreter>` shebang while the venv
interpreter path is short enough, and with a `/bin/sh` polyglot trampoline past that, because
a long `#!` line is not portable. Issue #709 reports the boundary at 127 characters,
reproduced there at 63 characters (direct) and 128 characters (trampoline); this record does
not re-derive that number, and it is `uv`'s private threshold rather than a project setting.

The trampoline breaks L5, but not for the reason it first appears, and **not on every
shell**. `sh` opens the script file to read it, and with fd 2 closed by the redirection the
lowest free descriptor is 2 — so on a shell that leaves that descriptor open across the
`exec`, the script file itself becomes fd 2, read-only. The interpreter then inherits a
valid-but-unwritable stderr instead of no stderr at all: writes buffer without raising, and
`Py_FinalizeEx`'s flush of the standard streams fails, which is exit 120.

Whether the descriptor survives the `exec` is the shell's choice, and the two common
`/bin/sh` implementations differ. Measured with `exec ls -l /proc/self/fd` as the script
body, under `sh -c "exec <script> 2>&-"`:

| `/bin/sh` | fd 2 after the exec |
|---|---|
| bash 5.x (measured on Fedora) | the script file — the failure is reachable |
| dash (measured in `debian:stable-slim`) | free; `ls` takes it — no failure |

`/proc/self/fd` is Linux-only, so those two rows are the measurements. By inference the bash
row covers the distributions whose `/bin/sh` is bash — Arch, RHEL, and macOS, whose bash 3.2
in POSIX mode was not measured — and the dash row covers Debian and the `ubuntu-24.04` CI
runners.

So dash opens the script exactly as bash does; it simply does not leave that descriptor on
fd 2. Measured on this branch (uv 0.12.1, `/bin/sh` → bash, CPython 3.11.15, x86_64), with a
uv-form trampoline written over this venv's own console-script body:

| Launch under `bash -c "exec … 2>&-"` | `os.fstat(2)` | `sys.stderr` | Exit on a stderr write |
|---|---|---|---|
| Trampoline | open, mode 100755 | real stream | 120 |
| Direct `#!` shebang | `EBADF` | `None` | the program's own status |
| `<python> -m <module>` | `EBADF` | `None` | the program's own status |

So under the trampoline L5 does not even inject the condition it means to inject: the sink is
not absent, it is unwritable. The child dies before emitting a JSON-RPC frame and L5 reports
"the server closed stdout without answering (exit=120, no stderr captured)" — which reads as
the server refusing to start under a closed sink, exactly the production behaviour L5 exists
to disprove, rather than as an install-path artifact. It is latent for ordinary checkouts,
and unreachable on all eight `ci` legs at *any* path length, because those runners' `/bin/sh`
is dash. It bites a developer whose `/bin/sh` is bash — Fedora, Arch, RHEL, macOS — once a clone lands
deep enough, and this repository's agent workflows routinely produce such clones (issue #709).
CI cannot catch it, which is most of why it is worth recording rather than only fixing.

## Decision

**L5 alone launches the server as `[sys.executable, "-P", "-m", "hmc_mcp"]`**, through a new
`src/hmc_mcp/__main__.py` whose only content is a call to the existing `hmc_mcp.main`.

The shell stays: `2>&-` is a POSIX shell redirection and is how L5 closes fd 2 at all, so the
blinded spawn remains `/bin/sh -c "exec … 2>&-"` with only the command prefix changed. What
changes is that `sh` now execs an interpreter **by path** and opens no script file, so fd 2
stays closed through the exec at any install path and under either shell. The invariant this
record establishes is therefore *no intermediate process may leave a descriptor open on fd 2
across the exec of the interpreter* — not "no shell", which is false here, and not "opens no
file", which is too broad: dash opens the script and the launch still works.

`-P` keeps the child's current working directory off `sys.path`, which `-m` would otherwise
prepend. That makes the child resolve `hmc_mcp` exactly as the parent does, so the fixture's
guard below binds the process that actually runs.

Both of L5's spawns consume one `command` list — the reference run that keeps stderr open and
the blinded run — so the two runs the test compares still differ only in the sink.

The other four live tests keep the shared `server_binary` console-script fixture unchanged.
L5 gets its own fixture, which asks that same interpreter where `hmc_mcp` resolves and
asserts the answer is inside this checkout.

`__main__.py` parses no arguments. Delegation is its whole body.

## Consequences

- L5 gives up the console script **under a closed sink**. That intersection is exercised
  reliably *below* `uv`'s threshold — on this checkout and on all eight `ci` legs, whose
  runner paths are short — and is unreachable above it. The loss is accepted: a closed stderr
  is not a supported production configuration, and L1–L4 and
  `test_an_audit_level_of_warning_suppresses_permits_but_keeps_denials` keep the shipped
  console script covered with the sink open.
- `python -m hmc_mcp` becomes a working invocation, recorded in `CHANGELOG.md`. It is held
  equivalent to `hmc-mcp` **by construction, not by a standing test**: `__main__.py`
  reproduces the generated console script's whole body — `sys.exit(main())`, written here as
  `raise SystemExit(main())` — and adds nothing, so the two cannot diverge without adding
  logic to it, which exclusion (a) forbids. The exit-status wrapper is part of that
  construction and not an optional flourish: a bare `main()` would diverge the moment
  `hmc_mcp.main` returned a status instead of raising, a change entirely inside `main` that
  exclusion (a) would not catch. L5 exercises the invocation twice on every run. No dedicated
  equivalence test is added — that would be new surface the frozen charter does not carry —
  and it is recorded as a follow-up candidate instead.
- The two invocations are not textually identical. Typer takes its program name from Click,
  which derives it from how the process was started, so it reports `python -m hmc_mcp` rather
  than `hmc-mcp` — in every `Usage:` line and usage-error message, not only in `--help`.
- `tests/app/test_fail_closed_startup.py`'s L1 docstring argues from "there is no
  `__main__.py`". The shim falsifies that sentence, so this change corrects it; the test's
  own behaviour is untouched.
- The new fixture reproduces one half of `server_binary`'s guarantee — the source is inside
  this checkout — while the environment half is carried instead by `sys.executable` being the
  interpreter running pytest. `server_binary` pins the environment and reaches source
  identity through the editable install; the new guard pins source identity directly.
- `tests/test_package_version.py::test_wheel_metadata_and_package_contents` already asserts
  every `src/hmc_mcp/*.py` appears in the wheel, so the shim cannot be dropped from a build
  without that test failing.
- **The fd-2 invariant is recorded, not enforced, and is structurally invisible to CI.** All
  eight legs run dash, so a future edit returning L5's launch to a script-based one would be
  green on every leg and red only on a contributor's bash-as-`/bin/sh` host — reported there
  with the ADR 0040/0043 regression signature rather than a harness one, which is the original
  #709 trap. Accepted rather than closed: a bash-as-`/bin/sh` CI leg or a standing
  shell-behaviour test is new surface this change does not carry.
- `python -m hmc_mcp` needs the `app` extra, exactly as the console script does; a bare
  library wheel ships the entry point and tracebacks on the same missing import. That is not a
  regression — before this change the same invocation failed with `No module named
  hmc_mcp.__main__` — but it is why the changelog entry is conditioned rather than absolute.

## Considered & rejected

- **Launch the interpreter without a module entry point** —
  `[sys.executable, "-P", "-c", "from hmc_mcp import main; main()"]`, or a private helper
  under `tests/`. verified: equally shebang-free and opens no script file, so it fixes the
  failure identically; it also avoids the wheel coupling and the changelog entry. judgment:
  rejected because the entry point is wanted for its own sake — `python -m hmc_mcp` is the
  invocation users expect a Python package to answer, issue #709 proposes it by name, the
  charter's permitted surface admits it, and inline source in a spawn argument is the less
  readable of the two.
- **Skip L5 when the console script is a trampoline** (issue #709's option 2: read the first
  line, `pytest.skip` naming the path length). verified: issue #709 records that this
  repository's agent workflows routinely clone into deep temporary directories, so the skip
  would fire precisely where the proof is most likely to run. judgment: a green run that
  silently proved nothing is a worse failure mode than the misleading red it replaces.
- **Parse the trampoline and exec the interpreter it names.** verified: the generated script's
  `exec` line does name this venv's interpreter, so it is recoverable. judgment: a bespoke
  parser for another tool's generated output, over a format `uv` does not document as stable,
  to reach the interpreter `sys.executable` already names.
- **Change how the console script is generated** — a shorter install path, or a different
  install mode. verified: `justfile:15` generates it via
  `uv sync --locked --extra app --link-mode copy`; the trampoline is `uv`'s own portability
  fallback. Excluded from this change by the operator, and it trades a test-harness
  constraint for a build-tooling one.
- **Do nothing.** verified: the measured table above. judgment: the failure is deterministic
  past the threshold and misattributes a harness artifact to the server, and it is invisible
  to CI — so it lands on whoever is least equipped to recognise it.
