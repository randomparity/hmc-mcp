# Composition-time capability ceiling and effective-permission inspection

Issue: [#221](https://github.com/randomparity/hmc-mcp/issues/221) — part of epic #218.
Decision record: [ADR 0037](../../adr/0037-composition-time-capability-ceiling.md).
Builds on [ADR 0035](../../adr/0035-enforceable-tool-security-metadata.md) (tool security
metadata) and [ADR 0036](../../adr/0036-server-access-policy-model.md) (the policy model).

## Goal

A fresh MCP application registers only the tools the startup-selected access policy
permits; the arbitrary-command escape hatch requires both its CLI flag and a policy grant;
and a read-only tool reports the effective permissions of the application it is registered
on, distinguishing what is enforced from what is merely declared.

## Scope

In scope: registration-time filtering, the arbitrary-command intersection, startup policy
selection on `serve`, and the inspection tool.

Out of scope, with owners: connection-scope authorization at dispatch (#222); target
constraint matching at call time (#223); structured audit events (#224); fail-closed
startup when no policy is selected and the legacy-equivalent policy generator (#225).

## Requirements

Each requirement is numbered and testable. R-prefixed identifiers are cited by the plan.

**R1 — Registration is filtered.** `server.create_mcp(policy=None)` returns an
application whose registered tool names are exactly
`{name for name in server.TOOL_SECURITY if policy is None or policy.permits_tool(name)}`,
minus `hmc_run_command`, which is never registered by `create_mcp`.

**R2 — No policy means no ceiling.** `create_mcp()` with no argument registers every tool
`create_mcp` registered before this change, plus `hmc_effective_permissions`: 129 tools.

**R3 — Applications stay independent.** Two `create_mcp` calls with different policies
produce applications whose registries differ accordingly and share no state; a call with
no policy after a call with a restrictive one is unaffected by it.

**R4 — The ceiling parameter is a predicate, not a policy object.**
`tool_registry.tool_module()`'s `register_tools` signature is
`register_tools(mcp: FastMCP, *, permits: Callable[[str], bool] | None = None) -> None`.
`tool_registry.py` imports nothing from `access_policy.py`.

**R5 — Arbitrary command is a conjunction.**
`server_command.configure_arbitrary_command_tool(enabled, mcp, *, permits=None)`
registers `hmc_run_command` when `enabled is True` and (`permits is None` or
`permits("hmc_run_command")`), and removes it in every other combination. It stays
idempotent and symmetric in both directions, as it is today.

**R6 — `arbitrary-command` is still ungrantable by effect class.** A policy whose only
grant is `effects = ["read", "mutate", "destructive"]` does not permit `hmc_run_command`
even with `--enable-arbitrary-command`. This is enforced by `access_policy.py` and this
change must not route around it.

**R7 — Startup selection.** `hmc-mcp serve --access-policy NAME` loads NAME from the
platform-native `access-policy.toml` via
`load_access_policy(NAME, server.TOOL_SECURITY)` and serves an application composed with
it. Without the option, the server composes with no policy.

**R8 — An explicit selection that cannot be loaded is a startup failure.** Any
`AccessPolicyError` — absent file, TOML error, unknown policy name, invalid grant — exits
non-zero with the error's own message on stderr and starts no server.

**R9 — The served application is freshly composed.** `server.main_stdio` and
`server.main_http` compose the application they serve through `create_mcp(policy)`; they
do not serve or mutate the module-level `server.mcp`.

**R9a — The entry points wire the ceiling into the escape hatch.** `main_stdio` and
`main_http` derive `permits` from the policy they serve and pass it to
`configure_arbitrary_command_tool`; neither relies on its `permits=None` default, which
means *no ceiling* and would fail open. The observable outcome, asserted at the entry point
and not only on the function: `--enable-arbitrary-command` together with a policy that
withholds `hmc_run_command` yields a served application whose registry does not contain it.

**R10 — The inspection tool exists and is classified.** `hmc_effective_permissions` is in
`server.TOOL_SECURITY` with `effect="read"`, `operation="permissions.describe"`,
`target_kind="none"`, `targets=()`, `connection_argument=None`. It takes no arguments and
its MCP annotations are `annotations_for("read")`.

**R10a — An empty served surface is visible, however it arose.** A warning is written when
the application about to be served registers zero tools. The
check is made *after* `configure_arbitrary_command_tool` has run, so it reports the surface
actually served, and it lives in the entry points because they are the only components that
hold a composed application — `serve` never does. Two documents reach an empty surface:
`grants = []`, a valid deny-everything policy under ADR 0036 and #220's rule P2; and a
policy whose whole ceiling is `tools = ["hmc_run_command"]`, since `create_mcp` never
registers that tool and the same ceiling withholds the inspection tool. That second policy
*with* `--enable-arbitrary-command` serves exactly one tool — the escape hatch — so the
warning must not fire there, which is why the check follows the toggle rather than
composition. Neither document is an error, so neither is rejected at selection. R18's
warning is suppressed when this one fires: an empty surface already implies the inspection
tool is absent, and one diagnosis is enough.

**R11 — The inspection tool is subject to the ceiling.** A policy that does not permit
`hmc_effective_permissions` yields an application without it. A policy granting
`effects = ["read"]` yields an application with it.

**R12 — Inspection output matches the registry exactly, checked two ways.** For any
application `app` composed by `create_mcp(policy)`, and after any sequence of
`configure_arbitrary_command_tool` calls on it, the `name` values in the tool's `tools`
field equal the names in `app.list_tools()`, as sets and as a sorted sequence. The handler
reads `app.local_provider.list_tools()`, a different accessor, so the two sides are not the
same call. The equality holds under the conditions this repository composes under: no tool
is disabled, none carries a per-tool `auth` check, and no session transform is installed.
Outside them the provider is the wider set — it includes disabled tools, where
`FastMCP.list_tools()` filters on enabled, app-visibility, and auth — so inspection would
over-report relative to `tools/list`. That is the same "inspection inherits the registry"
property ADR 0037 records, and nothing in this change creates such a tool. The reported set is *also* asserted against the policy-derived expectation —
`{n for n in TOOL_SECURITY if policy is None or policy.permits_tool(n)}`, minus
`hmc_run_command` unless the arbitrary-command tool is currently registered — so one
implementation defect cannot satisfy both checks.

**R13 — Inspection reports live effect classes, and never raises on an unknown tool.** The
`effects` field is the sorted set of `TOOL_SECURITY[name].effect` over the reported tools —
so a read-only policy reports `("read",)`, and an application with the arbitrary-command
tool registered reports `arbitrary-command` among them. A registered name the index does
not carry — reachable because callers hold the live application and may call `mcp.tool(...)`
on it — is reported with `effect`, `operation`, and `target_kind` all set to `"unknown"`
and is excluded from the `effects` set. The tool whose job is describing the surface does
not raise when the surface changes.

**R14 — Inspection reports the policy source, and verifies the ceiling it claims.**
`policy_name` and `policy_source` are the selected policy's `name` and `source`, or both
`None` when no policy is selected. `ceiling_enforced` is `True` only when a policy is
selected **and** every currently registered tool is permitted by it — it is checked against
the registry being reported, not inferred from selection. A registry that has drifted past
its ceiling therefore reports `ceiling_enforced is False` with a non-null `policy_name`,
which is the honest reading of that state. Drift is reachable by construction:
`configure_arbitrary_command_tool(True, app)` with its default `permits=None` registers
`hmc_run_command` on an application whose ceiling excluded it. No current caller exhibits
it — `scripts/live_test_runner.py:2911` uses that two-positional call shape, but against
the unfiltered module-level `mcp` (`:37`), which has no ceiling to exceed.

**R15 — Inspection reports grants individually.** `declared_grants` has one entry per
grant in document order, each carrying that grant's sorted tool names, sorted connection
tokens with `None` rendered as `"<default>"`, an `all_targets` boolean, and a `targets`
mapping of target kind to sorted selector strings — empty when `all_targets` is true.
Connections and targets from different grants are never merged.

**R16 — Inspection states what is enforced, and does not overstate it.**
`enforced_dimensions == ("tools",)` and `declared_only_dimensions == ("connections",
"targets")` exactly when `ceiling_enforced` is `True`; both are `()` otherwise. They are
derived from `ceiling_enforced` and share its verification, so no output can pair a claim
of tool enforcement with a registry that exceeds the ceiling. ADR 0037 records the same
derivation.

> **Amended by [ADR 0047](../../adr/0047-per-dimension-enforcement-labels.md)**
> (2026-08-19). **The encoding this requirement froze was wrong, and issue #254 is the
> report of it.** "Both are `()` otherwise" makes a drifted registry drop all three
> dimensions from a report that goes on enumerating the connections and targets its grants
> name — reproduced against `main` at `c5d0b58`, alongside a live denial proving the
> connection dimension was enforcing at the time the report said nothing was. The error is
> that `ceiling_enforced` verifies the **tool** dimension and this requirement spent that
> one verification on three labels. R16 now reads:
>
> `enforced_dimensions` and `declared_only_dimensions` partition `("tools", "connections",
> "targets")` whenever a policy is selected, and are both `()` when none is. `"tools"` is
> enforced exactly when `ceiling_enforced` is `True`. `"connections"` is enforced exactly
> when every reported tool that routes a connection is registered carrying the dispatch
> wrapper (ADR 0038). `"targets"` is enforced exactly when no reported tool escapes a
> target constraint a grant declares (ADR 0039): a tool with no connection argument
> registers unwrapped, so it costs this label when it declares selectors or when a
> `targets` table reaches it. Both dispatch labels are withheld for a name outside the
> authoritative index. Each label is verified against the registry being reported, so no
> output pairs a claim of enforcement in a dimension with a registry that is not performing
> it, and none withholds a label for a dimension that is.
>
> The rest of this specification is unaffected; R14's `ceiling_enforced` definition and
> R17's closed value allowlist are unchanged, and no field is added or removed.

**R17 — Inspection carries no credential, stated as a closed allowlist.** The output
contains exactly the fields of `EffectivePermissions` and nothing else, and every string
value in it is drawn from one of five sources: (a) a tool name, operation, effect, or
target kind read from the compiled-in `TOOL_SECURITY` index; (b) an operator-authored
identifier in the selected policy document (its name, a connection token, a target
selector); (c) the resolved policy path; (d) a fixed literal defined in
`server_tools/permissions.py` or `access_policy.py` — the two dimension labels `"tools"`,
`"connections"`, `"targets"`, the `"unknown"` classification fallback, and
`DEFAULT_CONNECTION_TOKEN` (`"<default>"`), which is synthesised for a compiled `None`; and
(e) a tool name read from the live registry. (e) is the only unbounded source — a name
reaches it only by someone calling `mcp.tool(...)` on the composed application, so it is
bounded by who holds that object, not by this design. A new field, or a value from a sixth
source, fails this requirement. Concretely and negatively: no value is read from `config.toml`, from an
`HMC_*` environment variable, or from the HMC. The policy path is *not* claimed to be
environment-free — `config_dir()` builds it from `XDG_CONFIG_HOME` on Linux and `APPDATA`
on Windows, so on those platforms it embeds an environment value by construction, and it
is disclosed deliberately.

**R18 — Withholding the inspection tool is visible.** When the served policy does not
permit `hmc_effective_permissions`, one warning line naming the tool and the policy is
written to stderr, then the server starts normally. Suppressed when R10a fires.

**R19 — An authored but unselected policy file is visible, and the check cannot fail the
start.** When no policy is selected and `resolve_access_policy_path()` names a file that
exists, one warning line is written to stderr saying the file is present, no policy was
selected, and no ceiling is applied; then the server starts normally. When the file does
not exist, nothing is written. When `resolve_access_policy_path()` raises `RuntimeError` or
`OSError` — `Path.home()` under a uid with no passwd entry and no `HOME` — the warning is
skipped and the server starts normally; it never propagates.

**R19a — An explicitly requested escape hatch the policy withholds is visible.** When
`--enable-arbitrary-command` is passed and the served policy does not permit
`hmc_run_command`, one warning line naming the flag and the policy is written to stderr,
then the server starts normally. The mirror case — the policy permits it and the flag is
absent — produces no warning.

**R20 — Both registration sites apply the same gate.** `register_permissions_tool` takes
`permits` and applies it itself. `create_mcp` passes the same predicate to it and to every
domain module, and applies no ceiling check of its own.

**R22 — Documentation matches.** `README.md` documents `serve --access-policy NAME`, the
four stderr warnings (R10a, R18, R19, R19a), and `hmc_effective_permissions` in its
read-only tool table. Where it documents that tool it states the disclosure: the tool
returns the policy name, its absolute path, every connection token, and every target
selector to any MCP client that can call it. Any policy that neither grants the `read`
effect class nor names the tool in a grant's `tools` withholds it; a policy granting
`read` reaches it and cannot exclude it. `just verify` does not check this, so it is
a requirement rather than a gate.

**R21 — Guardrails.** `just verify` passes bare, including the 90.00% coverage floor and
the no-`# pragma: no cover` rule in `tests/test_ci_pipeline.py`.

## Design

### Filtering during composition

`tool_module()`'s closure already holds a `list[ToolDefinition]`, each carrying `name`,
`handler`, and `security`. `register_tools` gains a keyword-only `permits` predicate and
skips a definition whose name the predicate rejects. `None` means no ceiling, which is
both the default and the only value any existing caller passes.

`server.create_mcp(policy=None)` derives `permits = None if policy is None else
policy.permits_tool` once and passes it, unchanged, to every domain module and to
`register_permissions_tool`. `create_mcp` itself performs no ceiling check: each
registration site applies the predicate, so there is one contract rather than one gate in
the loop and a hand-applied copy beside it. Nothing is ever registered and removed.

### The inspection tool

`src/hmc_mcp/server_tools/permissions.py` owns the result types and a factory:

```python
@dataclass(frozen=True)
class ToolPermission:
    name: str
    effect: str
    operation: str
    target_kind: str

@dataclass(frozen=True)
class DeclaredGrant:
    tools: tuple[str, ...]
    connections: tuple[str, ...]
    all_targets: bool
    targets: dict[str, tuple[str, ...]]

@dataclass(frozen=True)
class EffectivePermissions:
    policy_name: str | None
    policy_source: str | None
    ceiling_enforced: bool
    effects: tuple[str, ...]
    tools: tuple[ToolPermission, ...]
    declared_grants: tuple[DeclaredGrant, ...]
    enforced_dimensions: tuple[str, ...]
    declared_only_dimensions: tuple[str, ...]

EFFECTIVE_PERMISSIONS_SECURITY: ToolSecurity
def register_permissions_tool(
    mcp: FastMCP,
    policy: AccessPolicy | None,
    tool_security: Mapping[str, ToolSecurity],
    *,
    permits: Callable[[str], bool] | None = None,
) -> None: ...
```

`register_permissions_tool` returns without registering anything when `permits` rejects
`hmc_effective_permissions` — the gate lives here, not in `create_mcp`. Otherwise it builds
an async, argument-free handler named `hmc_effective_permissions` closing over `mcp`,
`policy`, and `tool_security`, and registers it with `annotations_for("read")`. The handler
reads the live registry through `await mcp.local_provider.list_tools()` — the provider, not
`FastMCP.list_tools()`. Three reasons: the provider is what `configure_arbitrary_command_tool`
mutates, so it is the registry the design actually reasons about; `FastMCP.list_tools()`
runs the `tools/list` middleware chain, session transforms, and per-session auth filtering,
so calling it from inside a `tools/call` would emit a phantom `tools/list` event into
whatever #224 hangs there and make the output session-dependent while `ceiling_enforced`
stayed policy-derived; and reading a different accessor than the test asserts against keeps
R12 from being a tautology. It maps each name through `tool_security`, falling back to
`"unknown"` for all three classification fields rather than raising on a miss, and renders
the policy's compiled grants.

`server_tools/permissions.py` must not import `server.py` (`server.py` imports it), which is why
the tool index arrives as a parameter — the same one-way dependency ADR 0036 fixed for
`load_access_policy`.

`ceiling_enforced` is computed, not inferred: a policy must be selected *and* every name in
the read registry must satisfy `policy.permits_tool`. Both dimension tuples derive from it,
so a constant `("tools",)` can never accompany a registry that exceeds its ceiling, and the
permissive default cannot claim an enforcement it is not performing. Their *contents* are
literals in this module and change when #222 and #223 land.

`DeclaredGrant.tools` carries the compiled grant's *resolved* tool set, so a grant authored
as `effects = ["read"]` and one authored as a ninety-name `tools` list render identically
and the authored `effects` list is not recoverable from the output. Retaining it would mean
adding a field to `access_policy.Grant`, which this change lists as read-not-changed. The
omission is accepted here and tracked as issue #251; it costs an operator comparing the
file against the tool's output a hand re-derivation of the effect expansion.

### Startup selection

`serve` gains `--access-policy NAME`. It loads the policy, converts `AccessPolicyError`
into the CLI's standard `_fail` path (stderr + exit 1), and passes the compiled object to
`main_stdio` / `main_http` as `access_policy`. Loading and error reporting are all `serve`
does with the policy.

**All four warnings live in one function**, `server._startup_warnings`, called by both entry
points after `create_mcp` and after `configure_arbitrary_command_tool`. That is the only
point at which every input exists at once — the served registry, the policy, and the
arbitrary-command flag — so keeping them together is what makes R10a's suppression of R18 a
single ordered decision rather than a coordination between two modules. It returns the
lines to write, so it is testable without capturing stderr; the entry points write them to
`sys.stderr` (never stdout, which carries JSON-RPC under stdio). The order is: empty surface
(suppressing the withheld-inspection-tool line), otherwise withheld inspection tool;
then authored-but-unselected; then withheld escape hatch.

The authored-but-unselected check reads no file — `resolve_access_policy_path()` plus
`Path.exists()` — and the resolution is wrapped in `try`, because it reaches `Path.home()`,
which raises `RuntimeError` where no home directory can be determined. A diagnostic that
can abort a start nobody asked to constrain is worse than no diagnostic.

### Errors

| Condition | Behaviour |
|---|---|
| `--access-policy` names a policy the file lacks | `AccessPolicyError` from `compile_access_policy` → exit 1, message names the file and available policies |
| `access-policy.toml` absent | `AccessPolicyError` "cannot be read" → exit 1 |
| Policy invalid (any ADR 0036 rule) | `AccessPolicyError` with the module's rendered message → exit 1 |
| Composed application registers zero tools (`grants = []`, or a ceiling of only `hmc_run_command`) | `serve` warns the surface is empty and starts; this warning replaces the withheld-inspection-tool one |
| Policy withholds `hmc_effective_permissions` | Warning on stderr; server starts |
| No `--access-policy`, no policy file on disk | No ceiling, no output; unchanged behaviour |
| No `--access-policy`, policy file present | Warning on stderr; server starts with no ceiling |
| No `--access-policy`, policy path unresolvable (`RuntimeError`/`OSError`) | No warning; server starts with no ceiling |
| `--enable-arbitrary-command` with a policy that withholds `hmc_run_command` | Warning on stderr; server starts without the tool |

### Testing

Behaviour, not implementation. New module `tests/app/test_capability_ceiling.py` covers
R1–R3, R5–R6, R11–R17 by composing applications from policies built with
`compile_access_policy` over `server.TOOL_SECURITY` and inspecting the resulting
registries. R12 is asserted by calling the registered inspection handler through the
application and comparing against `app.list_tools()` — including after toggling the
arbitrary-command tool, which is the case a recomputed answer would get wrong.

Two empty-surface cases get their own tests — `grants = []` and a ceiling of only
`hmc_run_command` — each asserting zero registered tools and the single empty-surface
warning. R9a is asserted at the entry point: `main_stdio` with
`--enable-arbitrary-command` and a policy withholding `hmc_run_command` serves an
application whose registry lacks it. R14's drift case is asserted directly:
`configure_arbitrary_command_tool(True, app)` with no predicate, then
`ceiling_enforced is False` with a non-null `policy_name` and empty dimension tuples. `README.md` is updated in the same change (R22).

> **Amended by [ADR 0047](../../adr/0047-per-dimension-enforcement-labels.md)**
> (2026-08-19). The drift case now asserts `enforced_dimensions == ["connections",
> "targets"]` and `declared_only_dimensions == ["tools"]` — the empty tuples above were the
> defect — and pairs the payload assertion with a live denial on an ungranted connection,
> so the claim is checked against the server's behaviour rather than against itself.

Existing tests that change: `tests/app/test_application_boundaries.py` (128 → 129),
`tests/app/test_tool_security.py` (G10 becomes a two-name allowance),
`tests/app/test_profile_routing.py` (`_NO_NETWORK_TOOLS` gains the new tool),
`tests/app/test_serve.py` (serve call assertions gain `access_policy`, and the entry
points no longer use `server.mcp`).

## Threat model

**Boundary inventory.** Boundaries this design *adds*: (a) a new MCP tool
`hmc_effective_permissions`, callable by any client that reaches the server, returning a
description of the server's own authorization state; (b) a new CLI option
`--access-policy NAME`, supplied by the operator starting the process, whose value
selects a key inside an operator-owned file. Boundaries this design *narrows*: the MCP
tool surface itself, which the ceiling now bounds. No boundary is widened in the reach
sense — no tool becomes callable that was not callable before.

**Actor model.** The untrusted party is the MCP client and the agent driving it: it
supplies tool calls and arguments, and ADR 0036 already records that a client-facing
annotation is advisory because this party is the one that would have to honour it. The
operator who starts the process and writes `access-policy.toml` is trusted — the file
sits at the same trust level as `config.toml`, and anyone who can write it can widen the
ceiling. The HMC is a trusted downstream service. This design places no trust in the
client and does not change what the operator is trusted with.

One assumption this model makes explicit, because the design leans on it: the four stderr
warnings reach the operator only where stderr is observed. `serve`'s default and documented
transport is stdio, launched as a subprocess by an agent host, so stderr is that host's log
file rather than a terminal anyone is watching. Under stdio the warnings are an audit trail
to be read after the fact, not an interactive prompt, and a policy that withholds
`hmc_effective_permissions` leaves no runtime evidence of the ceiling at all until #224
adds audit events. Under `--http` the operator started the process directly and stderr is
theirs.

**Control per boundary.**

- *MCP tool surface.* Control: the ceiling, applied at registration, derived from the
  policy's grants by `access_policy.py`. Fails closed only within a selected policy; with
  no policy selected there is no control, by the decision recorded in ADR 0037 and owned
  by #225. Leaks on failure: nothing — a withheld tool is absent, not refused.
- *`hmc_effective_permissions`.* No authorization beyond the ceiling that governs every
  tool: the tool is `read`, takes no arguments, performs no I/O, and reaches neither the
  HMC nor `config.toml`. Its disclosure is bounded by construction — it reads
  `TOOL_SECURITY` (compiled-in), the live registry, and the compiled `AccessPolicy`, and
  the policy document's grammar (`extra="forbid"`, four known keys) admits no field a
  credential could occupy. The policy file's absolute path is disclosed deliberately;
  ADR 0037 records why. Stated positively rather than by comparison: the tool discloses to
  the MCP client the policy name and absolute path, every connection-profile *token* the
  policy names — the same keys `config.toml` defines — and every target selector string,
  which are LPAR, managed-system, VIOS, cluster, and user names. It is tempting to bound
  that by noting `hmc_list_configured_hosts` already returns each profile's host and user
  to the same client, but this change is what makes that tool withholdable: under a
  `tools`-only policy that withholds it, inspection becomes the *widest* configuration
  disclosure on the surface, and under an `effects = ["read"]` policy the inspection tool
  cannot be withheld at all. An operator who considers the policy's contents sensitive
  writes a policy that neither grants the `read` effect class nor names
  `hmc_effective_permissions` in a grant's `tools`.
- *`--access-policy NAME`.* The value is operator-supplied and used only as a dictionary
  key inside the parsed document. It is never a path, never interpolated into a command,
  and `compile_access_policy` already `repr()`s it in the not-found message so a name
  carrying a control character cannot forge a log line. Validation of the file's contents
  is `access_policy.py`'s, already reviewed under ADR 0036.
- *Composed application.* Control: construction. `create_mcp` never registers a tool the
  predicate rejects, so there is no window in which the registry exceeds the ceiling.

**Explicitly out of scope.**

- A caller may still pass any `profile` value to any permitted tool; `connections` is
  declared, not enforced. Owner: #222. Inspection labels it.
- A caller may still name any target; `targets` is declared, not enforced. Owner: #223.
  Inspection labels it.
- No record is made of a call to a permitted tool, nor of a client probing for a withheld
  one. Owner: #224.
- A deployment that starts with no `--access-policy` has no ceiling. Owner: #225.
- The policy file's permissions are not checked. ADR 0036 decided this: checking one
  credential-adjacent file and not the other is theatre.
- Denial-of-service through repeated `hmc_effective_permissions` calls is not bounded.
  The handler does no I/O and no HMC call. Its result is proportional to the registry plus
  the sum of each grant's *resolved* tool set — an `effects = ["read"]` grant expands to
  roughly ninety names, and two grants differing by one connection token are distinct, so
  the size is operator-controlled rather than registry-bounded. Both terms are fixed for
  the process lifetime once the policy is compiled, and the handler remains cheaper than
  every tool that reaches the HMC.

## Open questions

None. The one design-changing question — behaviour when no policy is selected — is
decided in ADR 0037 and owned onward by #225.
