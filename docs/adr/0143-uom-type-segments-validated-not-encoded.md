# ADR 0143: UOM type path segments are validated, not percent-encoded

## Status

Accepted

## Context

Issue #809 asks a question the code answers only by repetition: twelve of the
fourteen `f"/rest/api/uom/{...}"` sites in `src/hmc_mcp/client/core.py`
interpolate a caller-supplied `resource_type`, `parent_type`, or `child_type`
raw, and nothing records whether that is a decision or an accident. #407 closed
the other half of the same question for `search_uom`'s property *value*, which
it percent-encodes; the type beside it on the same line is untouched.

Four facts bound the answer.

**The type reaches two destinations, not one.** Besides the path, the same
value is interpolated into the `Accept` media-type parameter by `_uom_headers`
(`application/atom+xml; type={resource_type}`). Percent-encoding is defined for
the first destination and meaningless in the second: RFC 2045 escapes a
parameter value with a quoted-string, not with `%XX`. Verified: `httpx`
0.28's `build_request` carries a CRLF in that value through untouched, and only
`h11` 0.16's `normalize_and_validate` refuses it at send time with
`LocalProtocolError` — so today the header destination's only control is a
transitive dependency's.

**Encoding is a no-op on every legitimate type.** `urllib.parse.quote(t,
safe="")` leaves RFC 3986 unreserved characters alone, so for every type name
this repository passes and every type segment the vendored corpus documents,
encoding changes no byte on the wire. Verified at `b269bbb6`: every
`/rest/api/uom/` type segment in `docs/refs/hmc-rest-api-p10` and
`docs/refs/hmc-rest-api-p11` matches `^[A-Za-z][A-Za-z0-9]*$`, as does every
literal the client passes and every member of `AdapterType`.

**The firmware already refuses an unknown type.** Captured at V1_17_0 and
recorded in `list_operations`' docstring: an unrecognised type is rejected at
the URL with HTTP 400 `INVALID_URL` — `REST000E` for an unknown root type,
`REST000C`/`REST000D` for an unknown child. So encoding a malformed type does
not make it work; it converts a refusal this client could make locally into a
network round trip that fails anyway, further from its cause, after putting the
caller's string in the HMC's audit log.

**`_reject_dot_segments` cannot host the check.** It runs at `_request`, the
single transport waist, where a path is an opaque string that legitimately
carries `?`, `(`, `)`, and `==`, and where nothing knows which segment was a
type. Its narrow contract — `.` and `..` segments, raw and once-decoded — is a
guard on path *form*.

## Decision

**Type segments are validated at the boundary against the HMC's own type-name
grammar. They are not percent-encoded, and `_reject_dot_segments` is not
widened.**

`_reject_unknown_uom_type(argument, value)` in `src/hmc_mcp/client/core.py`
refuses any value not matching `^[A-Za-z][A-Za-z0-9]*$` with a `ValueError`
naming the argument and the first offending character. It is called from two
places, one per destination:

- **Path.** Every client method that interpolates a caller-supplied type calls
  it on each such argument before building the path — the same shape as the
  UUID check `_request_with_uuid_path_arguments` already performs, and the same
  boundary-refusal shape as `validate_adapter_type` and ADR 0065's
  `validate_agent_id`: shared predicate, per-site declaration of which argument
  it governs. An AST test fails when a `/rest/api/uom/` f-string interpolates a
  name the inventory does not know, so a new site cannot join silently.
- **Header.** `_uom_headers` calls it on the `resource_type` it interpolates
  into `Accept`, whenever that argument is not `None`. The path sites cannot
  cover this one: four of them send `Accept: */*` and never reach
  `_uom_headers`, and `get_uom_path` takes a caller-supplied type with no uom
  f-string of its own. Checking where the value is used makes "nothing
  unvalidated reaches the `Accept` type parameter" a property of the function
  rather than of an enumeration of its callers. Verified at `b269bbb6`: every
  `resource_type` reaching it from outside `core.py` is a PascalCase literal
  already inside the grammar.

This makes two contracts explicit that were previously implicit:

- **The waist guards path form.** `_reject_dot_segments` refuses `.` and `..`
  segments and nothing else. That is its whole contract, by decision, because a
  path arriving there may legitimately carry query and grammar characters.
- **The boundary guards argument shape.** A segment naming a *schema
  identifier* is validated against that identifier's grammar before
  interpolation; a segment carrying *data* — a UUID, a property value — is
  checked or encoded by its own rule instead.

The refusal is `ValueError`, not `HMCError`, because it reports a malformed
caller argument rather than a path this client declines to send; that matches
`_request_with_uuid_path_arguments`, `validate_adapter_type`, ADR 0065, and
`run_limited_collection`'s own `limit` check.

## Consequences

- A type segment carrying `?` or `#` — the retargeting the issue reproduced,
  which it also establishes reaches no further than *within* `/rest/api/uom/` —
  is refused before any request is built, in both destinations, by one rule.
  The `Accept` destination stops depending on `h11` for its safety.
- **No wire-format change for any legitimate type**, so there is no firmware
  compatibility risk to re-capture for the accepting case. The risk runs the
  other way: a real HMC type name outside `^[A-Za-z][A-Za-z0-9]*$` would now be
  refused locally. No capture enumerates the HMC's complete type namespace, so
  **this is an accepted unknown, stated as one** — the grammar is derived from
  the vendored V10/V11 corpora and this client's own call set, not from an
  authoritative list. The remedy if it ever bites is to widen the character
  class in one place, with the refused type name as the evidence.
- An existing refusal identity moves. Three tests, five parametrized cases,
  passed a `..` type and asserted `HMCError` with `'..' segment`:
  `test_list_operations_rejects_a_dot_segment_type`,
  `test_list_quick_properties_refuses_bad_arguments` (two of its cases), and
  `test_list_search_parameters_refuses_a_dot_segment_resource_type`. A `..` type
  fails the grammar, so the boundary check now fires before the waist guard and
  those calls raise `ValueError`. The property each test exists for — refused
  before transport, no request recorded — is unchanged, and each is updated to
  the new identity rather than removed. `_reject_dot_segments` still refuses a
  `..` *instance* segment, which no boundary check sees.
- `hmc_list_resources` still accepts a free-form `resource_type` from the model,
  now bounded to the type grammar instead of to an arbitrary uom path. Whether
  it should expose the parameter at all is a separate reachability question,
  out of scope here per the issue.
- `get_quick_property` interpolates `property_name` into the same path raw, and
  this decision does not cover it: a quick-property name is not a type segment.
  Recorded so a reader does not infer coverage from silence.

## Considered & rejected

- **Percent-encode the type at all twelve sites (the issue's option 1).**
  verified: at `b269bbb6`, `quote(t, safe="")` is the identity function on every
  type literal this repository passes and every type segment in the vendored
  corpus, so it changes nothing for valid input; for invalid input it converts a
  local refusal into the captured HTTP 400 `INVALID_URL` round trip (V1_17_0).
  It also reaches only one of the value's two destinations, leaving
  `_uom_headers`' `Accept` parameter raw and the two disagreeing.
- **Record raw interpolation as intended (the issue's option 3).** judgment:
  legitimate, and the right answer if the type had one destination and an
  existing control. The header destination's only control is `h11`'s, which this
  repository neither owns nor tests.
- **Widen `_reject_dot_segments` to refuse `?` and `#`.** verified: `list_uom`
  and `get_uom` append `?group=` to the path they hand `_request`, and
  `search_uom` hands it `(prop==value)` — so a character rule at the waist
  refuses this client's own legitimate paths.
- **Deny a character list (`?`, `#`, `%`, `/`, space) instead of an allowlist.**
  judgment: a denylist over a URL path segment keeps being incomplete — `;`,
  `@`, `:`, CRLF each need discovering first. ADR 0065 declines to forbid
  characters with no recorded failure mode, which is right for a denylist over
  an open-ended value; the type namespace is closed and documented, so an
  allowlist has evidence a denylist extension would lack.
- **Thread a `type_path_arguments` mapping through `_get`/`_put`/`_delete`.**
  judgment: a parameter on five helpers to save twelve call-site lines, and
  `list_search_parameters` calls `_request` directly and would still need its
  own. An AST test covers the drift instead.
- **Do nothing and close the issue.** judgment: the issue's own terms — twelve
  sites agreeing by accident with no record — make this the one unavailable
  outcome.
