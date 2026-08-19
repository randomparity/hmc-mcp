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

**R2a — The five test modules importing `server.mcp` compose their own.**
`tests/app/test_capabilities.py`, `tests/app/test_tool_security.py`,
`tests/app/test_collection_limits.py`, `tests/app/test_profile_routing.py`, and
`tests/app/test_lifecycle_schema_descriptions.py` import the module-level application and
fail at collection once R2 removes it. G3 does not catch them — it guards `src/` and
`scripts/` — so they are named here rather than discovered as five collection errors. Each
binds a module-level
`create_mcp(compile_legacy_policy(TOOL_SECURITY, (DEFAULT_CONNECTION_TOKEN,)))`. No
assertion changes: all five use the application only for registry and schema inspection
through `list_tools()`, which the dispatch wrapper leaves untouched — `functools.wraps` sets
`__wrapped__`, and the existing schema-transparency test in
`tests/app/test_connection_authorization.py` is what holds that. The two that drive
`configure_arbitrary_command_tool(True, mcp)` and reset it afterwards keep working, and
gain isolation rather than losing it: the toggled application is now per-module rather than
process-wide.

**R2b — Two further test modules break on the transport contract itself.**
`tests/app/test_serve.py` owns the CLI transport contract this entry rewrites and breaks
against four requirements at once: it patches `server_app.mcp` (removed by R2), asserts
`access_policy=None` is forwarded (R5 makes those invocations exit 2), asserts
`calls == [(enabled, None, None)]` for the gate pair (R3 makes both non-optional), and calls
`main_stdio`/`main_http` without `access_policy` (R4 removes the default, so each raises
`TypeError`). Every one of those assertions is inverted to the new contract rather than
deleted: the forwarded value becomes a real `AccessPolicy`, and the omitted-option and
absent-file cases gain assertions on exit code 2 and exit code 1 respectively.
`tests/test_live_runner.py` asserts `application is runner.mcp` through a two-parameter
`configure` double, so it follows R15's composition and R15a's call signature.

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
naming `hmc-mcp config init-access-policy`, the path `resolve_access_policy_path()` would
use, `--access-policy NAME`, and the README's narrower policy examples — nothing at the
point of refusal distinguishes an upgrade from a first run, and a message offering only the
generator would send every fresh install to the widest policy expressible. A policy that is
selected but cannot be read, parsed, or compiled keeps ADR 0036's existing behaviour: exit
**1** through `_fail`, with the `AccessPolicyError` text. The codes distinguish "you have
not chosen" from anything about the policy itself.

**R5a — A named policy whose file does not exist gets the migration message too.** When
`--access-policy` is supplied and `resolve_access_policy_path()` does not exist, `serve`
emits the same text as R5 and exits **1** — the command line was right and the environment
was not. Without this the likeliest upgrade order (edit the launcher, then discover the
file) reaches `load_access_policy`'s unreadable-file arm and prints
`cannot be read: [Errno 2] No such file or directory`, naming neither the generator nor the
remedy.

**R5b — Both refusals resolve the policy path through a guard.** `serve` obtains the path
for R5's message and for R5a's existence check through one helper that catches
`RuntimeError`, `OSError`, and `ValueError` and returns `None` — the guard
`server._unselected_policy_file` carried, and whose removal must not take it with it. That
set is its, not `load_access_policy`'s: the latter guards path *resolution* against
`RuntimeError` and `ValueError` only, and catches `OSError` on `read_text`, in a later try
block reached only once resolution succeeded. `OSError` is in this set because the helper
also calls `.exists()`, and because an `OSError` escaping resolution would be the traceback
this requirement exists to prevent rather than something the load would convert.
`config_dir()` calls `Path.home()`, which
raises under a uid with no passwd entry and no `HOME`; unguarded, R5 would raise while
rendering its own usage error and R5a would raise before the load's guard was reached,
turning a refusal into a traceback in the deployment least able to read one. With `None`,
R5's message omits the path and R5a's check does not fire, so an unresolvable path reaches
`load_access_policy` and surfaces as its `AccessPolicyError` at exit **1**.

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

**R7a — The escape hatch is opt-in and unreachable from the CLI.** The document builder and
`compile_legacy_policy` take a keyword-only `include_arbitrary_command: bool = False`; when
true, `hmc_run_command` joins `tools`. `render_legacy_policy` does not take it and never
emits the name, so no file the generator writes can grant it. `scripts/live_test_runner.py`
is the only caller passing `True`, because it drives `hmc_run_command` against a real HMC
after `configure_arbitrary_command_tool(True, ...)`, whose registration is gated on
`permits("hmc_run_command")`.

**R8 — Connections are the deployment's own, plus the default token.**
`legacy_connections() -> tuple[str, ...]` returns
`("<default>", *sorted(k for k in profile_keys if k != DEFAULT_CONNECTION_TOKEN))`
read from the platform-native `config.toml` through `config.list_profiles_and_nicknames`,
whose nicknames half is discarded. That reader rather than `config.list_profiles`: only the
former converts every failure into a `ConfigError` — an unresolvable home, an unreadable or
non-UTF-8 or unparseable file, a malformed table — which is the contract this requirement
depends on and `connection_scope` already relies on. With no config file it returns
`("<default>",)`. A `ConfigError` propagates to the caller rather than being swallowed into
a shorter list. The sentinel is filtered out of the profile half because `[profiles.
"<default>"]` is a legal TOML key: without the filter the generator would manufacture the
duplicate `_check_entries` refuses, and that deployment could never generate a policy at
all — an unconditional prepend colliding with the operator's own data is the generator's
defect, not theirs. With the filter, `profiles` keys are unique by TOML's own rules, so a
duplicate `connections` entry is unreachable; an empty or padded key remains possible and
`_check_entries` names the offending value when R9b's pre-write load rejects it. Nicknames are excluded: ADR 0030 resolves a nickname
to its target before ADR 0038 compares it, so a granted nickname never matches.
`"<default>"` is always present because `connection_scope.selected_connection` collapses
every token to it under `HMC_HOST`, and because an omitted `profile` argument means it.

**R9 — Rendering round-trips through the real loader.**
`render_legacy_policy(tool_security, connections) -> str` returns TOML text that
`tomllib.loads` parses and `compile_access_policy` compiles without error, yielding a
policy named `legacy-equivalent` whose `tools` equals the R7 set and whose `connections`
equals the sequence given, entry for entry. The text carries a comment header stating what
the policy grants, how to add `hmc_run_command`, and the regeneration procedure —
`--output` to a scratch path, diff, merge by hand — since R11's command refuses to
overwrite and there is no `--force`.

**R9a — Rendering escapes every operator-supplied key.** The emitter is hand-written; no
TOML-writing dependency is added (epic requirement 11). Each `connections` entry is emitted
as a TOML basic string with `"`, `\`, U+0000-U+001F, and U+007F escaped, so no profile key
can terminate the string, inject a key, or open a `[[policies...grants]]` table. That set is
TOML's own and was checked against this checkout's `tomllib`: a raw U+007F in a basic string
is rejected (`Illegal character '\x7f'`) while a raw U+0085 parses, so C1 needs no escaping
and DEL does. A key holding a raw U+007F is reachable — `[profiles."a\u007Fb"]` is legal TOML
and decodes to one — which is why the boundary is stated rather than approximated. `config.list_profiles` returns raw TOML keys with no charset
validation, so this is the only thing standing between an odd key and either an unparseable
generated file or an injected grant. Proved by rendering a policy whose connections include
a key containing a double quote, a backslash, and a newline, then parsing the result with
`tomllib` and asserting the connection list round-trips byte-identically.

**R9b — The generator loads what it rendered before it writes.** Escaping makes the document
parse; it does not make it *load*. `_check_entries` rejects an empty, whitespace-padded, or
duplicated `connections` entry, and `[profiles.""]`, `[profiles." prod"]`, and
`[profiles."<default>"]` are all legal TOML keys; the first two produce exactly those, and
R8's filter is what keeps the third from becoming a duplicate the generator inflicted on
itself. So `config init-access-policy` parses and
compiles the rendered text through `compile_access_policy` before creating any file, passing
R10's fixed non-path label as `source`; a document that would not load is reported through
`_fail` at exit **1** and no file is created. The generator catches that `AccessPolicyError`
and re-raises it with one added clause naming `config.toml` as the origin, because every
noun in `_check_entries`'s text — the policy name, the grant index, the connections field —
belongs to a document that was never written, and the operator's actual edit is a profile
key. `[profiles." prod"]` is legal TOML that `load_profile` resolves today, so this is a
working deployment that meets the message at upgrade. Enumerating illegal key shapes in the
generator was rejected: the rules live in `access_policy` and a copy would drift from them.

**R10 — The same document compiles without a filesystem.**
`compile_legacy_policy(tool_security, connections) -> AccessPolicy` compiles the R7
document directly. Its `source` is a fixed non-path label identifying the generator, so a
message rendering it discloses no filesystem path.

**R11 — The generator command.** `hmc-mcp config init-access-policy` writes
`render_legacy_policy(server.TOOL_SECURITY, legacy_connections())` to
`resolve_access_policy_path()`, creating parent directories, using `O_WRONLY|O_CREAT|O_EXCL`
at mode `0o600` on POSIX and `open(..., "x")` on win32 — the same two-branch pattern
`config init` uses, and it resolves that destination through the same guard R5b specifies —
`config_dir()` reaches `Path.home()` here exactly as it does for `serve`, and an
unresolvable path is reported through `_fail` at exit **1** naming the
`HOME`/`XDG_CONFIG_HOME` remedy rather than raising. Whether `legacy_connections()` happens
to convert that failure first is an evaluation order this does not rely on. An existing file
is never overwritten, truncated, or read: the command exits **1** with a `FileExistsError`
naming the path. A write or close failure *after* the
descriptor exists — ENOSPC, EDQUOT, EIO — unlinks the destination before reporting.
`O_EXCL` alone gives "no partial file" only for failures at `os.open`, and a truncated file
here is worse than elsewhere: it exists, so the command's own no-overwrite rule refuses to
regenerate over it, and it does not compile, so `serve` refuses too — leaving a deployment
that cannot start and cannot regenerate without a manual delete nothing documents.
The unlink closes the exception arm only: a SIGINT, SIGTERM, OOM kill, or host loss between
the create and the final flush leaves the same state with no handler to run, and a zero-byte
file is enough, since an empty document fails the required `policies` key. That residual is
named rather than implied closed, and R16's migration section carries the recovery: if
`serve` reports a policy that will not compile and the generator reports the file already
exists, delete it and re-run the generator. A `ConfigError` from R8 is reported
the same way — exit **1** through `_fail`, with no file written. On success it prints the
path to stdout and one activation hint to stderr, and it does not start a server, modify
`config.toml`, or make the policy active.

**R11a — `--output PATH` redirects the write and nothing else.** The one option. It changes
only the destination: the same document, the same `O_EXCL` create, the same `0o600`, the
same refusal to overwrite, the same stdout path line. It exists because the command cannot
overwrite, which otherwise leaves no way to regenerate for a diff — the only detection path
for a tool an upgrade added, or a connection the operator added (see R16). It does not help
a split-identity deployment on its
own: R8's connections list is still read from the invoking identity's `config.toml`, so
that deployment must run the generator under the serving identity's `HOME` or
`XDG_CONFIG_HOME`, which places the default path correctly as well.
The path is the trusted local operator's own; parent directories
are created as for the default path, and a directory or unwritable destination surfaces the
`OSError` through `_fail` at exit **1** with no partial file.

**R11b — "Legacy-equivalent" has two documented edges, both inherited.** The name is a
claim about what the policy *grants*, and two settled decisions keep it from being exact.
`connection_scope` resolves an omitted `profile` argument to `<default>` without consulting
`HMC_PROFILE` or `default_profile` (ADR 0038 fixed that denotation and recorded its late
binding), so what the authorizer evaluates and what `build_config` loads can differ. And
`targets_permitted` denies an `UNREADABLE` selector value even under `all-targets`
(ADR 0039: a value the boundary declines to read is a malformed call, not a narrow one), so
a call the unpolicied server would have run can be denied. Neither is reopened here; both
are named so "legacy-equivalent" is read as the two shipped records define it.

**R12 — The generated policy authorizes a call that omits an optional selector.** A call to
a tool with an optional target selector, made with that argument unset, is permitted under
the compiled legacy-equivalent policy. `target_scope.ABSENT` is covered by `all-targets`
and by nothing else, so this is the property that makes the generator legacy-equivalent
rather than merely close.

**R13 — The generated policy authorizes the non-string-selector tools.** A call naming
`vios_partition_id` — the surface's only `int` selector, carried by three live tools — is
permitted under the compiled legacy-equivalent policy. Those tools are non-exhaustive, so
only `all-targets` reaches them.

**R14 — The written policy grants no arbitrary command.** `hmc_run_command` is absent from
`render_legacy_policy`'s output and from the policy compiled at the R7a default, so
`--enable-arbitrary-command` alone does not expose it and the existing startup warning
fires when the flag is passed. Under `include_arbitrary_command=True` it is present, and
`configure_arbitrary_command_tool(True, ...)` then registers it.

**R15 — Both scripts compose through the generator.** `scripts/smoke_mcp.py` builds
`create_mcp(compile_legacy_policy(TOOL_SECURITY, (DEFAULT_CONNECTION_TOKEN,)))`;
`scripts/live_test_runner.py` builds the same with `include_arbitrary_command=True`, so its
`configure_arbitrary_command_tool(True, ...)` call still registers the tool it goes on to
invoke. Neither holds an application at module scope. The smoke path proves on every
`just verify` and every CI leg that the grant compiles and composes; it does not serialize
TOML, so the emitted document's parse is R9's to prove and not smoke's.

**R15a — Every `configure_arbitrary_command_tool` call passes the gates it composed
under.** `scripts/live_test_runner.py` calls it as `(True, mcp)` today: `permits=None`
registers `hmc_run_command` whatever the policy says, and `authorize=None` makes
`tool_registry.authorized` return the bare handler, so the runner's escape-hatch calls
would run unauthorized while every other tool it drives is wrapped. That also falsifies
R7a's justification for `include_arbitrary_command`, which assumes the registration is
gated. The runner therefore passes `permits` and `authorize` derived from the policy it
composed, which is what makes the opt-in load-bearing rather than decorative — and what
makes the live run evidence about the path an operator takes.

**R16 — Documentation states the new default and its precondition.** `README.md` gains a
migration section covering the refusal, the generator, the two exit codes, the `--output`
regeneration-and-diff procedure over both the `tools` and `connections` arrays, the
recovery for a policy file that exists but will not compile (delete it, re-run the
generator), the
requirement that the generator run as the identity
`serve` runs under, the resolvable-`HOME`-or-`XDG_CONFIG_HOME` requirement for a container
or systemd unit, and the loss of `hmc_run_command` for a deployment that ran with
`--enable-arbitrary-command`; says plainly that the legacy-equivalent policy is a migration
aid rather than a recommended posture for a fresh install; keeps a
minimal read-only example; keeps a limited-mutation example; adds an abbreviated
legacy-equivalent example; drops the removed startup-warning row; and states that something
must drain the server's fd 2 — the MCP client under stdio — naming #269. `README.md`'s
"Without `--access-policy NAME` there is no authorizer, so no *authorization* record is
written" and the same claim in `docs/authorization-audit.md` are corrected: every deployment
now writes one record per decision.

**R16a — Every runnable `serve` command in the docs still runs.** `README.md` carries four
`hmc-mcp serve` invocations that exit 2 and start nothing after this entry: the three in the
MCP-server quick-start block, and the `hermes mcp add` client-registration line, which is
the one that matters most — a client launching it sees a non-zero exit and reports "server
failed to start" rather than surfacing R5's carefully written stderr. Each gains
`--access-policy NAME`, and the quick-start block is ordered so the generator command
precedes the first `serve` line. A command that fails when followed is a defect in the
change that broke it, not a stale example.

**R16c — `serve --help` stops advertising the mode it now refuses.**
`src/hmc_mcp/cli_app.py`'s `--access-policy` option help ("Without it, no capability ceiling
is applied and every tool is exposed") and the `serve` docstring ("Without the option no
policy applies") both describe omitting the option as a supported mode. `hmc-mcp serve
--help` is the first thing an operator runs after R5 refuses them, so they would read that
contradiction in the same terminal that just produced the refusal. Both describe the option
as required and name `hmc-mcp config init-access-policy`; `README.md`'s matching sentence is
corrected with them. R16a's principle, applied to help text rather than to a command.

**R16b — The docs keep the two files distinct by name.** The migration section states in one
sentence that `config.toml` holds HMC *connection profiles*, `access-policy.toml` holds
*server access policies*, they are separate files with separate lifecycles, and a grant's
`connections` entries are profile **keys** rather than profile contents. Charter criterion
(5)'s second half, and the conflation this entry invites more than any other: the generator
sits in the same `config` command group as `config init`, writes into the same directory
with the same `O_EXCL` refusal, and fills `connections` with `config.toml`'s own keys.

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

**L2 — the documented migration works end to end.** Run
`hmc-mcp config init-access-policy`, assert the file exists at mode `0o600`, run the
command a second time and assert exit 1 with the file unchanged byte-for-byte, then start
`hmc-mcp serve --access-policy legacy-equivalent` and complete an MCP `initialize` and
`tools/list` handshake over stdio.

Its isolation is stated here rather than by reference, because L2 is the only check in
this spec that **writes** into a resolved config directory. Setting `HOME` alone does not
steer `config_dir()`: on Linux it reads `XDG_CONFIG_HOME` first and only then falls back to
`Path.home()`. Followed literally on a machine with `XDG_CONFIG_HOME` exported, the command
would create a real `access-policy.toml` at `0o600` in the developer's own config
directory, silently changing what `--access-policy` resolves to for them afterwards. So the
fixture sets `HOME` to the temporary path, removes `XDG_CONFIG_HOME` and `APPDATA` from
both the test process and the child environment, patches `pathlib.Path.home`, and derives
its assertion target from `config_dir()` after that steering rather than by joining the
temporary path by hand — the same shape `tests/app/test_authorization_audit_live.py`
already uses, and the reason it is POSIX-only: on win32 `config_dir()` reads `APPDATA`
while `Path.home()` reads `USERPROFILE`, so the steering does not hold.

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
  a reviewed policy, and mode `0o600` matches `config init`. `--output` changes only the
  destination and keeps both; the path comes from the trusted local operator's own command
  line, which is the same trust level as the config directory itself, so there is no
  traversal question to answer. It reads profile **keys** only, never a password, a
  `password_env` value, or a host, so nothing secret can reach the generated file. On
  failure it emits the `ConfigError`, `FileExistsError`, or `OSError` text, which names a
  path — the same disclosure `config init` and `config show` already make to the same local
  operator.
- *Rendering operator-authored keys into a generated file* — the one place this entry turns
  text it did not author into a document something else parses. `config.list_profiles`
  applies no charset validation, so R9a's TOML basic-string escaping of `"`, `\`, and every
  control character is the control: it is total, so no key can terminate its string, and
  therefore no key can inject a `[[policies...grants]]` table into a file the operator is
  told to review, or produce one that fails to parse.
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
- *A uid with no passwd entry, no `HOME`, and no `XDG_CONFIG_HOME` can no longer serve.*
  `config_dir()` raises there and `--access-policy` names a policy at the platform-native
  path rather than a file. Accepted; the remedy is one environment variable in the unit or
  image, stated in the README. An `--access-policy-file` option was not added, because a
  second place a policy can come from is a second thing to get wrong.
- *A release that removes or renames a tool stops a generated-policy deployment from
  starting at all.* The subtractive arm of the pin, and categorically worse than the
  additive one: `_compile_grant` raises `unknown tool '<name>'` for any granted name absent
  from `TOOL_SECURITY`, so `serve` exits 1 and nothing runs — where an added tool merely
  goes ungranted while the server keeps serving. It is not hypothetical; ADR 0003 and
  ADR 0004 each consolidated tool pairs, which retires names. Accepted rather than closed,
  because the alternative is a loader that ignores unknown tool names, and ADR 0036 made
  that strictness deliberate. The remedies are procedural and belong to the project as much
  as the operator: regenerate as part of any upgrade, and call tool removals out in the
  release notes. Before this entry an unpolicied deployment was immune, so this is reach
  this entry creates.
- *An operator who never regenerates loses new tools **and new connections** silently.*
  Accepted and documented. Both arms of the grant are generation-time snapshots, and the
  connections arm is the more frequent one: adding a profile to `config.toml` is routine,
  while a tool-adding release is on cadence. After a profile is added, every call routed to
  it is denied at dispatch by a policy the operator was told is "legacy-equivalent". The
  denial names the tool, the connection, and the policy (ADR 0038), so the diagnosis is
  reachable, but nothing points at the generator. It is the fail-closed direction, and
  nothing inside the running server surfaces either arm — `hmc_effective_permissions`
  reports the registered set, which is exactly what the policy produced. Detection is
  `--output` to a scratch path plus a diff of the `tools` **and** `connections` arrays.
- *The permit/deny oracle* (ADR 0038) and *policy-content disclosure through
  `hmc_effective_permissions`* (ADR 0037) are unchanged and remain as those records state.
- *A local user who can already write the operator's config directory* can author any
  policy. Out of scope for the same reason ADR 0036 puts `config.toml` at the operator's
  trust level.

## Open questions

None. The two judgement calls the issue left open — whether the generated grant names tools
or effect classes, and whether it includes `hmc_run_command` — are decided in ADR 0041 with
their rejected alternatives recorded.
