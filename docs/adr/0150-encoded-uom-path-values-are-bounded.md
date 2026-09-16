# ADR 0150: Encoded uom path values are bounded

## Status

Accepted (2026-09-15)

## Context

`search_uom` percent-encodes `property_value` and `UsersMixin._child_path` percent-encodes
`console_uuid` before interpolating each into a uom request path. Issue #827 asks what bounds
those two values. Encoding makes a value safe to put in a path; it does not make it small.

Three facts bound the answer, all established at `101f2117` against httpx 0.28.1 — the version
this repository locks — by driving the real client with a stubbed transport.

**A long value is built and sent.** `search_uom("ManagedSystem", "SystemName", "A" * 65_000)`
puts a 65,065-character URL on the wire. `list_hmc_users("A" * 10_000)` puts a 10,060-character
one there.

**The only thing stopping it is a library constant that refuses anonymously.** httpx's
`MAX_URL_LENGTH` is 65536; past it `urlparse` raises `InvalidURL("URL too long")`, which since
ADR 0148 `_request` translates to `HMCError`. That refusal arrives after the whole value was
assembled, and — by ADR 0148's own recorded residual — names neither the argument that caused it
nor its size. It is a backstop against the transport, not a contract with the caller.

**Without a bound the accepted input length is not a constant.** httpx's ceiling applies to the
encoded URL, and `quote(value, safe="")` emits three characters per UTF-8 byte while a character
can be four UTF-8 bytes. So one input character becomes between one and twelve characters on the
wire: `quote("a")` is 1 character, `quote("é")` is 6, `quote("\U0001F600")` is 12. The input
length at which the existing ceiling fires therefore varies by a factor of twelve with what the
caller passed. Measured on the path `search_uom("ManagedSystem", "SystemName", ...)` builds, the
first refused length is 5,458 astral characters against 65,488 ASCII ones — and both figures move
with the resource type and property name sharing the line, so neither is a fixed number. Verified:
`search_uom(..., "\U0001F600" * 6_000)` is already refused as `HMCError` where `"A" * 65_000` is
sent.

ADR 0147's `_MAX_UOM_TYPE_LENGTH = 128` does not transfer, and issue #827 says so explicitly. That
number is four times the longest type name in a closed, documented, vendored namespace, and it
exists partly because a type segment also reaches the `Accept` header. These two values reach one
destination each and neither has an enumerable namespace: a `property_value` is an operator's own
resource name and a `console_uuid` is HMC-minted. The number here has to come from the wire.

## Decision

**A caller-supplied value percent-encoded into a uom path is refused above
`_MAX_UOM_PATH_VALUE_LENGTH = 256` characters, by a shared predicate in `client_contracts.py`,
before the value is encoded and before any request is built.**

```python
_MAX_UOM_PATH_VALUE_LENGTH = 256


def _reject_over_long_path_value(argument: str, value: str) -> None:
    if len(value) > _MAX_UOM_PATH_VALUE_LENGTH:
        raise ValueError(
            f"{argument} is {len(value)} characters; maximum is "
            f"{_MAX_UOM_PATH_VALUE_LENGTH}"
        )
```

**The number is derived from a wire budget, not fitted to observed values.** What a bound on input
actually controls is the encoded contribution, so the budget is chosen first and then divided by
the worst-case expansion. nginx documents `large_client_header_buffers 4 8k` as its default and
returns 414 for a request line exceeding one buffer, which is the most restrictive request-line
limit this repository can name. Half of that buffer — 4,096 characters — is the most any one
caller-supplied value may contribute: a per-value share, not a budget for the whole line, which
stays caller-length-dependent while `property_name` and `user_profile_uuid` are unbounded (see
Consequences). 3 KiB is the round figure under that share, and divided by the twelve characters an
input character can become, it gives 256.

The two edges are wide apart and 256 sits between them with margin on both sides. Below: the
longest value this repository passes to either argument is 36 characters, a UUID; the longest
`property_value` literal under `src/` or `tests/` is 10 — `"web server"`, at
`tests/unit/test_client.py:846`. Above: the first length httpx refuses on the path `search_uom`
builds is 5,458 in the worst case. 256 is seven times the longest known legitimate value and a
twentieth of the existing ceiling.

**`ValueError`, per argument, before encoding.** That is the family `_reject_unknown_uom_type` and
`validate_adapter_type` belong to (ADR 0143), and ADR 0148 draws the line explicitly: a
per-argument validator called where the segment is built raises `ValueError`, while a guard
running *at* the waist raises `HMCError` because it holds a path it did not build. This runs where
the segment is built, so it can name the argument — the thing the existing ceiling cannot do.

**The message names the argument and both lengths, never the value.** The rule
`_reject_unknown_uom_type` already follows, for the same reason: on the CLI and API paths these
values carry an operator's own strings. The wording is the one already used at
`src/hmc_mcp/config.py:60` and `src/hmc_mcp/ssh/lpar.py:33` for `agent_id` and `caller_token`.

**In `search_uom` the check sits at the top of the body, beside `_reject_unknown_uom_type`, not
beside the `quote` call it protects.** `validate=True` performs a discovery request between the
two positions, so checking at the encoding site would let an over-long value pay a network round
trip before being refused. Cheap local argument checks go together, ahead of the I/O.

## Consequences

- An over-long `property_value`, or an over-long `console_uuid` reaching any of the seven
  `UsersMixin` methods that build a path through `_child_path`, is refused in this process with a
  `ValueError` naming the argument. Both were built and sent before, up to httpx's ceiling.
- **`console_uuid` is bounded on seven of the nine `UsersMixin` entry points that take it.**
  `get_remote_access` (`client_users.py:113`) and `configure_remote_access` (`:134`) build
  `/rest/api/uom/ManagementConsole/{console_path_id}?group=RemoteAccess` with their own
  `quote(console_uuid, safe="")` instead of calling `_child_path`, so this change does not reach
  them. Stated rather than left implicit: they are in the file being edited and carry the same
  argument name. Routing them through the bound is a follow-up candidate — issue #827's criteria
  name `_child_path`, and those two sites build a different path with a query string on it.
- **The accepted input length becomes a constant.** It no longer depends on how many UTF-8 bytes
  the caller's characters occupy, and the encoded contribution of either argument is capped at
  3,072 characters whatever was passed.
- No wire-format change for any value this repository passes, and no existing test input is newly
  refused: the longest relevant literal under `src/` or `tests/` is 10 characters.
- A legitimate resource name longer than 256 characters would now be refused locally. This is the
  same accepted unknown ADR 0143 and ADR 0147 record for the type grammar, with the same remedy —
  widen one constant in one place, with the refused name as the evidence.
- **On one path that remedy has no evidence to act on, because the refusal is swallowed.**
  `list_managed_systems`' firmware fallback re-resolves each HMC-supplied name through
  `find_system_by_name` inside `except (HMCError, ValueError): continue`
  (`client_systems.py:124-126`), a handler written for the per-system null-property 500. A name
  over the bound now takes that branch, so the system is dropped from the returned list with no
  error and no log record, where before it was encoded and sent. The trigger is a managed-system
  name longer than 256 characters, which is inferred rather than constructed — no HMC observed
  here produces one — so this is a latent trap rather than a live defect, and narrowing that
  handler to `HMCError` alone is a follow-up candidate outside this change's surface. The sibling
  fallback in `get_managed_system` (`:161-162`) does not have the problem: it binds the same
  exception as `fallback_exc` and chains it into the `HMCError` it raises, so the refusal stays
  attributable there.
- **This bounds two arguments, not the paths they sit in.** `search_uom` encodes `property_name`
  on the line above and does not bound it, so the search path's total length is still a function
  of caller input. That is deliberate: ADR 0141 already checks a property *name* against the HMC's
  own per-type list under `validate=True`, and ADR 0146 declined a length bound for the same
  argument in `get_quick_property` on reasoning this change does not revisit. Recorded as a
  follow-up candidate, unowned, rather than closed here.
- Two further sites in the same defect family are unowned follow-up candidates, both outside issue
  #827's named arguments: `client_users.get_hmc_user` and its siblings encode
  `user_profile_uuid` without bounding it, and `operations/updates/service.py` encodes its own
  `console_uuid` without reaching `_child_path` at all.
- `client_contracts.py` gains a second private predicate beside `_reject_unknown_uom_type` and
  stays a leaf module — stdlib, `httpx`, `..config` — so no import cycle is introduced (ADR 0147).
- The bound says nothing about UUID shape. `console_uuid` may still be any string of 256
  characters or fewer; shape validation is owned by #262/#278.

## Considered & rejected

- **Reuse `_MAX_UOM_TYPE_LENGTH = 128`.** verified: issue #827 forecloses it — that number is four
  times the longest name in a closed vendored namespace and is partly justified by the `Accept`
  header, which neither of these values reaches. judgment: it would import an argument that does
  not apply and leave the real one unwritten.
- **Bound the encoded result instead of the input.** verified: `len(quote(v, safe=""))` is exactly
  what reaches the URL, so the check would be tight rather than worst-case. judgment: the message
  would then name a number the caller cannot map to anything they typed — "maximum is 3072" for a
  value they wrote as 256 characters — and the encoding would have to run before the refusal,
  which is the work the refusal exists to avoid.
- **Rely on httpx's `MAX_URL_LENGTH` and ADR 0148's translation.** verified: it is real and it
  fires — `"\U0001F600" * 6_000` is already an `HMCError`. judgment: it fires at 64 KiB after the
  value was assembled, at an input length that varies twelvefold with the caller's characters, and
  ADR 0148 records that its message names no argument. A backstop against the transport is not a
  contract with the caller.
- **Bound at 64, matching `agent_id`, `caller_token` and `_MAX_REPORTED_NAME_LENGTH`.** verified:
  those are identifiers this client mints or truncates for display; `_MAX_REPORTED_NAME_LENGTH` is
  a display bound, and ADR 0147 records why an acceptance bound should not be coupled to one.
  judgment: too close to the unknown edge — an operator's managed-system or partition name has no
  authoritative maximum available here, and refusing a real one is the expensive direction.
- **Bound at the first length httpx actually refuses.** verified: measured by binary search on the
  path `search_uom("ManagedSystem", "SystemName", ...)` builds, that is 5,458 astral characters —
  but it is not a ceiling to bound at, because the figure moves with the resource type and
  property name sharing the request line, and a different type gives a different number. judgment:
  a bound that has to be recomputed per call site is not a bound; it would also make the local
  refusal a restatement of the library's, keep a 64 KiB URL buildable from ASCII input, and leave
  nothing for a stricter server.
- **One constant per argument.** verified: they have different natures — a UUID is 36 characters,
  a resource name is open-ended. judgment: issue #827's own Scope calls them one defect family and
  says splitting them "would produce two ADRs arguing the same point"; two constants would be two
  numbers with one argument behind them, drifting apart for no reason either could state.
- **Put the check inside `_reject_unknown_uom_type`, as ADR 0147 put the type's length bound.**
  verified: that predicate's contract is the type-name grammar, and its message describes one;
  `property_value` is not a type and has no grammar here. judgment: it would make one predicate
  answer two unrelated questions and report both through a message about resource type names.
- **Also bound `search_uom`'s `property_name` while editing the same two lines.** verified: it is
  unbounded and it is the adjacent line. judgment: issue #827 names two arguments and the frozen
  scope carries them; ADR 0146 declined a bound for this same argument in `get_quick_property` six
  records ago, so widening it here would overturn an accepted decision while editing something
  else. Recorded as a follow-up candidate above.
- **Widen the `tests/unit/test_request_path_safety.py` AST walk to require a bound at every
  encoded site.** verified: `_ENCODED_SEGMENT_ARGUMENTS` holds `encoded_property` and
  `encoded_value`, and `get_quick_property`'s `encoded_property` is deliberately unbounded under
  ADR 0146 — so the assertion would fail on an accepted decision. judgment: a structural check
  cannot be written until the sites it walks agree, which is the follow-up above, not this change.
