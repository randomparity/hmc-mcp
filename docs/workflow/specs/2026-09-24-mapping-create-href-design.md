# Mapping create href design

## Problem

`map_storage_to_lpar` and `create_optical_mapping` build a new mapping's `AssociatedLogicalPartition`
href root-scoped (`/rest/api/uom/LogicalPartition/<lpar>`). [ADR 0168](../../adr/0168-vscsi-mapping-adapter-target-identity.md)
records every observed HMC-emitted document links a client partition system-scoped
(`/rest/api/uom/ManagedSystem/<sys>/LogicalPartition/<lpar>`) — a shape the create POST never sends
(issue #1036).

## Scope

[ADR 0179](../../adr/0179-mapping-create-href-system-scoped.md) governs the decision: `get_lpar_link`
builds the system-scoped href from a `system_uuid` read off the grouped VIOS GET's
`AssociatedManagedSystem` link — the same GET `_rmw_vios_mapping` already performs (ADR 0169). That
UUID is unknown before the GET returns, so the two creates pass `_rmw_vios_mapping` a
document-building closure that runs inside the mutate step, once the VIOS element is in hand.
`mutate` gains a second, always-passed `system_uuid: str | None` parameter; only the create closures
use it, raising `HMCError` with no POST when it is `None`. `delete_storage_mapping`'s closure takes
and ignores it — a VIOS missing the link still detaches. `lpar_uuid`/`storage_kind` keep their
existing pre-GET validation; only the href is deferred. Excluded: the delete/detach href/`If-Match`
path (#1037, merged); `VolumeGroup` read-modify-write (#936, closed); `lpar_uuid_from_href` and other
read-side parsing, already tolerant of either form and untouched.

### Failure model

1. **Actors and deployments.** An `hmcpctl` operator or the MCP server, against a customer-managed V10R3+ HMC.
2. **Invariants and assets at stake.** The VIOS's existing mapping set, posted back unchanged under
   the GET's `ETag`; a wrong href risks a safe HMC rejection or a mapping misassociated with the
   wrong partition.
3. **Accepted failure classes.** A VIOS GET missing `AssociatedManagedSystem` fails a create closed, no
   POST — unobserved but not ruled out; a detach on the same VIOS is unaffected.
4. **Covered elsewhere.** 412/`ETag` handling and the mutate contract (ADR 0169); mapping identity and
   read-side href tolerance (ADR 0168) — both unchanged.

## Success

- Both creates POST a mapping whose `AssociatedLogicalPartition` href is
  `.../ManagedSystem/<system_uuid>/LogicalPartition/<lpar_uuid>`, `<system_uuid>` from the VIOS's own `AssociatedManagedSystem` href.
- A grouped GET missing that link raises `HMCError` before any create POST; a detach is unaffected.
- `lpar_uuid`/`storage_kind` still reject before the GET; `lpar_uuid_from_href` and other read-side
  behavior named in Scope are unchanged. Confirmed by one live vSCSI create (and one optical create
  if a media repository exists) on the authorized lab HMC, reading back what the HMC stores.

## Validation

- Contract: create POSTs a system-scoped href. Mode: focused-test.
  `test_mapping_rmw.py::test_create_posts_existing_mappings_unchanged_under_if_match`, updated to assert
  the posted href contains `/ManagedSystem/<system_uuid>/LogicalPartition/<lpar>`. Red: the current
  root-scoped assertion fails the fixture's added link. Green: `uv run --no-sync pytest
  tests/storage/test_mapping_rmw.py -q`.
- Contract: a create fails closed with no `AssociatedManagedSystem`; `lpar_uuid`/`storage_kind`
  validation still precedes the GET; detach is unaffected. Mode: focused-test. New `test_mapping_rmw.py`
  case (link-less `vios_entry()`: `HMCError`/no POST for `_map`/`_mount`, `_detach` passes) plus
  `test_storage_tools.py::test_map_storage_invalid_kind_raises` extended to assert its GET mock was
  never called (today's helper doesn't capture it — new red). Green: `uv run --no-sync pytest
  tests/storage/test_mapping_rmw.py tests/storage/test_storage_tools.py -q`.
- Contract: read side unaffected; the HMC accepts the new href. Mode: task-test-not-applicable —
  `lpar_uuid_from_href` has no code change (`test_mapping_inventory.py` runs it unmodified); HMC
  acceptance needs a live V10R3 session, verified per `docs/live-testing.md` in the live window.
