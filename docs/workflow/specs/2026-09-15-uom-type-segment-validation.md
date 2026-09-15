# Validate UOM type path segments at the boundary

Issue #809. Decision record: [ADR 0143](../../adr/0143-uom-type-segments-validated-not-encoded.md).

## Problem

Twelve of the fourteen `f"/rest/api/uom/{...}"` sites in
`src/hmc_mcp/client/core.py` interpolate a caller-supplied type raw, as does the
`Accept` media-type parameter via `_uom_headers`. A type carrying `?` or `#`
retargets the request within `/rest/api/uom/`, and nothing records whether that
is a decision or an accident. ADR 0143 holds the evidence and the decision.

## Scope

**In scope**

- `_reject_unknown_uom_type(argument, value)` in `src/hmc_mcp/client/core.py`:
  refuses a value `re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", value)` rejects, with `ValueError`
  naming the argument and the first offending character.
- A call on every caller-supplied type argument, in `list_uom`, `get_uom`,
  `get_quick_property`, `list_quick_properties`, `search_uom`,
  `list_search_parameters`, `list_operations`, `list_child`, `create_child`,
  `delete_child`; and one in `_uom_headers` on a non-`None` `resource_type`,
  covering the `Accept` destination, which no path site reaches for the four
  `Accept: */*` methods or for `get_uom_path`.
- The `_reject_dot_segments` docstring, stating its narrow scope as a contract.
- `tests/unit/test_request_path_safety.py`: the grammar, both destinations, the
  refusal-before-transport property, and a site-directed AST drift test.
- `tests/unit/test_client.py`: update the three tests (six parametrized cases)
  whose refusal identity moves from `HMCError` to `ValueError`.

**Out of scope**

- `hmc_list_resources`' own model-controlled exposure — a separate reachability
  question per the issue. Owner: a new issue, if pursued.
- Retroactive rework of closed #407. Owner: n/a, shipped.
- `get_quick_property`'s `property_name` segment. Not a type segment; it needs
  its own grammar and evidence. Owner: a follow-up candidate returned to this
  campaign, also recorded in ADR 0143's consequences.
- Percent-encoding anything. Rejected by ADR 0143.

**Ownership**: the type-grammar policy is new and has one owner,
`_reject_unknown_uom_type`. `_reject_dot_segments` keeps its current owner and
contract unchanged — no transition, no caller migration, no obsolete path.

## Failure model

**Actors and deployments**

- A local operator running the CLI or the `hmc_mcp.api` facade.
- An MCP client model calling `hmc_list_resources`, whose `resource_type` is a
  free-form string parameter.
- Repository callers in `client_*.py` and `operations/`, all passing literals;
  `client_adapters.py` passes an `AdapterType` that `validate_adapter_type`
  already constrains to four literals.

**Invariants and assets at stake**

- A request addresses the resource its declared selector names (ADR 0039).
- The `Accept` media-type parameter stays a well-formed token.
- Every type name this repository already passes keeps working, byte-identical
  on the wire.

**Accepted failure classes**

- A real HMC type name outside `\A[A-Za-z][A-Za-z0-9]*\Z` would be refused
  locally. Accepted: no capture enumerates the HMC's type namespace, and every
  type segment in the vendored V10/V11 corpora and every literal this client
  passes is inside the grammar. Cost is bounded — one character class, one
  place, with the refused name as the evidence to widen it.
- A caller may still address any *valid* type it likes, including one the
  access policy would rather it did not. Accepted: unchanged by this work, and
  explicitly the excluded reachability question.
- A malformed type reaches `_uom_headers` only after a path site already
  refused it, so for those ten methods the header check is defence in depth.
  Accepted: `get_uom_path` reaches it with no path site in front, and it has no
  caller in `src/` or `tests/` — `HMCClient` is an `hmc_mcp.api` export
  (ADR 0118), so the module API is its only entry point.

**Covered elsewhere**

- Dot-segment path traversal: `_reject_dot_segments`, unchanged.
- UUID-shaped segments: `_request_with_uuid_path_arguments`, unchanged.
- Search property and value: `quote(..., safe="")` in `search_uom` (#407).
- Adapter child types: `validate_adapter_type` in `client_contracts.py`.

## Threat model

**Boundary inventory.** No boundary is added. Two existing ones are narrowed:
the URL path built from a non-literal type argument (ten methods), and the
`Accept` media-type parameter built from the same value (`_uom_headers`).

**Actor model.** The untrusted party is the MCP client model, which supplies
`resource_type` to `hmc_list_resources` with no enum and no validator. The CLI
operator and `api` caller are trusted to the extent the process is theirs;
their exposure is mis-addressing, not privilege. Trust is placed in the
repository's own literal call sites, which this change re-checks rather than
assumes.

**Control per boundary.** `_reject_unknown_uom_type`, before interpolation at
the path sites and inside `_uom_headers` for the header. Fails with `ValueError`
naming the argument and the offending character — no path, no host, no full
caller string, matching `_reject_dot_segments`' leak rule. On the header it
replaces `h11`'s send-time CRLF check, which the trailing-newline case escapes
and which this repository neither owns nor tests (ADR 0143).

**Explicitly out of scope.** Whether `hmc_list_resources` should accept a
free-form type at all; response-body trust; authorization, which ADR 0038 and
ADR 0039 own and this change does not touch.

## Success

- Every caller-supplied type argument named in Scope is refused before a request
  is built when `re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", value)` rejects it —
  `fullmatch` and not `^...$`, because Python's `$` accepts a trailing newline.
- `?` and `#` in any of those arguments are refused; the issue's reproduction no
  longer retargets.
- No wire byte changes for any type name the repository currently passes.
- `_reject_dot_segments`' behaviour is unchanged and its narrow scope is stated
  in its docstring as a contract.
- `just verify` and `uv run --no-sync prek run --all-files` are green.

## Validation

Six material contracts. The plan's Task 1 Verification inventory carries each
one's test name, expected red observation, and exact green command; they are
not restated here.

- The type grammar — `focused-test`, `tests/unit/test_request_path_safety.py`.
- Refusal precedes transport, per method (ten of them) — `focused-test`, same
  file.
- The `Accept` destination via `_uom_headers` — `focused-test`, same file.
- No site drifts: a site-directed AST walk of `core.py` requiring each
  type-interpolating function to carry its own predicate call, and refusing an
  unknown interpolated name — `focused-test`, same file.
- The moved refusal identity, three tests in `tests/unit/test_client.py` —
  `focused-test`.
- `_reject_dot_segments` unchanged — `task-test-not-applicable`: its existing
  parametrized accept/refuse cases are the observation and this change does not
  modify them, so a second test asserting identical behaviour would observe
  nothing new.
