# `hmc_power_lpar`: start, stop and restart as a logical operation

**Issue:** #1223 (epic #1215) · **Decision:** [ADR 0199](../../adr/0199-lpar-power-is-one-journaled-job.md)
· **Governing:** [H1 spec](2026-10-01-logical-lpar-workflows-design.md), ADRs 0188, 0189, 0190, 0195
· **Branch:** `feat/power-lpar-1223` from `main` · **Guardrails:** `just verify`;
`uv run --no-sync prek run --all-files`

## Purpose and boundary

Agents today pick PowerOff vocabulary, poll jobs and re-read state themselves
(`hmc_power_on_lpar`, `hmc_power_off_lpar`). `hmc_power_lpar` does that as one durable logical
operation on the ADR 0195 engine, and is the engine's first production consumer.

Out of scope (operator-approved exclusions): `dumprestart` (ADR 0188); holds and `hold_id`
(#1231); guest readiness (ADR 0191); the default listing switch (#1232); inspection and refcodes
(#1224); provisioning power-on (#1225, #1230); a CLI mirror; any live power run.

## Contract

**Inputs:** `lpar_name_or_uuid`, `system_name_or_uuid`, `action` (`start` / `stop` /
`restart`), `mode` (`graceful` default / `immediate`), `request_id`, `continuation` (`none`
default / `resume` / `abandon`), `wait_seconds` (0–600, default 60), `profile`. A continuation
may omit the selectors, `action` and `mode`; the operation's recorded values are used, and any
value given must match them (ADR 0190 Decision 3). A new operation needs all three of
`lpar_name_or_uuid`, `system_name_or_uuid` and `action`.

**Result:** the `OperationRecord` `hmc_operation_status` returns: phase `validating` then
`transitioning`, the one `power` effect, warnings and `next_actions`. When the body ends, its
`result` holds `action`, `mode`, `system_uuid`, `lpar_uuid`, `already_in_state` (bool),
`observed_state` (the last partition state read) and `job_id` (or null) when the operation ends
`completed` or pauses `needs_attention`. A `failed` operation has no `result`; its warning names
the partition UUID and the state or job status. Host power state is the only readiness
reported; guest readiness is not (ADR 0191). For `restart`, `observed_state` shows the
partition activated after a successful job, not proof that it cycled (ADR 0199 Decision 6).

**Registration:** `effect="destructive"`, `operation="lpar.power"`, `target_kind="console"`,
not exhaustive, with the two selectors declared optional so audit records name them. It is in
`PRIMARY_TOOLS` and registers only when the policy permits it.

## Authorization

ADR 0189 Decision 2, per action: `start` delegates to `hmc_power_on_lpar`; `stop` and `restart`
to `hmc_power_off_lpar`. Before `engine.submit`, the handler:

1. resolves the effective `action` and selectors: the call's, or on a continuation the
   recorded operation's (agent id and `request_id`, read without creating the store). A
   continuation with no record is refused `not_found` here, before the permit check,
   authorization or admission;
2. refuses with `PermissionError` naming the delegated tool when the policy does not permit it;
3. authorizes the call as that tool through `dispatch_authorizer` with its `lpar`,
   `managed_system` and connection arguments. A denial is raised unchanged, so the call fails
   and nothing is recorded.

One helper, `authorize_as`, performs step 3 for this tool, `hmc_plan_lpar` and `hmc_inventory`,
replacing the two copies of the same code in `server_tools/lpar/plan.py` and
`server_tools/inventory/logical.py`. Those two keep turning a denial into their own
`denied` result.

## Body

Runs in the engine's worker thread with its own HMC client for the recorded connection.

1. If the `power` effect is already `applied`, skip to step 5 with the recorded partition and
   JobID; nothing is resolved again, so a read error there pauses rather than fails. With no
   JobID (applied by classification) step 5 skips the job poll.
2. `validating`: resolve the system UUID (lower-cased) and list its partitions; the selector
   must match exactly one by UUID or name. With `HMC_AUTHORIZE_POWER_OPERATIONS` set, run the
   ADR 0011 ownership check (no override). Take the partition guard.
3. States per ADR 0199 Decision 5: already in state completes with no effect; any other state
   the action does not accept fails naming it; a graceful stop or restart without RMC `active`
   fails naming `mode=immediate`.
4. `transitioning`: write the one effect (ADR 0199 Decisions 1 and 3). The PowerOff submit
   shares `power_lpar`'s audit-before-submit step through one extracted function. Any submit
   error leaves the effect `uncertain` (ADR 0199 Consequences).
5. Poll the job (300 s) and settle the state (120 s) per ADR 0199 Decision 6.

Every refusal in steps 2–3 raises `OperationFailed`, so the operation ends `failed` and the guard
is released. Its message names no request argument: the partition is named by UUID, and an
ownership refusal keeps only its exception type. A failed job's warning carries the HMC's own
result text, which can name the partition: it is the HMC's answer, not a request argument, and
it tells the caller why the job failed.

Classifiers for `lpar.power_on` and `lpar.power_off` (ADR 0199 Decision 4) register when the
operations module is imported, read `PartitionState` through their own client, and record
identity `{"job_id": null}` when they answer `applied`.

## Failure model

**Actors and deployments**

- An MCP client agent on a server with a served access policy and one operation store (H1 spec
  deployments: one long-lived server, or stdio sessions on one host).
- Other HMC writers changing the partition's power state.

**Invariants and assets at stake**

- One operation writes at most one power job, and none is a dump restart.
- Stop or restart authority withheld from `hmc_power_off_lpar` is never conferred by the
  `hmc_power_lpar` grant.
- `immediate` is reached only when the caller states it.
- The operation record's effect status matches what was written.

**Accepted failure classes**

- *An interrupted write, or a refused submit, needs a human unless the partition already shows
  the target state.* Accepted: ADR 0199 Consequences; the alternative risks a second job.
- *Another writer changes the state mid-operation.* Accepted as in the H1 spec: not a fence. The
  settle step reports the observed state.
- *The 300 s and 120 s bounds are unmeasured.* Accepted: exceeding them pauses, and `resume`
  continues; a live run measures them.

**Covered elsewhere**

- Holds: #1231. Guest readiness: ADR 0191. Live proof: a separately authorized run.

## Threat model

**Boundaries:** added — the logical grant → the two power specialists' authority; widened —
continuation arguments → authorization (read from the store, ADR 0190's private file).

**Actors:** an MCP client holding a narrower grant than the server; trust is placed in the
served policy and the private state directory.

**Controls:** the delegated permit check and `dispatch_authorizer` per call, including every
continuation (re-authorized as a fresh call, ADR 0190 Decision 6); the engine's connection and
digest binding on continuations; ADR 0011 ownership as the specialists apply it; no dump restart
is reachable because the body builds only `osshutdown`, or `shutdown` with `immediate=true`
(ADR 0199 Decision 1), never `shutdown` with `restart` and without `immediate`.

**Out of scope:** a local user who can write the state directory (H1 spec threat model).

## Validation

| Contract | Mode | Evidence |
| --- | --- | --- |
| delegation per action, withheld tool, target denial, continuation re-authorization | focused-test | `tests/app/test_lpar_power_tool.py` |
| start/stop/restart bodies, already-in-state, state refusals, mode mapping (all four PowerOff documents), replay without re-resolving, read error after the write, settle, timeouts, guard key casing | focused-test | `tests/unit/test_lpar_power.py` |
| classifiers (applied without JobID, never resubmitted) and restart's absent classifier | focused-test | `tests/unit/test_lpar_power.py` |
| shared `authorize_as`; plan and inventory unchanged | focused-test | existing plan and inventory tool tests |
| PowerOff submit extraction | focused-test | existing `power_lpar` tests |
| registry counts, catalog, generated docs | focused-test | `tests/app/test_tool_security.py`, `just tool-docs-check`, capability inventory |
