# ADR 0142: Search-parameter discovery parses one named element, and its validation is opt-in

## Status
Accepted

## Context
#789 asks for `HMCClient.list_search_parameters`, reading the type-anchored
`/rest/api/uom/{R}/search` anchor, and asks that it feed `search_uom` as a pre-flight check. It is
the union of two merged siblings applied to a third anchor: #788 landed the `/quick` discovery read
(PR #800, ADR 0140) and #799 landed the opt-in validation wiring (PR #805, ADR 0141). Those two
records settle most of the shape. Two questions are this one's.

**The response body is unknown, and this repository has never spoken this endpoint.** The vendored
reference corpus carries the path grammar for the root and child anchors at
`docs/refs/hmc-rest-api-p10/000-hmc-rest-apis.md:67-68` and
`docs/refs/hmc-rest-api-p11/000-hmc-rest-apis.md:67-68,84-85`, and per-type prose pointing at the
anchor at `docs/refs/hmc-rest-api-p11/164-managed-system.md:100` and
`.../managed-system/165-logical-partition.md:85`. It carries no content type, no body example and
no element vocabulary: case-insensitive searches over the whole corpus for `SearchParameter`,
`Search_Collection`, `SearchElement` and `searchable` each return zero hits.
`docs/solutions/2026-09-14-fixtures-invented-for-an-endpoint-never-spoken.md` records what happened
the last time a discovery read was written against that gap — a green, mutation-tested,
five-times-reviewed method that could not work on any firmware ever observed — and names #789 as
one of three siblings queued to hit the same wall.

**Whether validating is the default.** `search_uom` is reachable through `HMCClient`, one of
ADR 0118's six `hmc_mcp.api` facade names, and has six in-repo call sites.

## Decision
**One named element, isolated.** `list_search_parameters` reads the texts of a single element,
document-wide, through the existing `client_parse._find_all_text` — the same parse ADR 0140 chose
for `/quick`, for the same reason: `_parse_feed`'s `element_to_dict` collapses a repeated element to
a bare value when the HMC sends exactly one. The element name is a module-level constant,
`_SEARCH_PARAMETER_NAME_ELEMENT = "Nickname"`, and that constant is the design's single point of
change when firmware settles the question.

`Nickname` is an **inference, not a capture**: it is the name-bearing element FW950 returns from
the sibling `/quick` discovery anchor, and the reference prose ties a type's searchable properties
to its quick properties. It is recorded here as unverified so that no later reader mistakes it for
observed behaviour.

Three properties bound the inference, and together they are why shipping it is acceptable:

- **A wrong guess fails loudly, not silently.** A 200 yielding no name raises `HMCError` naming the
  element that was looked for, exactly as `list_quick_properties` does and for the same stated
  reason — the HMC is known to answer 200 with an `HttpErrorResponse` feed, which is
  indistinguishable to a caller from a type defining nothing. The failure mode is therefore a
  diagnostic that names its own fix, not a plausible wrong answer.
- **A wrong guess cannot break `search_uom`.** The degradation rule below turns that `HMCError`
  into today's unvalidated behaviour.
- **The fix is one constant and the fixtures.** No control flow, no caller, and no signature
  depends on the element's name.

**Validation is opt-in.** `search_uom` gains one keyword-only parameter, `validate: bool = False`.
The default is unchanged behaviour. When `validate=True`, the client reads the names once per
resource type through `list_search_parameters(resource_type)` — the root anchor, no parent
arguments, because `search_uom` addresses a root-anchored path — and raises `ValueError` before
transport when `property_name` is not among them. `ValueError` is the signal this module already
uses for an argument value it rejects locally.

Names are cached on the `HMCClient` instance, keyed by resource type, **for the client's lifetime
with no invalidation**, serialized by a per-client `asyncio.Lock` that re-checks the cache after
acquiring it. **A discovery read that yields no usable names degrades to today's behaviour and is
cached as such** — any `HMCError`, its subclass `HMCTransportError` included, and a successful read
carrying no names, which is what a 204 returns. An empty answer is read as "the names are unknown",
never as "the type defines nothing". These four paragraphs are ADR 0141's decision applied
unchanged to a second method; they are restated rather than cross-referenced because a reader of
`search_uom` should not have to find `get_quick_property`'s record to learn what its cache does.

`Accept: */*` is sent, as both sibling discovery reads send. With no known content type for this
anchor, it is the one Accept that cannot fail negotiation; a typed uom Accept would be a second
guess stacked on the first.

## Consequences
`search_uom`'s default behaviour is unchanged, so the six in-repo call sites
(`client_lpars.py:60`, `client_systems.py:179,251`, `operations/vios/core.py:42`,
`operations/systems/core.py:44`, `operations/lpar/core.py:96`) are unaffected; all six pass string
literals and none opts in. The two `search_uom` declarations in `client_contracts.py` (lines 89,
319) are unchanged and stay satisfied: a keyword-only parameter with a default widens the
implementation without narrowing the protocol. The facade's six exported names are unchanged.

Validating costs at most one extra request per resource type per client session, failures included.
Because the cache is per client and `_app.with_client` builds one client per MCP tool call,
`validate=True` from MCP is effectively one extra request per call — which is why it is not the
default, and why the caller it serves is one holding a client across several reads.

**This branch ships an unverified response shape, and the PR says so.** The tests prove the parse
discriminates between implementations given the fixture; they cannot prove the fixture matches
firmware, and the solution record above is the account of a branch where every other gate read as
green on exactly that gap. A mutation score over these tests would measure the same thing and must
not be reported as fixture validation. The closing evidence is a live capture against the operator's
ppc64le host, which this PR exists to make reachable from that system; the follow-up is the
second step of the two-step the `/operations` read established — `test: replace the assumed fixture
with a live capture`. Until it lands, `list_search_parameters` is a method whose transport,
argument handling, caching and degradation are proven and whose parse is not.

A second consequence of shipping it anyway: a caller who passes `validate=True` against a level
where the guess is wrong pays one discovery request per resource type and gets no validation. That
is the degradation path working as designed, and it is indistinguishable at the call site from a
level that does not serve the anchor.

`CHANGELOG.md` records the new method and the new parameter, because both are reachable through
ADR 0118's facade.

## Considered & rejected
- **Wait for a live capture before writing any of it.** verified: the operator directed in the
  invoking session (2026-09-15) that the ppc64le live testing happens on a different host and needs
  this PR in place and reachable from that system first, and froze that ordering into the charter's
  exclusions on issue #789. judgment: the transport, argument pairing, caching, degradation and
  opt-in default are all verifiable without firmware and are most of the change; blocking them on
  the one part that is not would deliver nothing to test against.
- **Try several candidate element names in order.** judgment: it defeats the discriminator that
  makes a wrong parse detectable. The solution record's sharpest finding is that a fixture carrying
  realistic wrong siblings kills mutants an element-only fixture structurally cannot; a parse that
  accepts `Nickname` *or* `SearchParameter` accepts the wrong sibling by construction, and reports
  a name without saying which shape answered.
- **Parse the whole body into a dict with `_parse_feed` and let the caller pick.** verified:
  ADR 0139 records `element_to_dict` collapsing a repeated element to a bare value at a count of
  one, the hazard that made ADR 0140 read elements directly for `/quick`. judgment: it moves the
  same unverified guess to every call site instead of removing it.
- **Return the raw body and let the caller parse.** judgment: #789's stated outcome is the property
  names; returning a string makes every caller solve the problem this method exists to solve, and
  the HTTP 400 the pre-flight avoids would come back with it.
- **Make validation default-on.** verified: `src/hmc_mcp/_app.py:199-208` builds a fresh
  `HMCClient` per MCP tool call through the single factory at
  `src/hmc_mcp/client/client_factory.py:9-11`, so the per-client cache is discarded after each call
  and default-on would add a discovery request to nearly every `search_uom` call in that
  deployment. verified: all six call sites pass string literals, so validation would reject nothing
  they send while charging every one of them. verified: the operator selected the opt-in shape in
  the invoking session (2026-09-15) over default-on and over opting the six sites in.
- **Add `parent_type`/`parent_uuid` to `search_uom` so a child-anchored type can be validated.**
  verified: `search_uom` builds `/rest/api/uom/{R}/search/({P}=={V})`, a root-anchored path, so
  parent arguments would describe a resource it does not address. The same ground ADR 0141 gave for
  `get_quick_property`.
- **Raise `HMCError` rather than `ValueError` for a rejected property name.** verified:
  `HMCError.__init__` (`src/hmc_mcp/errors.py:17-44`) carries an HTTP status and a response body,
  and no request was sent. judgment: a misspelled property name is a bad argument value, which this
  module already answers with `ValueError`.
- **Let a failed or empty discovery read raise, or re-read it per call.** verified: ADR 0139's
  three V1_20_0 HMCs answering 500 at the sibling `/operations` anchor and ADR 0140's
  `NetworkBridge` answering 400 at the root `/quick` anchor would both become a broken `search_uom`,
  which #789's fourth acceptance criterion forbids. judgment: re-reading instead of caching the
  negative entry spends exactly the per-call request the cost bound rules out.
- **Do nothing; leave the anchor unread.** verified: `rg -n '/search"' src/` at `a0d29ac7` returns
  no type-anchored discovery path, and `docs/capabilities/rows.json` (381 rows) contains no
  occurrence of `search` — the gap #789 is filed against.
