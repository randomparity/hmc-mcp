# Single-system reads of a non-operating system — design (#1302)

Part of #1297. Charter: the `WORK:SCOPE` annotation on #1302 (token `q1302-349ff859`).

## Problem

Since #1301 (PR #1305), `HMCClient.list_logical_partitions(system_uuid)` and
`HMCClient.list_vios(system_uuid)` raise `HMCError` from
`require_operating_system` when the scoped feed is empty and the system's
`State` is not `operating`. The message names the system, its UUID, `State`
and `DetailedState`. The single-system paths below now receive that error;
this design fixes what each one does with it and pins it with tests.

## Design

**Ownership membership** (`operations/lpar/ownership.py`
`_verify_partition_on_system`). It wraps the `HMCError` in a `ValueError`
whose text advises "retry, or omit the selector to have the system
discovered". Neither helps a system in `recovery` or `no connection`:
discovery skips it as unreadable and ends in a not-found error. The new
message is `Cannot confirm LPAR <selector> belongs to managed system <uuid>:
<error>`. It keeps the client error text, which names the system and state,
and gives no advice the error cannot support. The exception type stays
`ValueError`.

**`list_lpar_ownership` with a system selector**: no code change. The client
error propagates; the test pins that it is raised and that no `[]` is returned.

**Decommission** (`operations/lpar/decommission.py`): no code change.
`_resolve_target_lpar` and `_inventory_storage_mappings` call the client feed
methods without a `try`, and `decommission_lpar` builds its inventory before
the dry-run return and before any mutating step. The `HMCError` therefore
aborts both the preview and the real run before `_missing_target_error` or an
empty mapping set can be produced. Tests pin this through `decommission_lpar`
itself, with `dry_run=True` and `dry_run=False`.

**Templates** (`operations/templates/core.py` `deploy_partition_template`):
warn, no code change. The two `list_logical_partitions` reads exist only to
infer the created LPAR for the ownership stamp. On `HMCError` both already
skip inference and stamping and return `BASELINE_SNAPSHOT_WARNING` or
`POST_SNAPSHOT_WARNING`. So no LPAR is ever inferred from the empty feed.

Considered and rejected:

- **Refuse the deployment when the baseline read raises.** judgment: the
  baseline is read only with `wait=True`, so the refusal would gate one mode
  of a create and not the other. It would also treat a non-operating system
  differently from every other baseline failure, which warns today
  (`test_waited_deploy_keeps_success_when_snapshot_fails`). Whether a
  deployment can run is the HMC's decision; the job reports it.
- **A `SystemNotOperatingError(HMCError)` subclass** so ownership can choose
  advice by type. judgment: one call site; the #1301 design rejected the same
  type for two.

## Failure model

1. **Actors and deployments**
   - MCP client, CLI operator or library caller acting on one named managed
     system that may be in `recovery` or `no connection`.
2. **Invariants and assets at stake**
   - Decommission (preview and run) never reports a missing target, an empty
     storage blast radius, or any result for a non-operating system whose
     LPAR or VIOS feed is empty; it raises before any mutating step.
   - Ownership membership and `list_lpar_ownership` with a system never report
     "not on system" or `[]` for that case.
3. **Accepted failure classes**
   - A transient feed error on the membership check loses the "retry" hint;
     the error text still names the cause. Bounded: wording only.
   - A template deployment to a non-operating system is still submitted when
     `wait=True`; the result carries the snapshot warning and no stamp, as for
     any snapshot failure today.
   - Neither the template warning nor its log line (`HTTP status None`) names
     the non-operating state; the operator learns it from the system's state.
     Bounded: no LPAR is inferred or stamped.
4. **Covered elsewhere**
   - Unscoped `list_lpar_ownership` and other HMC-wide feeds: #1293.
   - 204 shape and non-operating sweep: #1290.
   - Per-system I/O and SR-IOV feeds: out of scope.
   - Client feed check and fleet paths: #1301.
   - Live verification: operator (`verification:live-hmc`).

## Validation

Tests live in `tests/unit/test_empty_feed_system_state.py` and use an empty
(HTTP 204) scoped feed and a `no connection` / `Unknown` system named `sys-R1`
through a real `HMCClient` over respx.

| Contract | Mode | Evidence |
| --- | --- | --- |
| Membership check names system and state, no retry advice | focused-test | new test on `_verify_partition_on_system` |
| `list_lpar_ownership` with a system raises | focused-test | `list_lpar_ownership` row in `READS` |
| Decommission target resolution raises, no missing-target error | focused-test | `decommission` row in `READS` (dry run) |
| Decommission storage inventory raises, no preview or mutation | focused-test | new test: dry run and real run; exact `HMCError` naming system and state, no non-GET request |
| Template deployment warns and does not stamp | focused-test | new test: baseline and post snapshot |
