# One REST UUID-to-name lookup per resource type

Issue #1002. Lane: light-spec. No ADR: one owner is the only option the issue leaves open.

## Problem

Two functions read a managed system's `SystemName` over REST. `ssh/selectors.py`'s
`_system_name_from_rest` rejects a non-mapping `Resource` and a missing, non-string, or blank name,
then raises `ValueError`. `resource_identity.resolve_system_name` rejects only a falsy name, raises
`ResourceNotFoundError`, and raises `AttributeError` on a truthy non-mapping `Resource`. A fix to
one does not reach the other. On this surface `_lpar_name_from_rest` is the only LPAR copy.

## Scope

In: `resource_identity.py` owns `system_name_from_uuid(hmc, uuid)` and
`lpar_name_from_uuid(hmc, uuid)`. Each fetches the entry and returns the validated name, or `None`
when the payload is unusable, with `selectors.py`'s validation. The callers translate `None` at
their own boundary: `_system_name_from_rest` and `_lpar_name_from_rest` raise their current
`ValueError` messages, and `resolve_system_name` raises its current `ResourceNotFoundError`. REST
errors still propagate unchanged, so the SSH transport fallback is untouched. Callers of the
public resolvers change nothing.

Out: `operations/lpar/ownership.py`'s `_resolve_system_name` (operator); the SSH `-F` format
(#777). Outside the surface, owned by campaign 36310ffc40 as follow-ups: the `ownership.py`
`_partition_name` and `resolve_lpar_ownership_names` LPAR reads, and the ambiguity-message
system reads in `client/client_lpars.py` and `client/client_systems.py`.

### Failure model

- **An error contract drifts.** A caller could surface the other caller's exception type or
  message. Mitigated by tests that pin both.
- **The stricter validation refuses a name `resolve_system_name` used to accept.** A
  whitespace-only or truthy non-string `SystemName`, returned before, and a non-mapping `Resource`,
  an `AttributeError` before, now raise `ResourceNotFoundError`; an empty name already did. A
  usable CLI system name is a non-blank string, so no working call is refused.

## Success

1. `ssh/selectors.py` and `resource_identity.resolve_system_name` read a name over REST only
   through `system_name_from_uuid` and `lpar_name_from_uuid`.
2. `ssh.selectors` raises `ValueError` for an unusable payload with its existing messages, and not
   `ResourceNotFoundError`.
3. `resource_identity.resolve_system_name` raises `ResourceNotFoundError("managed system", uuid,
   ...)` with its existing message for every unusable payload, including a non-mapping `Resource`
   and a blank name, and passes a name through untouched.

## Validation

`just verify` and `uv run --no-sync prek run --all-files`, both bare, exit 0.

Contract inventory:

- `focused-test`: the `ValueError` contract, via the existing malformed-payload cases in
  `tests/app/test_ssh_resolvers.py`, plus an assertion that the exception is not a
  `ResourceNotFoundError`.
- `focused-test`: the `ResourceNotFoundError` contract, new in `tests/unit/test_common_resolvers.py`:
  malformed payloads raise with kind, selector, and message; a name makes no REST call; a UUID
  resolves. Expected red before the change: a whitespace-only name, a truthy non-string name, and
  a non-empty list `Resource`; `''`, `[]`, and `None` stay as already-green regression cases.
  Green: `uv run --no-sync pytest tests/unit/test_common_resolvers.py tests/app/test_ssh_resolvers.py --no-cov -q`.
