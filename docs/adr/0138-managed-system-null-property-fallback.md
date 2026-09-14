# ADR 0138: Managed-system null-property fallback via quick/All

## Status
Accepted

## Context
Issue #784: on HMC firmware whose managed-system inventory feed contains a null
`VirtualPersistentMemoryVolume/Uuid` sub-field, both `get_managed_system(uuid)` and
`list_managed_systems()` fail with HTTP 500 ("Nested path contains null property").
`find_system_by_name()` (via `search_uom`) already works on the same firmware, but
neither failing path has a system name to resolve with — `get_managed_system` is
keyed by UUID, and `list_managed_systems` has no name until it can list something.

## Decision
On the exact null-property HTTP 500 pattern, both methods fall back to
`GET /rest/api/uom/ManagedSystem/quick/All`, a JSON endpoint returning
`{UUID, SystemName, ...}` summaries for every managed system, then resolve each
summary via the already-working `find_system_by_name()`. `get_managed_system`
looks up its one UUID in the summary and delegates to `find_system_by_name`; a
miss (UUID absent from the summary, or the name lookup also fails) raises the
same actionable `HMCError` shape `list_managed_systems` already uses for this
firmware class. `list_managed_systems` resolves every summary entry the same
way and returns the subset that succeeds, skipping any system that still 500s;
if none resolve, it raises the original actionable error unchanged.

`quick/All` is not present in this repo's vendored HMC REST API reference
(`docs/refs/hmc-rest-api-p11/164-managed-system.md` documents only the
per-UUID `.../quick/{Property}` form). The only evidence it exists is IBM's own
public `project-pim` repository (`github.com/IBM/project-pim`), which uses it
extensively (`cli/utils/command_util.py:164`, `examples/hmc-agent/app/hmc.py:70,159`,
and the same `.../quick/All` shape for VIOS/LPAR/VirtualNetwork/VirtualSwitch
collections) without an `Accept` header, decoding the response as JSON directly.
This fix therefore carries the `verification:live-hmc` label and must be
confirmed against a live HMC before the fallback can be trusted in the field;
until then it is exercised only by respx-mocked unit tests.

## Consequences
`get_managed_system`/`list_managed_systems` gain one additional request path
(`_quick_all_system_names`, added to the `SystemsClient` protocol via `_request`)
that only activates on the specific firmware failure; unaffected firmware sees
no behavior change. A system whose `SystemName` collides with another's still
raises `find_system_by_name`'s existing ambiguous-name `ValueError`, which
`list_managed_systems`'s per-system resolution catches and skips rather than
propagating, and `get_managed_system` propagates the same as `find_system_by_name`
does today. The fallback trusts `quick/All`'s JSON shape defensively — an entry
missing `UUID` or `SystemName` is skipped rather than raised.

## Considered & rejected
- Fall back to `search_uom("ManagedSystem", "UUID", uuid)`. judgment: no
  evidence anywhere in this repo or in `project-pim` that `UUID` is a
  supported `ManagedSystem` search field; the HMC's search field list is
  firmware-dependent and undocumented, so this would be speculative behavior
  shipped without verification.
- Only translate to a clearer error message, without attempting resolution.
  judgment: leaves `systems show <UUID>` and `systems list` broken on affected
  firmware instead of usable, when a working resolution path is available.
- Enumerate `list_managed_systems` via `search_uom("ManagedSystem", "State", s)`
  over every known `State` value. judgment: depends on an exhaustive, versioned
  state enumeration and still can't cover `get_managed_system`'s single-UUID
  case; `quick/All` covers both call sites with one mechanism.
