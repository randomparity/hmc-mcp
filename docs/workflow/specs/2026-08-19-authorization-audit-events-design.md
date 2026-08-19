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
| `src/hmc_mcp/operations_lpar.py` | `_audit_lpar_ownership_override`'s body becomes a call into `audit`; the two call sites and the rest of the file are untouched. Converges the package's second audit emitter (`Refs #268`). |
| `docs/authorization-audit.md` (new) | the operator-facing record contract: field set, reason-code table, logger name, level split, how to route or silence it, and the merged-descriptor caveat. |
| `README.md` | one caveat beside the existing "never stdout" sentence in the startup-warnings section, which has the same descriptor-merge limit, plus a pointer to the new document. |

`audit.py` importing nothing from `hmc_mcp` is a hard constraint, not an accident: `target_scope`
imports the reason-code type from it, so any import back would be a cycle. Every value reaches
`audit.record` as a primitive.

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

## The record contract

One log record per decision. Its message is the complete record: a single line of
`json.dumps(payload, ensure_ascii=True)` output, with no prefix, no trailing text, and no
`Formatter` applied by the sink this package installs. The handler writes that message followed by
a single `"\n"` and flushes — a custom `emit` does not inherit `StreamHandler.terminator`, so the
terminator is explicit.

Two event types share that grammar and differ in their fields. `time` and `event` come first on
both; every caller-supplied value is truncated to 128 characters on both.

| `event` | emitted by | level | fields after `time`, `event` |
|---|---|---|---|
| `authorization` | `dispatch_scope.authorize` | deny `WARNING`, allow `INFO` | `policy`, `tool`, `effect`, `decision`, `reason`, `connection`, `targets`, `attribution` |
| `ownership-override` | `operations_lpar`, via `audit` | `WARNING` | `system`, `lpar`, `attribution` |

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

`targets` is `null` when the decision was reached before selectors were extracted (the
`configuration-unreadable` case alone) and a list otherwise; `[]` means the tool declares no
selector. Each entry's `state` ∈ `{"present", "absent", "unreadable"}` is taken from what
`target_scope.selected_targets` already computed — a `str`, the `ABSENT` singleton, or the
`UNREADABLE` singleton respectively — and `value` is the truncated caller string when present and
`null` in both singleton cases. The singletons are never passed to `json.dumps`; they are not JSON
values, so doing so would raise `TypeError` inside authorization on the two routine
selector denials.

`attribution` is always `{"claim": <str|null>, "source": "environment:HMC_AGENT_ID",
"verified": false}`. The claim identifies the server *process*: under stdio that is one client,
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
| the sink is not installed (library, CLI, in-process composition) | records are emitted to a logger with no handler and dropped by level; nothing raises |

Emission is total: building a record and writing one both drop on failure, leaving the
authorization outcome and the exception carrying it unchanged. The handler resolves `sys.stderr`
at emit time rather than binding it at install time, matching `server._warn`.

Two preconditions bound what the trail contains, and both are stated rather than assumed:

- **No policy, no records.** `server._gates` returns `(None, None)` without a selected policy and
  `tool_registry.authorized` then leaves every handler unwrapped, so no authorizer runs. A default
  `hmc-mcp serve` installs the sink and emits nothing, for the same reason it enforces nothing.
  #225 changes that default.
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
| 2 — output stream | the installed handler writes only to `sys.stderr`, resolved at emit time and skipped when `None`; the logger does not propagate, so no ancestor handler — including one pointed at stdout — can receive the record. This closes the in-process route only: a launcher merging fd 2 into fd 1 (`serve 2>&1`) makes stderr the JSON-RPC channel, which no in-process choice detects. Stated as a residual in ADR 0040 and documented for the operator, not defended in code. |
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

Every criterion below is falsifiable and gets a test. Sentinel-secret and redaction tests are
mutation-verified: the redaction is broken, the test is watched to fail, and the change reverted.

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
7. `attribution` is `{"claim": null, "source": "environment:HMC_AGENT_ID", "verified": false}`
   when `HMC_AGENT_ID` is unset.
8. `REASONS` equals the `Literal`'s arguments, and every code the boundary can emit is in it.
8a. No module in `src/hmc_mcp` other than `audit` calls `logging.getLogger` with a name equal to
    or below `hmc_mcp.audit` — asserted by scanning the package, so the reservation is a checked
    invariant rather than a convention.

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
14a. A record whose target selector is `ABSENT` or `UNREADABLE` renders without raising —
    `json.dumps` never sees a singleton.
14b. Emission is total: a renderer forced to raise, and a logger forced to raise, both leave
    `dispatch_scope.authorize`'s outcome and its exception type unchanged. A non-string connection
    token also renders `state="unreadable"` with `reason="connection-not-granted"` — the
    asymmetry with the target dimension, asserted so it cannot drift silently.
15. The sink applies no `Formatter`: the line on stderr equals the record's message exactly, and
    two consecutive records land on two lines rather than one — the terminator is written.

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
21. The record's `reason` agrees with the raised error's own case selection for all four target
    denials — driven from `target_scope.denial_reason` so the two cannot drift.

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
26. **Policy source path.** The policy file's path does not appear in any record.

### Ownership-override convergence — `tests/unit/test_ownership.py`, `tests/unit/test_audit.py`

26a. An approved override emits one `ownership-override` record on `hmc_mcp.audit` carrying the
    system, the LPAR, and `attribution.source == "config:agent_id"`, and emits nothing on
    `hmc_mcp.operations_lpar`.
26b. Its caller-supplied `system` and `lpar` are truncated to 128 characters and ASCII-escaped,
    like the authorization record's values.
26c. `operations_lpar` does not resolve the audit logger itself — `audit` is the only module in
    `src/hmc_mcp` that names `hmc_mcp.audit` (the same scan as test 8a).
26d. The override still reaches stderr on a CLI-shaped path where `install_audit_sink` was never
    called, at `WARNING`, so a CLI user does not silently lose it.
26e. Both call sites in `authorize_lpar_mutation` and
    `_authorize_lpar_ownership_description` still emit, and neither emits when
    `ownership_override` is false.

### Attribution — `tests/app/test_authorization_audit.py`

27. `HMC_AGENT_ID=<value>` is recorded as `attribution.claim` with `verified: false`.
28. Authorization outcome is invariant under `HMC_AGENT_ID`: the same call under an unset
    `HMC_AGENT_ID`, under a value naming the policy, and under a value naming a granted connection
    yields the identical decision and reason.

### Stdio transport — `tests/app/test_authorization_audit.py`

29. A real subprocess server driven over stdio through `fastmcp.client.transports.StdioTransport`,
    with the child's stderr captured to a file via that transport's `log_file`: a denied call and
    a permitted call both complete over the protocol — which they cannot if stdout is corrupted —
    and the captured stderr contains both audit records.
30. The same server started through `sh -c '… 2>&-'`, so fd 2 is closed at interpreter start and
    `sys.stderr` is `None`: the denied call still completes over the protocol and the process
    exits normally. POSIX only, skipped elsewhere, since `2>&-` is a POSIX shell redirection.

### Live proof — a precondition of the PR, not a nice-to-have

This record's entire contract is a *sink*. A unit test against a mock logger proves the payload
and almost nothing about delivery, so a real `hmc-mcp serve` stdio subprocess with a policy
selected must demonstrate all five of these before the PR is called ready. Driven over raw
newline-delimited JSON-RPC rather than a client library, so that anything printed outside the
protocol shows up as an unparseable line:

L1. A record arrives on stderr as **one physical line** that parses as JSON and carries the
    documented fields for its `event`.
L2. **Two back-to-back records do not share a line** — the `StreamHandler.terminator` claim,
    which is the one that fails silently only under volume.
L3. **stdout carries no non-JSON line**, matching the #223 baseline this must not regress.
L4. A caller value **over 128 characters arrives truncated** to 128.
L5. An **emission failure leaves the authorization outcome and its error unchanged** — the same
    denial type, the same message, with the sink made to fail.

Reproduced independently rather than taken on report. `config_dir()` on darwin is
`Path.home()/"Library"/"Application Support"/"hmc-mcp"` with no environment override, so the
subprocess's `HOME` is redirected to a fixture home holding `config.toml` and
`access-policy.toml`.

### Guardrails

`just verify` — run bare — must pass on the branch head.

## Acceptance criteria

- A1. One record per authorization decision at the dispatch boundary, and no record for the two
  cases ADR 0040 names as producing none. (tests 16–20)
- A2. The record carries policy, tool, effect, decision, reason, connection selector, and declared
  target selectors, in the fixed shape above. (tests 1, 6, 16)
- A3. Seven stable reason codes, closed and agreeing with the raised error. (tests 8, 17, 21)
- A4. `HMC_AGENT_ID` recorded as unverified attribution with explicit provenance, and provably
  unable to change an outcome. (tests 7, 27, 28)
- A5. Credentials, whole argument sets, command text, generated documents, and response bodies are
  absent from every record on allow and deny paths, proven with sentinels whose tests are shown to
  bite. (tests 22–26)
- A6. The sink cannot write to stdout and cannot abort the server when its destination is
  unavailable, under the stdio transport. (tests 9–13, 29, 30)
- A7. Caller-supplied values are bounded at 128 characters and cannot inject a control character
  or a line break. (tests 2, 3, 4, 5)
- A8. ADR 0040 records the decision, the two no-record cases, and the residuals.
- A9. Operator documentation describes both records, the reason codes, the logger name, and how to
  route or silence them.
- A10. `just verify` passes bare on the branch head.
- A11. The package has exactly one audit emitter module. `operations_lpar`'s override record is
  produced by `audit`, in the same grammar, and no longer through `extra=`. (tests 26a–26e)
- A12. The five claims ADR 0040 makes about this checkout are pinned by tests, not by assertion:
  the three logging-tree facts, `_gates(None) == (None, None)`, `authorized` leaving the two
  `connection_argument=None` handlers unwrapped, `_startup_warnings` gating its no-policy line on
  an existing file, and a custom `emit` not inheriting `StreamHandler.terminator`.
- A13. The live proof L1–L5 runs and passes on the branch head.
