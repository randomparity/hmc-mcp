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

`quick/All` is not present in this repo's vendored HMC REST API reference for
`ManagedSystem` (`docs/capabilities/corpora.json`, topic `rest-p11:managed-system`,
captured from `https://www.ibm.com/docs/en/power11/9824-22A?topic=apis-managed-system`
— it documents only the per-UUID `.../quick/{Property}` form; `quick/All` appears
in that corpus only under the `Cluster` resource). The only evidence it exists
for `ManagedSystem` is IBM's own public `project-pim` repository
(`github.com/IBM/project-pim`), which uses it extensively
(`cli/utils/command_util.py:164`, `examples/hmc-agent/app/hmc.py:70,159`,
and the same `.../quick/All` shape for VIOS/LPAR/VirtualNetwork/VirtualSwitch
collections) without an `Accept` header, decoding the response as JSON directly.
This repo's own sibling `get_quick_property` (`client/core.py`) independently
confirms the header constraint: its docstring records that a typed
`uom+xml` `Accept` header causes HTTP 406 on `quick/` endpoints, so the new
helper sends no typed `Accept` header either, matching both precedents.
This fix therefore carries the `verification:live-hmc` label and must be
confirmed against a live HMC before the fallback can be trusted in the field;
until then it is exercised only by respx-mocked unit tests.

## Consequences
Both methods gain one additional request path (`_quick_all_system_names`,
added to `SystemsClient` via `_request`) that only activates on the specific
firmware failure; unaffected firmware sees no behavior change. Unlike
`find_system_by_name`'s own contract, both fallbacks now catch its
ambiguous-name `ValueError` rather than propagate it — a `SystemName`
collision can't disambiguate the target, so it counts as a fallback miss
(skipped per-system in `list_managed_systems`, folded into the actionable
error in `get_managed_system`). The fallback trusts `quick/All`'s JSON shape
defensively — an entry missing `UUID` or `SystemName` is skipped rather than
raised. `list_managed_systems`'s fallback resolves each entry with its own
sequential `find_system_by_name` call, so N systems cost 1+N round trips
instead of one; accepted because it only activates on firmware already
broken today, with no evidence this firmware class hosts large enough
inventories to make the added latency material.

## Considered & rejected
- Fall back to `search_uom("ManagedSystem", "UUID", uuid)`. judgment: no
  evidence in this repo or `project-pim` that `UUID` is a supported
  `ManagedSystem` search field — speculative behavior shipped unverified.
- Only translate to a clearer error message, without attempting resolution.
  judgment: leaves both methods broken on affected firmware instead of
  usable, when a working resolution path is available.
- Enumerate `list_managed_systems` via `search_uom("ManagedSystem", "State", s)`
  over every known `State` value. judgment: needs an exhaustive, versioned
  state enumeration and still can't cover `get_managed_system`'s single-UUID
  case; `quick/All` covers both with one mechanism.
