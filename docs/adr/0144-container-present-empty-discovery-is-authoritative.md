# ADR 0144: A container-present discovery answer with no name element is a fact about the type

## Status

Accepted (2026-09-15). Amends ADR 0141 in part and ADR 0142 in part: the clause each carries
reading *every* empty discovery answer as "the names are unknown". The opt-in default, the cache
key and lifetime, the parse, and the degradation on a failed read are unchanged in both.

## Context

Issue #812. `search_uom(..., validate=True)` and `get_quick_property(..., validate=True)` read the
type's names once per client and then check the caller's name against them. Both caches collapse
the read with `frozenset(names) if names else None`, and `None` means "do not validate". So the
pre-flight is inert on exactly the types where a local refusal would be certain rather than
probabilistic: **six of the eleven types captured at `V1_17_0` and `V1_20_0` define no search
parameters** (ADR 0142 — `ManagementConsole`, `VirtualSwitch`, `VirtualNetwork`, `NetworkBridge`,
`LogicalUnit`, `SharedProcessorPool`), and for all six the round trip that pre-flight exists to
save is captured as an HTTP **500**, not a 400.

The parse already draws the distinction the cache throws away. `list_search_parameters` (ADR 0142)
and, since #811, `list_quick_properties` both consult a container element when no name is found:
container present with no names is a type defining none and returns `([], version)`; container
absent is the HMC's known 200-with-`HttpErrorResponse` shape and still raises. Both then hand the
caller a `[]` that a 204 also produces, and the cache cannot tell them apart.

ADR 0141 and ADR 0142 declined to trust the empty answer because an empty *positive* set refuses
every name for the client's lifetime, and because on an unmeasured level an empty set may be a
parse artefact rather than a fact. The capture is what makes the first two cases separable. The
third — a level that keeps the container and moves the names — remains unmeasured, and is the
risk this record decides about rather than resolves.

## Decision

**An empty answer that carries the container and no name element at all is a fact about the type.
Every other empty answer stays unknown.**

Both discovery reads return `tuple[list[str] | None, str | None]`. `None` in the first position
means the level's answer is not a fact about the type; a list — empty or not — means it is:

| Answer | Returned | Cached |
|---|---|---|
| 200, one or more non-empty names | `(names, version)` | `frozenset(names)` |
| 200, container, **no name element** | `([], version)` | `frozenset()` |
| 200, container, name elements all empty | `(None, version)` | `None` |
| 204 | `(None, version)` | `None` |
| 200, no usable name and no container; other status; transport failure | raises `HMCError` | `None` |

The container is consulted only when no usable name was found, so a body carrying names is
returned whether or not the container is present — row 1 does not depend on it.

The third row is the narrowing that keeps this from being a blanket trust in emptiness. A body
carrying `<ParameterName/>` or `<Nickname/>` elements whose texts are all empty has parameters
this parse cannot name — which is the parse-artefact shape, not the captured one — so it reads as
unknown. `xmlutil.find_all_text` returns one entry per matching element, so the count of elements
is already available beside the count of usable names.

Both caches then store `None` only for an unknown answer, and `validate=True` refuses locally on a
type whose positive set is empty. Its message says so instead of rendering `_summarize_names`'
bare `"."`: `<Type> defines no search parameter named 'X'. The type defines none at all.`
`_summarize_names` keeps its non-empty precondition, now held because each caller branches on the
empty set before calling it rather than because an empty set is unreachable.

The quick twin takes the identical contract. No level has been observed answering the `/quick`
anchor with the container and no `QuickProperty`; what this record decides for that twin is how
such an answer is *read*, not that any level produces one.

## Consequences

**`validate=True` gains a new way to refuse everything, and it is accepted here.** A firmware
level that keeps the container and nests its names under a different element is parsed as a type
defining nothing, cached as an empty positive set, and refuses every name for that client's
lifetime. Today that level degrades to unvalidated behaviour instead. The acceptance rests on
three bounds and on no firmware claim:

- **Opt-in.** `validate` defaults to `False`, and no in-repo call site of `search_uom` or
  `get_quick_property` passes `True`. Verified at `fc43e3e7`: the two `validate=True` occurrences
  under `src/` are `_submit_migration_job`'s unrelated LPM flag
  (`src/hmc_mcp/operations/lpar/migration.py:347,388`). The default is the escape, and it is the
  same escape ADR 0141 and ADR 0142 already rely on for the wrong-set class.
- **Lifetime.** `_app.with_client` builds one `HMCClient` per MCP tool call through the single
  factory in `client_factory.py`, so "for the client's lifetime" is one call in the MCP and CLI
  deployments. The actor who pays the full lifetime is a library caller holding a client across
  several reads — which is the only actor the opt-in was designed for.
- **Diagnosability.** The refusal is local, immediate, and names the condition in the message, so
  a wrong empty set surfaces as a `ValueError` a reader can act on. The failure it replaces is a
  per-call HTTP 500 from the HMC on the majority of captured types.

**What is not claimed.** No capture covers a level that nests names differently, and this record
does not assert that none exists. The unknown is unchanged by this change; only its direction is.
The remedy if it bites is `validate=False` at the call site, then one parse constant.

Both empty-answer clauses this record amends stay correct for the answers they still govern: a
204 and a failed read are still unknown, and ADR 0142's `ManagementConsole` sentence is the case
that moves.

`list_search_parameters` and `list_quick_properties` change return type. Neither is on ADR 0029's
lifecycle allowlist that ADR 0118 retains — `__init__`, `__aenter__`, `__aexit__`,
`is_logged_on`, `logon`, `logoff` — so both are unsupported generic UOM helpers, and neither has a
caller in `src/` outside `core.py`. No compatibility path is retained, and `CHANGELOG.md`'s
Unreleased entries for both are rewritten in place rather than given a `Changed` note, because the
methods have never shipped.

Three merged records carry empty-answer clauses this decision falsifies, and each is struck
through in place with a pointer here rather than left standing: the #789 spec's *wrong-set* and
*performs no check* failure-model entries, the #799 spec's *fewer names* entry, and the #788
spec's declared return type.

## Considered & rejected

- **Keep failing open — close the issue with no code change.** verified: the pre-flight is inert
  on six of the eleven types captured at `V1_17_0` and `V1_20_0` (ADR 0142), and the round trip it
  fails to save is captured as a 500. judgment: an opt-in check that does nothing on the majority
  of sampled types is worse than no check, because the caller paid a discovery request for it.
- **Trust any nameless 200 carrying the container, including one whose name elements are all
  empty.** verified: `xmlutil.find_all_text` (`src/hmc_mcp/xmlutil.py:308-318`) returns `""` for a
  matching element with no text, so the two shapes are already distinguishable at no parse cost.
  judgment: a container holding unnamed parameters is the parse-artefact shape this decision's
  accepted risk is about; reading it as "defines nothing" would widen that risk for nothing.
- **Make the search twin authoritative and leave the quick twin failing open**, since no level has
  been seen answering `/quick` with an empty collection. verified: #811 added the quick container
  discrimination for exactly this case, and the issue asks for both surfaces. judgment: the
  decision is how to read an answer, not a claim that one occurs; splitting it leaves two twins
  with different contracts and a reader no way to tell which is which.
- **Re-read discovery per call so a wrong empty set self-corrects.** verified: ADR 0141 and
  ADR 0142 both bound validation's cost at one extra request per type per client, and
  `_app.with_client` makes that one request per tool call. judgment: re-reading spends that bound
  on every call to soften a failure the `False` default already escapes.
- **Invalidate the empty positive set after N local refusals.** judgment: state that changes under
  the caller's own traffic, to make a wrong answer eventually right — more machinery than the flag
  that turns the check off, and it makes the refusal non-deterministic.
- **Keep `list_*` returning `([], version)` and add a private discriminated read for the caches
  only.** verified: neither method is on ADR 0029's lifecycle allowlist that ADR 0118 retains, and
  neither has a caller in `src/` outside `core.py`, so nothing needs the old shape. judgment: it
  keeps a public method that
  discards the distinction at the return boundary — the defect the issue names — and pays a
  wrapper per twin to do it.
- **Return a small result type or a third tuple element instead of `None`.** judgment: a third
  element every caller destructures for one bit, or a class for a two-state answer; `None` beside
  a list is the shape this module already uses for "the HMC said nothing" and reads the same way
  at every call site.
