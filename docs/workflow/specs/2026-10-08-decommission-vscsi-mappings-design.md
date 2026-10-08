# Decommission target vSCSI mappings before deleting their client (#1387)

## Authority and scope

Frozen scope: issue #1387, token `q1387-53c0b682`, scope comment 6065679326.
Operator2026-10-07 chose option A and excluded backing-storage deletion (#1229).
Operator2026-10-08 approved the fail-closed prerequisite and the necessary
ADR 0192 consistency amendment. Root authorizes existing full ST41 routing only;
execution still needs a separate exact pushed-head live-slot grant.
One PR; non-trivial/full-spec; M/250 changed-line denominator; iterating review.

## Problem

The canonical decommission deletes client adapters and the partition but leaves
its VIOS vSCSI mapping. The supported detach operation authorizes the existing
mapped client, so callers cannot use it after that client disappears. ST41 currently
pre-detaches the mapping and therefore does not exercise the product defect.

## Global constraints

Python 3.11 minimum; CI Python 3.11–3.14 on amd64 and arm64.
No new dependency, configuration setting, arm, selector, numbered record or facade export.
Sibling worktree only; `just setup` is the only environment sync; every `uv run` uses `--no-sync`.
No pushed rebase, force push, squash or worker merge. Root owns merging and live grants.
Public evidence is PII-redacted; raw test-results JSON stays private.

## Architecture and ownership

Extend `operations/lpar/decommission.py`, the current canonical workflow owner.
MCP `server_tools/lpar/lifecycle.py` and CLI `cli_lifecycle.py` continue delegating
without signature changes. Reuse `client_storage.storage_mapping_id`,
`mapping_lpar_uuid` and `HMCClient.delete_storage_mapping(vios_uuid, mapping_id,
lpar_uuid)`: the existing client verifies fresh target identity and If-Match.
Do not route through a new authorization bypass or change client reconciliation.
`collect_storage_records` and its direct `lpar.inspect` caller retain their
non-mutating curated observation contract. The new exact deletion plan is private
and derived from the same current storage-detail reads; it is not a mapping UUID.
ADR 0027 governs baseline mapping detach; ADR 0192 keeps future owned backing
storage cleanup, ledger provenance and explicit intent with owner #1229.

## Inventory and execution contract

- For the selected system's listed VIOSes, retain the current vSCSI/vFC blast radius.
- Build a separate exact vSCSI plan from valid client UUID links. Unknown/numeric-only
  client identity cannot establish target or foreign ownership and blocks execution.
- Missing VIOS UUID, missing/malformed detail Resource, malformed mapping collection
  or member, missing target mapping ID, or duplicate ID involving a target mapping
  makes vSCSI inventory incomplete. A missing requested collection is incomplete (ADR 0169); an explicitly empty
  collection or attribute-only empty container is valid empty.
- Validate target mapping identity with the existing helper. Foreign mappings with
  valid non-target client UUIDs are retained; their backing/device fields are not
  required except where an ID duplicates a target ID. Compare UUIDs case-insensitively.
- A sparse vFC observation alone remains a warning and does not block vSCSI completion.
- Dry-run returns current warnings and four dry-run steps, including the exact
  `{vios_uuid, mapping_id}` detach plan; no write or ownership bypass occurs.
- Execution with incomplete vSCSI inventory returns four ordered statuses:
  power_off skipped, detach_storage_mappings error, detach_adapters skipped,
  delete_lpar skipped. `workflow_completed` and `resource_deleted` are false.
  This check runs before the existing first-mutation ownership check and power job.
- Complete execution retains existing ownership checks, powers off as needed, and
  verifies exactly `not activated` before mapping writes. Detach sorted exact
  VIOS/mapping identities once each through the guarded client primitive.
- Mapping failure returns an error containing successfully detached identities and
  the original diagnostic; adapters and partition deletion are skipped. Missing,
  duplicate or retargeted fresh identity, missing ETag, 412, or transport/HMC failure
  is failure, even if an existing diagnostic readback suggests a side effect.
- Recheck inactive state before adapter deletion. Preserve existing adapter ordering,
  per-phase partial results, fail-fast/no rollback and final deletion semantics.

## ST41 proof and cleanup

Use existing preflight `--group lpar-power`, named `scripts/live_lpar_power.py`,
and recovery, serially on the exact granted pushed head. No new selector.
Retain the existing dedicated-activation prerequisite gap (#1390); do not fix it.
For P, snapshot state, exact client-adapter inventory, VIOS mappings and volumes
around dry-run; require complete matching target mapping IDs in its new dry-run
phase as well as existing adapter/backing-volume inventory. A failed/unverified
preview stops before real decommission and retains P/volume for manual recovery.
Remove the successful-path `_detach` workaround. Real decommission must report
completed/deleted; readbacks must show P and its mapping gone, the run LV retained,
and foreign mappings/volumes unchanged. Existing final baseline comparisons remain.
After a failed/ambiguous decommission, mark the run for manual recovery and bypass
automatic mapping/partition/volume teardown; do not retry the detach. Successful
proof allows the existing explicit arm LV cleanup only after confirmed no mapping.
New judged assertions: `storage-mapping-absent`, `backing-volume-retained`.
A failed live observation remains failed; no password/RMC or readback-success claim.

## Success

C1–C4: exact dry-run plan and no writes; incomplete vSCSI blocks first mutation;
ordered guarded mapping detach precedes adapters/delete; target errors stop later
writes; the new phase retains vFC/foreign/backing resources.
C5: the granted existing ST41 path proves the native mapped teardown and unchanged
unrelated baseline without prior detach, and retains failed state for manual recovery.
C6: decision/runbook/spec consistency, focused red-green and controlled faults,
full local guards/hooks, bounded independent review and exact-head CI.

## Failure model

1. Actors/deployments: local authenticated operators via MCP/CLI/library, offline CI,
   and one orchestrator-granted exclusive live writer on the bounded test system.
2. Assets/invariants: target ownership, other partitions' attachments and storage;
   truthful partial teardown; no deletion after failed/uncertain mapping detach.
3. Accepted classes: out-of-band additions after the independent inventory snapshot
   are outside the advisory inventory guarantee (ADR 0011); no new lock is introduced.
   Lost responses may have effects and retain manual recovery (ADR 0136), never retry.
   HMC mutation refusals are failed evidence, not promoted success. Dry-run is no lease.
4. Covered elsewhere: future ledger-backed backing deletion #1229/ADR 0192; provision
   dedicated activation #1390; detach/RMC policy investigation #1391; existing client
   RMW/If-Match invariants ADRs 0168/0169; server dispatch policy remains unchanged.

## Threat model

- Boundaries: operator selectors enter existing system/child resolution and ownership;
  external VIOS detail enters the new exact plan; fresh HMC XML enters existing RMW.
  No new dispatch grant, credential, command construction or endpoint is introduced.
- Actors: authorized caller may supply foreign selectors; concurrent administrators
  or malformed responses may change mapping/client identity. HMC itself is trusted
  for enforcement; identity checks still reject unverifiable input.
- Controls: existing target-child and ownership guards; strict vSCSI structure/client
  UUID/ID completeness; fresh exact one-mapping client UUID + ETag; original errors
  stop later deletion. Public summaries contain curated IDs, not raw HMC payloads.
- Outside: exclusive-writer violations and HMC ignoring ETag are existing advisory
  concurrency risks; no post-delete orphan authorization or generic reconciliation.

## Validation

The ignored implementation plan inventories focused behavior tests for exact plan,
incomplete structure/identity, foreign/vFC retention, UUID case, order, ownership,
partial mapping failure, fresh RMW retarget/ETag refusal, and ST41 manual retention.
Existing inspection tests protect its unchanged caller contract. Controlled faults
remove target-client validation, preflight stop and mapping-error stop in turn and
must fail meaningful safety tests before restoration. Prose is inspected against
frozen C1–C6; no prose-string tests. Generated tool/capability consequences use their
existing structural gates and regenerators. First native proof is required before
MERGE-READY, alongside recovery and generated commit-stamped filtered evidence.
