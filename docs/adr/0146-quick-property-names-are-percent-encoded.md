# ADR 0146: Quick property names are percent-encoded

## Status

Accepted (2026-09-15)

## Context

Issue #818 asks what rule governs the `property_name` that `get_quick_property`
interpolates into a uom request path:

```python
path = f"/rest/api/uom/{resource_type}/{uuid}/quick/{property_name}"
```

`resource_type` is refused outside its grammar by `_reject_unknown_uom_type` and
`uuid` by `_request_with_uuid_path_arguments`. `property_name` passes through
raw, and no record says whether that is a decision. ADR 0143 hardened the type
segment on this same line and disclosed in its Consequences that a quick-property
name falls outside its outcome and needs its own grammar and its own evidence.

Four facts bound the answer. The first three were established against
`856e3aec` with httpx 0.28.1, the version this repository locks, by driving the
real client and capturing the request it builds.

**The value reaches one destination.** `get_quick_property` passes
`headers={"Accept": "*/*"}` literally and calls
`_request_with_uuid_path_arguments` directly, so it never enters `_uom_headers`.
That is forced rather than incidental: a typed `uom+xml` Accept header makes a
`quick/` endpoint answer 406, which the method's own docstring records. So the
two-destination fact that led ADR 0143 to decline percent-encoding for the type
segment — that the same value also lands in an `Accept` media-type parameter,
where `%XX` is meaningless — has no counterpart here.

**Raw interpolation does three separable things.**

- `property_name="PartitionState?group=None"` builds
  `.../quick/PartitionState?group=None` — the caller appends a query string this
  client did not name.
- `property_name="PartitionState#/rest/api/web/HmcUser/root"` builds
  `.../quick/PartitionState`. httpx drops everything from the `#`, so the request
  silently reads a *different* property from the one the caller named.
- `property_name="PartitionState\r\nX-Evil: 1"` raises `httpx.InvalidURL`. Its
  MRO is `(InvalidURL, Exception, BaseException, object)` — verified locally, not
  cited — so it is neither `httpx.TransportError` nor `httpx.HTTPError`, passes
  through `_request`'s two handlers untranslated, and escapes this client's
  `HMCError`/`HMCTransportError`/`ValueError` contract. A bare trailing `"\n"`
  does the same.

**Encoding is a no-op on every name this repository passes.** At `856e3aec`,
`rg -n -o "quick/[A-Za-z0-9_.%-]*" src/ tests/ docs/` returns six names —
`PartitionState`, `PartitionID`, `SystemType`, `NoSuchProperty`, `all`, `All` —
beside a bare `quick/` and two prose artifacts (`quick/All.`,
`quick/PartitionState.`). `quote(n, safe="")` returns each of the six unchanged,
so no byte on the wire moves. All five `src/` call sites pass the literal
`"PartitionState"`.

**A local grammar has no evidence base here, and a better local check already
exists.** `just setup` on this host reports `reference corpus not available on
this host: docs/refs`, so a character class would be extrapolated from those
six names — where ADR 0143's type grammar could be checked against a vendored
corpus and a closed, documented namespace. More decisively, this repository has
already decided how a quick-property *name* is checked locally: ADR 0141 checks
it against the HMC's own per-type list via `list_quick_properties`, opt-in under
`validate=True`, and ADR 0144 makes a type defining none refuse every name. A
guessed grammar would be a second, weaker namespace check layered over an
accurate one, and its failure mode is refusing a real property name the HMC
serves.

## Decision

**`property_name` is percent-encoded with `quote(property_name, safe="")` before
it is interpolated into the quick-property path. It is not validated against a
grammar, it is not length-bounded, and `_reject_dot_segments` is not widened.**

The site binds the result to a local, the shape `search_uom` already uses one
method away for this same argument:

```python
encoded_property = quote(property_name, safe="")
path = f"/rest/api/uom/{resource_type}/{uuid}/quick/{encoded_property}"
```

Reusing `search_uom`'s `encoded_property` name is deliberate: the classification
set in `tests/unit/test_request_path_safety.py` loses `property_name` and gains
nothing, so the segment inventory shrinks rather than growing a synonym. The
binding is load-bearing. A site-directed AST walk in that module requires
every `/rest/api/uom/` f-string in `core.py` that interpolates a name classed as
encoded to have a literal `quote(<name>, safe="")` assignment binding it in the
same function — the declaration form `_is_boundary_check` supplies for a type
segment and `_is_quote_binding` already supplies for `?group=`. Without it,
moving `property_name` into an "encoded" class would be the same vacuous
classification this issue exists to remove: a name in a set because of what it is
called. The walk also reaches `search_uom`'s existing `encoded_property` and
`encoded_value`, which were classified but never site-checked.

## Consequences

- The three behaviours reproduced above close for `property_name`, in one line
  at one call site, with no new predicate.
- **No wire-format change for any name this repository passes**, so every test
  pinning a quick-property path is untouched and there is no firmware question
  to re-capture.
- **One refusal moves, and the protection it stood for tightens.** Literal `..`,
  `.` and `../../x` names are still refused by `_reject_dot_segments` with
  `HMCError`: `quote` leaves `.` alone, and `"../../x"` becomes `"..%2F..%2Fx"`,
  which the guard's percent-decoding arm still reads as dot segments. Only a name
  the caller pre-encoded changes, in any of its forms (`%2e%2e` as well as
  `..%2f`) — `"..%2f..%2fweb%2fHmcUser%2froot"` becomes
  `"..%252f..%252fweb%252fHmcUser%252froot"`, which neither arm reads as a dot
  segment, so it is sent rather than refused. It goes as one inert segment: a
  single decode now yields `..%2f..%2f…` rather than dot segments, so the
  decoding that would have reached the retargeting no longer does. What moves is
  where the caller learns, and a test pins it rather than leaving it to this
  record — the shape ADR 0145's implementation used for the identical residual it
  opened for `group`.
- **`property_name` stays unvalidated against a name namespace by default.** That
  is ADR 0141's decision, not a gap this record opens: `validate=True` checks the
  name against the HMC's own list, and this change leaves it untouched. An
  unknown name still reaches the HMC and is answered there.
- **An empty `property_name` still addresses the `/quick/` container anchor**
  rather than a property — `quote("")` is `""`, so the path ends `.../quick/`.
  This record does not close it: it addresses the same resource, and refusing it
  is a grammar fragment, the option this record declines. It is not the anchor
  `list_quick_properties` reads: both of that method's paths end at `/quick`,
  where this one ends at `/quick/` with an empty final segment.
- A `property_name` that is not a `str` now raises `TypeError` from `quote`
  rather than being interpolated through `f"{...}"`, and `bytes` is decoded
  rather than repr'd. The `str` signature already forbids both; the annotation is
  unenforced at runtime, so this is an accepted escape from the
  `HMCError`/`HMCTransportError`/`ValueError` contract rather than an overlooked
  one, on the same terms ADR 0145 accepted for `group`. A `str` raises at most
  `UnicodeEncodeError`, which subclasses `ValueError` and stays inside it.
- **Reachability is unchanged.** No CLI command or MCP tool exposes
  `property_name`, and `get_quick_property` sits outside the
  `_SUPPORTED_CLIENT_LIFECYCLE` set `tests/unit/test_public_api.py` pins — so it
  is reachable only by code importing `HMCClient` from `hmc_mcp.api`, the
  callable-but-unsupported class ADR 0118 records.

## Considered & rejected

- **Give `property_name` its own grammar, as ADR 0143 gave the type segment.**
  verified: `just setup` at `856e3aec` reports `reference corpus not available on
  this host: docs/refs`, and `rg -n -o "quick/[A-Za-z0-9_.%-]*" src/ tests/ docs/`
  returns six distinct names, the repository's whole evidence for what a quick
  property name may look like. judgment: ADR 0143's allowlist is defensible
  because the type namespace is closed, documented and vendored; the same
  construction here extrapolates a character class from six samples, and it
  would duplicate — less accurately — the per-type check ADR 0141 already makes
  against the HMC's own list.
- **Bound the length as ADR 0147 bounds the type segment.** verified: that bound
  exists because the type also reaches the `Accept` header, which
  `get_quick_property` never builds; `_request_with_uuid_path_arguments` is the
  only transport this method uses. judgment: no reproduction, no second
  destination, and encoding already fixes the shape of what is sent — a bound
  here would be a number with nothing behind it.
- **Record raw interpolation as intended, the issue's third option.** verified:
  the fragment case above silently reads a different property, and the CRLF case
  raises outside the client's exception contract. judgment: raw is right where
  the value already has a control; this one has none, and the control that costs
  one line changes no byte for a legitimate name.
- **Widen `_reject_dot_segments` to refuse `?` and `#`.** verified: `list_uom`
  and `get_uom` hand it paths carrying `?group=`, and `search_uom` hands it
  `(prop==value)`, so a character rule at that waist refuses this client's own
  legitimate requests. ADR 0143 recorded the same finding for the type segment.
- **Encode inside `_request` for every path segment instead of at this site.**
  verified: `_request` receives an assembled path that legitimately carries `?`,
  `(`, `)` and `==`, so it cannot tell a caller's data from this client's own
  grammar. judgment: it would re-parse a string this client already built.
- **Translate `httpx.InvalidURL` into the client's exception contract at
  `_request`.** verified: `get_uom_path` hands a caller-supplied path to `_get`
  unchanged, so the escape is not specific to `property_name`. judgment: a
  transport-waist change outside this issue's surface, already reported as a
  follow-up candidate by ADR 0145 and not claimed here.
- **Do nothing and close the issue.** verified: the three behaviours above, and
  `_KNOWN_UOM_SEGMENT_ARGUMENTS` still listing `property_name`. judgment: distinct
  from recording raw as intended, which at least leaves a record; doing nothing
  leaves the unclassified-segment assertion passing vacuously for this argument,
  which is the outcome issue #818's third criterion forecloses.
