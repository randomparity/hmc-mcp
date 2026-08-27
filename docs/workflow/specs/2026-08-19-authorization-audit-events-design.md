# Structured, redacted authorization audit events — design

Issue: [#224](https://github.com/randomparity/hmc-mcp/issues/224). Epic:
[#218](https://github.com/randomparity/hmc-mcp/issues/218), requirement 7 and decomposition
entry 6. Decision record: [ADR 0040](../../adr/0040-authorization-audit-events.md), which owns
every choice with a viable alternative; this document specifies what gets built and how it is
proven.

## Goal

Every authorization decision made at the MCP dispatch boundary writes one structured, redacted
record to the server process's logging sink, so an operator can answer what a connected agent
attempted, what was permitted, what was refused, and why — without any credential, whole argument
set, command text, generated document, or response body appearing in the output, and without the
record being readable by the agent whose calls it describes.

## Non-goals

- Durable retention, sequencing, integrity protection, or export. #218's open questions place
  these outside this saga.
- Closing the permit/deny oracle carried forward from #222. This design records it; ADR 0040
  states the residual.
- Suppressing FastMCP's traceback rendering of a routine denial (issue #267).
- Any audit of direct CLI or reusable Python API calls, which do not cross this boundary
  (ADR 0029).
- Any new environment variable, CLI flag, or configuration key.

## Architecture

One source file is added, three change, and one operator document is added.

| file | responsibility after this change |
|---|---|
| `src/hmc_mcp/audit.py` (new) | the record's field set, its rendering, the reason-code vocabulary, and the sink. Imports nothing from the package. |
| `src/hmc_mcp/dispatch_scope.py` | unchanged decision; additionally assembles and emits exactly one record per decision it reaches. |
| `src/hmc_mcp/target_scope.py` | gains `denial_reason`, the single owner of the four-way target-denial case selection; `target_denial` reads it instead of repeating it. |
| `src/hmc_mcp/server.py` | `_serve_application` installs the sink, beside its existing `_warn` call. |
| `src/hmc_mcp/operations/lpar.py` | `_audit_lpar_ownership_override`'s body becomes a call into `audit`; the two call sites and the rest of the file are untouched. Converges the package's second audit emitter (`Refs #268`). |
| `tests/unit/test_ownership.py` | `test_authorize_lpar_mutation_override_is_audited` asserts `record.getMessage() == "LPAR ownership override approved"` and reads `record.hmc_system` / `hmc_lpar` / `hmc_agent_id` off the `extra=` payload on logger `hmc_mcp.operations.lpar` — exactly what convergence removes, so it is **replaced** by test 26a rather than left to fail. Its sibling `…_normal_access_has_no_override_audit` still asserts the right thing but would pass vacuously against the old logger name, so it is repointed too. |
| `docs/authorization-audit.md` (new) | the operator-facing contract for both records: field sets, reason-code table, logger name, level split, how to route or silence them, the merged-descriptor caveat, the `<default>`/`<unresolved>` reserved-rendering collision, and the instruction to skip a non-parsing line rather than fail (the reservation is checked inside this package only). |
| `README.md` | one caveat beside the existing "never stdout" sentence in the startup-warnings section, which has the same descriptor-merge limit, plus a pointer to the new document. |

`audit.py` importing nothing from `hmc_mcp` is a hard constraint, not an accident: `target_scope`
imports the reason-code type from it, so any import back would be a cycle. Every value reaches
`audit.record` as a primitive, with exactly one exception: **`attribution.claim` is read by
`audit` itself**, as `os.environ.get("HMC_AGENT_ID")`. It has to be. Test 8b forbids every module
on the decision path from naming that variable, so `dispatch_scope` cannot pass it in, and reading
it through `HMCConfig` would apply validators that reject exactly the malformed values worth
recording (test 4). This is the one place `audit` reaches outside its arguments.

### Data flow

```
tool_registry.authorized  ->  dispatch_scope.authorize
                                 |
                                 |-- selected_connection(token)   (may raise ConnectionScopeError)
                                 |-- selected_targets(security, arguments)
                                 |-- per-grant conjunction over policy.grants_for(tool)
                                 |
                                 +-> audit.record(...)  exactly once
                                        |
                                        +-> logging.getLogger("hmc_mcp.audit")
                                               |
                                               +-> _AuditHandler -> sys.stderr   (serve path only)
```

The record is written **before** the denial is raised and **before** the permitted handler runs,
so a record can never describe a call that did not reach the boundary, and an outbound HMC request
can never precede its own record.

One arm needs new control flow and is called out so an implementer does not have to infer it from
the diagram: `selected_connection` *raises* `ConnectionScopeError`, and `authorize` has no
`try`/`except` today. It gains one — around that call only — which emits the
`configuration-unreadable` record with `targets` and `connection.resolved` both `null` and then
re-raises the original error unchanged. Emitting inside `connection_scope` instead would put a
second emitter in the package, which is the thing this design exists to avoid.

## The record contract

One log record per decision. Its message is the complete record: a single line of
`json.dumps(payload, ensure_ascii=True)` output, with no prefix, no trailing text, and no
`Formatter` applied by the sink this package installs. The handler writes `message + "\n"` in **one** `write` call and flushes. One call, not
message-then-terminator: `logging`'s handler lock serialises audit records against each other but
against nothing else writing to `sys.stderr`, and two other writers share that stream on this exact
path — FastMCP's 41-line `rich` traceback panel (#267) and `server._warn`. A record split by
another writer's output is a line that does not parse, which the operator documentation tells
consumers to skip, so the failure mode is a silently lost record under exactly the denial-storm
load the oracle residual says to expect. A custom `emit` also does not inherit
`StreamHandler.terminator`, so the newline is explicit either way.

Two event types share that grammar and differ in their fields. `time` and `event` come first on
both; every caller-supplied value is truncated to 128 characters on both.

| `event` | emitted by | level | fields after `time`, `event` |
|---|---|---|---|
| `authorization` | `dispatch_scope.authorize` | deny `WARNING`, allow `INFO` | `policy`, `tool`, `effect`, `decision`, `reason`, `connection`, `targets`, `attribution` |
| `ownership-override` | `operations.lpar`, via `audit` | `WARNING` | `system`, `lpar`, `attribution` |

`ownership-override` omits the authorization fields rather than nulling them: an ADR 0011
ownership override is not an access-policy decision, and empty fields would read as one. Its
`attribution.source` is `"config:agent_id"` — `hmc.config.agent_id or "hmc-mcp"`, the effective
value the ownership check compared — against `"environment:HMC_AGENT_ID"` for the authorization
record. `verified` is `false` on both.

### The `authorization` record

Fields, always present, in this order:

| field | type | value |
|---|---|---|
| `time` | string | `datetime.now(timezone.utc).isoformat()` |
| `event` | string | the constant `"authorization"` |
| `policy` | string | the selected `AccessPolicy.name` |
| `tool` | string | the MCP tool name |
| `effect` | string | `ToolSecurity.effect` |
| `decision` | string | `"allow"` or `"deny"` |
| `reason` | string | one of the seven codes below |
| `connection` | object | `{"state", "selector", "resolved"}` |
| `targets` | array or null | `null`, or a list of `{"kind", "argument", "state", "value"}` |
| `attribution` | object | `{"claim", "source", "verified"}` |

`connection.state` ∈ `{"present", "absent", "unreadable"}`, mirroring
`connection_scope.selected_connection`'s arms: a string token is present, `None` or `""` is
absent, any other type is unreadable. `connection.selector` is the caller's own string when
present and `null` otherwise — a value of an unexpected type is never rendered.
`connection.resolved` is the profile key the call would use, `"<default>"` for the
environment/default connection, `"<unresolved>"` when the token names nothing configured, or
`null` in the `configuration-unreadable` case, where nothing was resolved at all.

`"<default>"` and `"<unresolved>"` are **reserved renderings sharing a string space with legal
profile keys**: `selected_connection` returns the caller's token verbatim when it names a profile,
so a `config.toml` profile literally named `<unresolved>` is indistinguishable from a token that
resolved to nothing — and it silently joins the result set of the `resolved == "<unresolved>"`
filter ADR 0040 recommends. A profile named `<default>` is stranger still: `access_policy`
compiles the policy-side `<default>` to `None`, so no compiled grant can hold that string and such
a call always denies, while rendering like the ordinary permit case. Narrow — it needs an oddly
named profile — so it is documented and pinned by a test rather than escaped, which would cost
more mechanism than the risk earns.

`targets` is `null` when the decision was reached before selectors were extracted (the
`configuration-unreadable` case alone) and a list otherwise; `[]` means the tool declares no
selector. Each entry's `state` ∈ `{"present", "absent", "unreadable"}` is taken from what
`target_scope.selected_targets` already computed — a `str`, the `ABSENT` singleton, or the
`UNREADABLE` singleton respectively — and `value` is the truncated caller string when present and
`null` in both singleton cases. The singletons are never passed to `json.dumps`; they are not JSON
values, so doing so would raise `TypeError` inside authorization on the two routine
selector denials.

`_value`'s two remaining arms are part of the contract and are easy to overlook. An **integer**
selector — `vios_partition_id`, the surface's only one, on three tools — is coerced by
`target_scope` and records `state="present"` with its decimal rendering, a string the caller did
not literally send. A **boolean** records `state="unreadable"`, because `bool` is tested before
`int` so that `True` cannot compare as `"True"` against a resource name.

`attribution` is always `{"claim": <str|null>, "source": "environment:HMC_AGENT_ID",
"verified": false}`. **The claim is the raw environment value** — unvalidated, bounded at 128
characters, JSON-escaped. `docs/environment-variables.md` documents `HMC_AGENT_ID` as 1-64
printable ASCII with a forbidden-character set, which is `config.validate_agent_id`'s contract for
*configuration*; this record deliberately bypasses it, so a recorded claim may be wider and
stranger than that contract allows. The operator document must say so, or the two documents
contradict each other. The claim identifies the server *process*: under stdio that is one client,
under streamable HTTP it is every client, so the field carries no per-caller information there.

### Reason codes

| code | decision | raised by |
|---|---|---|
| `permitted` | allow | — |
| `configuration-unreadable` | deny | `ConnectionScopeError` from `selected_connection` |
| `connection-not-granted` | deny | `connection_denial` |
| `target-selector-unreadable` | deny | `target_denial` case 1 |
| `target-unboundable` | deny | `target_denial` case 2 |
| `target-selector-absent` | deny | `target_denial` case 3 |
| `target-not-granted` | deny | `target_denial` case 4 |

The vocabulary is closed. `audit.Reason` is a `Literal` and `audit.REASONS` the derived
`frozenset`, mirroring `tool_registry.Effect` / `EFFECTS`.

There is no `connection-selector-unreadable` beside `target-selector-unreadable`: `reason` names
the decision and the `state` fields name the input, and the connection dimension has exactly one
denial template by ADR 0038's design. A malformed connection token is reported by
`connection.state == "unreadable"`.

### Bounding

Every caller-supplied value — `connection.selector`, each target `value`, and
`attribution.claim` — is truncated to **128 characters** before rendering. No truncation marker is
appended (ADR 0040). Everything else in the record is a compile-time constant, the policy name, or
the resolved connection, all of which the operator authored.

The number of target entries is bounded by the tool's own `ToolSecurity.targets`, fixed at import;
the largest in this checkout is 3.

### Levels and routing

- deny → `WARNING`; allow → `INFO`; logger name `hmc_mcp.audit`, reserved for `audit.record` —
  nothing else in the package logs to it or below it, so every message on it is a complete record.
- A handler an operator attaches to `hmc_mcp.audit` must not write to `sys.stdout` under stdio:
  `install_audit_sink` defers to an existing handler without inspecting it.
- Compatibility rule for the record: a field may be added, never renamed, removed, or retyped; a
  reason code may be added, never repurposed; consumers ignore what they do not know.
- `audit.install_audit_sink()` sets `propagate = False` unconditionally; attaches its handler only
  when the logger carries none; and sets the level to `INFO` only when the level is `NOTSET`. What
  the operator configured wins, what they left unconfigured gets a default.
- `server._serve_application` calls it; `create_mcp` does not, and neither does import.

## Error handling

| condition | behaviour |
|---|---|
| `sys.stderr is None` (fd 2 closed at interpreter start) | the handler returns without writing; nothing raises |
| write raises `OSError` (broken pipe) | caught; the record is dropped |
| write raises `ValueError` (closed stream) | caught; the record is dropped |
| stderr is open but undrained (a full pipe) | `write` blocks; not covered, and not detectable in-process. Residual in ADR 0040, filed as #269 |
| a tool declares `connection_argument = None` | no record; the authorizer is not on its dispatch path |
| a malformed call raises `KeyError` on a declared argument | no record; no decision was reached |
| the sink is not installed (library, CLI, in-process composition) | `INFO` permits are dropped by level. `WARNING` records — every denial, and the ownership override — reach `logging.lastResort` on stderr, which `Logger.callHandlers` consults whenever the walk finds **zero** handlers; `propagate` does not affect that condition, so closing propagation costs an unconfigured caller nothing. They do **not** reach an ancestor handler. Nothing raises. |

Emission is total: building a record and writing one both drop on failure, leaving the
authorization outcome and the exception carrying it unchanged. The handler resolves `sys.stderr`
at emit time rather than binding it at install time, matching `server._warn`.

Two preconditions bound what the trail contains, and both are stated rather than assumed:

- **No policy, no *authorization* records.** `server._gates` returns `(None, None)` without a
  selected policy and `tool_registry.authorized` then leaves every handler unwrapped, so no
  authorizer runs. A default `hmc-mcp serve` installs the sink and emits no authorization record,
  for the same reason it enforces nothing. #225 changes that default. **The ownership-override
  record is not policy-gated** — it comes from the ADR 0011 check inside the handler, and from the
  CLI and Python API paths where no policy exists at all — so an unpolicied server still produces
  those, and only those. Line 217's error-handling row already had this right; this bullet did
  not.
- **No level lever from the CLI.** `hmc-mcp serve` exposes no logging option and hands control to
  `.run()`, so only an in-process caller of `main_stdio`/`main_http` can set the level before
  `install_audit_sink` runs. Filed as #270.

## Threat model

### Boundary inventory

Boundaries this design **adds**:

1. **Caller-supplied values → the audit sink.** A connection token, target selector values, and
   (indirectly) `HMC_AGENT_ID` are rendered into a stream an operator or a log pipeline reads.
2. **The audit sink → the process's output streams.** Under the stdio transport, stdout carries
   JSON-RPC framing; anything written there corrupts the protocol.
3. **The audit sink → whoever reads the operator's logs.** `connection.resolved` discloses a
   `config.toml` profile key that the caller's own token need not equal.

Boundaries this design **widens**: none. It reads no new input, opens no socket, and adds no
argument, flag, environment variable, or file.

### Actor model

- **The calling MCP agent** is untrusted. It controls every tool argument, including the
  connection token and every target selector, and it may be prompt-injected or compromised. It
  reads tool results. It does not read the server process's stderr through the MCP protocol.
- **The operator** is trusted. They author `config.toml` and `access-policy.toml`, set
  `HMC_AGENT_ID`, and choose where logs go. `connection.resolved` and `policy` disclose only what
  they already wrote.
- **The agent's host process** launches the server and may capture its stderr. The design places
  its trust here explicitly: the record is not in the JSON-RPC response channel, which is a
  property of this code; whether a particular host surfaces captured stderr back to the model is a
  deployment property this design does not control and does not claim to.

### Control per boundary

| boundary | control |
|---|---|
| 1 — caller values into the sink | only *declared* selectors and the connection token are rendered; the whole argument mapping is never serialized. Each value is truncated to 128 characters. `json.dumps(ensure_ascii=True)` escapes every control character, escape sequence, and non-ASCII codepoint, so no value can forge a line or move a terminal cursor. A value of an unexpected type is recorded as a state, never as a `repr()`. |
| 1 — credentials | structurally absent: `authorize` receives only the tool name, its `ToolSecurity`, and the bound arguments. No `HMCConfig`, client, session token, or password is in scope at the emission point. |
| 1 — command text, documents, response bodies | structurally absent: `hmc_run_command` declares no target selector, so `cmd` is never extracted; the record is written before the handler runs, so no response body or generated document exists yet. |
| 2 — output stream | the installed handler writes only to `sys.stderr`, resolved at emit time and skipped when `None`; the logger does not propagate, so no ancestor handler — including one pointed at stdout — can receive the record. Three routes stay open and are stated rather than claimed closed: a launcher merging fd 2 into fd 1 (`serve 2>&1`), which no in-process choice detects; a handler the operator attaches to `hmc_mcp.audit` itself, which `install_audit_sink` defers to without inspecting (pinned by test 14a); and — until this change closed it — the in-process composition path. `propagate = False` is now set where the logger is *defined*, not only inside `install_audit_sink`, so an embedder composing with `create_mcp` and serving stdio itself no longer reaches a root stdout handler (#272). Two routes remain open, both outside any in-process choice. |
| 3 — resolved connection | accepted and stated. The record goes to the operator's sink, not to the tool result; ADR 0038 deferred this question here on that ground. An operator forwarding the sink onward inherits the disclosure. |
| availability | the handler catches `OSError` and `ValueError` and returns on a `None` stream, so an unavailable destination drops records instead of aborting a call or a start (#221). |

### Explicitly out of scope

- **The permit/deny oracle** (#222). Recorded, not closed; ADR 0040 states why and what closing it
  would cost.
- **Log integrity and retention.** Records are neither signed, sequenced, nor persisted. An
  operator with write access to the sink can forge or delete one.
- **Denial-of-service by log volume.** An agent calling a denied tool in a loop writes one
  `WARNING` per call. Levels are the only lever offered; rate limiting is rejected in ADR 0040 as
  a new DoS surface aimed at the operator's own agents.
- **Traceback noise on a denial** (#267).
- **A launcher that merges fd 2 into fd 1.** Recorded as an ADR 0040 residual and stated in the
  operator documentation and beside README's existing "never stdout" sentence, which has the same
  limit for the four startup warnings today.
- **The audit level split from the CLI** (#270) and **retention or integrity** of either record.

## Testing

Every criterion below is falsifiable and gets a test.

### Logging isolation — a precondition of every test below

`install_audit_sink()` mutates process-global state: it sets `propagate = False` on
`hmc_mcp.audit` unconditionally, attaches a handler, and sets a level. Tests 9, 10, 14 and 14a
call it, test 10 attaches a `StreamHandler(sys.stdout)` to the **root** logger, and 14a
pre-attaches one to `hmc_mcp.audit`. `just verify` runs `pytest -q` in a single process and
`tests/conftest.py` has no logging fixture, so without a contract the first of those tests to run
leaves `propagate = False` for the rest of the session — and every later test that reads records
through `caplog` (which attaches at the root) sees nothing and passes vacuously.

It is not only those four. Once the sink is installed on the serve path, `_serve_application`
calls it too, and four existing tests drive that function in-process —
`tests/app/test_capability_ceiling.py:489,504,553` and
`tests/app/test_connection_authorization.py:418` — none of which is an audit test.

Nor is it only the files that *read* records. Once the override is converged, six further test
files emit one by driving `ownership_override=True`. Any list of "files that need isolation" goes
stale, so there is no list.

So: **one `autouse=True` fixture in `tests/conftest.py`**, covering the whole suite. It resets
`hmc_mcp.audit` at setup (no handlers, `NOTSET`, `propagate = True`) and restores the snapshot at
teardown, over `handlers`, `level` and `propagate` on `hmc_mcp.audit` and `handlers` on
`logging.root`. Reset *and* restore: a fixture that only restored would faithfully restore
whatever contamination an earlier non-audit test left, which is the failure it exists to prevent.
In `conftest.py` rather than a helper module each file imports, because that import binds a name
the module never references — ruff `F401`, and this checkout runs ruff's default rule set.

And a precondition on the redaction tests specifically: tests 22-26 and 26a-26b assert that a
sentinel is *absent*, which is trivially true of an empty capture. Each must first assert that at
least one record was captured, or it proves nothing on a session where isolation has silently
removed the records it was meant to search.

### Mutation verification, per test, with the mutation named

"Break the redaction and watch it redden" is not a procedure here, because most of what these
tests assert is **structural absence** rather than a redaction step: `authorize` is handed only
the tool name, its `ToolSecurity`, and the bound arguments; `hmc_run_command` declares no target
selector, so `cmd` is never extracted; and the record precedes the handler, so no response body
exists yet. There is no redaction function to break for those. A general instruction would let
every one of them pass on an implementation that never had the property.

So each mutation is named, applied one at a time, and the test ids that redden are recorded in a
table in the PR body:

| # | mutation | must redden |
|---|---|---|
| M1 | add `payload["arguments"] = dict(arguments)` to the record builder | 23, 24 |
| M2 | interpolate the `ConfigError`'s own message — which names the **config.toml** path — into the `configuration-unreadable` record | 26 |
| M3 | delete the truncation call in the value renderer | 3, 4, L4 |
| M4 | pass `ensure_ascii=False` to `json.dumps` | 2 |
| M5 | drop the `"\n"` from the handler's write | 15, L2 |
| M6 | make `install_audit_sink` set the level unconditionally | 14 |
| M7 | render the `ABSENT`/`UNREADABLE` singletons instead of mapping them | 14b, 17 |
| M8 | resolve the call's connection through `common.build_config` in the record builder and add `config.password` / `config.user` to the payload | 22 |
| M9 | move the `audit.record` call **out of** `dispatch_scope.authorize` into `tool_registry.guarded`, after `handler(...)`, and add the handler's return value to the payload | 17, 18, 25 |
| M10 | interpolate the resolved **access-policy** path into the record | 26 |

M1's reach is narrower than it looks and the table says so: test 22's sentinels are a
`config.toml` password and `HMC_PASSWORD`/`HMC_USER`, which are never tool arguments, so only M8
can redden it; and test 25's sentinel is what a stubbed handler *returns*, which only M9 can put
in a record. M2 and M10 exist separately because `config.toml` and `access-policy.toml` are two
different paths and test 26 must assert both are absent.

**A mutation that reddens no test is itself a finding.** It means the test asserts a structural
property and is claiming to prove a redaction it does not exercise; the fix is to reword the test
to assert the structure — and, where the structure is what protects us, to add a test that the
structure holds (test 8b below).

### Rendering — `tests/unit/test_audit.py`

1. A permitted decision renders every field, in order, with `decision="allow"` and
   `reason="permitted"`.
2. Output is a single line and is pure ASCII, for a target value containing `\n`, `\r`, `\x1b`,
   ` `, and an RTL override.
3. A 500-character selector value is truncated to exactly 128 characters, with no marker.
4. A 500-character `HMC_AGENT_ID` is truncated to exactly 128 characters.
5. A non-string connection token renders `state="unreadable"`, `selector=null`, and the token's
   `repr()` appears nowhere in the output.
6. `targets` and `connection.resolved` are both `null` for `configuration-unreadable`; `targets` is
   `[]` for a tool declaring no selector; `connection.resolved` is `"<unresolved>"` for a token
   naming nothing configured and `"<default>"` for an omitted one.
6a. An integer selector value records `state="present"` with its decimal rendering; a boolean
   records `state="unreadable"` with a `null` value. `_value`'s two arms are already covered
   (`tests/unit/test_target_scope.py:77,93`); what is not covered is the **mapping from its result
   to the record's `state`**, which is why `target_scope` gains `audit_state` beside `_value` and
   this test asserts against that rather than duplicating the existing two.
6b. A `config.toml` profile named `<unresolved>` renders `resolved == "<unresolved>"`,
   indistinguishable from a token that resolved to nothing. Asserted so the collision is a chosen
   behaviour with a documented caveat rather than a discovery.
7. `attribution` is `{"claim": null, "source": "environment:HMC_AGENT_ID", "verified": false}`
   when `HMC_AGENT_ID` is unset.
8. `REASONS` equals the `Literal`'s arguments, and every code the boundary can emit is in it.
8a. No module in `src/hmc_mcp` other than `audit` calls `logging.getLogger` with a name equal to
    or below `hmc_mcp.audit` — asserted by scanning the package, so the reservation is a checked
    invariant rather than a convention.
8b. **No module on the decision path reads the agent identity.** `dispatch_scope`,
    `connection_scope`, `target_scope`, `access_policy`, and `tool_registry` contain no reference
    to `HMC_AGENT_ID` or `agent_id`. This, not test 28's three samples, is what makes A4 an
    invariant: it catches a future read added to `grants_for` or to a connection rule, which
    sampling three environment values never would.

### Sink — `tests/unit/test_audit.py`

9. After `install_audit_sink()`, a record reaches `sys.stderr` and stdout receives nothing.
10. `install_audit_sink()` sets `propagate = False`; a `StreamHandler(sys.stdout)` on the root
    logger receives no audit record afterwards.
11. `sys.stderr = None` → emitting raises nothing and writes nothing.
12. A stream whose `write` raises `ValueError` → emitting raises nothing.
13. A stream whose `write` raises `OSError` → emitting raises nothing.
14. Calling `install_audit_sink()` twice attaches one handler; a pre-attached handler is left in
    place and not duplicated; and a level the operator set before the call survives it, while an
    unset (`NOTSET`) level becomes `INFO`.
14b. A record whose target selector is `ABSENT` or `UNREADABLE` renders without raising —
    `json.dumps` never sees a singleton.
14a. The deferral is deliberate, not accidental: with a `StreamHandler(sys.stdout)` pre-attached
    to `hmc_mcp.audit`, `install_audit_sink()` adds no second handler **and** a record does reach
    that stdout handler. Pins the documented hazard as a chosen behaviour.
14c. Emission is total, asserted **on the rendered payload** rather than only on the absence of
    an exception — a totality guard that swallows M7 would otherwise make this pass while the
    record silently vanished. A renderer forced to raise, and a logger forced to raise, both leave
    `dispatch_scope.authorize`'s outcome and its exception type unchanged. A non-string connection
    token also renders `state="unreadable"` with `reason="connection-not-granted"` — the
    asymmetry with the target dimension, asserted so it cannot drift silently.
15. The sink applies no `Formatter`: the line on stderr equals the record's message exactly, and
    two consecutive records land on two lines rather than one — the terminator is written. The
    handler issues **one** `write` call per record, asserted with a recording stream that counts
    them, so nothing else writing to stderr can land between a record and its newline.

### Boundary — `tests/app/test_authorization_audit.py`

16. One record per permitted call through a composed application, with the tool, effect, policy,
    connection, and targets the call actually used.
17. One record per denial, for each of the six deny reason codes, driven through a real composed
    application where reachable and through `dispatch_authorizer` directly otherwise.
18. Exactly one record per call — not zero, not two — across allow and deny.
19. A tool with `connection_argument = None` produces no record when its authorizer is called
    directly.
20. A malformed call (a declared argument absent from the bound arguments) produces no record and
    still raises.
21. Each of `target_scope`'s four `TargetScopeError` message templates maps to exactly one reason
    code, pinned as a table rather than derived — after `target_denial` is refactored to read
    `denial_reason`, asserting that the two "agree" is the same function call twice and cannot
    fail.
21a. `denial_reason` names the condition that actually held. For each of the four constructed
    denial inputs, `targets_permitted` returns `False` **and** `denial_reason` returns the code
    for the arm that caused it. This is the assertion test 21 was reaching for: `targets_permitted`
    checks UNREADABLE, then `AllTargets`, then `exhaustive_targets`, then per-selector, while
    `denial_reason` checks UNREADABLE, `exhaustive_targets`, ABSENT, then all — two orders that
    could drift apart, and only this pins them together.

### Redaction — `tests/app/test_authorization_audit.py`

22. **Credentials.** A `config.toml` profile carrying a sentinel password, and `HMC_PASSWORD` /
    `HMC_USER` carrying sentinels, produce no occurrence of any sentinel in any record, on allow
    and on deny.
23. **Whole arguments.** A call to `hmc_create_lpar` passing a sentinel in its `name` argument —
    a public argument that `REQUIRED_TARGET_ARGUMENTS` deliberately excludes, so it is never a
    declared selector — produces no occurrence of that sentinel in the record, while the declared
    `system_name_or_uuid` selector *is* present.
24. **Command text.** A `hmc_run_command` call whose `cmd` is a sentinel produces no occurrence of
    the sentinel, on allow and on deny.
25. **Response bodies and generated documents.** A permitted call whose handler is stubbed to
    return a sentinel-bearing payload produces no occurrence of the sentinel — the record is
    written before the handler runs.
26. **File paths.** Neither the `config.toml` path nor the resolved `access-policy.toml` path
    appears in any record — both, because they are different paths reached by different failures
    and M2 and M10 leak them independently. Two construction requirements, without which the
    mutations cannot bite: the test must include a `configuration-unreadable` record produced
    from a genuinely unreadable config file (the only arm where M2's `ConfigError` message
    exists), and it must load its policy through `load_access_policy` on a real path, so
    `policy.source` is a filesystem path M10 has something to leak.

### Ownership-override convergence — `tests/unit/test_ownership.py`, `tests/unit/test_audit.py`

26a. An approved override emits one `ownership-override` record on `hmc_mcp.audit` carrying the
    system, the LPAR, and `attribution.source == "config:agent_id"`, and emits nothing on
    `hmc_mcp.operations.lpar`.
26b. Its caller-supplied `system` and `lpar` are truncated to 128 characters and ASCII-escaped,
    like the authorization record's values.
26c. `operations.lpar` does not resolve the audit logger itself — `audit` is the only module in
    `src/hmc_mcp` that names `hmc_mcp.audit` (the same scan as test 8a).
26d. The override still reaches stderr on a CLI-shaped path where `install_audit_sink` was never
    called, at `WARNING`, so a CLI user does not silently lose it. **The test must isolate the
    logging tree to mean anything:** the mechanism is `logging.lastResort`, which
    `Logger.callHandlers` consults only after walking the whole ancestor chain and finding
    zero handlers — and pytest's logging plugin attaches a `LogCaptureHandler` to the root
    logger for every test item, so under the default harness the chain is never empty,
    `lastResort` is never reached, and the record lands in pytest's capture instead. Either
    save and clear `logging.root.handlers` (and `hmc_mcp.audit`'s) for the duration and read
    `capsys`, or drive it as a subprocess and read the child's stderr. Written naively it
    passes for a reason unrelated to what it claims.
26e. Both call sites in `authorize_lpar_mutation` and
    `_authorize_lpar_ownership_description` still emit, and neither emits when
    `ownership_override` is false.

### Attribution — `tests/app/test_authorization_audit.py`

27. `HMC_AGENT_ID=<value>` is recorded as `attribution.claim` with `verified: false`.
28. Authorization outcome is invariant under `HMC_AGENT_ID`: the same call under an unset
    `HMC_AGENT_ID`, under a value naming the policy, and under a value naming a granted connection
    yields the identical decision and reason.

### Stdio transport

Numbers 29 and 30 are **retired**. They specified a real stdio subprocess and an `sh -c '… 2>&-'`
subprocess with weaker assertions than the live proof's Run A and Run B, which drive the same two
shapes. Keeping both would be two mechanisms for one job. The live proof below is the stdio
transport coverage; nothing cites 29 or 30.

### Claims ADR 0040 makes about this checkout — `tests/app/test_authorization_audit.py`

Five statements in ADR 0040 are assertions about *this* codebase rather than about the design.
Each gets a test, so a later change that falsifies one reddens the suite instead of leaving a
durable record quietly wrong. All five were verified by execution before being written down.

31. **The logging tree drops an unheard record.** Needs the same isolation as 26d — clear
    `logging.root.handlers` and read `capsys`, or use a subprocess — because `logging.lastResort`
    is reached only after an ancestor walk finds zero handlers, and pytest keeps one on the root. ADR 0040's Context measures three facts about
    `fastmcp` 3.4.7 and the `hmc_mcp` tree and concludes "an audit control that emits nothing by
    default is not a control". The test asserts the *consequence this package owns* — with no sink
    installed and no ambient configuration, a record emitted on `hmc_mcp.audit` at `INFO` does not
    reach stderr — and not that the `fastmcp` logger carries a `RichHandler`, which would pin a
    dependency's internals and redden on a version bump with nothing here changed.
32. **`server._gates(None) == (None, None)`** — the reason a default `hmc-mcp serve` emits no
    authorization record at all, which A1 and the Consequences both rest on.
33. **`tool_registry.authorized` returns the handler itself** for both tools declaring
    `connection_argument = None` (`hmc_list_configured_hosts`, `hmc_effective_permissions`), and
    for every tool when `authorize is None` — while a connection-bearing tool with an authorizer
    *is* wrapped. This is what makes "those two produce no record" structural rather than a
    convention.
34. **`server._startup_warnings` emits its no-policy line only when a policy file exists**, so an
    operator with no policy file gets neither a warning nor a record — the gap ADR 0040 names
    against #225.
The `StreamHandler.terminator` fact — that a custom `logging.Handler` subclass does not inherit it
— is asserted **inside test 15**, whose explicit newline it explains, rather than as a test of its
own: it is a CPython property no change in this repository can falsify, so it documents a premise
rather than guarding a regression.

**Note on 32 and 34.** Both pin behaviour issue #225 exists to change: today no policy is selected
by default, so `_gates(None)` returns `(None, None)` and `_startup_warnings` stays silent without a
policy file. The test file says so, so that their future failure reads as #225 landing rather than
as a regression here.

### Live proof — `tests/app/test_authorization_audit_live.py`

Pytest items under `tests/app/`, so `just verify` runs them and A13 is re-runnable by anyone;
The whole module is POSIX-only and skipped elsewhere. Run B needs `2>&-`, a POSIX shell
redirection; Run A's fixture needs `HOME` to steer `config_dir()`, which on win32 resolves from
`APPDATA` and reads `USERPROFILE` rather than `HOME` — so on Windows the fixture would write its
sentinel-bearing `config.toml` over the developer's real one. These items
**replace** the retired tests 29 and 30, which were specified before this section existed and
drove the same two subprocess shapes with weaker assertions; Runs A and B are those shapes with
the assertions written out.

This record's entire contract is a *sink*. A unit test against a mock logger proves the payload
and almost nothing about delivery, so a real `hmc-mcp serve` stdio subprocess with a policy
selected must demonstrate all five of these before the PR is called ready. Driven over raw
newline-delimited JSON-RPC rather than a client library, so that anything printed outside the
protocol shows up as an unparseable line:

**Fixture.** `HOME` is redirected to a scratch directory, and both `config.toml` and
`access-policy.toml` are written into **`hmc_mcp.config.config_dir()` resolved after that
redirection** — never a hard-coded `Library/Application Support/…`. That path is
platform-dependent (Darwin uses `~/Library/Application Support/hmc-mcp`; elsewhere
`$XDG_CONFIG_HOME/hmc-mcp` or `~/.config/hmc-mcp`), and a fixture written to the macOS path on
Linux produces *no config at all*: `lab` would resolve to `UNRESOLVED` and L1's expected `allow`
would come back `connection-not-granted` — a plausible-looking wrong answer, which is the worst
outcome for a proof whose purpose is to be re-run by someone else. Both files are asserted to
exist at the resolved path before the first frame is sent, so a misplaced fixture fails setup
rather than changing the verdict.

**The child's environment is `os.environ` copied with four names deleted** — a copy, not a
from-scratch mapping, which would carry no `PATH` and so could not find the `hmc-mcp` console
script at all. `selected_connection` gates its
entire TOML branch on `HMC_HOST` and returns the `<default>` connection for *any* token when it is
set — before the config file is read at all — and the grant names `connections = ["lab"]`, which
never contains that. So a developer with `HMC_HOST` exported in their shell gets L1 back as
`connection-not-granted`: the same plausible-looking wrong answer the config-path guard above
exists to prevent, from a cause that guard does not cover. `HMC_HOST`, `HMC_PROFILE`,
`XDG_CONFIG_HOME`, and `APPDATA` are deleted from the child env — the same four
`tests/app/test_connection_authorization.py` already deletes — `HOME` is set to the scratch
directory, and their absence is asserted in the child env mapping beside the file-existence
assertion, so this cause also fails setup rather than changing the verdict.

`config.toml` holds profiles `lab` and `prod`, both with unreachable `.invalid` hosts and a
sentinel password. `access-policy.toml`:

```toml
[[policies.lab-scoped.grants]]
effects = ["read", "mutate", "destructive"]
connections = ["lab"]
targets = { lpar = ["db-01"], managed_system = ["sys-a"] }
```

`destructive` is in the list because the proof drives `hmc_power_off_lpar`, which is the tool that
declares **both** an `lpar` and a `managed_system` selector — `hmc_power_on_lpar` declares only
`lpar_name_or_uuid` and takes no `system_name_or_uuid` at all, so a call passing one is rejected
by schema validation before any authorization decision is reached. A setup step asserts every
argument of each call is present in that tool's generated schema, so this class of error fails at
setup rather than as a wrong verdict.

Launched as `hmc-mcp serve --access-policy lab-scoped`, driven with `initialize`, then
`notifications/initialized`, then `tools/call` frames.

**Run A — observation (L1–L4).** stderr captured to a file, stdout read frame by frame.

| item | call | assertion |
|---|---|---|
| L1 | `hmc_power_off_lpar(lpar_name_or_uuid="db-01", system_name_or_uuid="sys-a", profile="lab")` | exactly one stderr line parses as JSON with `event=="authorization"`, `decision=="allow"`, `reason=="permitted"`, `policy=="lab-scoped"`, `tool=="hmc_power_off_lpar"`, `effect=="destructive"`, `connection.resolved=="lab"`, and the `targets` entry **whose `argument` is `lpar_name_or_uuid`** carrying `db-01`. Selected by name, never by index: `selected_targets` preserves declaration order, so an index assertion silently follows a signature change. The call then fails at the transport on DNS, which is the correct shape — authorization is what is under test. |
| L2 | the L1 call **twice more** | the count of stderr lines **that parse as audit JSON** equals the number of calls made. stderr is expected to carry non-JSON from other writers — the fixture's `.invalid` hosts make even a permitted call fail at the transport, and that exception reaches the same FastMCP error path that renders the 41-line panel (#267) — so the assertion filters rather than counting total lines. That is also why L2 detects M5: three records with no terminator cannot yield three parsing lines. |
| L3 | all of the above | every stdout line parses as a JSON-RPC frame; zero unparseable lines. |
| L4 | `hmc_power_off_lpar(lpar_name_or_uuid="A"*500, system_name_or_uuid="sys-a", profile="lab")` | denied `target-not-granted`; the `lpar_name_or_uuid` entry's `value` is exactly 128 characters. |

**Run B — failure injection (L5), a separate subprocess.** The observation channel and the
failure injection cannot coexist: every mechanism that makes the sink fail either closes stderr or
empties it, which is the stream Run A reads. So L5 asserts on **stdout only**.

Launched through `sh -c '… 2>&-'` so fd 2 is closed at interpreter start and `sys.stderr` is
`None` — the #221 condition, and the arm the handler guards with an early return. Issue the same
denied call as Run A's L4 and assert, **on the parsed frame rather than its bytes**: the same
JSON-RPC error code, `isError` set, and the same ADR 0039 denial message string. Not a
byte-identical comparison — the two bodies come from separately launched processes, and their
key ordering and any request metadata are FastMCP's to change, so a byte assertion is stronger
than the property under test and would block a PR on a rendering change. The denial *message* is
the deterministic part, and it is what ADR 0038 and ADR 0039 fixed as the client contract. Then
assert the process is still serving (a subsequent `tools/list` succeeds).

The `OSError`/EPIPE arm is covered at unit level by tests 12–13; forcing it live would require
closing the parent's read end, which destroys the same channel again for no additional assurance.

POSIX only — `2>&-` is a POSIX shell redirection — and skipped elsewhere.

### Guardrails

`just verify` — run bare — must pass on the branch head.

## Acceptance criteria

- A1. One record per authorization decision at the dispatch boundary, and no record for the two
  cases ADR 0040 names as producing none. (tests 16–20)
- A2. The record carries policy, tool, effect, decision, reason, connection selector, and declared
  target selectors, in the fixed shape above. (tests 1, 6, 16)
- A3. Seven stable reason codes, closed and agreeing with the raised error. (tests 8, 17, 21)
- A4. `HMC_AGENT_ID` recorded as unverified attribution with explicit provenance, and unable to
  change an outcome **at the dispatch boundary** — explicitly not a package-wide claim, since
  ADR 0011 ownership takes a real decision from the same value, which is why A11 exists. Test 8b
  is the invariant and test 28 the behavioural sample; 8b is a textual scan, so it catches a
  literal read and not an identity threaded in as a parameter. (tests 7, 8b, 27, 28)
- A5. Credentials, whole argument sets, command text, generated documents, and response bodies are
  absent from every record on allow and deny paths, proven with sentinels whose tests are shown to
  bite. (tests 22–26)
- A6. The sink **this package installs** writes only to `sys.stderr`, does not propagate to an
  ancestor handler, and cannot abort the server when its destination is absent, broken, or
  closed — under the stdio transport. A handler the operator attaches to `hmc_mcp.audit` is
  theirs to keep off stdout; that deferral is deliberate, documented in
  `docs/authorization-audit.md`, and pinned by test 14a rather than left as an unexercised claim.
  Totality covers both sides — building a record and writing one — so a failure in either drops
  the record and leaves the authorization outcome and its exception unchanged.
  (tests 9–13, 14a, 14c, 15, L1–L5)
- A7. Caller-supplied values are bounded at 128 characters and cannot inject a control character
  or a line break. (tests 2, 3, 4, 5)
- A8. ADR 0040 records the decision, the two no-record cases, and the residuals.
- A9. Operator documentation describes both records, the reason codes, the logger name, and how to
  route or silence them.
- A10. `just verify` passes bare on the branch head.
- A11. The package has exactly one audit emitter module. `operations.lpar`'s override record is
  produced by `audit`, in the same grammar, and no longer through `extra=`. (tests 26a–26e)
- A12. The claims ADR 0040 makes about *this checkout* are pinned by tests, not by assertion.
  This traces to the campaign orchestrator's ruling of 2026-08-19 — "verify by execution the five
  facts the ADR asserts about this checkout, and either pin each with a test or correct the ADR" —
  which is external authority, so the charter carries it as a twelfth completion criterion rather
  than the design inventing it. Two of the five moved to where they belong rather than standing
  alone: 31 is the not-installed half of the sink group (9-13 are the installed half), and the
  `StreamHandler.terminator` fact is an assertion inside test 15, whose behaviour it explains,
  rather than a test of its own. (tests 15, 31, 32, 33, 34)
- A13a. The live-proof harness is safe to leave in `just verify`: a bounded per-frame read
  deadline, a fixture that terminates then kills the child and asserts it exited, and setup
  assertions (both config files at the resolved path, the four environment variables absent,
  `shutil.which("hmc-mcp")` not `None`) that fail setup rather than hang. Not prose — this is the
  suite's first long-lived `hmc-mcp serve` child, and a blocking read on one that never answers
  hangs every CI leg with no diagnostic.
- A13. The live proof runs and passes on the branch head: Run A (L1-L4) against a real
  `hmc-mcp serve --access-policy lab-scoped` stdio subprocess, and Run B (L5) as a separate
  `sh -c '… 2>&-'` subprocess. POSIX only; skipped elsewhere.
