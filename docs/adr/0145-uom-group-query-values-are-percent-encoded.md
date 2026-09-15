# ADR 0145: UOM group query values are percent-encoded

## Status

Accepted (2026-09-15)

## Context

Issue #819 asks what rule governs the `group` value that `list_uom` and
`get_uom` append to a uom request path:

```python
path += f"?group={group}"
```

No grammar, no encoding, and no record — while `search_uom`, in the same class,
percent-encodes both of the values it puts in a query string. One of the two is
an accident. ADR 0143 settled the *type segment* on these same paths, and its
evidence does not transfer: it declined percent-encoding partly because the type
also reaches the `Accept` media-type parameter, where `%XX` is meaningless. A
`group` value reaches no media-type parameter — `_uom_headers` takes only
`resource_type`.

Four facts bound the answer. All were established against `4d823cbb` with
httpx 0.28.1, the version this repository locks.

**The value has one destination, and encoding is defined there.** It is a query
parameter value, and percent-encoding is the rule RFC 3986 defines for one —
unlike the second destination that split ADR 0143's answer.

**Encoding is a no-op on every group name this repository passes.** There are
three, all literals: `RemoteAccess` (`client_users.py`), `ViosSCSIMapping` and
`ViosFCMapping` (`client_storage.py`, `client_systems.py`). `quote(g, safe="")`
returns each unchanged and `build_request` produces a byte-identical URL either
way, so there is no wire-format change and no firmware question to re-capture.

**Raw interpolation is reachable and does three separable things.** Verified by
building the request:

- `group="None&foo=bar"` yields `?group=None&foo=bar` — the caller appends a
  query parameter this client did not name.
- `group="None#/rest/api/web/HmcUser/root"` yields `?group=None` — httpx drops
  everything from the `#`, so the value is silently truncated rather than sent.
- `group="None\r\nX-Evil: 1"` raises `httpx.InvalidURL`. Its MRO is
  `(InvalidURL, Exception, BaseException, object)`: it is neither
  `httpx.TransportError` nor `httpx.HTTPError`, so it passes through
  `_request`'s two handlers untranslated and reaches the caller as an exception
  outside this client's `HMCError`/`HMCTransportError`/`ValueError` contract.

**No group namespace is available to check a value against.** ADR 0143 derived
its type grammar from the vendored V10/V11 corpora; this checkout has no corpus
to derive a group grammar from — `scripts/link_reference_corpus.py` reports
`reference corpus not available on this host: docs/refs` — and the repository's
entire evidence is the three literals above.

## Decision

**A `group` value is percent-encoded with `quote(group, safe="")` before it is
interpolated into the query string. It is not validated against a grammar, and
`_reject_dot_segments` is not widened.**

Both sites bind the result to a local named for what it is, the shape
`search_uom` already uses for `encoded_property` and `encoded_value`:

```python
encoded_group = quote(group, safe="")
path += f"?group={encoded_group}"
```

That naming is load-bearing twice. It declares at the call site which rule
governs the value, as ADR 0143's per-site `_reject_unknown_uom_type` call does;
and it gives `tests/unit/test_request_path_safety.py` a declaration to hold the
sites to, in a site-directed AST test requiring every `?group=` f-string in
`core.py` to interpolate a declared encoded name.

ADR 0143's second contract already anticipated this answer: a segment naming a
schema identifier is validated against that identifier's grammar, and a segment
carrying data is checked or encoded by its own rule. `group` is data, so it is
encoded — the rule `search_uom`'s property value already follows and the type
segment could not. The waist's contract is unchanged.

## Consequences

- Query-parameter injection, silent `#` truncation, and the unhandled
  `httpx.InvalidURL` all close, in one line per site, with no new predicate.
- **No wire-format change for any group name this repository passes**, so the
  three literal sites and every test that pins their paths are untouched.
- `group` stays unvalidated against a group-name namespace, so an unknown name
  reaches the HMC and is answered there. Accepted rather than overlooked: there
  is no list to check against, and an allowlist extrapolated from three samples
  would refuse real group names for no measured gain. The remedy, if one ever
  becomes worth refusing locally, is a grammar in one place with the refused
  value as its evidence.
- **An undocumented multi-group affordance closes.** The HMC takes repeated
  `group=` parameters, and today `list_uom(group="A&group=B")` could reach that
  by exploiting the raw interpolation; afterwards the value names one group
  called `A&group=B`. Nothing passes it — `group` has **no caller in `src/` or
  `tests/`**, and `get_vios_storage_detail` serves the package's one multi-group
  read from its own literal path. The remedy, if it is ever wanted, is a
  sequence parameter joining encoded names with `&group=`.
- **An incidental refusal moves.** A `group` containing `/..` today reaches
  `_reject_dot_segments`, which splits the whole string — query included — on
  `/` and raises `HMCError`. Afterwards `/` is `%2F` and the value passes as
  data. Nothing is retargeted: httpx leaves `%2F` encoded, and the value sits
  after the `?` where no path resolution applies. That guard's contract is path
  form, so a query value was never its subject; the refusal was a side effect of
  its not parsing the query off a relative path.
- `hmc_list_resources`, the CLI, and every MCP tool are unaffected: none passes
  `group`. The parameter is reachable only through the pre-release `HMCClient`
  module API (ADR 0123) — the reachability class ADR 0143 recorded for
  `get_uom_path`.

## Considered & rejected

- **Validate `group` against a grammar, on the same footing as the type
  segment.** verified: `just setup` on this host reports `reference corpus not
  available on this host: docs/refs`, and `rg -n '\?group=' src/` returns eight
  lines at `4d823cbb`: two interpolating sites in `core.py`, one docstring
  mention, and five literals naming three distinct group names. Those three
  names are the repository's whole evidence for what a group name may look
  like. judgment: ADR 0143's allowlist is defensible
  because the type namespace is closed, documented, and present in a vendored
  corpus; the same construction here would extrapolate a character class from
  three samples, and its failure mode is refusing a real group name the HMC
  serves.
- **Record raw interpolation as intended, as ADR 0143 did for the type's third
  option.** verified: raw `group="None&foo=bar"` builds
  `?group=None&foo=bar`, and raw `group="None\r\n..."` raises
  `httpx.InvalidURL`, which `_request` catches with neither of its
  `httpx.TimeoutException` and `httpx.TransportError` handlers (httpx 0.28.1).
  judgment: raw is the right answer where the value already has a control; this
  one has none, and the encoding that would give it one costs one line and
  changes no byte for a legitimate name.
- **Widen `_reject_dot_segments` to refuse `&`, `=`, `?` or `#`.** verified:
  `get_vios_storage_detail` passes
  `/rest/api/uom/VirtualIOServer/{uuid}?group=ViosSCSIMapping&group=ViosFCMapping`
  through that same waist, so all four characters occur in this client's own
  legitimate requests. ADR 0143 recorded the `?` case; `&` and `=` are this
  issue's.
- **Build the query with `urllib.parse.urlencode({"group": group})`.**
  verified: `urlencode` encodes space as `+` via `quote_plus`, where
  `quote(..., safe="")` emits `%20`; `search_uom`'s two existing query values
  use the latter. judgment: more machinery for the same result, in a different
  encoding from the neighbour it would sit beside.
- **Accept a sequence of group names now, so multi-group survives the change.**
  judgment: speculative. `group` has no caller, and the one multi-group read in
  the package builds its own path.
- **Do nothing and close the issue.** judgment: the issue's terms — two sites
  disagreeing with their neighbour by accident, with no record — make this the
  one unavailable outcome.
