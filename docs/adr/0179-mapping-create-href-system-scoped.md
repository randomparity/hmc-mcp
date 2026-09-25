# ADR 0179: Build a mapping create's client-LPAR href system-scoped

## Status

Accepted (2026-09-24).

## Context

`map_storage_to_lpar` and `create_optical_mapping` build a new `VirtualSCSIMapping`'s
`AssociatedLogicalPartition` href as `/rest/api/uom/LogicalPartition/<lpar>`
(`get_lpar_link`). ADR 0168 records that every observed HMC-emitted document links a
mapping's client partition absolutely and system-scoped
(`/rest/api/uom/ManagedSystem/<sys>/LogicalPartition/<lpar>`) and that the read side
already tolerates either form. The create POST therefore sends an href shape the HMC has
never been observed to emit for this element (issue #1036, found during #961/#962 review,
PRs #972 and #987).

Both creates already read-modify-write the VIOS's grouped `ViosSCSIMapping` document
(ADR 0169, `_rmw_vios_mapping`) before posting, and that GET's `VirtualIOServer` resource
carries an `AssociatedManagedSystem` link naming the managed system
(`tests/storage/vios_identity_v10r3.xml`). The managed-system UUID a system-scoped href
needs is therefore already present in data the create fetches, not new data to source.

## Decision

`get_lpar_link` takes both `system_uuid` and `lpar_uuid` and builds
`{base}/rest/api/uom/ManagedSystem/{system_uuid}/LogicalPartition/{lpar_uuid}`; it no longer
builds a root-scoped href. The system UUID is the final path segment of the VIOS's own
`AssociatedManagedSystem` href, read from the same grouped GET the read-modify-write already
performs, mirroring the parsing rule ADR 0168 already applies to `AssociatedLogicalPartition`.

Because that UUID is known only after the GET, the mapping document is now built inside
`_rmw_vios_mapping`'s mutate step (after the VIOS element is fetched) rather than before the
read-modify-write starts. `_rmw_vios_mapping`'s `mutate` callback gains a second parameter,
`system_uuid: str | None`, read from the fetched VIOS's `AssociatedManagedSystem` link and
passed to every caller's closure — `_rmw_vios_mapping` itself does not interpret it. Only the
two create-side closures use it: each raises `HMCError` and never builds a document when it is
`None`, giving both creates the same fail-closed shape ADR 0169 already gives a missing `ETag`
or `VirtualSCSIMappings` collection, without adding that check to `_rmw_vios_mapping` itself.
`delete_storage_mapping`'s `_detach_one` closure takes and ignores the parameter; a VIOS with
no `AssociatedManagedSystem` link still detaches normally, unaffected by this decision.
`lpar_uuid` and `storage_kind` keep their existing validation before the RMW GET runs, in
`map_storage_to_lpar`/`create_optical_mapping` themselves; only the href, which needs
`system_uuid`, is deferred into the mutate closure.

The read side (`lpar_uuid_from_href`) is unchanged: it already accepts either href form.

## Consequences

`get_lpar_link` becomes a two-UUID call; nothing calls it before the RMW GET runs. The
mapping-creation document text is built lazily by a closure passed to the shared append
helper instead of once up front; `map_storage_to_lpar` and `create_optical_mapping` keep
their existing public signatures and their existing early `lpar_uuid`/`storage_kind`
validation, unmoved. `_rmw_vios_mapping`'s two other callers, `delete_storage_mapping` and any
future mutate closure, gain one ignorable parameter each. A VIOS whose grouped GET omits
`AssociatedManagedSystem` (unobserved on real firmware, but not ruled out) makes both creates
report a clear error instead of posting an href form; a detach against the same VIOS is
unaffected. Test fixtures for the mapping RMW gain an `AssociatedManagedSystem` element to
reflect real VIOS responses.

## Considered & rejected

- **Keep the root-scoped href.** verified: the V10R3 read-only probes recorded in ADR 0168
  (issue #940) and `tests/storage/vios_identity_v10r3.xml`/`vscsi_mapping_v10r3.xml` (#979)
  show every observed HMC-emitted `AssociatedLogicalPartition` and `AssociatedManagedSystem`
  link is absolute and system-scoped; no observed HMC response uses the root-scoped form this
  client currently sends.
- **Fetch the managed system separately (a new GET) to get its UUID.** judgment: the grouped
  VIOS GET this read-modify-write already performs carries the same link; a second round trip
  duplicates data already fetched and adds another failure mode for no benefit.
- **Require callers to pass `system_uuid` into `map_storage_to_lpar`/`create_optical_mapping`.**
  judgment: widens two public client methods (and the CLI/MCP tools above them) for a value
  the client can derive from data it already fetches; no caller tracks a managed-system UUID
  today.
