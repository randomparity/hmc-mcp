# ADR 0180: An audit record for each LPAR PowerOff, naming the variant sent

## Status

Accepted (2026-09-28). Adds one member to ADR 0040's closed `Event` vocabulary under ADR 0040's
stability rule (a field or event may be added, never renamed, removed, or retyped).

> **Amended by #1115** (2026-09-29): decision 3's "one emit point" and the Consequences bullet
> exempting the decommission workflow no longer hold. The decommission workflow's own PowerOff
> (`hmc_decommission_lpar`, `hmcpctl lpars decommission`, `decommission_lpar`) writes one
> `lpar-power-off` record too, immediately before its submit and after the ownership check it
> already runs, with `operation="shutdown"`, `restart=false` and the `immediate` it sends. The
> exemption's reason, that decommission has no variant to tell apart, answered whether crash and
> graceful stop can be distinguished, not whether the stop is recorded at all; an operator
> filtering for every partition stop missed these. A partition already `not activated` is not
> submitted a PowerOff and writes no record, and a dry run writes none. A submit that raises is
> still recorded, as in `power_lpar`.

## Context

ADR 0164 gave the LPAR PowerOff job a closed `operation` vocabulary — `shutdown`, `osshutdown`
and `dumprestart` — next to the `immediate` and `restart` flags. These are different acts.
`osshutdown` asks the operating system to stop cleanly. `dumprestart` crashes the partition and
takes a platform dump. `restart=true` starts the partition again after it stops.

The audit stream could not tell them apart (#895). The served path writes one `authorization`
record per call (ADR 0040), but its identity is the tool name, which is the same for all three
variants, and it carries only the declared target selectors. Its field set is fixed and
ordered, and it is written by `dispatch_scope.authorize` before the handler validates anything.
The CLI and the Python API write no `authorization` record at all. The ownership records
(ADR 0100) appear only when the ADR 0011 guard is on and a refusal or override happens.

`power_lpar` is the one function that every LPAR power-off entry point reaches:
`hmc_power_off_lpar`, `hmcpctl lpars power-off`, and a direct Python API call. It validates the
operation (ADR 0164), then runs the ownership guard when `authorize_power_operations` is on
(ADR 0092), then builds the document and submits it.

## Decision

1. **A new event, `lpar-power-off`.** It joins the closed `Event` vocabulary, with its own
   builder, `records.record_lpar_power_off`. It is emitted at `WARNING`, like the other
   non-authorization events.
2. **Its fields, in this order:** `time`, `event`, `lpar` (the resolved partition UUID),
   `host` (`HMCConfig.host`), `operation`, `immediate`, `restart`, and `attribution` (the
   `config:agent_id` claim, `hmcpctl` when unset — the same claim the install and console
   records carry). `operation`, `immediate` and `restart` are the values the job document
   carries. `lpar`, `host` and `operation` take the shared 128-character bound.
3. **One emit point, in `power_lpar`'s PowerOff arm,** after the ADR 0164 validation and the
   ADR 0011 guard, and immediately before `submit_job`. A refused call sent nothing and writes
   no record. A submit that raises may still have reached the HMC, so it is recorded, as
   `install-attempted` is (ADR 0102) and `console-write` is (ADR 0176).
4. **PowerOn is not recorded by this event.** The operator excluded it from #895's scope.

## Consequences

- An operator can filter `event == "lpar-power-off" and operation == "dumprestart"` to find
  every forced crash, on every entry point, with or without a served access policy.
- A served call now writes two records for one power-off: the `authorization` permit and this
  one. They are joined on time and the target, not on a shared identifier, the same as the
  install records.
- With the ownership guard on, an approved override writes `ownership-override` and then
  `lpar-power-off`, in that order.
- The record proves an attempt, not that the partition stopped. With `wait=false` nothing
  observes the job's outcome.
- The decommission workflow submits its own PowerOff and does not go through `power_lpar`. It
  always sends `shutdown` with `restart=false`, so it has no variant to tell apart, and it is
  not recorded by this event.
- Absence of the record is not proof that nothing was submitted, for the reasons
  `docs/authorization-audit.md` gives for every record: sink drops, `--audit-level ERROR`, and
  an embedder's logging configuration off the serve path.
- Who may request `dumprestart` is a separate question, owned by #896.

## Considered & rejected

- **A field on the `authorization` record.** judgment: fit. That record's fields are "always
  present and in this order" (ADR 0040), so an `operation` field would be `null` on every other
  tool. It is written before the handler validates the argument, so it would record what was
  asked, including a value about to be refused, rather than what was sent. It is also absent on
  the CLI and Python API paths.
- **A field on the ownership records.** judgment: fit. They are written only when the guard is
  on and refuses or is overridden, so most power-offs would leave nothing.
- **Emit from the `hmc_power_off_lpar` tool body.** judgment: fit. The tool body runs before
  validation and the guard, and it misses the CLI and the Python API.
- **One `lpar-power` event for both directions.** judgment: cost. PowerOn has a different
  parameter set, and auditing it is outside this change's approved scope.
- **Import `PowerOffOperation` into the audit module.** judgment: fit. The module imports
  nothing from `hmcpctl` but the sink, and `target_scope` imports back from it. The value is
  validated upstream and bounded here, as `effect` already is.
- **Do nothing.** judgment: fit. A forced crash and a graceful stop leave the same trace.
