# Live-test storage writes use the configured volume group

Issue #967. Part of #871.

## Problem

The VIOS lists volume groups in no fixed order. Three live-test paths use the first group
listed, not `LiveTestConfig.vdisk_volume_group_name` (`LIVE_TEST_VDISK_VOLUME_GROUP_NAME`):

- ST3 falls back to the first group, and ST14 creates the test disk and provisions in it.
- ST16 creates the media repository in it and reads `FreeSpace`, which is GiB, as MiB.
- ST22 deletes media and the repository in `vg_uuid`, even when this run created no
  repository.

`hmc_list_volume_groups` returns the `VolumeGroup` projection (`uuid`, `name`,
`free_space_gib`). The harness reads only the raw keys (`UUID`, `GroupName`, `FreeSpace`), so
it never matches a name. A subset run (`--group` or a single subtask) also restores
`vg_uuid` and `vmedia_repo_created` from an earlier results document. A document written
before this change can carry another group's UUID.

## Design

**Resolver.** `storage.resolve_configured_volume_group(state, stage, data, dependents)`
returns a frozen `ConfiguredVolumeGroup(uuid, resource, free_space_mib)` for the entry whose
`name`, or raw `GroupName`, equals the configured name. On a match it sets
`artifacts.vg_uuid` and `artifacts.vdisk_vg_name`. `vdisk_vg_name` is already in the results
document, so the match is recorded there. On a miss, meaning no matching entry or a match
without a UUID, it clears both fields, SKIPs each name in `dependents`, and returns `None`.
Free space is `int(gib * 1024)`, or `None` when the field is absent or unparsable.

**Trust.** `storage.configured_vg_uuid(state)` returns `vg_uuid` only when `vdisk_vg_name`
equals the configured name, and `None` otherwise. Only the resolver writes that pair, so a
restored document from before this change is never trusted.

**Call sites.**

- ST3 resolves, then reads disk capacity from the resolved group only.
- ST14's pre-flight takes `vg_uuid` from `configured_vg_uuid`. It fails before
  `_remove_previous_test_lpar` when that value is `None`. `rmvlog` uses the configured name.
- ST16 resolves from its own listing, where the dependents are repository create and get. It
  SKIPs both when the listing fails, or when `free_space_mib` is below
  `vmedia_repository_size_mib`. Unknown free space proceeds, as it does today.
- `vmedia._owns_repository(state)` is `vmedia_repo_created and configured_vg_uuid(state)`.
  It gates ST17, ST18, ST19 and ST20. It also gates all of ST22's mapping unmounts, media
  deletes and repository delete; when it is false these SKIP with
  `no repository created by this run`. ST16 sets the flag on create PASS. ST17's final
  restore step keeps today's True on PASS and False on failure.

## Failure model

1. **Actors and deployments:** a local operator running `scripts/live_test_runner.py`,
   either the full run or a subset run that restores a results document, against one
   authorized HMC/VIOS. CI runs the offline unit tests only.
2. **Invariants:** within ST3, ST14 and ST16–ST22, no volume-group, repository or optical
   create, delete, mount or unmount targets a group other than the configured one. None of
   them removes a repository, medium or mapping when `_owns_repository` is false.
3. **Accepted failure classes:**
   - A create that reports FAIL but actually succeeded leaves a repository behind for the
     operator.
   - If ST17 fails between deleting the small repository and restoring the main one, the flag
     goes False and a repository the run created is left behind. Leaving it is the safe
     direction.
   - Unknown free space proceeds to the create, which is today's behaviour.
4. **Covered elsewhere:** RepositorySize MiB/GiB in the client layer → #963. ST3 disk-size
   discovery (the projection carries no disks) and VLAN settings → #970. Live proof is
   deferred to the #879 window.

## Validation

| Contract | Mode | Evidence |
|---|---|---|
| The resolver selects the configured group listed last, records the name, and gives 1.5 GiB → 1536 MiB | focused-test | `tests/scripts/test_inventory.py` |
| A miss SKIPs the dependents and clears a pre-set `vg_uuid` | focused-test | `tests/scripts/test_inventory.py` |
| ST16 creates in the configured non-first group, and SKIPs at 5 GiB | focused-test | `tests/test_live_runner.py` |
| ST14 pre-flight fails with no power-off or delete when `vdisk_vg_name` is untrusted | focused-test | `tests/test_live_runner.py` |
| ST17 and ST22 issue no write when the repository is not owned | focused-test | `tests/test_live_runner.py` |
| Live listing order on a real VIOS | task-test-not-applicable | The order is non-deterministic and no offline VIOS exists; deferred to #879 |
