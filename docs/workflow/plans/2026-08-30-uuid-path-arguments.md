# Implement explicit UUID request-path validation

## Goal and architecture

Validate method arguments documented as UUID-only at a shared request-boundary helper before HTTP,
while leaving name-addressed arguments and the existing dot-segment guard unchanged. Client methods
provide explicit argument metadata; `resource_identity.is_uuid` remains the canonical predicate.

Tech stack: Python, httpx, pytest, Ruff, ty, and the repository `just` guardrails.

## Global constraints

- Add no dependency.
- Support the repository's Python floor and amd64/arm64 targets.
- Error text names only the argument and contains no path, host, credential, or rejected value.
- Preserve raw and once-decoded dot-segment rejection.
- Do not validate name, name-or-UUID, opaque identifier, disk-name, or media-name segments as UUIDs.

## Task 1: Add the request-boundary validator

Files: `src/hmc_mcp/resource_identity.py`, `src/hmc_mcp/client/core.py`, and
`src/hmc_mcp/client/client_contracts.py`.

Interfaces:

- Consume `is_uuid(value: str) -> bool` from `resource_identity` after moving its `HMCClient` import
  behind `TYPE_CHECKING` to avoid a runtime cycle.
- Define `HMCClient._request_with_uuid_path_arguments(method: str, path: str, *,
  uuid_path_arguments: Mapping[str, str], **kwargs: Any) -> httpx.Response`.
- Extend `_get`, `_post`, `_put`, and `_delete` with keyword-only
  `uuid_path_arguments: Mapping[str, str] | None = None`; when present they delegate through the
  new helper, otherwise they retain their existing `_request` path. Their current headers,
  accepted status codes, empty-response behavior, and parsing remain unchanged.
- Mixins rely on matching helper and wrapper protocol signatures in `client_contracts.py`.

Steps:

1. Add focused tests that pass an invalid mapping, assert `HMCError("vg_uuid must be a UUID")`,
   seal `client._http.request`, and assert no attempt. Run
   `uv run --no-sync pytest tests/unit/test_request_path_safety.py -q`; expect failure because the
   helper does not exist.
2. Move the type-only client import, add the shared helper and protocol signatures, validate
   mapping values in insertion order, then delegate unchanged to `_request`. Thread the optional
   mapping through `_get`, `_post`, `_put`, and `_delete` without changing their other behavior.
3. Re-run the focused command; expect the new helper tests and existing traversal tests to pass.

Acceptance: invalid metadata fails locally; canonical mixed-case UUIDs delegate; existing `_request`
semantics are unchanged. Rollback removes the helper and type-only import change together.

## Task 2: Mark UUID-only storage and adapter paths

Files: `src/hmc_mcp/client/client_storage.py`, `src/hmc_mcp/client/client_adapters.py`,
`src/hmc_mcp/client/core.py`, `tests/unit/test_request_path_safety.py`,
`tests/unit/test_client.py`, `tests/unit/test_client_domain_mixins.py`, and the bounded storage
fixture modules `tests/storage/test_mapping_inventory.py`, `test_media.py`,
`test_media_inventory.py`, `test_media_operations.py`, `test_media_tools.py`,
`test_safe_delete.py`, `test_storage_tools.py`, and `test_upload_iso.py`, plus the platform-update
fixture module `tests/system/test_update_upgrade.py` and the body-only compatibility fixture in
`tests/storage/test_optical_inventory.py`.

Interfaces:

- Consume `_request_with_uuid_path_arguments` from Task 1.
- Supply mappings whose keys are the exact public parameter names (`vios_uuid`, `vg_uuid`,
  `adapter_uuid`, `system_uuid`, `parent_uuid`, `child_uuid`, or `uuid`) and whose
  values are the interpolated strings. `delete_storage_mapping.mapping_uuid` remains an XML
  identity selector; only its `vios_uuid` and response-derived `system_uuid` enter path metadata.
  Storage `lpar_uuid` values used only in XML bodies or response filters remain outside path
  metadata.

Steps:

1. Add parametrized behavior tests for the documented UUID-only methods and a source-enumeration
   regression test that encodes the spec's frozen method/argument inventory and fails while known
   builders still call `_request` without metadata. Include generic UOM/child builders, broker
   helpers, `submit_platform_update`, every listed storage builder, and adapter coverage inherited
   through child builders. Migrate readable non-UUID stand-ins only in tests that exercise an
   inventoried path argument, preserving their original behavior assertions.
   Run the focused pytest command and expect those tests to fail.
2. Replace only the relevant request calls with the metadata-aware helper or wrapper: GET builders
   pass metadata through `_get`, create/update builders through `_post` or `_put`, deletes through
   `_delete`, and the broker plus storage read-modify-write direct calls through
   `_request_with_uuid_path_arguments`. Keep disk names, media names, resource-type strings, job
   identifiers, and generic name-addressed requests unchanged.
3. Add compatibility cases for `vg-1` on ordinary `_request` and for the existing raw/encoded
   traversal inputs. Run the focused pytest command; expect all cases to pass.
4. Run `just test`; expect exit 0 and exact coverage success. Commit with a Conventional Commit.

Acceptance: every selected UUID-only builder rejects before HTTP; legitimate names and traversal
semantics are preserved. Rollback is a single commit revert.

## Task 3: Verify the complete change

Files: the ADR, spec, plan, implementation, and focused tests above.

Interfaces: no new interface; verification consumes the completed branch.

Steps:

1. Run `just verify`; expect exit 0.
2. Run `uv run --no-sync prek run --all-files`; expect exit 0.
3. Review `git diff "$(git merge-base HEAD origin/main)"` for scope, sensitive error text, and
   accidental name validation. Expect only the planned implementation, focused test, and bounded
   fixture-migration files plus necessary generated artifacts.

Acceptance: both required guardrails are green with zero warnings; no generated artifact is stale.
Rollback: revert the implementation commit and its design commit together.
