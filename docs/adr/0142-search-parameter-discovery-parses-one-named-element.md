# ADR 0142: Search-parameter discovery parses one named element, and its validation is opt-in

## Status
Accepted

> **Amended by [ADR 0144](0144-container-present-empty-discovery-is-authoritative.md)**
> (2026-09-15): an empty answer carrying the `SearchParameterSet` container and neither a
> `SearchParameter` nor a `ParameterName` element under it is now read as "the type defines
> nothing" and cached as an empty positive set, so `ManagementConsole` and the five other captured
> types defining nothing are refused locally. A 204, a failed read, and a container holding
> parameters this parse cannot name are still read as "the names are unknown" — but the value
> carrying that reading changed: `list_search_parameters` returns `(None, version)` for them, not
> the `([], version)` this record's body states.

## Context
#789 asks for `HMCClient.list_search_parameters`, reading the type-anchored
`/rest/api/uom/{R}/search` anchor, and asks that it feed `search_uom` as a pre-flight check. It is
the union of two merged siblings applied to a third anchor: #788 landed the `/quick` discovery read
(PR #803, ADR 0140 — the live rounds that settled its response shape are recorded on the earlier
PR #800, which was closed unmerged) and #799 landed the opt-in validation wiring (PR #805,
ADR 0141). Those two
records settle most of the shape. Two questions are this one's.

**The response body was unknown when this was written, and the corpus still does not carry it.**
The vendored reference corpus carries the path grammar for the root and child anchors at
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

**A live capture then settled it, and this record was revised against that capture.** The operator
ran the protocol on PR #807 against Power HMCs at `V1_17_0` and `V1_20_0`. The decision below is
what the capture supports; the inference it replaces is retained where it explains a cost, because
the gap between the two is the only calibration datum this repository has for the next anchor.

**Whether validating is the default.** `search_uom` is reachable through `HMCClient`, one of
ADR 0118's six `hmc_mcp.api` facade names, and has six in-repo call sites.

## Decision
**One named element, isolated.** `list_search_parameters` reads the texts of a single element,
document-wide, through the existing `client_parse._find_all_text` — the same parse ADR 0140 chose
for `/quick`, for the same reason: `_parse_feed`'s `element_to_dict` collapses a repeated element to
a bare value when the HMC sends exactly one — and `Cluster` defines exactly one search parameter,
so that collapse is reachable rather than theoretical. The element name is a module-level constant,
`_SEARCH_PARAMETER_NAME_ELEMENT = "ParameterName"`.

**The captured shape.** Both levels answer the root anchor 200 `application/atom+xml` with an
`<entry>` whose content is a `<SearchParameterSet>`: an `<ElementName>` naming the type, then a
`<SearchParameters>` holding one `<SearchParameter>` per property, each carrying
`<ParameterName>`, `<Comparator>` and `<XPath>`. The names are the `ParameterName` texts.

**A second constant, `_SEARCH_PARAMETER_CONTAINER_ELEMENT = "SearchParameterSet"`, is consulted
only when no name was found.** A type answering 200 with a `SearchParameterSet` carrying no
`SearchParameters` child defines no search parameters, and that is a legitimate answer returning
`([], version)`, not an error. A 200 carrying neither the names nor the container still raises
`HMCError`, because the HMC is known to answer 200 with an `HttpErrorResponse` feed and without the
container the two are indistinguishable. Before the capture this distinction could not be drawn and
every nameless 200 raised; the empty case was believed unreachable.

**It is the common case, not a corner.** Six of the eleven types captured define nothing —
`ManagementConsole`, `VirtualSwitch`, `VirtualNetwork`, `NetworkBridge`, `LogicalUnit` and
`SharedProcessorPool`. Had the element name alone been corrected, the pre-capture code would still
have raised `HMCError` on a majority of the sampled types.

### What the inference cost, recorded for the next anchor

The pre-capture decision read `<Nickname>` from a `<SearchParameter_Collection>`, inferred from the
sibling `/quick` anchor and from the corpus tabling a type's searchable properties under a
`Quick property` heading. **Both halves were wrong**, and the three properties this record gave for
why shipping the inference was acceptable held unevenly:

- **"A wrong guess that matches nothing fails loudly"** — held exactly. `Nickname` appears nowhere
  in the captured body, so every call would have raised `HMCError` naming the element it looked
  for. The designed loud failure is what the capture would have produced in the field.
- **"A wrong guess cannot break a caller who did not opt in"** — held. `validate` defaults to
  `False` and the degradation rule turns the zero-match `HMCError` into unvalidated behaviour.
- **"The fix is one constant and the fixtures"** — **did not hold.** That property was explicitly
  conditioned on the difference being only the element name, and it was not: the container differed
  too, which this record named as the case costing "a parse rewrite rather than a constant edit".
  The correction was the two constants, a new container branch with its own error message, and
  every fixture — not a one-line edit. The pessimistic branch of the stated upper bound is the one
  that happened, on the anchor the corpus constrained least. Treat that as the expected outcome for
  the remaining unspoken anchors, not the unlucky one.

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
carrying no names, which is what a 204 returns and what the six captured types defining nothing
return. An empty answer is read as "the names are unknown",
never as "the type defines nothing". This is ADR 0141's cache decision applied unchanged to a second
method; see that record for the reasoning, which is not repeated here.

`Accept: */*` is sent, as both sibling discovery reads send. The captured content type is
`application/atom+xml`, and the capture also sent `application/atom+xml; type=feed` and got 200
with a byte-identical body. A typed *uom* Accept (`application/vnd.ibm.powervm.uom+xml; type=R`)
was **not** probed, so nothing is known about how this anchor negotiates that. `*/*` is kept
because it is the one Accept that cannot fail negotiation on a level nobody has measured.

## Consequences
`search_uom`'s default behaviour is unchanged, so nothing in this repository is affected: the spec's
*Ownership* paragraph enumerates the six call sites, the two `client_contracts.py` declarations and
the facade exports, and none of them moves.

Validating costs at most one extra request per resource type per client session, failures included.
Because the cache is per client and `_app.with_client` builds one client per MCP tool call,
`validate=True` from MCP is effectively one extra request per call — which is why it is not the
default, and why the caller it serves is one holding a client across several reads.

**The root anchor's parse is confirmed at `V1_17_0` and `V1_20_0`, across eleven types.** The
fixtures are reconstructed from the capture's two shape reports rather than from verbatim bodies,
because the raw bodies carry instance data. Nothing in them is invented: round 2 supplied the
`Comparator` text — one string, `Regular Expression or String Match`, on every parameter of every
type captured — and the `XPath` form, a schema path ending in `/Value`. Each reported text matches
the length statistics round 1 reported independently.

**No captured level serves a child-anchored form, and `parent_type`/`parent_uuid` were removed.** #789's first
acceptance criterion asked that both anchors be reachable. They are not, and the second capture
round settled it with a control rather than another data point: under **one** `ManagedSystem`
parent, in one session,

| Anchor | Status |
|---|---|
| `…/{UUID}/LogicalPartition` (plain child feed) | 200 |
| `…/{UUID}/LogicalPartition/quick` | 200 |
| `…/{UUID}/LogicalPartition/search` | 400 `INVALID_URL` |
| `…/{UUID}/VirtualIOServer/search` | 400 `INVALID_URL` |

with the message *"REST000B The URL presented to the Management Console REST Web Services is not
valid."* The HMC calls the URL **shape** invalid while serving two other child anchors on that
exact parent, and it does so for two different child types. That excludes the parent being wrong,
which is the only other reading a bare 400 would have allowed. The corpus documents a path grammar
this firmware does not implement.

The parameters were therefore cut rather than shipped as unproven surface: a keyword argument whose
only observed behaviour is `HMCError` is a worse contract than its absence, and removing it now
costs nothing, whereas removing it after release would be a breaking change. **This is a knowing
partial miss of criterion 1, on evidence, recorded rather than worked around.** If a later level
serves the form, re-adding two keyword-only arguments with defaults is backward-compatible.

**`search_uom`'s unsupported-property status is 500, not the 400 this was designed against.** Both
levels answer `ReasonCode: Unknown internal error.` with *The left hand side of the expression is
not a registered search parameter*. Nothing in the design depends on which it is — both surface as
`HMCError` — but a 500 is a worse round trip to spend than a 400, which strengthens rather than
weakens the case for the pre-flight.

The `X-HMC-Schema-Version` warning is confirmed rather than merely inherited: every captured 200
returned this client's own `X-Audit-Memento` in that header, never a firmware level. Callers must
not parse it as one.

A caller who passes `validate=True` against a level where the anchor is absent or answers
differently pays one discovery request per resource type and gets no validation. That is the
degradation path working as designed, and it is indistinguishable at the call site from a level
that does not serve the anchor. A type defining no parameters reads the same way, deliberately:
`ManagementConsole` caches as "unknown" rather than as an empty positive set, which would otherwise
reject every property name for the client's lifetime.

`CHANGELOG.md` records the new method and the new parameter, because both are reachable through
ADR 0118's facade.

## Considered & rejected
- **Wait for a live capture before writing any of it.** verified: the operator directed in the
  invoking session (2026-09-15) that the ppc64le live testing happens on a different host and needs
  this PR in place and reachable from that system first, and froze that ordering into the charter's
  exclusions on issue #789. judgment: the transport, argument pairing, caching, degradation and
  opt-in default are all verifiable without firmware and are most of the change; blocking them on
  the one part that is not would deliver nothing to test against. verified in hindsight: the
  ordering worked — the capture ran against this branch and corrected it before merge — and the
  parts predicted to survive firmware did survive it. Only the parse and its fixtures changed.
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
  the HMC-side rejection the pre-flight avoids — captured as a 500 — would come back with it.
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
  `NetworkBridge` answering 400 at the root `/quick` anchor would both become a broken `search_uom`
  on levels where the anchor is simply absent. verified: the operator selected the opt-in shape in
  the invoking session (2026-09-15), and an opt-in pre-flight that can break the call it guards is
  not the pre-flight that was chosen. judgment: re-reading instead of caching the negative entry
  spends exactly the per-call request the cost bound rules out.

  This bullet previously cited "#789's fourth acceptance criterion" as its ground. That citation
  was wrong — #789's fourth criterion is that an unknown resource type surfaces `HMCError` carrying
  the HMC status, which the design meets separately — and it is withdrawn rather than reworded.
- **Do nothing; leave the anchor unread.** verified: `rg -n '/search"' src/` at `a0d29ac7` returns
  no type-anchored discovery path, and `docs/capabilities/rows.json` (381 rows) contains no
  occurrence of `search` — the gap #789 is filed against.
