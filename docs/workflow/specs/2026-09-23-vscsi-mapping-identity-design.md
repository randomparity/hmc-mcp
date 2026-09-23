# vSCSI mapping identity from the observed HMC shape (#940)

Decision: [ADR 0168](../../adr/0168-vscsi-mapping-adapter-target-identity.md), superseding the
selector in ADR 0079.

## Problem

`storage list-mappings` raises `list_storage_mappings returned no usable UUID` on the first real
mapping, because a V10R3 `VirtualSCSIMapping` has no `UUID`. Detach and optical unmount select
by that UUID, so neither can act on a real VIOS. Separately, every client-LPAR check expects a
`/rest/api/uom/LogicalPartition/<uuid>` path, but the HMC sends an absolute
`https://<hmc>/rest/api/uom/ManagedSystem/<sys>/LogicalPartition/<uuid>` href. Real inventory
therefore reports no LPAR, `--lpar` filters return nothing, and detach refuses every mapping.
The synthetic fixtures encode both shapes the HMC does not send.

## Design

Two functions in `src/hmcpctl/client/client_storage.py` own the rules, and every consumer in
the table below calls them. `operations/lpar/decommission.py` keeps its own href parser and
`UUID` read; it is outside this surface and reported as a follow-up.

- `storage_mapping_id(mapping: Mapping[str, Any]) -> str | None` takes the parsed-dict form a
  `VirtualSCSIMapping` has after `_parse_feed`/`element_to_dict`. It returns
  `f"{adapter}/{target}"` when `ServerAdapter.AdapterName` is a non-empty string without `/`,
  and `TargetDevice` is a dict with exactly one child besides `element_to_dict`'s `@attrs`. That
  child must be a dict whose `TargetName` is a non-empty string without `/`. A name element that
  carries unignored attributes is read from its `{"text": ...}` form. Otherwise it returns
  `None`.
- `lpar_uuid_from_href(href: object) -> str | None` returns the text after the last
  `/LogicalPartition/` in `urlparse(href).path`. It returns `None` for a non-string, a missing
  marker, an empty tail, or a tail containing `/`.

Consumers:

| Consumer | Change |
|---|---|
| `StorageClient.list_storage_mappings` LPAR filter | keep mappings whose `lpar_uuid_from_href(href) == lpar_uuid` |
| `_mapping_targets_lpar` (optical filter) | same comparison; the signature takes `lpar_uuid` |
| `StorageClient.delete_storage_mapping(vios_uuid, mapping_id, lpar_uuid)` | convert each `VirtualSCSIMapping` element with `element_to_dict` and select by `storage_mapping_id`. Exactly one match is removed, and only when `lpar_uuid_from_href` of its link equals `lpar_uuid`. Zero matches is `not found`, several is `duplicated`, and another LPAR is `does not belong`. All three raise `HMCError` with no POST. A mapping with no identity is ignored. An empty selector raises `ValueError`. |
| `StorageMapping` / `_storage_mapping` | the `uuid` field becomes `id: str \| None` from `storage_mapping_id`; no `UUID` is required; `lpar_uuid` comes from `lpar_uuid_from_href` |
| `detach_storage_mapping(hmc, vios, mapping_id, ...)` | match inventory by `storage_mapping_id`. Zero matches raises `ValueError` "not found"; several raises "ambiguous"; no LPAR from the href raises "does not identify its client LPAR". It then authorizes that LPAR and passes it to the remover. |
| `unmount_optical_media` | pass `storage_mapping_id(match)` and the authorized LPAR to the remover; `None` raises `HMCError("VirtualSCSIMapping has no adapter/target identity")` |
| CLI `storage list-mappings` / `detach-mapping` | column "Mapping ID"; argument `mapping_id` |
| MCP `hmc_detach_storage_mapping` / `hmc_list_storage_mappings` | parameter `mapping_id`, returned value `mapping_id`; the list docstring names `id` |

There is no alias for `mapping_uuid` or `uuid`. The generated `docs/tools/` pages and
`docs/capabilities/operations.json` (hand-edited signature; no generator exists) are updated. `CHANGELOG.md` gets one `Fixed` entry.

Fixtures: `tests/storage/vscsi_mapping_v10r3.xml` holds the redacted observed element.
The mapping, optical, safe-delete, media-operation, tool, and result tests in `tests/storage/` move to
absolute system-scoped hrefs and adapter/target identities with no `UUID`.

## Failure model

1. **Actors and deployments.** An operator runs the `hmcpctl` CLI, or an MCP client calls the
   tools, against an HMC. The HMC's REST responses are the input being parsed.
2. **Invariants and assets.** Detach and unmount remove exactly one mapping, or none. Backing
   storage and all other VIOS content are preserved. The LPAR authorized is the LPAR the
   removed mapping names. The public MCP/CLI contract is renamed exactly once.
3. **Accepted failure classes.**
   - A mapping whose adapter name is unreported, for example while the VIOS is not running,
     cannot be detached. This fails closed, as ADR 0168 accepts.
   - A mapping change between the inventory read and the remover's GET. The remover
     re-selects from its own GET: a vanished or duplicated identity, or a reused name now on
     another LPAR, fails with no POST. A name the VIOS reused for the same LPAR selects that
     LPAR's current mapping (ADR 0168). A change between the GET and the POST is overwritten
     (ADR 0079 puts serialization on callers).
   - The LPAR UUID comparison is exact and case-sensitive, as before. A UUID selector typed in
     another letter case than the HMC's matches no mapping. This fails closed to empty or
     not-found; it is unchanged by this work.
4. **Covered elsewhere.**
   - Live confirmation against a lab VIOS, including that the remover's plain
     `GET /VirtualIOServer/{uuid}` carries mappings in the observed shape: the #879 live window.
   - `decommission.py`'s mapping `UUID` read and `delete_virtual_disk`'s href-only in-use guard
     do not match the observed shape: follow-up candidates, outside this surface.
   - REST write headers: #935.
   - Virtual-disk create: #936.

### Threat model

- **Boundary.** HMC response → identity and LPAR parse, which widens the existing parse. The
  HMC is an authenticated service the operator configured and is trusted for content. The MCP
  or CLI selector is untrusted caller input.
- **Controls.**
  - The selector is compared by string equality only; there is no substring or prefix matching.
  - Uniqueness is proved in the fetched document before any POST.
  - The ownership check (`resolve_and_authorize_lpar_mutation`) runs on the LPAR parsed from the
    selected mapping.
  - Parsing stays on `defusedxml`.
- **Out of scope.** A hostile HMC that lies about ownership links. The HMC is trusted by every
  tool.

## Validation

| Contract | Mode | Evidence |
|---|---|---|
| identity derivation | focused-test | `tests/storage/test_mapping_inventory.py`: the fixture element gives `vhost0/vtscsi0`, including with an extra attribute on `TargetDevice` and on `AdapterName`. A missing adapter name, a missing target name, two targets, and a `/` in a name each give `None`. |
| href parse | focused-test | the same module: absolute system-scoped, relative, and host-only forms give the UUID; empty, trailing-slash, missing-marker, and non-string forms give `None` |
| inventory without UUID | focused-test | `tests/storage/test_storage_results.py`: the observed shape projects `id`, `lpar_uuid`, and the backing |
| LPAR filters | focused-test | storage and optical filters keep only the LPAR named in an absolute href |
| remover | focused-test | exact removal; not-found, duplicate, other-LPAR, and empty selector fail with no POST; unidentifiable siblings are retained |
| detach operation | focused-test | `tests/storage/test_storage_tools.py`: authorizes the LPAR from an absolute href; a missing LPAR or ambiguous id raises before any removal |
| optical unmount | focused-test | `tests/storage/test_media_operations.py`: removes by derived id; no identity raises with no removal. The unmount fixture in `tests/lpar/test_reconfiguration_ownership.py` gains adapter and target names, a necessary consequence of the shared remover. |
| CLI/MCP rename | focused-test | `tests/app/test_cli_commands.py` and `tests/storage/test_storage_tools.py` call with `mapping_id` |
| generated docs | focused-test | `just tool-docs-check` and the capabilities check pass after regeneration |
