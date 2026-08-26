# Authorization audit records

The server writes one structured record for every authorization decision it makes at the
MCP dispatch boundary, and further records for the other things an operator has to be
able to audit. The sections under [The records](#the-records) are the full set; this
document is the contract they keep. The decision behind it is
[ADR 0040](adr/0040-authorization-audit-events.md).

## What you get, and when you get nothing

**`authorization` records** exist for calls the access policy authorizes — which is all of
them, since ADR 0041 made a policy mandatory. Before it, a server started without
`--access-policy NAME` had no authorizer on any dispatch path and emitted no authorization
record at all, for the same reason it enforced nothing at all. There is no such server now,
so every deployment writes one record per decision, and the delivery guarantee below
applies to every deployment rather than only to those that opted in.

**`ownership-override` and `ownership-denied` records are not policy-gated.** They come
from the ADR 0011 ownership check inside the handler, which runs whether or not a policy
is selected — and on the CLI and Python API paths, which have no policy at all. So an
unpolicied server can still produce those. For an `hmc_mcp.api` consumer that check is the
only authorization boundary that applies, which is why its refusals are recorded here
rather than left to the `authorization` event ([ADR 0100](adr/0100-ownership-denial-audit-record.md)).

**`install-attempted` records are not policy-gated either**, and for a stronger reason:
the detached `installios` path has no ownership check to gate and no HMC job to poll, so
for an `hmc_mcp.api` consumer this record is the only trace an irreversible install
leaves in this process ([ADR 0102](adr/0102-install-submission-audit-record.md)).

Other things produce no record, by design:

- a call to a tool the policy's ceiling withheld — it is never registered, so nothing
  reaches the boundary;
- `tools/list`, which discloses the whole ceiling in one call.

`hmc_list_configured_hosts` and `hmc_effective_permissions` declare no connection
argument and used to be a third case. They are recorded now: the target dimension
decides for them, so a `targets` table denies both with reason `target-unboundable`
and an `"all-targets"` grant permits them. Their `connection` object reads
`"state": "absent"` with a **null** `"resolved"`, which is what distinguishes a tool
with no connection argument from a caller who omitted `profile` — the latter resolves
to `"<default>"`.

**An empty audit stream is therefore not evidence that nothing was attempted.**

## The records

Each is one physical line of ASCII JSON. `time` and `event` come first on every one,
and every caller-supplied value is truncated to **128 characters** with no marker — a
truncated value is exactly that long, so you can measure it.

### `event: "authorization"`

| field | value |
|---|---|
| `time` | UTC, ISO 8601 |
| `event` | `"authorization"` |
| `policy` | the selected policy's name |
| `tool` | the MCP tool name |
| `effect` | `read`, `mutate`, `destructive`, or `arbitrary-command` |
| `decision` | `"allow"` or `"deny"` |
| `reason` | one of the codes below |
| `connection` | `{"state", "selector", "resolved"}` |
| `targets` | a list of `{"kind", "argument", "state", "value"}`, or `null` |
| `attribution` | `{"claim", "source", "verified"}` |

```json
{"time":"2026-08-19T18:00:00+00:00","event":"authorization","policy":"lab-scoped","tool":"hmc_power_off_lpar","effect":"destructive","decision":"deny","reason":"target-not-granted","connection":{"state":"present","selector":"lab","resolved":"lab"},"targets":[{"kind":"lpar","argument":"lpar_name_or_uuid","state":"present","value":"other-01"}],"attribution":{"claim":null,"source":"environment:HMC_AGENT_ID","verified":false}}
```

`connection.state` is `present` when the caller supplied a connection string, `absent`
when it supplied nothing or an empty string, and `unreadable` when it supplied a value
of another type. That value's `repr()` is never rendered. `connection.selector` is the
caller's own string, or `null` in the other two states. A `targets` entry's `state` is
the same vocabulary, describing one selector instead of the connection.

`connection.resolved` is which HMC the call would actually have used: a profile key,
`"<default>"` for the environment/default connection, `"<unresolved>"` when the token
names nothing configured, or `null` when the configuration could not be read at all.

> **`<default>` and `<unresolved>` are reserved renderings that share a string space
> with legal profile keys.** Profile names are TOML keys and are not validated, so
> `[profiles."<unresolved>"]` and `[profiles."<default>"]` are both legal — and a call
> naming one renders identically to the sentinel it collides with. A filter on either
> value would collect both cases. Rename such a profile if you have one.

> **An `HMC_HOST` collapse is visible only when the caller named a connection.**
> `state: "present"` with `resolved: "<default>"` arises no other way — unless you have a
> profile literally named `<default>`, per the collision above. A caller that omitted the
> argument renders `absent`/`"<default>"` whether or not `HMC_HOST` is set.

`targets` is `null` when the selectors were never extracted — the
`configuration-unreadable` case, and also the rarer one where assembling the record
failed and it degraded to nulls rather than being dropped. Treat `null` as "not
recorded", for any reason code, rather than as a synonym for one. `[]` means the tool
declares no selector. The same reading applies to a `null` `connection.resolved`. An integer selector —
`vios_partition_id`, the only one — records `state: "present"` with its decimal
rendering; a boolean records `unreadable`.

### `event: "ownership-override"`

Emitted when an [ADR 0011](adr/0011-multi-agent-lpar-ownership.md) LPAR ownership
override is exercised. Always `WARNING`.

**Not every record is a human decision.** Most are: a caller passing
`ownership_override` is an operator-approved exception to a single mutation. One
internal caller also emits it — `provision_lpar`'s activation leg passes the
override unconditionally, because the ownership token it would authorize against is
the one the same workflow stamped moments earlier
([ADR 0092](adr/0092-uniform-lpar-ownership-authorization-rule.md) Consequences).
That leg is reached only when `HMC_AUTHORIZE_POWER_OPERATIONS` is on, so with the
guard off — the default — every record in this stream is caller-supplied. With it
on, expect one record per `provision_lpar(power_on=True)`. The record carries no
field distinguishing the two sources; an alert on this event should account for that
before the guard is enabled.

The converse holds too, and it is the more surprising half: while the guard is off,
the power path emits **no** record even when a caller passes `ownership_override`.
The parameter is read only inside the guarded branch, so with the setting off it is
inert — nothing was bypassed, because nothing was checked. Silence in this stream on
the power path is therefore not evidence that no override was requested.

**This event records ownership checks that were *bypassed*, never ones that
*refused*.** A refusal is the `ownership-denied` record below, which #467 added; an
alert on *this* event therefore counts approved exceptions rather than attempts, and
the one below is what tells "nobody tried" from "many were refused".

```json
{"time":"2026-08-19T18:00:00+00:00","event":"ownership-override","system":"sys-a","lpar":"db-01","host":"hmc-a.example","attribution":{"claim":"agent-7","source":"config:agent_id","verified":false}}
```

`host` is the hostname or address of the HMC the override was approved on — the
same `HMCConfig.host` whose `agent_id` the attribution records. It names a
machine, not a grant, so it is its own field rather than an arm of the
authorization record's `connection` object. An unset `HMC_HOST` renders as an
empty string.

It carries no `policy`, `decision`, `reason`, or `targets`, and not as nulls —
an ownership check on a token parsed from an LPAR description is not an
access-policy decision, and empty fields would read as one.

### `event: "ownership-denied"`

Emitted when an [ADR 0011](adr/0011-multi-agent-lpar-ownership.md) LPAR ownership
check *refuses*, immediately before the `PermissionError` is raised. Always
`WARNING`, so `--audit-level WARNING` — the setting that drops permits — keeps
these. The decision is [ADR 0100](adr/0100-ownership-denial-audit-record.md).

| field | value |
|---|---|
| `time` | UTC, ISO 8601 |
| `event` | `"ownership-denied"` |
| `operation` | `lpar-mutation` or `lpar-decommission-snapshot` |
| `denial` | `malformed-token` or `foreign-owner` |
| `system` | the managed system the partition lives on |
| `lpar` | the partition whose mutation was refused |
| `owner` | the owner the LPAR's token claims, or `null` |
| `host` | the HMC the check read the token from |
| `attribution` | `{"claim", "source", "verified"}` |

```json
{"time":"2026-08-26T18:00:00+00:00","event":"ownership-denied","operation":"lpar-mutation","denial":"foreign-owner","system":"sys-a","lpar":"db-01","owner":"agent-3","host":"hmc-a.example","attribution":{"claim":"agent-7","source":"config:agent_id","verified":false}}
```

`operation` names which guard entry point refused — `lpar-mutation` covers every
guarded mutation, `lpar-decommission-snapshot` the ownership read that precedes a
decommission — and not which MCP tool or API function called it. Per-tool
granularity is deliberately out of scope; where the caller matters and the transport
is MCP, join on the `authorization` record for the same call.

`denial` names which of the guard's two rules refused. `foreign-owner` is a
well-formed token naming another agent, and the record carries *both* halves of the
comparison that failed: `owner` is the claimed owner, `attribution.claim` the agent
that was refused. `malformed-token` is a description carrying `[hmc-mcp` that no
token could be parsed from, and there `owner` is `null` — nothing parsed, so the
record carries the actor alone.

A `malformed-token` record identifies the partition but not the malformation. Triage
means reading the description off the HMC out of band, and two alerts on one
permanently-broken token read the same as an ongoing incident. That is accepted
rather than closed: a description is unbounded operator-authored text that would be
cut at 128 characters — often before the malformation — and it can carry an ADR 0064
caller token beside the ownership one, so a field for it would disclose more than it
triages.

**A denial count is not a count of hostile attempts.** `docs/environment-variables.md`
prescribes retry-after-refusal as the sanctioned override procedure, so a legitimate
override is now *preceded* by a denial record carrying the same `system`, `lpar`, and
`attribution.claim` as the `ownership-override` record seconds later. There is no
correlation identifier, so pairing them means matching those three fields inside a
time window — and an override that was never refused (`provision_lpar`'s activation
leg, or any caller who passes `ownership_override` on the first attempt) will pair
against an unrelated earlier denial if one is in the window. A refused `--dry-run`
decommission preflight — the sequence `README.md` prescribes — emits the same record
as a refused destructive one, because the inventory read authorizes ahead of the
dry-run return. No field distinguishes either case; this is the same caveat the
override record above carries for its own two sources.

**Silence here is not proof of no refusal**, for one specific reason worth writing
down: with `HMC_AUTHORIZE_POWER_OPERATIONS` off — the default — the power path never
runs the guard, so no denial is possible there and none is recorded.

A denied caller can drive these at attempt rate, bounded in practice by the HMC round
trip each denial costs. Under `hmc-mcp serve` they land on the bounded sink, which
drops and says so with a `records-dropped` count; on the CLI and Python API paths
nothing installs a sink, so the line goes synchronously to stderr through
`logging.lastResort` with no bound and no drop count — exactly as the
`ownership-override` record already does there.

The denial record names its `operation` and the override record does not. Closing
that asymmetry would mean adding a field to the override record, which #467
deliberately left alone; the denial stream is complete without it.

It carries no `policy`, `decision`, `reason`, `targets`, or `connection`, and not as
nulls, for the reason the override record gives.

### `event: "install-attempted"`

Emitted immediately **before** `install_lpar_os` or `install_vios` submits a detached
`installios` to the HMC. Always `WARNING`, so `--audit-level WARNING` keeps it. The
decision is [ADR 0102](adr/0102-install-submission-audit-record.md).

```json
{"time":"2026-08-26T18:00:00+00:00","event":"install-attempted","system":"sys-a","partition":"vios-01","log_path":"/var/hmc/log/installios.vios-01.log","host":"hmc-a.example","attribution":{"claim":"agent-7","source":"config:agent_id","verified":false}}
```

`system` and `partition` are the resolved HMC CLI names the submission was composed
with; `host` is the `HMCConfig.host` of the client that submitted, and an unset
`HMC_HOST` renders as an empty string. `attribution.claim` is the acting agent —
`HMC_AGENT_ID`, or `hmc-mcp` when unset — the same claim the ownership records carry,
so an unconfigured deployment's records name one actor and can be joined.

`log_path` is the HMC-side path the install writes to, and the field to read when a
submission raises. It is keyed on the **partition name alone**: the managed system is
not part of it, and the redirect truncates, so same-named partitions on different
managed systems behind one HMC share one file and each destroys the other's only
diagnostic record. That is why `system` and `host` sit beside it here.

**This record names an attempt, never an outcome.** The submission is detached, so
there is no HMC job to poll and nothing observes whether `installios` accepted the
target. A record means the process reached the point of submitting against that
partition's disks; whether anything started is in the log it names. It is emitted
ahead of the submit deliberately, because a submission that raises cannot tell a
resolution failure from a failed submit — the case where an operator most needs the
partition and the path.

**It is the only record naming the partition.** There is no HMC job
([ADR 0069](adr/0069-installlpar-and-installvios-absent-from-hmc-rest.md)), and no
[ADR 0011](adr/0011-multi-agent-lpar-ownership.md) ownership check and so no
`ownership-denied` or `ownership-override`. A served deployment also writes an
`authorization` permit for the tool call, but that record names the tool and never the
resolved system, partition, or `log_path`, and being a permit it is dropped by
`--audit-level WARNING`. An `hmc_mcp.api` consumer gets no `authorization` record at all.

**Absence of this record is not proof that no install was submitted**, for the reasons the
lead section gives generally and three that apply here specifically: under `hmc-mcp serve`
it lands on the bounded sink, which drops under load and reports only a `records-dropped`
count — a number, not an identity, so a reader cannot tell whether a dropped line was an
install; `--audit-level ERROR` or `CRITICAL` silences the reserved logger outright; and a
record that fails to build or write is swallowed rather than failing the call, because a
diagnostic must not abort an operation. Alert on the records you have, not on their absence.

It carries no `policy`, `decision`, `reason`, `targets`, or `connection`, and not as
nulls: the record is not an access-policy decision, and it is also emitted on the Python
API path, where no policy connection exists to name.

<!-- The `source` values below are read by tests/test_authorization_audit_doc.py and held
     to `client.VERIFY_SSL_SOURCES`. Keep them a comma-and-`or` run introduced by the
     words "where the effective setting came from"; that clause is the anchor. -->
### `event: "tls-verification-disabled"`

Emitted when an `HMCClient` is constructed with `verify_ssl` off, so the audit stream
can answer "were credentials ever sent over an unverified channel, and to which HMC".
Always `WARNING`. The logon-time `warnings.warn` stays and is the CLI user's channel;
under the default warning filter it renders once per process per location, which is
why this record exists alongside it.

```json
{"time":"2026-08-22T18:00:00+00:00","event":"tls-verification-disabled","host":"hmc-a.example","source":"environment:HMC_VERIFY_SSL"}
```

`host` is `HMCConfig.host`, the HMC the unverified session would reach; an unset
`HMC_HOST` renders as an empty string. `source` names where the effective setting came
from — `explicit-argument`, `environment:HMC_VERIFY_SSL`, or `field-default` — because
that is which knob an operator has to turn.

**One record per client construction**, not per request — which would flood the sink —
and not per process, which would miss a later client built with different settings.
Read the rate accordingly: an MCP tool invocation builds a fresh client, so on a server
left at the insecure default (`HMC_VERIFY_SSL` is `false` until 1.0, per
`docs/environment-variables.md`) this stream carries roughly one record per tool call
rather than one at startup. Deduplicate on `host` and `source` if you are alerting.

It carries no `policy`, `decision`, `reason`, `targets`, or `attribution`. Building a
client is not an access-policy decision, and it happens on the CLI and Python API paths
too, where there is no policy to name.

### `event: "records-dropped"`

Emitted by the sink itself, not by a decision, when lines were lost since the previous
such marker. It appears immediately **before** the next line that lands, so it reads as
"N lines are missing above this point".

```json
{"time":"2026-08-19T22:14:03.881271+00:00","event":"records-dropped","count":37}
```

It carries no `policy`, `attribution`, or anything a caller supplied — it describes the
sink's queue. Because it comes from the sink rather than the `hmc_mcp.audit` logger, it is
not affected by the level you set on that logger, and it is not produced at all when you
attach your own handler: your handler has no such queue. See
[ADR 0043](adr/0043-non-blocking-stderr-diagnostics.md).

## Reason codes

| code | decision | meaning |
|---|---|---|
| `permitted` | allow | one grant covered the tool, connection, and targets together |
| `configuration-unreadable` | deny | the configured HMC connections could not be read at all — **your** configuration, not the caller's input |
| `connection-not-granted` | deny | no grant naming the tool allows the selected connection |
| `target-selector-unreadable` | deny | a declared selector carried a value the boundary declines to read |
| `target-unboundable` | deny | the tool's selectors cannot bound it, so no `targets` table can |
| `target-selector-absent` | deny | a declared selector was omitted from the call |
| `target-not-granted` | deny | no grant allowed that combination of targets |

There is deliberately no `connection-selector-unreadable`: `reason` names the
*decision* and `state` names the *input*, and the connection dimension has one denial
template on purpose. To find malformed calls, filter
`connection.state == "unreadable"`.

## Attribution is never identity

Not every record carries `attribution`; each section above says whether its own does.
Where it appears, `verified` is always `false`, and neither value influences an
authorization decision at the dispatch boundary. Where the claim comes from differs, and
the `source` field is what tells you which you are reading.

**`source: "environment:HMC_AGENT_ID"`** — on the `authorization` record. Read straight
from the server process's environment at emission.

**`source: "config:agent_id"`** — on the `ownership-override` and `ownership-denied`
records. This is `HMCConfig.agent_id`, the effective value the ADR 0011 check compared,
and it differs from the other in three ways worth knowing: it may come from a
`config.toml` profile rather than the environment, it *is* validated by
`validate_agent_id`, and it renders the literal `hmc-mcp` when no identity is configured
at all rather than `null`. The last of those is what lets an unconfigured deployment's
ownership records be joined on the actor: the guard compares that literal, so the record
names it too.

Two things follow for the `authorization` record that are easy to get wrong:

- **It identifies the process, not the caller.** Under stdio one process serves one
  client and the two coincide. Under streamable HTTP one process serves many clients,
  so every record carries the same claim and the field tells you nothing per-caller.
- **It is the raw environment value, unvalidated.** This applies to the `authorization`
  record only — the ownership records' claims go through `validate_agent_id`.
  `docs/environment-variables.md` documents
  `HMC_AGENT_ID` as 1–64 printable ASCII with a forbidden-character set; that is the
  rule for *configuration*, and this record deliberately bypasses it so a malformed
  value is still reportable. A recorded claim may therefore be wider and stranger than
  that contract allows — bounded at 128 characters and JSON-escaped, but not validated.

ADR 0011's ownership protocol *does* take a real decision from the same value. That is
a separate mechanism, and `verified: false` describes this record's provenance rather
than the value's authority everywhere.

## Routing, levels, and silencing

Records go to the `hmc_mcp.audit` logger. A permit is `INFO`; everything else the
logger emits is `WARNING`.

Importing `hmc_mcp.audit` sets `propagate = False`, so no ancestor handler receives
audit records — including on the in-process path, where an embedder composes an
application itself and never calls the installer. `hmc-mcp serve` additionally attaches
a handler writing to **stderr**. With neither a handler nor propagation, a `WARNING`
record still reaches `logging.lastResort` on stderr, which is what a CLI user sees. To route them elsewhere, attach your own
handler to `hmc_mcp.audit` **before** calling `main_stdio` / `main_http` — the server
defers to a handler that is already there and will not add a second.

> The non-blocking guarantee is the **shipped sink's**, not the logger's. Your handler is
> called on the dispatch path, synchronously, so if it can block then so can the call.
> `logging.lastResort` — what a CLI process with no sink installed uses — is synchronous
> for the same reason.

> To set the level from the command line, pass `--audit-level LEVEL` to `hmc-mcp serve`:
> `DEBUG` and `INFO` keep everything the logger emits, `WARNING` drops permits and keeps
> the rest, and `ERROR` or `CRITICAL` silences the stream. Read `WARNING` as a volume floor
> rather than a quiet
> setting: on a server left at the insecure `HMC_VERIFY_SSL` default it still carries one
> TLS record per tool call, which the deduplication advice above applies to. The
> `records-dropped` marker survives every setting, since it comes from the sink rather
> than the logger. The name is validated — a misspelling is a usage error
> that starts nothing. Omitted, the shipped sink's own `INFO` default stands. An in-process
> caller keeps configuring the logger directly, before calling `main_stdio` / `main_http`.

Three things to know if you consume this stream:

- **Do not put your handler on `sys.stdout` under the stdio transport.** That stream
  carries JSON-RPC framing, and one audit record on it corrupts the protocol.
- **A launcher that merges the descriptors does the same thing.** `serve 2>&1`, or a
  unit file or wrapper doing it, makes stderr the JSON-RPC channel. Nothing inside the
  process can detect that. The same caveat applies to the startup warnings.
- **Skip a line that does not parse rather than failing on it.** `hmc_mcp.audit` is
  reserved for these records and that is checked inside this package, but a dependency
  or your own code can still log there, and other writers share stderr. Since
  [ADR 0051](adr/0051-fastmcp-logging-through-the-bounded-sink.md) FastMCP's own records
  arrive on the same queue as these — one concise line for a denial, a plain traceback for
  a genuine handler bug — and its startup banner is written straight to the stream by
  `rich` before serving begins.

## What this is not

Records are written to a process logging sink. They are not persisted, sequenced, or
signed, and retention and export are yours. A record is written *before* the denial is
raised and *before* a permitted handler runs, so a permitted call is recorded as
**authorized**, not as **succeeded**.

**Records are droppable, and delivery is asynchronous.** Since
[ADR 0043](adr/0043-non-blocking-stderr-diagnostics.md) a record is handed to a bounded
in-memory queue (1024 lines) drained by one background thread, so nothing on the dispatch
path waits on the destination. A destination that is absent, broken, closed, or simply not
being read costs records rather than costing the server: the queue fills and further lines
are dropped.

A drop is never silent. The count is carried in-band, ahead of the next line that lands:

```json
{"time": "2026-08-19T22:14:03.881271+00:00", "event": "records-dropped", "count": 37}
```

Read it as *37 items are missing above this point*. Two limits worth knowing: `count` is
items rather than records, because the startup warnings and — since ADR 0051 — FastMCP's own
records share this queue, so a non-zero count does not mean audit records were lost; and reporting
needs a destination that accepts a write again, so a stream that never recovers — or a
process killed with `SIGKILL` — still loses without a marker. An empty stream is therefore
still not evidence of an idle server. A record may also reach stderr slightly after the
tool result reaches the client; order among records is preserved.

**The record is readable by the party it is about, and it says more than the denial does.**
Under stdio the MCP client owns the server's stderr, so a client can read the records describing
its own calls. That matters for one field: `connection.resolved` carries the profile key a token
resolved to, or `<unresolved>` when it named nothing configured. The denial *message* withholds
that distinction on purpose — ADR 0038 makes an unresolvable token and a withheld one deny
identically, so a caller cannot use denials to test membership of `config.toml` — and the record
does not. A caller reading the stream therefore learns which of its guesses name configured
profiles, and a correctly-guessed nickname yields the profile key it targets. The disclosure is
names only, and `hmc_list_configured_hosts` offers a client more; if your profile inventory is
sensitive, withhold that tool by policy and route this stream somewhere the client cannot read.

Who has to do that depends on the transport, and under stdio it is not you. The MCP
client spawns the server and owns fd 2, so whether the stream is read binds the client
rather than the operator deploying it — choose one that reads its child's stderr. Under
`--http` it is whatever supervisor or journal collects the unit's stderr. Since ADR 0041
made a policy mandatory this applies to every deployment, and an ungranted caller can drive
the writes at call rate, because the record precedes the denial. Since ADR 0051 a denied call
puts *two* items on the queue — this record, then FastMCP's one-line denial — so the queue
fills in about half the calls it used to. Size the destination for the permitted path too:
on the insecure `HMC_VERIFY_SSL` default a permitted call also puts a TLS record on the
queue, per the rate noted with that record above. That caller can therefore make records
drop — bounded to the queue, visible as a `records-dropped` count, and never able to stall
a call.
`hmc-mcp serve --audit-level WARNING` halves what that caller can produce — permits are gone —
but the denials themselves stay, because an unrecorded probe is worse than a recorded one.
