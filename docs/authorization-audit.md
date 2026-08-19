# Authorization audit records

The server writes one structured record for every authorization decision it makes at
the MCP dispatch boundary, and one for every approved LPAR ownership override. This
document is the contract those records keep. The decision behind it is
[ADR 0040](adr/0040-authorization-audit-events.md).

## What you get, and when you get nothing

Records exist only for calls the access policy authorizes, which means **a policy must
be selected**. Without `--access-policy NAME`, no authorizer is on any dispatch path,
so `hmc-mcp serve` installs the sink and emits nothing at all — for the same reason it
enforces nothing at all. That default is what issue #225 changes.

Three other things produce no record, by design:

- a call to a tool the policy's ceiling withheld — it is never registered, so nothing
  reaches the boundary;
- `tools/list`, which discloses the whole ceiling in one call;
- `hmc_list_configured_hosts` and `hmc_effective_permissions`, which declare no
  connection argument. The second is worth knowing about: it returns the policy's name
  and source and every grant's constraints, so the most informative read the server
  offers is one it does not record.

**An empty audit stream is therefore not evidence that nothing was attempted.**

## The two records

Both are one physical line of ASCII JSON. `time` and `event` come first on both, and
every caller-supplied value is truncated to **128 characters** with no marker — a
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
| `reason` | one of the seven codes below |
| `connection` | `{"state", "selector", "resolved"}` |
| `targets` | a list of `{"kind", "argument", "state", "value"}`, or `null` |
| `attribution` | `{"claim", "source", "verified"}` |

```json
{"time":"2026-08-19T18:00:00+00:00","event":"authorization","policy":"lab-scoped","tool":"hmc_power_off_lpar","effect":"destructive","decision":"deny","reason":"target-not-granted","connection":{"state":"present","selector":"lab","resolved":"lab"},"targets":[{"kind":"lpar","argument":"lpar_name_or_uuid","state":"present","value":"other-01"}],"attribution":{"claim":null,"source":"environment:HMC_AGENT_ID","verified":false}}
```

`connection.state` is `present` when the caller supplied a connection string, `absent`
when it supplied nothing or an empty string, and `unreadable` when it supplied a value
of another type — whose `repr()` is never rendered. `connection.selector` is the
caller's own string, or `null` in the other two states.

`connection.resolved` is which HMC the call would actually have used: a profile key,
`"<default>"` for the environment/default connection, `"<unresolved>"` when the token
names nothing configured, or `null` when the configuration could not be read at all.

> **`<default>` and `<unresolved>` are reserved renderings that share a string space
> with legal profile keys.** A `config.toml` profile literally named `<unresolved>`
> renders identically to a token that resolved to nothing, so a filter on that value
> would collect both. Rename such a profile if you have one.

> **An `HMC_HOST` collapse is visible only when the caller named a connection.**
> `state: "present"` with `resolved: "<default>"` can arise no other way. A caller that
> omitted the argument renders `absent`/`"<default>"` whether or not `HMC_HOST` is set.

`targets` is `null` only in the `configuration-unreadable` case, where selectors were
never extracted; `[]` means the tool declares none. An integer selector —
`vios_partition_id`, the only one — records `state: "present"` with its decimal
rendering; a boolean records `unreadable`.

### `event: "ownership-override"`

Emitted when an operator approves an [ADR 0011](adr/0011-multi-agent-lpar-ownership.md)
LPAR ownership override. Always `WARNING`.

```json
{"time":"2026-08-19T18:00:00+00:00","event":"ownership-override","system":"sys-a","lpar":"db-01","attribution":{"claim":"agent-7","source":"config:agent_id","verified":false}}
```

It carries no `policy`, `decision`, `reason`, `connection`, or `targets`, and not as
nulls — an ownership check on a token parsed from an LPAR description is not an
access-policy decision, and empty fields would read as one. It does not say **which
HMC** the override applied to; that is issue #271.

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

`attribution.claim` is `HMC_AGENT_ID` read from the **server process's** environment.
`verified` is always `false`, and the value never influences an authorization decision
at this boundary.

Two things follow that are easy to get wrong:

- **It identifies the process, not the caller.** Under stdio one process serves one
  client and the two coincide. Under streamable HTTP one process serves many clients,
  so every record carries the same claim and the field tells you nothing per-caller.
- **It is the raw environment value.** `docs/environment-variables.md` documents
  `HMC_AGENT_ID` as 1–64 printable ASCII with a forbidden-character set; that is the
  rule for *configuration*, and this record deliberately bypasses it so a malformed
  value is still reportable. A recorded claim may therefore be wider and stranger than
  that contract allows — bounded at 128 characters and JSON-escaped, but not validated.

ADR 0011's ownership protocol *does* take a real decision from the same value. That is
a separate mechanism, and `verified: false` describes this record's provenance rather
than the value's authority everywhere.

## Routing, levels, and silencing

Records go to the `hmc_mcp.audit` logger. Denials and ownership overrides are
`WARNING`; permits are `INFO`.

`hmc-mcp serve` attaches a handler writing to **stderr** and sets `propagate = False`,
so no ancestor handler receives audit records. To route them elsewhere, attach your own
handler to `hmc_mcp.audit` **before** calling `main_stdio` / `main_http` — the server
defers to a handler that is already there and will not add a second.

> Setting the level from the `hmc-mcp serve` command line is **not** possible today;
> that path exposes no logging option. The lever works for an in-process caller that
> configures the logger first. Issue #270 covers the gap.

Three things to know if you consume this stream:

- **Do not put your handler on `sys.stdout` under the stdio transport.** That stream
  carries JSON-RPC framing, and one audit record on it corrupts the protocol.
- **A launcher that merges the descriptors does the same thing.** `serve 2>&1`, or a
  unit file or wrapper doing it, makes stderr the JSON-RPC channel. Nothing inside the
  process can detect that. The same caveat applies to the startup warnings.
- **Skip a line that does not parse rather than failing on it.** `hmc_mcp.audit` is
  reserved for these records and that is checked inside this package, but a dependency
  or your own code can still log there, and other writers share stderr — FastMCP renders
  a traceback panel for a routine denial today (issue #267).

## What this is not

Records are written to a process logging sink. They are not persisted, sequenced, or
signed, and retention and export are yours. A record is written *before* the denial is
raised and *before* a permitted handler runs, so a permitted call is recorded as
**authorized**, not as **succeeded**.

If the destination is absent, broken, or closed, the record is dropped silently — no
counter, no marker. That keeps a diagnostic from failing a call, but it means an empty
stream is not evidence of an idle server. A destination that is open but never drained
is a different case: the write blocks (issue #269), so keep fd 2 drained.
