# hmcpctl clean cutover

Issue #898. Decision record: [ADR 0165](../../adr/0165-hmcpctl-clean-cutover.md).

## Problem

The active project identity is split across the distribution `hmc-mcp`, Python package
`hmc_mcp`, console command `hmc-mcp`, configuration directory `hmc-mcp`, runtime and audit
labels, ownership stamps, and snapshot/media-type identifiers. The maintainer selected
`hmcpctl` before the first release and confirmed that no persistent managed systems or saved
snapshots require legacy compatibility. A partial rename would leave users and maintainers
choosing between two names and would make future removal harder.

## Architecture

The change is one atomic repository-wide replacement with three deliberately different
classes of text. Active code, packaging, tests, scripts, generated references, and current
guidance use `hmcpctl`/`hmcpctl`. Historical decision evidence retains the spelling that was
true when recorded. GitHub URLs retain `randomparity/hmc-mcp` until the separately owned
repository rename because links must work when this PR lands.

The source package moves from `src/hmc_mcp` to `src/hmcpctl`; active imports move with it.
There is no forwarding package. `hmcpctl.api` keeps ADR 0118's exact six exports, so the
facade contract changes only its import path. The application extra and library dependency
split are unchanged.

## Components

### Packaging and execution

`pyproject.toml` owns the distribution name, `hmcpctl = "hmcpctl:main"` console entry,
Hatch package selection, type-check overrides, and coverage source. `uv.lock` is regenerated
with `uv lock`, not edited by hand. Wheel validation proves that a library-only environment
imports `hmcpctl.api` without app dependencies, while an app installation exposes only the
`hmcpctl` command and loads every CLI group and the MCP server.

A checked-in Python wheel-smoke harness owns the fresh-environment orchestration. It uses `uv`
subprocesses and interpreter-derived environment paths rather than Bash arrays or POSIX-only
`bin/python` assumptions, so the same contract can be invoked from zsh and CI without importing
the checkout's source tree.

The package move is mechanical but complete: production imports, test imports and patch
targets, scripts, CI snippets, module-name assertions, and source-path checks all use
`hmcpctl`. The old module and command must be absent from built artifacts.

### Local configuration and access policy

`hmcpctl.config` resolves only the platform-native `hmcpctl/config.toml`; `config_dir()`
returns only the `hmcpctl` directory. `access-policy.toml` remains beside it through the
existing `config_dir()` ownership. No runtime fallback or conflict logic is added. Upgrade
guidance gives executable Linux/XDG, macOS, and PowerShell directory moves and requires the
MCP client command to change before restart. The transaction stops the old process, refuses to
merge into an existing destination directory, moves the whole directory, uninstalls the old
`hmc-mcp` tool, installs `hmcpctl` from an explicit local checkout, verifies that the new command
works and the old command is absent, updates the MCP command, and only then restarts.

### Audit and ownership authorization

The default audit memento, agent-derived memento, reserved agent identifier, audit fallback
identity, emitted ownership stamp, ownership parser, caller-token parser, malformed-prefix
check, diagnostics, and server instructions all use `hmcpctl`. The behavior around the prefix
does not change: a well-formed current-owner stamp permits, a foreign owner denies, a malformed
new-prefix stamp denies, and an unrelated or old-prefix description is unowned. Overrides keep
their existing audit behavior.

### Snapshots and media types

Version 1 snapshots use `hmcpctl.lpar-snapshot`. The runtime-placement, affinity-score, and
minimum-affinity-policy media types use `application/vnd.hmcpctl...`. Validation remains strict:
the old discriminator and media types fail through the existing actionable error boundary.
No conversion or second schema version is introduced because the operator confirmed that no
saved snapshots exist.

### Documentation and operator cutover

Current README, contribution, security, changelog, configuration, CLI, MCP, API, development,
live-test, recipe, environment, and generated tool-reference material uses the new product,
command, import, and config path. Generation-backed files are changed only through their
recipes. Historical ADR bodies remain unchanged except ADR 0165; old spellings in history are
not evidence of an incomplete active rename.

The cutover guide distinguishes actions in this PR from operator actions. This PR documents
source installation and the config/MCP-client upgrade without claiming PyPI availability.
The operator checklist names GitHub repository rename/link verification, project-URL updates
after the rename, PyPI trusted-publisher/token setup, clean artifact build, publication, and
post-publication installation verification. It performs none of them.

## Data flow

At install time, build metadata selects `src/hmcpctl` and creates the `hmcpctl` console script.
At startup, the CLI or MCP process resolves only the new config and access-policy paths, then
uses unchanged `HMC_*` precedence. Each HMC request sends the new audit identity. LPAR creation
writes the new ownership stamp; later mutation reads the same grammar before applying the
unchanged authorization decision. Snapshot capture writes the new discriminator and media types;
validation accepts only those exact version-1 values.

Upgrade is deliberately outside runtime: the operator stops the old process, checks that the new
config destination is absent, moves the config directory, uninstalls the old tool, installs the
new source distribution, verifies the command cutover, updates MCP client command entries, and
starts `hmcpctl`. There is no moment when one process merges old and new state.

## Failure model

**Actors and deployments.** A local operator using the CLI; an MCP client launching stdio or
HTTP service mode; a Python library consumer; CI building and installing artifacts; and the
maintainer performing the later GitHub/PyPI cutover. Live HMC verification runs only against the
operator's temporary test system under `docs/live-testing.md`.

**Invariants and assets at stake.** The wheel exposes one package and command; the six-name
facade remains exact at its new path; `HMC_*` behavior and library/app dependency separation do
not change; configuration and policy resolve beside each other; ownership authorization remains
fail-closed for foreign and malformed new stamps; snapshot validation remains strict; public
instructions do not name an unavailable package or repository.

**Accepted failure classes.** Old imports and command invocations fail because the clean cut is
the requested outcome. Startup before the operator moves the config directory reports missing
configuration/policy through existing errors. Old-prefix ownership descriptions are unowned and
old snapshot identifiers are unsupported; accepted because the operator confirmed that only
disposable systems exist and no snapshots are retained. GitHub URLs contain the old repository
slug until external rename; accepted because they remain live and the operator owns cutover.

**Covered elsewhere.** HMC operation semantics remain under their existing ADRs and tests.
Credential secrecy and `HMC_*` validation remain under existing configuration controls. Package
publication and repository settings are owned by the operator checklist. Recovery from live tests
is owned by `scripts/live_test_recovery.py` and `docs/live-testing.md`.

## Threat model

**Boundary inventory.** No new entry point is added. Existing trust boundaries are renamed: CLI
arguments and MCP calls enter the same handlers; config and access-policy TOML enter the same
parsers from a new directory; HMC descriptions enter the ownership parser under a new prefix;
snapshot JSON enters the strict version-1 parser under new identifiers; CI installs the renamed
wheel and executes its console entry.

**Actor model.** MCP callers remain untrusted within the existing access-policy boundary. Local
operators and library callers remain trusted to supply configuration. The HMC is authenticated
through the existing session. Snapshot text and HMC description text remain untrusted inputs.
The maintainer is trusted for the later repository and package-registry operations.

**Control per boundary.** Access-policy loading remains fail-closed and beside `config.toml`.
Ownership regex bounds owner syntax; new-prefix malformed tokens deny, foreign owners deny, and
override remains explicit and audited. Snapshot Pydantic models, exact discriminators, byte and
nesting limits, and no-echo diagnostics remain unchanged apart from literals. Release validation
inspects wheel contents, metadata, dependency separation, importability, and console entry. CI
retains pinned actions and its amd64/arm64 × Python 3.11–3.14 matrix.

**Explicitly out of scope.** Compatibility with old names and persisted identifiers is rejected
by operator decision. Repository rename and PyPI credentials/publication remain operator actions.
No authentication, permission grant, TLS default, secret handling, or network exposure changes.

## Success

1. The built distribution is `hmcpctl`; its only project console entry is `hmcpctl`; its source
   and wheel contain `hmcpctl` and contain no `hmc_mcp` package.
2. `hmcpctl.api` exports exactly ADR 0118's six names, and application modules remain absent from
   a library-only installation.
3. Active Python imports, test patch targets, scripts, and CI snippets use `hmcpctl`; importing
   `hmc_mcp` and invoking `hmc-mcp` fail after installation.
4. Platform-native config and access-policy resolution use only the `hmcpctl` directory on Linux,
   macOS, and Windows while preserving existing `HMC_*` precedence.
5. Audit values and ownership stamps use `hmcpctl`; owned, foreign-owned, malformed, unowned, and
   override cases retain their current decisions under the new grammar; old stamps are unowned.
6. Version-1 snapshot capture and validation use the new discriminator and media types; old
   identifiers are rejected without adding conversion behavior.
7. Current documentation and generated references use the new product surfaces, except valid
   old repository URLs and explicit former-name upgrade prose; historical ADR bodies are intact.
8. Upgrade instructions provide executable source-install, config-directory move, and MCP-client
   edits, refuse a destination collision, remove the old tool, and verify the old command is
   absent; the external-cutover checklist does not claim those actions already occurred.
9. `just verify`, `uv run --no-sync prek run --all-files`, fresh library/app wheel proofs, CLI
   help, MCP handshake, and the hosted matrix are green for the delivered commit. Dedicated-arm
   row `fixture ownership stamp` (or its read-back-confirmed variant) is PASS for that commit,
   generated evidence cites it, and `live_test_recovery.py` exits 0.

## Validation

- Success 1–3: focused tests in `tests/test_project_metadata.py`,
  `tests/unit/test_public_api.py`, `tests/test_release_artifacts.py`, and
  `tests/validate_release_artifacts.py`; fresh wheel environments exercise both extras modes.
- Success 4: focused platform-path and profile/access-policy tests in
  `tests/unit/test_config.py`, `tests/app/test_cli_config.py`, and access-policy suites.
- Success 5: ownership and audit tests in `tests/unit/test_ownership.py`,
  `tests/lpar/test_power_ownership_guard.py`, configuration tests, and the dedicated arm's required
  PASS ownership-stamp row.
- Success 6: focused snapshot model, capture, and surface tests in
  `tests/unit/test_snapshot.py`, `tests/unit/test_snapshot_capture.py`, and
  `tests/app/test_snapshot_surfaces.py`.
- Success 7–8: existing project-metadata, generated-doc, tool-doc, environment-variable, recipe,
  and documentation assertions. POSIX directory moves are exercised in disposable directories;
  PowerShell commands are exercised when `pwsh` is available and otherwise carry an explicit
  unavailable-host note. Prose itself is reviewed rather than snapshot-tested.
- Success 9: the exact repository guardrails and CI checks named in the criterion, the portable
  wheel-smoke harness, and the exact dedicated-arm PASS/recovery observations above. No material
  changed executable contract uses `task-test-not-applicable`; operator-only GitHub/PyPI actions
  are non-executable in this PR and are verified as an explicit checklist without claiming
  completion.
