# VIOS mapping create by read-modify-write (#962)

## Problem

`map_storage_to_lpar` and `create_optical_mapping` POST a `VirtualIOServer` document whose
`VirtualSCSIMappings` (`kb="CUD"`) holds only the new mapping. If the HMC reads that as the
complete set, one create removes every other mapping on the VIOS. The #879 window proved the
safe form: GET `?group=ViosSCSIMapping`, append one mapping, then POST it to the same URL with
`If-Match`. Decision: [ADR 0169](../../adr/0169-vios-mapping-create-read-modify-write.md).

## Scope

- One private `StorageMixin._append_vios_mapping(operation, vios_uuid, mapping_document)` in
  `client_storage.py`, called by both create methods:
  1. GET `/rest/api/uom/VirtualIOServer/<vios>?group=ViosSCSIMapping` with a typed `Accept`
     and no schema-version header. A non-200 raises `HMCError`.
  2. A missing `ETag`, a mismatched VIOS identity (`_find_vios_element`), or an absent
     `VirtualSCSIMappings` raises `HMCError` before any POST.
  3. Append the builder document's one `VirtualSCSIMapping` to the fetched collection. Every
     fetched element is left untouched.
  4. POST the serialized `VirtualIOServer` element to the GET path with `If-Match`, `Accept: */*`
     and `Content-Type: ...; type=VirtualIOServer`. A 412 raises an `HMCError` saying the VIOS
     changed. The POST stays inside `_reconcile_storage_mutation`.
- The builders keep their signatures and child order (`AssociatedLogicalPartition`, `Storage`,
  `TargetDevice`). Only their docstrings change.
- Docs: the bootable-disk recipe in `docs/cli.md` drops `adapters add-vscsi`, and the
  `hmc_map_storage_to_lpar` docstring (regenerated into `docs/tools/storage.md`) says the
  mapping creates its own adapter pair.
- This is a clean extension with no ownership transition. `delete_storage_mapping` is unchanged.

### Failure model

1. Actors: an operator or MCP client driving `hmcpctl` against a V10R3 HMC; offline CI.
2. Invariants: a create never removes or alters a mapping the GET returned. Other partitions'
   storage is the asset at stake. No POST is sent without that GET's `If-Match`.
3. Accepted:
   - A grouped GET missing `VirtualSCSIMappings`, `UUID` or `ETag` cannot be mapped. The run
     fails closed before any write, and the error names the missing part.
   - A concurrent writer between the GET and the POST gets a 412, which is not retried.
4. Covered elsewhere: the shared VolumeGroup RMW (#936); live proof (#879); the provisioning
   `add_vscsi` step and the partition href scope (follow-up candidates).

## Success

1. Each create method issues one GET and one POST to the grouped VIOS URL, and the POST's
   `If-Match` equals the GET's `ETag`.
2. Every `VirtualSCSIMapping` from the GET appears byte-identical in the POST body, and the
   new mapping follows them.
3. A missing `ETag` or collection sends no POST, and a 412 raises `HMCError`.
4. Neither the recipe nor the docstring tells the operator to add a vSCSI adapter first.

## Validation

- S1–S3 · Mode: focused-test · `tests/storage/test_mapping_rmw.py`. The fixture is the
  `vscsi_mapping_v10r3.xml` mapping in a VIOS entry, and each refusal asserts that no POST was
  sent. Red on main: no GET. Green: `uv run --no-sync pytest tests/storage/test_mapping_rmw.py`.
- S4 · Mode: task-test-not-applicable · the change is prose in `docs/cli.md` and a docstring.
  `just tool-docs-check` asserts only that the page matches the regenerated output.
- The existing create tests in `tests/unit/` and `tests/storage/` now serve a grouped GET with
  an `ETag`.
