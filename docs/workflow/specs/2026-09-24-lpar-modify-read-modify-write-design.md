# LPAR rename and DLPAR: whole-partition read-modify-write (#1057)

## Problem

Rename, DLPAR processor, DLPAR memory and the `lpars modify` workflow POST sparse
`LogicalPartition` documents through `modify_logical_partition`. Rename and the modify
workflow's resources leg reuse the create builder, so they also send the create-only
`PartitionType kb="COD"`.

Live probe, V10R3 M1060, authorized lab partition (not activated), 2026-09-24, private evidence
`2026-09-24-q1057` on the live-test host. Every mutation had a full `?group=Advanced` read
before and after:

- **Current code:** rename through `modify_logical_partition` gets HTTP 406 (#935) before
  validation. Nothing changed.
- **Same sparse bodies with the #980 header shape** (`Accept: */*`, typed `Content-Type`,
  no `If-Match`). Each was rejected and changed nothing:
  - rename: HTTP 500, "LogicalPartition incompatible with ManagedSystem".
  - DLPAR memory: 400 `REST0001`, `PartitionName` expected.
  - DLPAR processor: 400 `REST0001`. `PartitionProcessorConfiguration` sits where the schema
    expects earlier elements.
  - modify-workflow resources: 400 `REST0001`. `PartitionType` sits where `PartitionName` is
    expected.
- **Whole-partition read-modify-write, #980 shape.** The GET `?group=Advanced` returns an
  `ETag`. The POST sends the edited element to the same URL with `If-Match`, and each
  returns 200:
  - `PartitionName`, `PartitionMemoryConfiguration/{Desired,Minimum}Memory`,
    `SharedProcessorConfiguration/{DesiredProcessingUnits,DesiredVirtualProcessors}` and
    `SharingMode` all round-tripped.
  - Each changed only the edited fields, their read-only `Current*`/`Runtime*` mirrors, and
    the volatile `MigrationStorageViosDataStatus`.
- **Sharing mode.** Setting `SharingMode=capped` removes `SharedProcessorConfiguration/
  UncappedWeight`, with or without an explicit weight of 0. Setting `uncapped` on a capped
  partition recreates the element with weight `0`.

So on V10R3 these operations write nothing today. A sparse POST is rejected by schema
validation, not applied as a partial update or a replacement.

## Decision

**One whole-partition read-modify-write in the client, used by every LPAR modify write.**
`LparsMixin.update_logical_partition(lpar_uuid, updates, subject)` generalizes the #980
`set_pending_boot_string` body without changing its request shape:

1. GET `/rest/api/uom/LogicalPartition/<uuid>?group=Advanced` with a typed `Accept` and no
   `X-HMC-Schema-Version`. A non-200 response, a missing `ETag`, invalid XML, or no
   `LogicalPartition` element is refused before any POST.
2. `updates(lpar)` returns a mapping from a slash-separated child path (for example
   `PartitionMemoryConfiguration/DesiredMemory`) to new text. The helper looks up each path
   under the partition element. A missing element is refused before any POST with
   `GET <path> has no <field>; refusing to write <subject>`. The code never creates an element.
   An exception raised by `updates` also propagates before any POST.
3. Only those elements' text changes. Every attribute, sibling and ordering stays as read.
4. The helper POSTs to the same URL with `Accept: */*`, a typed `Content-Type` and `If-Match`.
   A 412 means nothing was written. Any status other than 200, 201 or 202 raises `HMCError`.

`set_pending_boot_string` becomes a call with
`{"BootListInformation/PendingBootString": boot_string or None}` and subject `"the boot order"`.
Its requests, posted bytes and messages stay identical. `modify_logical_partition` loses its
last callers and is removed.

**The field mapping lives with the documents.** `documents/lpar.py` gains
`partition_updates(lpar, *, name=None, resources=None) -> dict[str, str]`. It is not named
`build_*`, because it builds no XML string. It maps:

- `name` → `PartitionName`.
- Memory values → `PartitionMemoryConfiguration/{Desired,Maximum,Minimum}Memory`, as integers.
- Processor values. The partition's mode is read from
  `PartitionProcessorConfiguration/HasDedicatedProcessors`. It is always written back
  unchanged, so a read missing it is refused. The requested `dedicated` value must match that
  mode. A different value raises `ValueError`, because switching needs a configuration element
  the read does not carry.
  - Dedicated partitions: `DedicatedProcessorConfiguration/{Desired,Maximum,Minimum}Processors`,
    as integers. A virtual-processor count raises `ValueError`.
  - Shared partitions: `SharedProcessorConfiguration/{Desired,Maximum,Minimum}ProcessingUnits`,
    rendered as the create builder renders them, plus `{...}VirtualProcessors`.
  - `SharingMode` follows the create builder's rule: dedicated partitions use `sharing_mode`;
    shared partitions use `uncapped` if `uncapped` is set, else `sharing_mode`, else `capped`
    when `uncapped=False`. An invalid `sharing_mode` raises `ValueError`. `UncappedWeight` is
    never written.

`build_dlpar_proc_document` and `build_dlpar_mem_document` are removed. `build_lpar_document`
becomes create-only; its docstring stops offering modify. `PartitionType` therefore reaches no
modify write.

**Operations.** Each operation authorizes first, as today. Then:

- `rename_lpar` and `modify_lpar`'s rename leg call the helper with `name` and subject
  `"the partition name"`.
- The modify workflow's resources leg makes one call with `resources` and subject
  `"the partition resources"`. It keeps its step records and error translation.
- `set_lpar_processors` and `set_lpar_memory` refuse before authorization or any request when
  `resources` carries none of their fields. They call the helper with subjects
  `"the processor configuration"` and `"the memory configuration"`. The memory call passes only
  the memory fields.
- `translate_lpar_write_error` still wraps each call.

The two DLPAR tool docstrings stop describing a minimal document, and `docs/tools/` is
regenerated. `CHANGELOG.md` records the fix and the narrowed processor contract.

## Considered & rejected

- **Keep sparse POSTs, fix placement and drop `PartitionType`.** verified: the 2026-09-24 probe
  above. V10R3 schema validation needs `PartitionName` and the full element order, so a
  valid "sparse" body is the whole partition anyway.
- **A generic path-to-text mapping without a read-dependent callable.** judgment: `dedicated`
  unset must follow the partition's current mode. Only the read knows that mode.
- **Switch dedicated↔shared by building the missing configuration block.** judgment: it makes
  up an element with guessed `kb` values and order. #961 showed that V10R3 rejects such
  elements, and the switch was not probed.
- **One RMW for rename plus resources in `modify_lpar`.** judgment: it would merge the
  workflow's separate `rename` and `resources` step results, a published output.

## Failure model

1. **Actors and deployments:** a local CLI operator and an agent-driven MCP client. Both write
   through `hmcpctl` to one HMC, V10R3 M1060 as observed. V11R2 is unobserved.
2. **Invariants and assets:**
   - Partition fields outside the returned mapping must not change. The POST sends the values
     read, under `If-Match`.
   - The boot-order request shape stays byte-identical (#980).
   - Rename, DLPAR and `lpars modify` are published CLI and tool contracts.
3. **Accepted failure classes:**
   - A dedicated↔shared switch, and virtual-processor counts on a dedicated partition, are
     refused before any POST. They wrote nothing on V10R3 before this change either.
   - Capping drops `UncappedWeight`, and uncapping a capped partition leaves weight `0`. That is
     HMC behaviour. No weight parameter exists, and adding one is out of scope.
   - An invalid `sharing_mode`, or a mode mismatch, is found after the GET and before the POST.
     This costs one read, and nothing is written.
   - DLPAR on a *running* partition was not probed. The partition stayed not activated, and
     activation was not required to show which fields change. A running partition may reject
     or defer the change. The HMC's answer is reported.
   - A 406 on these headers is reported as the HMC's error (#935). On V10R3 they got 200.
4. **Covered elsewhere:** other sparse writers (operator); the 406 header strategy (#935).

### Threat model

- **Boundaries:** caller names and numbers enter XML text nodes. ElementTree escapes them once,
  and `LparResources` types bound them. The HMC response is parsed with `defusedxml`.
- **Actor:** an MCP client can send any value. Authorization is the existing
  `resolve_and_authorize_lpar_mutation`, and it runs before the GET.
- **Out of scope:** a hostile HMC response. The client trusts its HMC.

## Success

1. For rename, DLPAR memory, DLPAR processor and both `modify_lpar` legs, a respx test shows:
   one GET `?group=Advanced`, then one POST with `If-Match`, `Accept: */*`, and the fixture
   partition with only the mapped fields' text changed.
2. The helper refuses without a POST on a missing ETag, a missing mapped element, a non-200
   GET, invalid XML, and a mode mismatch. It reports a 412 as nothing written.
3. No request body from the operations in 1 contains `PartitionType`.
4. The existing `tests/lpar/test_boot_order.py` passes unchanged.
5. Live: the new code renames and restores the partition, and changes and restores memory and
   processor values. Before/after full reads diff only the target fields and their mirrors. The
   final read equals the baseline.

## Validation

- Focused tests:
  - `tests/lpar/test_lpar_rmw.py`: new. Covers the helper and `partition_updates`
    (criteria 1-3).
  - `tests/lpar/test_boot_order.py`: unchanged (criterion 4).
  - The updated `tests/lpar/test_dlpar_operations.py`,
    `tests/lpar/test_reconfiguration_ownership.py` and
    `tests/unit/test_lpar_modify_workflow.py`.
- Guardrails: `just verify` and `uv run --no-sync prek run --all-files`.
- Live acceptance: the operator's live-test host under the campaign lock (criterion 5).
