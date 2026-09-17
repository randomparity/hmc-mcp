# ADR 0160: UOM segment resource identities are canonical

## Status

Accepted (2026-09-17)

## Context

ADR 0159's inventory leaves seven segment classes site-inventoried but
ungoverned. The six resource identities (`cluster_uuid`, `lpar_uuid`,
`network_uuid`, `system_uuid`, `vg_uuid`, `vios_uuid`) interpolate into
request paths and one document link without an explicit per-argument rule at
many of their construction sites, while `console_path_id` is quote-bound and
length-bounded by ADR 0150/0157. ADR 0112 established that UUID-only path
arguments are validated at the boundary by explicit per-argument mapping, and
`get_uom` already enforces the canonical 8-4-4-4-12 hexadecimal form for the
same resources through the generic `uuid` argument. #850 requires the
remaining sites to carry each argument's rule before I/O. IBM's local REST
reference (`docs/refs/hmc-rest-api-p11/000-hmc-rest-apis.md:43-47,71-93`)
defines instance URLs in terms of UUID values. The VirtualNetwork reference
(`docs/refs/hmc-rest-api-p11/virtual-network-management/237-virtual-network.md:14-21`)
also uses UUID identities. No shorthand form was found in these references;
abbreviated mock fixture identifiers do not establish an HMC alias contract.

## Decision

The six resource identities accept exactly the canonical 8-4-4-4-12 ASCII
hexadecimal form `resource_identity.is_uuid` already enforces: either case,
no version or variant restriction, no normalization. A non-canonical value is
refused with `ValueError` naming the argument before I/O — the
same boundary-refusal family as `_request_with_uuid_path_arguments` (ADR 0112,
current `ValueError` identity). Sites already passing
`uuid_path_arguments` keep it. Newly guarded builders call
`_reject_non_uuid_path_argument(argument, value)` before constructing their
paths, reusing the refusal extracted from the mapped request helper into
the leaf `client_contracts.py`. This preserves collaborator signatures and
validates even when a request helper or job submission is substituted.
`console_path_id` is unchanged. `switch_uuid` remains an inventoried
document-link identity with no runtime rule. Comparison-only LPAR links stay
comparisons. The widened inventory asserts, per concrete site, the
enforcement the decision requires and fails an unclassified or unguarded new
site.

## Consequences

- Every residual request site enforces its argument's rule before I/O;
  supported values are preserved (canonical
  either-case UUIDs, console strings ≤256 characters).
- A documented real shorthand for one of the six classes would now be refused
  locally — an accepted unknown, stated as one; the remedy is a reviewed
  widening of one predicate with the refused identity as evidence.
- `client_contracts.py` gains the shared refusal beside its existing
  predicates and stays a leaf module, so no import cycle is introduced.
- `switch_uuid` remains an explicit document-link policy limitation under the
  operator-approved scope; operations power policy remains with #845.

## Considered & rejected

- **Preserve noncanonical identifiers at permissive sites with bounded
  encoding.** verified: ADR 0150/0157 supply the 256-character bound and
  quote for console values; no comparable bound-and-encode policy exists for
  resource identities, and the same resource's getter already refuses
  non-canonical values, so the alternative preserves an inconsistency between
  the read and mutate paths of one resource. Operator decision 2026-09-17
  chose the existing UUID contract instead.
- **Classify by argument name alone, package-wide.** verified: ADR 0159
  rejects name-based classification; `vg_uuid` is enforced at its explicit
  sites while `network_uuid` was not, so the name proves nothing about the
  site. judgment: a new site must not inherit an old site's exception.
- **Add a runtime rule for `switch_uuid`.** judgment: it supplies an optional
  Atom link inside a request document, not a request destination; ADR 0159
  assigns it policy review, not certification by spelling, and the approved
  scope adds no runtime expansion.
- **Reuse the console bound for the six identities.** verified:
  `_MAX_UOM_PATH_VALUE_LENGTH = 256` exists because its values are open-ended
  data (`client_contracts.py:91-101`); a canonical UUID identity is closed and
  shape-checked, so a length bound answers a question the identity check
  already answers.
