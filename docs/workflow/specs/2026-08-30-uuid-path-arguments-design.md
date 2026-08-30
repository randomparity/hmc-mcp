# UUID-only request-path argument validation design

## Scope and outcome

Issue #278 requires UUID-only sub-resource arguments to be shape-validated before interpolation
can reach HTTP, while preserving name-addressed path segments and PR #266's raw and once-decoded
dot-segment rejection. The operator-supplied ISO download URL is excluded.

ADR [0112](../../adr/0112-explicit-uuid-path-arguments.md) selects explicit argument metadata at
the request boundary. The change is one independently reviewable slice across the client core,
the storage and adapter mixins, resource identity, and focused tests.

## Contract

`HMCClient` exposes an internal `_request_with_uuid_path_arguments` helper taking the same method,
path, and transport keyword arguments as `_request`, plus a keyword-only mapping from public
argument name to value. Before delegating, it validates every value with the canonical
`resource_identity.is_uuid` predicate. The first invalid value raises `HMCError` with
`"<argument> must be a UUID"`; the message contains neither path, host, credentials, nor the
offending value. No HTTP request is attempted.

Only arguments whose method documentation promises UUIDs are included. Arguments documented as a
name, name-or-UUID selector, opaque server identifier, media name, disk name, or resource type stay
outside the mapping. `_request` retains `_reject_dot_segments` unchanged, so every accepted UUID
path still receives both checks and every ordinary path still receives traversal protection.

The authoritative in-scope inventory is:

- generic UOM builders: `get_uom.uuid`, `get_quick_property.uuid`,
  `list_child.parent_uuid`, `create_child.parent_uuid`, and both
  `delete_child.parent_uuid` and `delete_child.child_uuid`;
- broker helpers: `_broker_file_create.vios_uuid`, `_broker_file_create.vg_uuid`,
  `_broker_iso_import.vios_uuid`, and `_broker_iso_import.vg_uuid`;
- storage builders: every interpolated `vios_uuid`, `vg_uuid`, `lpar_uuid`, or `system_uuid` in
  `list_volume_groups`, `get_volume_group`, `create_volume_group`, `create_virtual_disk`,
  `delete_virtual_disk`, `map_storage_to_lpar`, `list_storage_mappings`,
  `delete_storage_mapping`, `_get_vg_raw_xml`, `_post_vg_xml`, `get_media_repository`,
  `list_optical_media`, `list_optical_mappings`, and `create_optical_mapping`;
- adapter methods inherit the generic child-builder guarantees, so `list_adapters.lpar_uuid`,
  `delete_adapter.lpar_uuid`, `delete_adapter.adapter_uuid`, and the three adapter-creation
  methods' `lpar_uuid` values require no second validation site.

`delete_storage_mapping.mapping_uuid` is deliberately absent: it selects an element from returned
XML and is not interpolated into a request path. Disk, media, volume-group display names,
`resource_type`, `parent_type`, `child_type`, and job identifiers are also absent. The regression
test asserts this frozen method/argument inventory rather than deriving expectations from whichever
call sites happen to use the helper.

## Data flow and errors

A client method builds its path, passes the path and explicit UUID argument mapping to the helper,
and the helper validates before calling `_request`. `_request` then applies the existing path and
job-href checks before invoking httpx. UUID validation is local, deterministic, and side-effect
free. Upper- and lower-case canonical hexadecimal UUIDs are accepted because that is the existing
`is_uuid` contract.

## Threat model

- Boundary: MCP, CLI, and reusable-API callers control client-method string arguments. The change
  adds no boundary and narrows the existing request-path boundary.
- Actor: an authenticated operator or MCP caller may supply malformed identifiers. The client
  trusts only argument contracts declared by its own methods, not route text or caller claims.
- Controls: explicit mappings select UUID-only values; `is_uuid` validates shape; `_request` keeps
  raw and once-decoded dot-segment rejection; errors disclose only the argument name; focused tests
  seal the transport against rejected values.
- Out of scope: authorization policy, resource existence, Unicode normalization of name-addressed
  values, repeated percent-decoding beyond PR #266's contract, and operator-supplied ISO URLs.

## Testing and compatibility

Focused tests prove a malformed UUID-only storage or adapter argument raises before transport, the
error names the correct argument without sensitive context, canonical mixed-case UUIDs pass, a
legitimate non-UUID name-addressed segment such as `vg-1` remains accepted on an ordinary request,
and the existing dot-segment suite remains green. A static enumeration test keeps the known
UUID-only sub-resource builders on the metadata-aware helper.

The implementation adds no dependency and supports the repository's Python floor and both declared
target architectures, amd64 and arm64.
