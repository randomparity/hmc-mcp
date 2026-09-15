# ADR 0141: Quick-property name validation is opt-in and cached for the client's lifetime

## Status
Accepted

## Context
ADR 0140 landed `HMCClient.list_quick_properties`, which answers what quick-property names a
resource type defines, and left it with no caller; its *Consequences* names #799 as the owner of
wiring validation into `get_quick_property`. #799 leaves two questions for this record: how long the
discovered names are cached and when they are invalidated, and whether validating is the default.

Neither is free to answer. `get_quick_property` is reachable through `HMCClient`, one of ADR 0118's
six `hmc_mcp.api` facade names, so changing what it does by default is facade movement; three of its
five call sites also read `PartitionState` inside partition lifecycle polling
(`src/hmc_mcp/operations/lpar/core.py:140,417,481`). Two facts constrain the rest:

- **The client is per-call in the deployment that matters.** `with_client`
  (`src/hmc_mcp/_app.py:199-208`) opens a fresh `HMCClient` through `client_from_env`
  (`src/hmc_mcp/client/client_factory.py:9-11`) for each MCP tool invocation and closes it again.
  Any cache living on the client is discarded after one tool call.
- **Discovery is not available everywhere.** ADR 0139 records three V1_20_0 HMCs answering 500 at
  the sibling `/operations` anchor, and ADR 0140 records `NetworkBridge` answering 400 at the root
  `/quick` anchor and 200 only under `ManagedSystem`.

## Decision
`get_quick_property` gains one keyword-only parameter, `validate: bool = False`. The default is
unchanged behaviour: the name is interpolated into the path and sent, exactly as today.

When `validate=True`, the client reads the names once per resource type through
`list_quick_properties(resource_type)` — the root anchor, no parent arguments — and raises
`ValueError` before transport when `property_name` is not among them. `ValueError` is the signal
this module already uses for an argument value it rejects locally, as
`_request_with_uuid_path_arguments` does for a non-UUID.

The names are cached on the `HMCClient` instance, keyed by resource type. `get_quick_property`
always addresses a root-anchored instance path, so the anchor the key covers is the root anchor in
every case — fixed rather than absent.

**Lifetime is the client's, with no invalidation.** A fresh `HMCClient` starts empty and re-reads on
first validated use. Nothing expires or refreshes an entry within a session, so a long-lived client
that outlives a firmware change keeps the names it read at first use until it is closed.

**A failed discovery read degrades to today's behaviour and is cached as such.** Any `HMCError` —
which covers its subclass `HMCTransportError` — records a negative entry for that type and the call
proceeds unvalidated. Caching the failure is what holds the cost bound: a retry per call is exactly
the extra request this design promises not to make.

## Consequences
Validating costs at most one extra request per resource type per client session, failures included,
and a caller that does not ask for it pays nothing. Because the cache is per client and the client
is per tool call, `validate=True` from MCP is effectively one extra request per call — which is why
it is not the default, and why the caller it serves is one holding a client across several reads.

The five existing call sites are unchanged and unaffected: they pass string literals whose live
resolution ADR 0140 records, and none opts in. The facade's six exported names are unchanged;
`bool` adds no public type.

A stale cache fails only in the direction the default makes opt-in. A stale positive set can wrongly
reject a name a newer level added, and the escape is the default `validate=False`. A stale negative
entry only means validation stays off for the session, which is today's behaviour.

Concurrent validated calls for one type, before the first read returns, each issue their own read.
The result is identical and the request idempotent, so only the cost bound slips, and only for calls
already in flight; serializing them would need a per-type lock to save at most one GET in a client
that lives for one tool call.

`CHANGELOG.md` records the new parameter, because the method is reachable through ADR 0118's facade.
What it records is an addition, not a behaviour change — which is the reason the default is `False`.

## Considered & rejected
- **Make validation default-on.** verified: `src/hmc_mcp/_app.py:199-208` builds a fresh
  `HMCClient` per MCP tool call through the single factory at
  `src/hmc_mcp/client/client_factory.py:9-11`, so the per-client cache is discarded after each call
  and default-on would add a discovery request to nearly every `get_quick_property` call in that
  deployment — which #799's *Expected* excludes ("without adding an HMC round trip to every call").
  verified: all five call sites pass string literals (`operations/vios/core.py:99`,
  `operations/lpar/core.py:140,417,481`, `operations/lpar/decommission.py:542`) and ADR 0140 records
  the live run feeding discovered names back through `get_quick_property` successfully, so there is
  no typo at any current call site for the cost to catch.
- **Cache process-wide or on the class, so default-on becomes affordable.** judgment: a cache
  outliving the client outlives the session and host it was read under, and sharing it between
  clients pointed at different HMCs needs a host-and-level key — rebuilding this record's
  invalidation problem somewhere harder.
- **Give the cache a TTL.** verified: the only provenance a discovery read returns is
  `X-HMC-Schema-Version`, and ADR 0139 (V1_17_0) and ADR 0140 (FW950) both record the HMC filling it
  with the request's `X-Audit-Memento` echo, so it cannot key an invalidation. judgment: a
  wall-clock timer on an object whose dominant lifetime is one tool call never fires.
- **Raise `HMCError` rather than `ValueError`.** verified: `HMCError.__init__`
  (`src/hmc_mcp/errors.py:17-46`) carries an HTTP status and response body, and no request was sent.
  `_reject_dot_segments` does raise `HMCError` pre-flight, but its subject is a path shape this
  client will not send at all; a misspelled property name is a bad argument value, which this module
  already answers with `ValueError` at `_request_with_uuid_path_arguments` ("`{argument}` must be a
  UUID") and in `list_quick_properties`' parent-pairing check.
- **Add `parent_type`/`parent_uuid` so a child-anchored type can be validated.** verified: ADR 0140
  records `NetworkBridge` as the one known type answering 400 at the root anchor and 200 under
  `ManagedSystem`, and the degradation rule already turns that 400 into unvalidated behaviour.
  judgment: `get_quick_property` reads a root-anchored instance path, so parent arguments would
  describe a resource it does not address, for one known type.
- **Let a failed discovery read raise.** verified: ADR 0139's three V1_20_0 HMCs answering 500 and
  ADR 0140's 400 for `NetworkBridge` would both become a broken `get_quick_property`. This is #799's
  fourth acceptance criterion.
- **Re-read after a failed discovery instead of caching the failure.** judgment: the per-type
  request bound is this design's only cost promise, and a retry per call is precisely the request it
  promises not to make.
- **Validate by catching the HMC's own 400 or 404 and reporting it better.** judgment: that is the
  round trip #799 exists to remove, wearing a nicer message.
- **Do nothing; leave `list_quick_properties` unwired.** verified: `rg -n list_quick_properties
  src/` at `fac7c19e` returns only its own definition at `src/hmc_mcp/client/core.py:717`, the
  consumer gap ADR 0140 assigns to #799.
