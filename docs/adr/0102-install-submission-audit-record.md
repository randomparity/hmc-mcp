# ADR 0102: An audit record for a detached `installios` submission

## Status

Accepted (2026-08-26)

## Context

`install_lpar_os` and `install_vios` submit an irreversible OS install against a
partition's disks and detach. There is no HMC job to poll (ADR 0069/0070), no ADR 0011
ownership guard and therefore no `ownership-denied` or `ownership-override` record
(ADR 0092 §3.4a), and for a `hmc_mcp.api` consumer no #218 dispatch-boundary
`authorization` record either. The HMC-side install log is keyed on the partition name
alone, shared across managed systems, and truncated by the next submission.

#366 left two `_logger.info` calls on `hmc_mcp.operations_install` in place of a record.
Nothing configures that namespace: `server.py` binds only the reserved `hmc_mcp.audit`
logger and the four third-party ones, and no `basicConfig` or `dictConfig` exists in
`src/hmc_mcp`. The module logger's effective level is the root's `WARNING`, so an `INFO`
record is dropped before formatting, and `logging.lastResort` is `WARNING` too —
`server.py` already documents this failure mode where it pins `uvicorn` to `INFO`.

What that leaves depends on the caller. A bare `hmc_mcp.api` consumer gets no local trace
at all. A served deployment gets one: `dispatch_scope.authorize` emits an `authorization`
permit for the tool call, unconditionally since ADR 0041 made a policy mandatory, and both
install tools declare a partition and a managed-system selector, so the permit's `targets`
carry the caller's values for them. Three gaps remain even there. The permit records the
selector, not the resolved name, so a UUID selector names no partition; it has no
`log_path`; and it is a permit at `_ALLOW_LEVEL`, so `--audit-level WARNING` drops it. No
caller can answer "where is its log", and a bare `hmc_mcp.api` consumer cannot answer
"which partition" either.

## Decision

### 1. A new `install-attempted` event on the reserved logger

`audit.Event` gains `install-attempted`, built by a new `audit.record_install_attempted`
and called from `operations_install._submit_install`. The reserved logger is the one path
already installed at `INFO` with the bounded ADR 0043 sink and `propagate=False` in both
serve paths, and an unguarded destructive submission is what that stream is for.

Additive in the sense `audit.py`'s stability rule covers — a consumer ignores what it does
not know — and no existing filter changes meaning, which is the ground ADR 0100 §1 used for
the same addition. `EVENTS` is derived from the `Literal`, but
`test_events_matches_the_literal_and_every_emitter_uses_it` restates the set and enumerates
its emitters by hand, so it is edited here too.

### 2. Fields, and why it is emitted *before* the submit

```json
{"time":"2026-08-26T18:00:00+00:00","event":"install-attempted","system":"sys-a","partition":"vios-01","log_path":"/tmp/hmc-mcp-installios-vios-01.log","host":"hmc-a.example","attribution":{"claim":"agent-7","source":"config:agent_id","verified":false}}
```

The record names the attempt, not the outcome, and is emitted immediately before
`run_installios`. A submit that raises is the ambiguous case — the operations' own
`Raises:` blocks say the exception cannot distinguish a resolution failure from a failed
submission — so a record written afterwards would be missing exactly where an operator
needs to know a partition may have an install in flight and where its log is.

`log_path` is what a raised submission leaves the operator to read, and the reason
`system` and `host` sit beside it: ADR 0070 composes it as
`/tmp/hmc-mcp-installios-<slug>.log` from the partition name alone, and the slug replaces
every character outside `[A-Za-z0-9._-]` — so the file collides across managed systems and
across names that differ only outside that set. `host` and `attribution` follow
ADR 0100 §2 — `hmc.config.agent_id or "hmc-mcp"`, the same claim the ownership records
carry, so an unconfigured deployment's records name one actor and can be joined. Every
value passes through `_value`, so each is truncated and JSON-escaped by the shared
renderer. It carries no `policy`, `decision`, `reason`, `targets`, or `connection`, and not
as nulls: this builder takes no access-policy decision, and it also runs from the Python
API, where no policy connection exists to name.

### 3. `WARNING`, matching the denial record

`_DENY_LEVEL`. A process that never called `install_audit_sink` — every `hmc_mcp.api`
consumer — has no handler on this logger and no propagation, so `logging.lastResort` is
what puts the line on stderr, and it drops anything below `WARNING`. That is the caller
with no trace of any kind today, so `INFO` would silence the record precisely where it is
the only one. `WARNING` also survives `hmc-mcp serve --audit-level WARNING`, the setting
that drops the `authorization` permit named in the Context.

### 4. The post-submit line stays on the module logger

The PID it carries is already in the returned `InstallHandle`, so routing it too would put
a second record per install on the bounded sink for a value the caller already holds. It
stays a convenience for an embedder that configures the `hmc_mcp` namespace.

Say plainly what that costs, because the caller and the operator are not the same principal
under `hmc-mcp serve`: there the handle is the tool result and goes to the MCP client — the
agent that asked for the install — while the operator reads the audit stream. So the PID
does not reach the operator's trace. With no HMC job on this path, `installios -u` and the
`log_path` this record does carry are what an operator has; recovering the PID means `ps` on
the HMC or the log itself. Closing that would mean either a second record or a shape the
frozen `InstallHandle` does not have, and both are more than this issue authorizes.

## Consequences

- An operator can count and locate detached installs per system, per partition, per HMC and
  per acting agent, on every transport — including the two the dispatch-boundary policy does
  not reach.
- A record is not evidence that an install started; it is evidence that one was attempted.
  Pairing it with an outcome means reading the HMC-side log the record names, which the next
  submission against that partition name truncates.
- The operator's trace does not carry the in-flight PID, for the reason §4 gives. On a path
  with no HMC job that PID is the only handle on an install already running, so aborting one
  after a mistaken or unauthorized submission means reading it off the HMC. Named as a
  residual rather than closed here; #544 owns it.
- A caller can drive these records at attempt rate. Under `hmc-mcp serve` they land on the
  bounded ADR 0043 sink, which drops and says so with a `records-dropped` count; on the
  Python API path the record goes synchronously to stderr through `logging.lastResort` with
  no bound, exactly as the `ownership-override` record already does there. Reaching one
  costs a REST resolution round trip and, for a UUID target, an SSH one.
- Absence of the record is not proof no install was submitted, and the reasons are in this
  change's own delivery path: the serve-path sink drops under load with only a
  `records-dropped` count to show for it, `--audit-level ERROR` silences the reserved logger
  outright, and `_emit` swallows a failure to build or write rather than failing the call.
  `docs/authorization-audit.md` carries this caveat for the record, as it does for its
  siblings.
- No change to `hmc_mcp.api.__all__` and no movement of the frozen public signature digest:
  the builder lives in `audit`, which the facade does not export, and no exported signature
  changes. `CHANGELOG.md` records the widened literal under ADR 0029's convention.
- This closes the observability half of the gap only. The install path still has no
  ownership guard and no preflight on partition type or power state; ADR 0092 §3.4a and #460
  own those.

## Considered & rejected

- **Configuring the `hmc_mcp` namespace so the existing `_logger.info` survives.** verified:
  ADR 0040 rejects installing logging state at import, and `install_audit_sink` is called
  only from `server._serve_application` — so a library consumer would still get nothing
  unless it configured logging itself, which is the case this issue names. judgment: a
  package that configures the root of its own namespace takes a choice that belongs to the
  embedding application.
- **An `operation` field naming which of the two entry points submitted.** verified: both
  reach one `_submit_install` and compose the same `installios` command against the same log
  path; the vocabulary would need a `Literal`, a derived frozenset, a field row in
  `docs/authorization-audit.md`, and an entry in that document's drift guard. judgment: the
  distinction is the resolution feed, not the submission, and nothing in the record's use
  turns on it.
- **Emitting after the submit, with the PID.** verified: the `Raises:` blocks on both
  operations say the exception does not say whether anything was submitted. judgment: the
  one case with no return value is the one the record exists for.
- **Reusing the `ownership-override` or `authorization` event.** verified: neither applies —
  no ownership token is read on this path and no policy decision is taken. judgment: an event
  whose name asserts a check that never ran is worse than no event.
