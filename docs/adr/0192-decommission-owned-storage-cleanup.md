# 0192 — Decommission deletes only recorded, unreferenced owned storage, and only on request

## Status

Accepted (2026-10-01), issue #1216. Partially supersedes ADR 0027's "never deletes storage
mappings or backing storage", for the owned resources defined here only. ADR 0027's
selection, ownership, ordering and no-rollback rules stand.

## Context

ADR 0027 inventories storage as blast radius and deletes none of it. Epic #1215 requires
teardown to delete owned virtual storage on explicit intent and retain shared media,
repositories and physical volumes. Incomplete inventory must block destructive cleanup, and
recovery must remain possible after the partition is gone. Virtual disks and media carry no
description field, so ADR 0011's stamp cannot mark them.

## Decision

1. **Intent.** `hmc_decommission_lpar` gains `storage_cleanup`: `retain` (the default, and
   today's behavior) or `delete_owned`.
2. **Owned** means the resource has an entry for this partition in the ADR 0190 resource
   ledger, which is never pruned. The resource is either a virtual disk (VIOS UUID, volume group
   UUID, disk name) or an installer media name. A matching name alone is never ownership.
3. **Deletable** means owned, and at execution time referenced by no mapping except one to
   this partition, read in the same pass. Physical volumes, media repositories, shared
   or foreign media and foreign disks are always retained.
4. **Incomplete inventory blocks deletion.** Any VIOS whose storage detail is unavailable, or
   any mapping too sparse to classify, retains every owned item. ADR 0027's partition teardown
   still proceeds with the warning.
5. **Order and resume.** The partition is torn down first. Owned items are then unmapped,
   unmounted and deleted one at a time, each as a recorded effect. Items left over after a
   failure remain in the operation record. Resume authorizes against the recorded system, VIOS
   and volume-group targets, because the partition selector no longer resolves.
6. **No provenance, no deletion.** Partitions created before the store existed, or whose
   records are lost, are retained under `delete_owned`. Each retained item is reported with its
   reason.

## Consequences

- Teardown reports deleted, retained (with reasons) and pending items separately.
- A store loss converts pending cleanup into retained storage, never into deletion.
- Volume-group writes keep the read-modify-write and `If-Match` contract of ADR 0171; whether
  the HMC enforces `If-Match` is unconfirmed (#879). Until it is, cleanup needs the exclusive
  writer window in the spec.

## Considered & rejected

- **Delete by name prefix.** judgment: fit. A foreign or hand-made disk with a matching name
  would be destroyed.
- **Delete every storage item mapped only to this partition.** judgment: fit. It destroys
  storage another workflow attached and expects to keep.
- **Leave storage cleanup to the specialists.** judgment: fit. The epic requires one teardown
  to report and resume its cleanup.
- **Default to `delete_owned`.** judgment: fit. Existing callers would delete storage without
  having asked to.
