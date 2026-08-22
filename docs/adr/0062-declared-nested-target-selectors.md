# ADR 0062: Declared nested target selectors and the second extraction rule

## Status

Accepted (2026-08-22). Amends ADR 0039 in part: the rejected alternative that
"would mean the authorization boundary reaching into caller-supplied objects to
read attributes" is now adopted, narrowed to a declared, validated form. Closes
the declaration half of #260; the grant half waits on #259.

## Context

ADR 0039 made `hmc_provision_lpar` grantable only under `targets =
"all-targets"` because two of the identities it mutates arrive one level below
the handler signature — `ProvisionStorage.vios_uuid` and
`ProvisionNetwork.vios_partition_id` — where `build_targets` could not see them
and `selected_targets` could not read them. The record's own rejected
alternative named the end state: let both become real selectors and the tool
becomes narrowable. It was rejected there because extraction had never yet been
load-bearing and the second rule's failure modes were unpriced.

The static half has been ready since #223: G15 detects exactly this shape
(`network.vios_partition_id`, `storage.vios_uuid`) through
`_unbounded_identities`. What was missing was the fixable path.

## Decision

**Nested selectors are declared, never derived.** `extra_targets` accepts a
dotted form, `("vios", "storage.vios_uuid")`, alongside the existing bare
parameter names. `build_targets` validates each dotted path at declaration time:
the container must be a parameter, its resolved annotation must be a dataclass
or pydantic model, and the field must exist — with the field's own default
deciding `required`, conjoined with the container's. A container that can be
`None` (a `None` default or an optional annotation) is refused outright: the
extraction rule below would render every call to such a tool UNREADABLE, and a
declaration that ships a dead tool must fail the author, not the operator.

The derivation alternative — descending into every structured parameter
automatically — stays rejected for the reason ADR 0039 kept `exhaustive_targets`
a declaration: a name-table match one level deep must not silently rewrite a
tool's security record or its policy semantics. Only what the author writes is
extracted.

**Extraction gains a second rule, priced as follows.** For a selector whose
container names a structured argument, `selected_targets` reads
`arguments[container].field`:

- a missing container key remains a malformed call (`KeyError`), unchanged from
  the top-level rule — defaults were applied, so it cannot happen through MCP;
- a `None` sub-object reads UNREADABLE, denying under `all-targets` too. The
  schema types these arguments as objects, so `None` is a malformed call, not a
  narrow one;
- an object without the declared attribute reads UNREADABLE for the same
  reason — reachable only by a direct caller of the wrapped object;
- a well-formed object whose field is `None` reads ABSENT, exactly like an
  omitted optional top-level selector: the object was supplied, the optional
  identity on it was not.

The extracted location renders dotted (`storage.vios_uuid`) in denial messages
and audit records; a bare field name would be ambiguous across a tool that also
declares top-level selectors.

## Consequences

`hmc_provision_lpar` declares both nested selectors. Extraction, the audit
record, and every denial message now see the VIOS identities instead of only
the managed system, which is what makes the storage half auditable and the
guardrail finding *fixable* rather than merely reportable.

The tool remains `exhaustive_targets=False`: `vios_partition_id` sits in
`UNBOUNDED_ARGUMENTS` (ADR 0039/0044), so no table can bound it regardless of
declaration, and G15 still refuses exhaustiveness while it is accepted.
Narrowing arrives when #259 gives the slot number a fleet-unique form; the
declaration this entry adds is the piece that was missing for that flip to be
a one-line change.

G3/G14/G16 learn the nested shape: a nested selector must appear in its
container's schema properties, must resolve its type through the container's
annotation, and is "read" when the handler loads the container it passes onward.
The one-level limit is inherited unchanged from `_nested_field_names`.

## Considered & rejected

- **Automatic descent for all structured parameters.** Rejected above: it
  derives authority from a heuristic at registration and would have added
  selectors to tools whose authors never wrote them.
- **Reading ABSENT for a None sub-object.** ABSENT means "a well-formed call
  left an optional identity unset". A `None` where the schema requires an
  object is not well-formed, and ABSENT permits under `all-targets` — fail-open
  for exactly the input the second rule exists to judge.
- **Also declaring the nested pair on the three adapter tools.** Their
  `vios_partition_id` is top-level already; nothing about nesting applies, and
  their refusal is #259's, not this entry's.
