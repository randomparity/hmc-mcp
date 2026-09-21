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
`resource_type` — so it has one destination, and percent-encoding is the rule
RFC 3986 defines for one.

Two facts bound the answer, both established against `4d823cbb` with httpx
0.28.1, the version this repository locks.

**Raw interpolation does three separable things**, each verified by building the
request:

- `group="None&foo=bar"` yields `?group=None&foo=bar` — the caller appends a
  query parameter this client did not name.
- `group="None#/rest/api/web/HmcUser/root"` yields `?group=None` — httpx drops
  everything from the `#`, so the value is silently truncated rather than sent.
- `group="None\r\nX-Evil: 1"` raises `httpx.InvalidURL`. Its MRO is
  `(InvalidURL, Exception, BaseException, object)`: it is neither
  `httpx.TransportError` nor `httpx.HTTPError`, so it passes through
  `_request`'s two handlers untranslated and reaches the caller as an exception
  outside this client's `HMCError`/`HMCTransportError`/`ValueError` contract.

**Encoding is a no-op on every group name this repository passes.** There are
three, all literals: `RemoteAccess` (`client_users.py`), `ViosSCSIMapping` and
`ViosFCMapping` (`client_storage.py`, `client_systems.py`). `quote(g, safe="")`
returns each unchanged and `build_request` produces a byte-identical URL either
way, so there is no wire-format change and no firmware question to re-capture.

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

The binding is load-bearing, not cosmetic: a site-directed AST walk in
`tests/unit/test_request_path_safety.py` requires every `?group=` f-string in
`core.py` to interpolate a name that a literal `quote(<name>, safe="")`
assignment binds in the same function — the declaration form
`_is_boundary_check` already uses for the type segment — so a future site
interpolating an unencoded name fails before review sees it. This is ADR 0143's
second contract applied to data rather than to a schema identifier.

## Consequences

- The three behaviours reproduced above all close for `group`, in one line per
  site, with no new predicate.
- **No wire-format change for any group name this repository passes**, so the
  five literal `?group=` sites and every test that pins their paths are
  untouched.
- `group` stays unvalidated against a group-name namespace, so an unknown name
  reaches the HMC and is answered there. Accepted rather than overlooked: there
  is no list to check against, and an allowlist extrapolated from three samples
  would refuse real group names for no measured gain. The remedy, if one ever
  becomes worth refusing locally, is a grammar in one place with the refused
  value as its evidence.
- **An undocumented multi-group affordance closes.** The HMC takes repeated
  `group=` parameters, and today `list_uom(group="A&group=B")` could reach that
  by exploiting the raw interpolation; afterwards the value names one group
  called `A&group=B`. Nothing passes it — `rg -n 'list_uom\(|get_uom\(' src/
  tests/` returns 22 call sites and none supplies `group` — and
  `get_vios_storage_detail` serves the package's one multi-group read from its
  own literal path. The remedy, if it is ever wanted, is a sequence parameter
  joining encoded names with `&group=`.
- **No refusal moves, and one narrow residual opens.** `_reject_dot_segments`
  checks the percent-decoded form as well as the raw one (`core.py:253`), so a
  `group` whose decoded form holds a `.` or `..` segment is refused with
  `HMCError` both before and after: `quote("a/../b", safe="")` is `a%2F..%2Fb`,
  which the decode arm still reads as a dot segment. What changes is a value the
  caller percent-encoded itself — `group="x%2F..%2Fy"` becomes `x%252F..%252Fy`,
  which one decode resolves to `x%2F..%2Fy` rather than to a dot segment, so it
  now reaches the HMC as data. Accepted: it sits after the `?`, where no path
  resolution applies, and httpx leaves it encoded on the wire.
- A `group` that is not a `str` now raises `TypeError` from `quote` rather than
  being interpolated through `f"{...}"`, and a `bytes` value is decoded rather
  than repr'd. Both are inputs the `str | None` signature already forbids, so
  this is accepted rather than guarded; the signature is the contract.
- **No supported surface reaches `group`, and one unsupported one does.**
  `hmc_list_resources` takes `resource_type`, `profile` and `limit` only, and no
  CLI command or MCP tool passes `group`. It stays reachable by any code that
  imports `HMCClient` — one of `hmc_mcp.api`'s six stable names — and calls the
  method directly. ADR 0118 permits exactly that: it keeps `HMCClient`'s
  lifecycle allowlist and records that *inherited protocol methods are callable
  but unsupported*, and `list_uom`/`get_uom` sit outside the
  `_SUPPORTED_CLIENT_LIFECYCLE` set `tests/unit/test_public_api.py` pins. This is
  the reachability class ADR 0143 recorded for `get_uom_path`, citing the same
  record.

## Considered & rejected

- **Validate `group` against a grammar, on the same footing as the type
  segment.** verified: `just setup` on this host reports `reference corpus not
  available on this host: docs/refs`, and `rg -n '\?group=' src/` returns eight
  lines at `4d823cbb`: two interpolating sites in `core.py`, one docstring
  mention, and five literals naming three distinct group names. Those three
  names are the repository's whole evidence for what a group name may look like.
  judgment: ADR 0143's allowlist is defensible because the type namespace is
  closed, documented, and present in a vendored corpus; the same construction
  here would extrapolate a character class from three samples, and its failure
  mode is refusing a real group name the HMC serves.
- **Delete `group` from both signatures — the one alternative smaller than this
  decision.** verified: `rg -n 'list_uom\(|get_uom\(' src/ tests/` returns 22
  call sites, none supplying `group`, and `rg -n 'group: str \| None'
  src/hmc_mcp/client/client_contracts.py` returns six protocol declarations of
  the same parameter. judgment: removing it edits protocol classes outside this
  issue's surface and drops an extended-group read the HMC documents and five
  literal sites already use, to save a one-line encode.
- **Record raw interpolation as intended, as ADR 0143 did for the type's third
  option.** verified: raw `group="None&foo=bar"` builds `?group=None&foo=bar`,
  and raw `group="None\r\n..."` raises `httpx.InvalidURL`, which `_request`
  catches with neither of its `httpx.TimeoutException` and
  `httpx.TransportError` handlers (httpx 0.28.1). judgment: raw is the right
  answer where the value already has a control; this one has none, and the
  encoding that would give it one costs one line and changes no byte for a
  legitimate name.
- **Translate `httpx.InvalidURL` into the client's exception contract at
  `_request`, closing the class rather than one argument.** verified:
  `get_uom_path` hands its caller-supplied `path` to `_get` unchanged, so
  `/rest/api/uom/LogicalPartition/x\r\nX-Evil: 1` reaches `build_request` and
  raises `httpx.InvalidURL` (httpx 0.28.1) — the escape is not specific to
  `group`. `get_job_entry` is not that route: it applies
  `urlparse(job_href).path` first, which strips CR and LF, so the same value
  goes out as `/rest/api/uom/jobs/1X-Evil: 1` instead. judgment: a
  transport-waist change, outside this record's surface and outside issue #819.
  Reported as a follow-up candidate, not a residual this record accepts.
- **Widen `_reject_dot_segments` to refuse `&`, `=`, `?` or `#`.** verified:
  `get_vios_storage_detail` passes `?group=ViosSCSIMapping&group=ViosFCMapping`
  through that same waist, so all four characters occur in this client's own
  legitimate requests. ADR 0143 recorded the `?` case; `&` and `=` are this
  issue's.
- **Build the query with `urllib.parse.urlencode({"group": group})`.** verified:
  `urlencode` encodes space as `+` via `quote_plus`, where `quote(..., safe="")`
  emits `%20`; `search_uom`'s two existing query values use the latter.
  judgment: more machinery for the same result, in a different encoding from the
  neighbour it would sit beside.
- **Accept a sequence of group names now, so multi-group survives the change.**
  verified: those same 22 call sites supply no `group` at all, and
  `get_vios_storage_detail` builds its own multi-group literal. judgment:
  speculative — a signature nobody has asked for.
- **Do nothing and close the issue.** verified: the three behaviours reproduced
  in Context. judgment: leaving an untrusted value able to append a parameter
  this client did not name, truncate itself, and raise an exception outside the
  client's contract is not a decision anyone would write down.
