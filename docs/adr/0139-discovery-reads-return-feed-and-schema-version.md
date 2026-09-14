# ADR 0139: Discovery reads return the parsed feed with the response schema version

## Status
Accepted

## Context
Issue #787 adds the first of the HMC's self-describing discovery reads,
`GET /rest/api/uom/{R}/operations`, which answers what job operations a firmware level
actually defines. That answer is only meaningful for the schema version the HMC answered
under, so one call has to yield both the parsed entries and the response's
`X-HMC-Schema-Version`. The module's typed read helper `_get` returns body text only, and
its 27 call sites across `src/hmc_mcp/` never need a header. Epic #785 queues three more
anchor reads behind this one (#788 `/quick`, #789 `/search`, #791 job feeds), so the shape
chosen here is the shape they inherit.

## Decision
`HMCClient.list_operations(resource_type, *, parent_type=None, parent_uuid=None)` returns
`tuple[list[dict[str, Any]], str | None]`: the existing `_parse_feed` output paired with
the response's `X-HMC-Schema-Version`, `None` when the HMC sends no such header.

The request goes through `_request_with_uuid_path_arguments`, which keeps UUID validation and
dot-segment rejection on the one path that already owns them and leaves the live
`httpx.Headers` reachable. It sends `Accept: */*` and no `X-HMC-Schema-Version` request
header: `/operations` is an endpoint this repository has never spoken, no reference here
documents its media type, and `*/*` is the choice that cannot fail negotiation.

## Consequences
Callers destructure a 2-tuple; a call site that forgets is a `ty` error rather than a
silent list-of-one-element bug. The return is built from `list`, `dict`, `str` and `None`,
so no new public type joins ADR 0118's six-name facade even though `HMCClient` is exported
there. `_get` keeps its single-value contract and its 27 callers are untouched.

The `Accept: */*` choice and the feed's element shape are not confirmed against a live HMC
in this change; #793 (`verification:live-hmc`) owns that reconciliation. A firmware level
that insists on a typed `Accept` answers 406, which surfaces as `HMCError` carrying 406 —
a wrong-header report, not a parse failure.

The request-and-parse body is four statements. Repeating it once per discovery read is
accepted here; #788, #789 and #791 land the second through fourth instances, and extracting
a shared helper is theirs to justify once three exist.

## Considered & rejected
- **Return only the parsed entries and publish the schema version separately.** judgment: a
  second call to learn which schema version produced the first answer is a race the caller
  cannot close, and the pairing is the whole point of capturing the header.
- **Widen `_get` to return `(text, headers)`.** verified: `rg -n "self\._get\(" src/hmc_mcp/`
  reports 27 call sites at 975b0121, every one of which would have to destructure a tuple it
  does not use, to serve one new caller.
- **Use the public `raw_get` escape hatch, which already returns `(body, headers)`.**
  verified: it returns `dict(resp.headers)`, and httpx lowercases header names on iteration —
  `dict(httpx.Response(200, headers={"X-HMC-Schema-Version": "V1_0"}).headers)` has the single
  key `x-hmc-schema-version`, so a lookup under the documented capitalization reads `None`
  (httpx 0.29.2, this checkout). A caller that has to know the casing rule to read the header
  correctly is the trap this decision exists to remove. Keeping the live `httpx.Headers`, which
  is case-insensitive, also keeps `_request_with_uuid_path_arguments` on the path, so the child
  anchor needs no second UUID check beside the one that helper owns (`raw_get` calls `_request`
  directly, `src/hmc_mcp/client/core.py:894-907` at 975b0121). The cost is that this method
  repeats `raw_get`'s five-line status block.
- **Send the configured `X-HMC-Schema-Version` request header.** judgment: a request header
  that can provoke 406 is the wrong thing to add on the fail-open side of an endpoint this
  repo has never spoken, and nothing here can test which way the HMC takes it.
- **Do nothing; keep building `do/{Operation}` paths from names hardcoded in Python.**
  verified: `rg -n "/operations" src/` returns no match at 975b0121, and issue #787 names 14
  call sites that construct those paths with nothing able to ask the HMC whether it defines
  them.
