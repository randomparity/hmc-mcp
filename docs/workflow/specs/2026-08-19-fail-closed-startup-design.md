# Fail-closed composition and the legacy-equivalent policy generator

Issue: [#225](https://github.com/randomparity/hmc-mcp/issues/225) — part of epic #218.
Decision record: [ADR 0041](../../adr/0041-fail-closed-startup-and-legacy-policy-generation.md).
Builds on [ADR 0036](../../adr/0036-server-access-policy-model.md) (the policy model),
[ADR 0037](../../adr/0037-composition-time-capability-ceiling.md) (the ceiling),
[ADR 0038](../../adr/0038-dispatch-time-connection-scope.md) (connection scope),
[ADR 0039](../../adr/0039-dispatch-time-target-scope.md) (target scope), and
[ADR 0040](../../adr/0040-authorization-audit-events.md) (audit records). The generated
policy's connection list follows [ADR 0030](../../adr/0030-profile-nicknames.md); the
generator command follows [ADR 0007](../../adr/0007-cli-config-commands.md).

## Goal

No MCP application can be composed without an access policy, both transports and both
in-repo scripts reach that one composer, and an operator upgrading an existing deployment
has a non-interactive command that writes a reviewable policy granting exactly what the
unpolicied server used to grant.

## Scope

In scope: `server.create_mcp`'s signature and the removal of the module-level application;
`server._gates`, `_serve_application`, `main_stdio`, `main_http`, and the startup-warning
set; the `--access-policy` requirement on `hmc-mcp serve`; a new `hmc_mcp.legacy_policy`
module and the `hmc-mcp config init-access-policy` command that writes its output;
`scripts/smoke_mcp.py` and `scripts/live_test_runner.py`; and the operator documentation,
including the corrected statements about unpolicied servers in `README.md` and
`docs/authorization-audit.md`.

Out of scope, with owners: the blocking synchronous stderr write whose reach this entry
widens (#269); making the audit level split reachable from `hmc-mcp serve` (#270);
suppressing the traceback panel on a routine denial (#267); accepting any
request-supplied scoped grant (epic #218 requirement 12 — the extension point is recorded
in ADR 0041 and built by nothing here); and the decision procedures of ADR 0038 and
ADR 0039, which this entry uses unchanged.

Deliberately unchanged: `tool_registry.register_tools` and `tool_registry.authorized` keep
their optional `permits` and `authorize` parameters, and `server_permissions.describe`
keeps its `AccessPolicy | None` parameter. ADR 0041 records why.

## Requirements

Each requirement is numbered and testable. R-prefixed identifiers are cited by the plan.

**R1 — Composition requires a policy.** `server.create_mcp(policy: AccessPolicy) -> FastMCP`
has no default for `policy`. `create_mcp()` raises `TypeError` from Python's own argument
binding. `create_mcp(None)` raises `TypeError` from an explicit guard whose message names
`hmc-mcp config init-access-policy`, because an annotation alone does not refuse at runtime
and `None` would otherwise compose an unbounded application.

**R2 — There is no module-level application.** `server.mcp` does not exist;
`from hmc_mcp.server import mcp` raises `ImportError`. Nothing in `src/` or `scripts/`
holds a composed application at import time.

**R3 — Both gates are non-optional and still derived together.** `server._gates(policy:
AccessPolicy) -> tuple[Callable[[str], bool], Authorize]` returns `policy.permits_tool` and
`dispatch_authorizer(policy)`. It keeps its ADR 0038 contract: a site given one without the
other registers tools it does not authorize.

**R4 — Both transports take a required policy.** `main_stdio(enable_arbitrary_command,
access_policy)` and `main_http(host, port, enable_arbitrary_command, allow_remote,
access_policy)` have no default for `access_policy`, and `_serve_application` takes
`access_policy: AccessPolicy`.

**R5 — `serve` refuses without `--access-policy`.** With the option omitted the command
exits **2**, starts no server, composes no application, and writes to stderr a message
naming `hmc-mcp config init-access-policy`, the path
`resolve_access_policy_path()` would use, and `--access-policy NAME`. A policy that is
selected but cannot be read, parsed, or compiled keeps ADR 0036's existing behaviour: exit
**1** through `_fail`, with the `AccessPolicyError` text. The two codes distinguish "you
have not chosen" from "what you chose is wrong".

**R6 — The unselected-policy warning is gone.** `server._unselected_policy_file` is removed
and `_startup_warnings(tool_count: int, access_policy: AccessPolicy,
enable_arbitrary_command: bool)` emits at most the three lines that remain: an empty
surface, a policy withholding `hmc_effective_permissions`, and
`--enable-arbitrary-command` without a `hmc_run_command` grant. The condition it dropped is
unreachable — a server cannot start with no policy.

**R7 — The generated document's shape.** A new module `hmc_mcp.legacy_policy` exports
`LEGACY_POLICY_NAME = "legacy-equivalent"` and builds one policy holding exactly one grant:
`tools` is `sorted(set(tool_security) - {"hmc_run_command"})`, `connections` is the
caller-supplied sequence, and `targets` is the string `"all-targets"`. The grant carries no
`effects` key, so the policy grants no tool that does not exist at generation time.
`hmc_mcp.legacy_policy` imports no `server` module: `tool_security` arrives as a parameter,
as it does for `compile_access_policy`.

**R8 — Connections are the deployment's own, plus the default token.**
`legacy_connections() -> tuple[str, ...]` returns `("<default>", *sorted(profile keys))`
read from the platform-native `config.toml` through `config.list_profiles`. With no config
file it returns `("<default>",)`. A `ConfigError` propagates to the caller rather than
being swallowed into a shorter list. Nicknames are excluded: ADR 0030 resolves a nickname
to its target before ADR 0038 compares it, so a granted nickname never matches.
`"<default>"` is always present because `connection_scope.selected_connection` collapses
every token to it under `HMC_HOST`, and because an omitted `profile` argument means it.

**R9 — Rendering round-trips through the real loader.**
`render_legacy_policy(tool_security, connections) -> str` returns TOML text that
`tomllib.loads` parses and `compile_access_policy` compiles without error, yielding a
policy named `legacy-equivalent` whose `tools` equals the R7 set. The text carries a
comment header stating what the policy grants, that it must be regenerated after an
upgrade that adds tools, and how to add `hmc_run_command`.

**R10 — The same document compiles without a filesystem.**
`compile_legacy_policy(tool_security, connections) -> AccessPolicy` compiles the R7
document directly. Its `source` is a fixed non-path label identifying the generator, so a
message rendering it discloses no filesystem path.

**R11 — The generator command.** `hmc-mcp config init-access-policy` takes no options.
It writes `render_legacy_policy(server.TOOL_SECURITY, legacy_connections())` to
`resolve_access_policy_path()`, creating parent directories, using `O_WRONLY|O_CREAT|O_EXCL`
at mode `0o600` on POSIX and `open(..., "x")` on win32 — the same two-branch pattern
`config init` uses. An existing file is never overwritten, truncated, or read: the command
exits **1** with a `FileExistsError` naming the path. A `ConfigError` from R8 is reported
the same way — exit **1** through `_fail`, with no file written. On success it prints the
path to stdout and one activation hint to stderr, and it does not start a server, modify
`config.toml`, or make the policy active.

**R12 — The generated policy authorizes a call that omits an optional selector.** A call to
a tool with an optional target selector, made with that argument unset, is permitted under
the compiled legacy-equivalent policy. `target_scope.ABSENT` is covered by `all-targets`
and by nothing else, so this is the property that makes the generator legacy-equivalent
rather than merely close.

**R13 — The generated policy authorizes the non-string-selector tools.** A call naming
`vios_partition_id` — the surface's only `int` selector, carried by three live tools — is
permitted under the compiled legacy-equivalent policy. Those tools are non-exhaustive, so
only `all-targets` reaches them.

**R14 — The generated policy grants no arbitrary command.** `hmc_run_command` is absent
from the compiled policy's `tools`, so `--enable-arbitrary-command` alone does not expose
it and the ADR 0041 startup warning fires when the flag is passed.

**R15 — Both scripts compose through the generator.** `scripts/smoke_mcp.py` and
`scripts/live_test_runner.py` build their application as
`create_mcp(compile_legacy_policy(TOOL_SECURITY, (DEFAULT_CONNECTION_TOKEN,)))`. The smoke
path therefore proves on every `just verify` and every CI leg that the generated document
compiles and composes.

**R16 — Documentation states the new default and its precondition.** `README.md` gains a
migration section covering the refusal, the generator, and the two exit codes; keeps a
minimal read-only example; keeps a limited-mutation example; adds an abbreviated
legacy-equivalent example; drops the removed startup-warning row; and states that a
deployment must drain the server's fd 2, naming #269. `README.md`'s "Without
`--access-policy NAME` there is no authorizer, so no *authorization* record is written" and
the same claim in `docs/authorization-audit.md` are corrected: every deployment now writes
one record per decision.

**R17 — Nothing accepts a request-supplied grant.** No MCP tool argument, request field,
header, or environment variable selects, widens, or supplies an access policy or a grant.
The extension point for separately authenticated short-lived grants is recorded in
ADR 0041 and implemented by nothing here.

## Guardrails

**G1 — the composer's signature cannot silently regain a default.**
`inspect.signature(server.create_mcp).parameters["policy"].default is
inspect.Parameter.empty`. Paired with R1's runtime guard, so a future edit that restores a
default or drops the `None` check reddens.

**G2 — the legacy-equivalent policy composes the registry the unpolicied composition used
to.** `set(names of create_mcp(compile_legacy_policy(TOOL_SECURITY, ("<default>",))))`
equals `set(TOOL_SECURITY) - {"hmc_run_command"}` — the exact assertion the retired
`test_no_policy_applies_no_ceiling` made about `create_mcp()`. This is the legacy-
equivalence claim as a test rather than as prose.

**G3 — no import-time application anywhere.** `hmc_mcp.server` exposes no `mcp` attribute,
and no module under `src/hmc_mcp` or `scripts/` calls `create_mcp` at module scope.

## Live checks

Driven as real `hmc-mcp` subprocesses, because an in-process assertion cannot see the exit
code an operator sees. They run on every CI leg (`amd64`/`ubuntu-24.04` and
`arm64`/`ubuntu-24.04-arm`, Python 3.11-3.14); there is no macOS or Windows leg.

**L1 — `hmc-mcp serve` with no `--access-policy` exits 2 and serves nothing.** The child
terminates on its own; its stderr names the generator command. Portable — no shell
redirection and no `HOME` steering — so it runs on every platform the suite runs on.

**L2 — the documented migration works end to end.** In a temporary `HOME`: run
`hmc-mcp config init-access-policy`, assert the file exists at mode `0o600`, run the
command a second time and assert exit 1 with the file unchanged byte-for-byte, then start
`hmc-mcp serve --access-policy legacy-equivalent` and complete an MCP `initialize` and
`tools/list` handshake over stdio. POSIX-only for the reason
`tests/app/test_authorization_audit_live.py` gives: the fixture steers `config_dir()`
through `HOME`, which on win32 would write over the developer's real file.

## Retired tests

Three tests assert behaviour this entry removes. Each is inverted rather than deleted, the
way ADR 0039 inverted ADR 0036's A7 test, so the retirement is visible in the diff:

- `tests/app/test_capability_ceiling.py::test_no_policy_applies_no_ceiling` (R2 of the
  ceiling spec) becomes an assertion that `create_mcp()` and `create_mcp(None)` both raise.
- `tests/app/test_capability_ceiling.py::test_inspection_reports_no_policy_honestly`
  (R14/R16 of the ceiling spec) is retargeted at `server_permissions.describe(names, None,
  ...)` directly, which ADR 0041 keeps as a documented direct-caller case.
- `tests/app/test_connection_authorization.py::test_no_tool_is_wrapped_without_a_policy`
  (R14 of the connection spec) becomes an assertion that under the legacy-equivalent policy
  every connection-bearing tool *is* wrapped — the successor claim, and the one that makes
  "legacy-equivalent is not legacy" checkable.

A test inventory guard in the new test module names every R- and G-identifier above and
fails when the test implementing one stops existing, copying the pattern
`tests/unit/test_audit.py` established.

## Threat model

**Boundary inventory.** This entry adds one trust boundary and widens one.

- *Added:* `hmc-mcp config init-access-policy` writes a new file into the operator's
  platform-native config directory. Input: the operator's own `config.toml` profile keys
  and the compiled-in tool index. No untrusted input reaches it.
- *Widened:* the dispatch boundary. It was reached only by deployments passing
  `--access-policy`; it is now reached by all of them. The authorizer, the target
  extraction, and the ADR 0040 audit write are now on every deployment's untrusted-call
  path.

**Actor model.** The untrusted party is the MCP client and the agent driving it. It
controls tool arguments and call rate. It does not control the process environment,
`config.toml`, `access-policy.toml`, or the command line — those are the operator's, at one
trust level (ADR 0036). The operator running the generator is trusted; the file it writes
is trusted input to the loader.

**Control per boundary.**

- *Generator writing a file* — `O_EXCL` refuses to overwrite, so the command cannot destroy
  a reviewed policy, and mode `0o600` matches `config init`. It reads profile **keys** only,
  never a password, a `password_env` value, or a host, so nothing secret can reach the
  generated file. On failure it emits the `ConfigError` or `FileExistsError` text, which
  names the config path — the same disclosure `config init` and `config show` already make
  to the same local operator.
- *Loading the generated file* — unchanged: `load_access_policy` validates and compiles it
  under ADR 0036's strict rules. The generator gets no privileged path into the loader, and
  R9 proves the round trip through the real loader rather than through a private
  constructor.
- *The widened dispatch boundary* — unchanged controls (ADR 0038, ADR 0039), now always on.
  Denials keep their closed templates.
- *The refusal at startup* — the message names a command and a path, both the operator's
  own, and no policy content, since no policy has been read.

**Explicitly out of scope.**

- *An undrained fd 2 wedges the server.* Issue #269. This entry makes it reachable for
  every deployment and by an ungranted caller, since ADR 0040 emits the record before the
  denial. Not closed here; stated in ADR 0041 and in the README as a deployment
  precondition. This is the one accepted risk this entry knowingly widens.
- *An operator who never regenerates loses new tools silently.* Accepted and documented:
  the fail-closed direction, and `hmc_effective_permissions` shows it.
- *The permit/deny oracle* (ADR 0038) and *policy-content disclosure through
  `hmc_effective_permissions`* (ADR 0037) are unchanged and remain as those records state.
- *A local user who can already write the operator's config directory* can author any
  policy. Out of scope for the same reason ADR 0036 puts `config.toml` at the operator's
  trust level.

## Open questions

None. The two judgement calls the issue left open — whether the generated grant names tools
or effect classes, and whether it includes `hmc_run_command` — are decided in ADR 0041 with
their rejected alternatives recorded.
