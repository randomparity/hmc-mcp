# Mapping create href design

## Problem

`map_storage_to_lpar` and `create_optical_mapping` build a new mapping's `AssociatedLogicalPartition`
href root-scoped (`/rest/api/uom/LogicalPartition/<lpar>`). [ADR 0168](../../adr/0168-vscsi-mapping-adapter-target-identity.md)
records that every observed HMC-emitted document links a mapping's client partition system-scoped
(`/rest/api/uom/ManagedSystem/<sys>/LogicalPartition/<lpar>`) — a shape the create POST has never
been observed to send (issue #1036).

## Scope

[ADR 0179](../../adr/0179-mapping-create-href-system-scoped.md) governs the decision: `get_lpar_link`
builds the system-scoped href from a `system_uuid` read off the grouped VIOS GET's
`AssociatedManagedSystem` link — the same GET `_rmw_vios_mapping` already performs (ADR 0169). That
UUID is unknown before the GET returns, so `map_storage_to_lpar` and `create_optical_mapping` pass
`_rmw_vios_mapping` a document-building closure instead of a finished XML string; it runs inside the
mutate step, once the VIOS element is in hand. A VIOS response missing `AssociatedManagedSystem`
fails the create closed, no POST, mirroring the missing-`ETag`/missing-collection precedent.
Excluded: the delete/detach href and `If-Match` path (#1037, merged); `VolumeGroup` read-modify-write
(#936, closed); `lpar_uuid_from_href` and all other read-side parsing, already tolerant of either
form and untouched.

### Failure model

1. **Actors and deployments.** An `hmcpctl` operator or the MCP server, via `map_storage_to_lpar` /
   `create_optical_mapping`, against a customer-managed V10R3+ HMC.
2. **Invariants and assets at stake.** The VIOS's existing mapping set, posted back unchanged beside
   the new mapping under the GET's `ETag` (ADR 0169); a wrong href risks either a safe HMC rejection
   or a mapping silently misassociated with the wrong partition.
3. **Accepted failure classes.** A VIOS GET missing `AssociatedManagedSystem` is a fail-closed
   `HMCError` before any POST — unobserved on real firmware but not ruled out, matching the `ETag`/`VirtualSCSIMappings` precedent.
4. **Covered elsewhere.** 412 handling, `ETag` enforcement, and the append-vs-detach mutate contract
   are ADR 0169's; mapping identity and read-side href tolerance are ADR 0168's — both unchanged.

## Success

- `map_storage_to_lpar` and `create_optical_mapping` POST a mapping whose `AssociatedLogicalPartition`
  href is `.../ManagedSystem/<system_uuid>/LogicalPartition/<lpar_uuid>`, `<system_uuid>` being the
  fetched VIOS's own `AssociatedManagedSystem` href's final segment.
- A grouped VIOS GET with no `AssociatedManagedSystem` link raises `HMCError` before any POST.
- `lpar_uuid_from_href` and every read-side behavior named in Scope are unchanged.
- Confirmed by one live vSCSI mapping create (and one optical create if a media repository exists)
  on the authorized lab HMC, reading back what the HMC stores.

## Validation

- Contract: create POSTs a system-scoped `AssociatedLogicalPartition` href. Mode: focused-test.
  `tests/storage/test_mapping_rmw.py::test_create_posts_existing_mappings_unchanged_under_if_match`,
  updated to assert the posted href contains `/ManagedSystem/<system_uuid>/LogicalPartition/<lpar>`.
  Red: today's root-scoped assertion fails against the fixture's added `AssociatedManagedSystem`
  link. Green: `uv run --no-sync pytest tests/storage/test_mapping_rmw.py -q`.
- Contract: a VIOS GET with no `AssociatedManagedSystem` link fails the create closed. Mode:
  focused-test. New case in `tests/storage/test_mapping_rmw.py` asserting `HMCError` and
  `post.called is False` when `vios_entry()` omits the link. Red: no such case exists today. Green:
  `uv run --no-sync pytest tests/storage/test_mapping_rmw.py -q`.
- Contract: read-side parsing unaffected, and the HMC actually accepts the new href. Mode:
  task-test-not-applicable — `lpar_uuid_from_href` has no code change (`test_mapping_inventory.py`
  keeps exercising it unmodified); HMC acceptance needs a live V10R3 session, verified per
  `docs/live-testing.md` in this quest's live window, neither reproducible in the unit suite.
