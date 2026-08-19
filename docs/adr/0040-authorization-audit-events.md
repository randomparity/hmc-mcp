# 0040 — One structured audit record per dispatch-boundary authorization decision

## Status

Accepted (2026-08-19)

## Context

ADR 0035 gave every MCP tool a `ToolSecurity` record. ADR 0036 compiled the access policy and
fixed that a grant is evaluated conjunctively. ADR 0037 enforced the tool dimension by not
registering a withheld tool. ADR 0038 enforced the connection dimension, and ADR 0039 the target
dimension; #223 moved the per-grant loop out into `dispatch_scope.dispatch_authorizer`, which is
now the only place in the package that iterates `AccessPolicy.grants_for`.

Every one of those layers decides and then forgets. The caller learns the outcome from a denial
message or from the handler's result; the operator learns nothing at all. Epic #218 requirement 7
asks for one structured record per decision, carrying the policy, tool, effect class, decision,
reason code, connection selector, and declared target selectors — and never credentials, whole
argument sets, command text, generated documents, or response bodies. Its open questions fix the
sink for this saga: "audit output uses the process logging sink", with retention, signing, and
export left to the deployment.

Three findings from the preceding issues bear directly on the shape of that record.

**The permit/deny oracle.** #222 concluded that an agent holding one granted tool can recover
that tool's connection dimension one bit per call, and that no wording of the denial *message*
closes it. The conclusion recorded there was that this is an audit question.

**Message length passes through 1:1.** #223 observed that denial messages render the caller's own
selector values under `repr()` with no bound. A sink that inherits that policy inherits an
unbounded one.

**The stdio output hazard.** #221 shipped a defect in which startup warnings could reach stdout,
which carries JSON-RPC framing under the stdio transport, because CPython sets `sys.stderr` to
`None` when fd 2 is closed at interpreter start and a closed stream raises `ValueError` past an
`OSError` guard. `server._warn` is the fix: it reads `sys.stderr` fresh, returns on `None`, and
catches both `OSError` and `ValueError`. A per-call audit logger is the component most likely to
reintroduce that defect, because it writes on every call rather than four times at startup.

Three facts about the logging tree in this checkout, measured against `fastmcp` 3.4.7 by
importing `hmc_mcp.server` and inspecting `logging`:

- the `fastmcp` logger carries a `RichHandler` and sets `propagate = False`, so it is configured
  independently of the root logger and of anything under `hmc_mcp`;
- the `hmc_mcp` logger carries no handler and propagates to the root logger, which carries no
  handler and sits at `WARNING`;
- consequently a record emitted under `hmc_mcp` at `INFO` reaches `logging.lastResort`'s level
  filter and is dropped. An audit control that emits nothing by default is not a control.

## Decision

### One record, at the dispatch boundary, for every decision it reaches

`dispatch_scope.authorize` emits exactly one record per authorization decision: one for a permit,
one for each denial. Nothing else in the package emits an authorization record, and no tool body
is instrumented.

Two calls reach that function without producing a decision, and neither produces a record:

- A tool declaring `connection_argument = None` — `hmc_list_configured_hosts` and
  `hmc_effective_permissions` in this checkout. `tool_registry.authorized` returns such a handler
  **unwrapped**, so no authorizer is on its dispatch path at all and the early `return` in
  `authorize` is reachable only by a direct call in a test. What governs those two tools is the
  ADR 0037 capability ceiling, applied once at registration; a per-call record for a per-process
  decision would be a record of nothing.
- A malformed call, where a declared connection or selector argument is absent from the bound
  arguments and the indexing raises `KeyError`. ADR 0038 and ADR 0039 both fixed that indexing
  deliberately, and a `KeyError` there is a defect in the registration path rather than an
  authorization outcome. No decision was reached, so there is nothing to record.

Records are emitted for **every** effect class, not for mutations alone. Epic #218 requirement 7
names "MCP mutation attempts", and that is a floor: the permit/deny oracle above is cheapest to
run through `read` tools, so restricting the record to mutations would leave unrecorded exactly
the calls an agent would probe with. The effect class is a field, so an operator filtering to
mutations can still do so; an operator cannot recover a record that was never written.

### The record is one line of ASCII JSON

The message *is* the record — a single line produced by `json.dumps(..., ensure_ascii=True)`,
with no prefix and no separate structured payload. One mechanism, not two.

`ensure_ascii=True` is load-bearing rather than cosmetic. Every caller-supplied value in the
record is escaped to `\uXXXX`, so a newline, a carriage return, an ANSI escape, a bidirectional
override, or U+2028 in an LPAR name cannot forge a line, move a cursor, or reorder the rendering
of one. That is the same property ADR 0038's `repr()` buys for the denial message, obtained here
by the encoder rather than by argument.

The fields, always present and always in this order:

| field | value |
|---|---|
| `time` | UTC, ISO 8601, from `datetime.now(timezone.utc)` |
| `event` | the constant `"authorization"` |
| `policy` | the selected policy's name |
| `tool` | the tool name |
| `effect` | the tool's effect class |
| `decision` | `"allow"` or `"deny"` |
| `reason` | one of the seven codes below |
| `connection` | `{"state", "selector", "resolved"}` |
| `targets` | a list of `{"kind", "argument", "state", "value"}`, or `null` |
| `attribution` | `{"claim", "source", "verified"}` |

`connection.state` is `"present"` when the caller supplied a string, `"absent"` when it supplied
nothing or the empty string, and `"unreadable"` when it supplied a value of another type —
mirroring `connection_scope.selected_connection`'s own three arms. `connection.selector` is the
caller's own string, or `null` in the other two states; an object of an unexpected type is never
rendered, for the reason `target_scope.target_denial` already refuses to render one, namely that
an arbitrary `repr()` is not the caller's token in any useful sense and can carry anything.

`targets` is `null` — distinct from `[]` — when the decision was reached before the selectors were
extracted, which is exactly the `connections-unreadable` case. `[]` means the tool declares no
selector. Each entry's `state` and `value` come from what `target_scope.selected_targets` already
computed, so the record cannot disagree with the decision about what the call named.

The seven reason codes:

| code | decision | meaning |
|---|---|---|
| `permitted` | allow | a single grant covered the tool, connection, and targets together |
| `connections-unreadable` | deny | the configured connections could not be read at all |
| `connection-not-granted` | deny | no grant naming the tool allows the selected connection |
| `target-selector-unreadable` | deny | a declared selector carried a value the boundary declines to read |
| `target-unboundable` | deny | the tool's selectors cannot bound it, so no `targets` table can |
| `target-selector-absent` | deny | a declared selector was omitted from the call |
| `target-not-granted` | deny | no grant allowed that combination of targets |

The four target codes are the four cases `target_scope.target_denial` already selects between, in
its order. To keep the record and the message from drifting apart, that selection moves into
`target_scope.denial_reason`, and `target_denial` reads it rather than repeating it: one function
decides which case applies, and the message and the reason code are both derived from the answer.

### Caller-supplied values are truncated to 128 characters, unmarked

Every value in the record is one of: a compile-time constant (the event name, decision, reason
code, effect class, target kind, selector argument name, attribution source); the policy name and
the resolved connection, both operator-authored; or a value the caller itself supplied. Only the
last class is unbounded, and each such value is truncated to 128 characters.

No truncation marker is appended. A marker is forgeable — a caller can name an LPAR after it — so
it would put a claim into an audit record that the caller controls, in exchange for
distinguishing a 128-character value from a longer one, which is a distinction no operator acts
on. The bound is documented instead.

### `resolved` records the normalized connection; the message still does not

ADR 0038 renders the caller's own token in the denial *message* and never the normalized value,
because under its rule 3 that value is a profile key read from `config.toml` and each denial is
one probe. The audit record carries both: `connection.selector` is the caller's token and
`connection.resolved` is the profile key the call would have used, `"<default>"` for the
environment connection, or `"<unresolved>"` when the token names nothing configured.

The two are not the same disclosure. The denial message is returned to the caller as a tool
result; the audit record is written to the server process's logging sink, which is not the
JSON-RPC response channel. ADR 0038 deferred the "does this name exist" question to this issue on
exactly that ground, and #223 recorded that the server can put what it knows where the caller
cannot read it. The trust assumption is stated rather than assumed: an operator who forwards this
sink to a party who may not read `config.toml` inherits that disclosure.

### `HMC_AGENT_ID` is attribution, read at emission, and never an input

`attribution.claim` is `os.environ.get("HMC_AGENT_ID")` truncated to 128 characters,
`attribution.source` is the constant `"environment:HMC_AGENT_ID"`, and `attribution.verified` is
the constant `false`.

It is read directly from the environment rather than through `HMCConfig`, because constructing
that model reads a `.env` file and validates the value, and neither belongs on a per-call
authorization path. The consequence is that the recorded claim is *unvalidated* as well as
unverified, which the truncation and the JSON escaping above are what make safe.

It is passed to the renderer, never to a decision. `dispatch_scope.authorize` reads it nowhere;
the value reaches `audit.record` and stops there. The constant `false` is what makes an operator
able to filter on it without knowing this ADR exists.

### The sink is stderr, installed by the serve path, and does not propagate

`audit.install_audit_sink()` is called by `server._serve_application`, so both transports and
neither the in-process composer (`create_mcp`) nor any library or CLI caller install global
logging state. It:

- sets `propagate = False` on the `hmc_mcp.audit` logger, **always**;
- attaches a handler writing to `sys.stderr`, **only** when that logger carries no handler
  already;
- sets the logger's level to `INFO`.

`propagate = False` is unconditional because propagation to an unknown ancestor handler is the
stdio hazard itself: an operator or a dependency that puts a `StreamHandler(sys.stdout)` on the
root logger would, without it, corrupt the protocol stream once per authorized call.
`hmc_mcp.audit` is the documented attachment point, and an operator routing audit elsewhere
attaches a handler there.

The handler resolves `sys.stderr` at emit time, returns when it is `None`, and catches `OSError`
and `ValueError` around the write — the same three guards, for the same three reasons, that
`server._warn` already applies. It writes `record.getMessage()` directly and installs no
`Formatter`, so nothing can wrap, box, or re-prefix a line whose single-line grammar is the
contract.

Denials are logged at `WARNING` and permits at `INFO`, so an operator who wants denials only sets
the level on `hmc_mcp.audit` and needs no new configuration key.

## Consequences

- The operator gets a per-call authorization trail with a stable grammar, and the calling agent
  cannot read it: it is not in the tool result.
- An audit record is written before the denial is raised and before the handler runs. A permitted
  call is recorded as *authorized*, not as *succeeded* — the handler may still fail, and this
  record says nothing about that.
- A `read` tool called in a loop produces one `INFO` record per call. On a busy server that is the
  dominant log volume, and the level split is the only lever offered.
- `target_scope` gains a public `denial_reason`, and `target_denial` is refactored to read it.
  Behaviour is unchanged; the case selection now has one owner.
- `audit.py` imports nothing from the package. Every value reaches it as a primitive, so it can be
  tested without a policy, a config file, or an application, and no import cycle is possible.
- Records are dropped rather than raised when the destination is unavailable. A server whose
  stderr is closed authorizes calls it does not record, which is the deliberate trade: #221
  established that a diagnostic must not abort a start, and the same reasoning binds a diagnostic
  that must not abort a call.
- Because `propagate` is set unconditionally, an operator who had attached an audit handler to the
  root logger before this change stops receiving records and must attach to `hmc_mcp.audit`. There
  is no prior release in which audit records existed, so nothing in the field breaks.

## Residuals

**The permit/deny oracle is recorded, not closed.** An agent holding one granted tool still
recovers one bit of the connection or target dimension per call, and this record does not prevent
it. What changes is that every probe is now written down with its reason code, so a run of
denials against one policy and tool is visible to the operator. Closing the oracle needs a
mechanism this issue does not introduce — rate limiting, lockout, or uniform response timing —
each of which is a new denial-of-service surface aimed at the operator's own agents, and none of
which #218 asks for. It is stated here so the next reader does not mistake the record for a fix.

**A routine denial still renders a traceback.** Reproduced on `main` @ `58455bc` with no logging
configuration applied: one denied call writes a 41-line `rich` traceback panel to stderr through
FastMCP's own tool-error handling, beside the one-line record this ADR adds. Nothing reaches
stdout, so it is not a framing defect. Every fix reaches past authorization — suppressing it
suppresses genuine handler bugs too, and converting the error at the boundary changes the client
contract ADR 0038 and ADR 0039 fixed — so it is filed as #267 rather than decided here.

**Retention, integrity, and export are the deployment's.** The record is written to a process
logging sink and is neither persisted, sequenced, nor signed. #218's open questions already place
those outside this saga; nothing here should be read as promising them.

## Considered & rejected

**Instrument the tool bodies.** Rejected by the issue itself, and by arithmetic: 128 wrapped tools
would carry 128 opportunities for a record to disagree with the decision, and a tool added later
would carry none at all. The dispatch boundary has one emission point that no registration site
can forget.

**Do nothing — let the denial message be the record.** The message reaches the caller and not the
operator; it carries no policy name in the target case, no effect class, no attribution, and no
record whatsoever of a permitted call. An access-control layer whose only output is what it tells
the party it denied has no observability at all.

**Emit through `extra=` on a normal log record**, as `operations_lpar._audit_lpar_ownership_override`
does. Fields passed through `extra` are invisible unless the operator's formatter names each one,
so under this checkout's default configuration — no handler, no formatter — the record would
reach a sink carrying only its message and would silently lose every field that makes it an audit
record. Carrying the fields *and* a rendered message is two mechanisms for one job.

**Key-value text rather than JSON.** Cheaper to read unaided, and it is what the existing denial
messages look like. Rejected because escaping is then hand-rolled: quoting, embedded delimiters,
and control characters each become a rule this ADR would have to state and a test would have to
defend, where `json.dumps(ensure_ascii=True)` settles all three in the standard library.

**Give the audit sink its own environment variable or CLI flag** — a destination, a level, an
on/off switch. Rejected as configuration nobody has asked for: `logging` already provides all
three through the `hmc_mcp.audit` logger, and a second mechanism would have to be kept in
agreement with it.

**Install the sink in `create_mcp`, or at import.** Rejected because both mutate global logging
state for a caller that only composed an application — the in-process path every test and the
supported Python API of ADR 0029 use. `_serve_application` is where the process has already been
established as a server, and where `_warn` already writes to stderr for the same reason.

**Let the record propagate to the root logger**, so operators configure it the way they configure
everything else. Rejected on the stdio hazard: under that transport the root logger's handler is
exactly what the server cannot vouch for, and one `StreamHandler(sys.stdout)` anywhere in the
process would put an audit record into the JSON-RPC stream on every authorized call. A dedicated
non-propagating logger is the only shape in which "cannot write to stdout" is a property of the
code rather than a hope about the deployment.

**Record only denials.** Halves the volume and loses the ability to answer "what did this agent
do", which is the question an audit trail exists for. Requirement 7 names both.

**Omit `connection.resolved`.** Would keep the record to values the caller already holds, which is
ADR 0038's rule for the *message*. Rejected because the sink and the tool result are different
channels with different readers, and the operator-facing one is where "which HMC did this call
reach" is answerable at all — under an `HMC_HOST` collapse or an ADR 0030 nickname the caller's
token does not answer it.
