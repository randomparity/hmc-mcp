# 0047 — Label each policy dimension from evidence about that dimension

## Status

Accepted (2026-08-19)

## Context

`hmc_effective_permissions` reports two tuples that tell a caller what the selected
access policy actually does: `enforced_dimensions`, the dimensions that constrain calls
to this server, and `declared_only_dimensions`, the dimensions the report enumerates in
`declared_grants` but that constrain nothing. ADR 0037 fixed both as derivations of one
boolean, `ceiling_enforced` — a policy is selected *and* every registered name satisfies
its ceiling — and requirement R16 of
`docs/workflow/specs/2026-08-18-capability-ceiling-design.md` froze that: "both are `()`
otherwise."

That derivation was already wrong when it was written, and became more wrong as the other
two dimensions were enforced. `ceiling_enforced` re-checks the **tool** dimension only.
ADR 0038 and ADR 0039 then put connection and target scope on the dispatch wrapper
`tool_registry.authorized` builds, whose presence is a property of the registered callable
and has nothing to do with whether the registry stayed inside its ceiling. So one boolean
about tools decides three labels, and it misreports in both directions.

The state is reachable by construction, and was reproduced before this record was written.
`configure_arbitrary_command_tool(True, app, permits=<a wider policy>, ...)` registers
`hmc_run_command` on an application composed from a narrower one. Against `main` at
`c5d0b58` that produced:

```
policy_name:               "test"
ceiling_enforced:          false
enforced_dimensions:       []
declared_only_dimensions:  []
declared_grants:           [ ... connections: ["<default>"], all_targets: true ... ]
```

while a live `hmc_list_systems(profile="prod")` call against the same application was
denied — `ConnectionScopeError: ... is not permitted on connection 'prod' by access policy
'test'`. All three dimensions vanished from a report that was still enumerating the
connections and targets they name, and the one field whose job is to say "this constrains
nothing" fell silent in the one state where a reader most needs it. The natural reading of
an empty `declared_only_dimensions` is "no dimension is merely declared", which here
inverts the truth about `tools` and understates enforcement on `connections` and `targets`.

Issue #254 proposed making `declared_only_dimensions` unconditional whenever a policy is
selected and leaving `enforced_dimensions` gated on `ceiling_enforced`. That proposal was
written while the module still held `DECLARED_ONLY_DIMENSIONS = ("connections",
"targets")`. Since #223 landed it holds `()`, so the proposal is now a no-op: it changes
no byte of any payload and leaves the misreport above exactly as it is. It is adopted in
its reasoning — a dimension's label is not a fact about the tool ceiling — and replaced in
its mechanism.

## Decision

**Each dimension's label is decided on evidence about that dimension**, checked against
the registry being reported, the way `ceiling_enforced` already checks the tool dimension.

- `tools` is enforced when a policy is selected and every reported name satisfies
  `permits_tool`. Unchanged; `ceiling_enforced` keeps exactly this meaning and stays the
  field that reports it.
- `connections` and `targets` are enforced when a policy is selected and every reported
  tool that needs the dispatch wrapper carries it. They move together and are decided
  together because `authorized` applies **one** wrapper that runs both checks: no registry
  can have one without the other.

**The two tuples partition the three dimensions whenever a policy is selected.** A
dimension appears in exactly one of them, so the report can no longer drop a dimension it
is still enumerating grants for. With no policy selected both are empty — nothing is
declared, so nothing is declared-only either — and that asymmetry is deliberate rather
than a residue of the old encoding.

**The wrapper is witnessed by a marker `authorized` sets, not by inference.**
`tool_registry` gains `is_authorized_wrapper(handler)`, reading an attribute the wrapper
sets on itself after `functools.wraps` has run. It lives beside `authorized` because a
wrapper and its recogniser drift the moment they live apart. `__wrapped__` alone is not
the witness: `functools.wraps` sets it, so any decorator forges it.

**`describe` takes the registry's callables, not just its names.** Its first parameter
becomes a `Mapping[str, object]` of name to registered callable. The callable is the only
evidence that the connection and target checks are applied at all, so a signature that
could not see it could not answer the question this record is about. The handler reads
`getattr(tool, "fn", None)` from the local provider: `fastmcp` declares `fn` on
`FunctionTool` and not on the `Tool` base the provider is typed to return, and a
registration carrying no callable cannot witness the wrapper.

**Two states withhold the connection and target claim**, both fail-closed:

- a name the authoritative index does not carry, which could need the wrapper and be
  missing it with nothing here able to tell — the same default `_permission` already
  applies to `exhaustive_targets`;
- a tool declaring target selectors but **no** connection argument, which would register
  unwrapped because `authorized` keys the wrapper on the connection argument alone. No such
  tool is in the index today and a guard test pins that, but the branch is what keeps the
  target label honest if one is ever added.

**ADR 0037 and R16 are amended in place, and say they were wrong.** Both are accepted,
non-superseded records that describe the defective encoding as the intended one. Their
paragraphs are replaced by an amendment note naming this record, rather than quietly
rewritten: a reader arriving at ADR 0037's dimension paragraph from ADR 0038's residual or
from #254 needs to find the correction where the error was, not to infer it from silence.
Nothing else in either record changes, and neither is superseded — ADR 0037's ceiling,
registration gate, warning placement, and disclosure argument all still govern.

## Consequences

- **The drifted registry now reports `enforced_dimensions: ["connections", "targets"]`
  with `declared_only_dimensions: ["tools"]`.** That is a payload change for any caller
  reading these fields in that state. No shipped caller produces the state — it needs a
  direct call to `configure_arbitrary_command_tool` with a `permits` wider than the
  composed policy — and every composed application inside its ceiling reports all three
  enforced, as before.
- **`declared_only_dimensions` acquires a second meaning, and the field name is now
  slightly narrow for it.** It held "the build records this dimension but never applies
  it", the state #222 and #223 emptied. It now also holds "this registry escaped a
  dimension the build does enforce" — which is what `tools` under drift is. Both are the
  same answer to the question the field is asked (*does this constrain calls to this
  server?*), and one tuple answering it for every dimension was judged better than a third
  field splitting the reasons. A reader who needs the reason has `ceiling_enforced`.
- **ADR 0038's unsafe direction is closed for `describe`, and narrowed rather than closed
  overall.** ADR 0038 recorded that a caller passing `permits` but omitting `authorize`
  would keep `ceiling_enforced` true and so claim connection enforcement over an
  application whose connection-bearing tools were unwrapped. `describe` no longer makes
  that claim: it reads the callables. What remains is that the witness proves *an*
  authorizer is applied, not that the authorizer was derived from the policy the report
  names. The reproduction above is exactly that gap — `hmc_run_command` is wrapped with an
  authorizer built from the wider policy, and the report names the narrower one. Closing it
  would require the authorizer to carry its policy identity, which is not decided here.
  ADR 0041 already made `authorize` a required argument at both registration sites, so no
  composed application reaches the unwrapped state at all; the residual binds a direct
  caller who builds an authorizer from the wrong policy.
- **An unindexed registered name now costs the connection and target labels too.** It
  already cost `ceiling_enforced`, since no policy permits a name it has never seen, so the
  report for such a registry goes from "tools unenforced" to "nothing enforced". That is
  the honest reading and it is deliberately conservative: the tool that describes the
  surface should not vouch for a name it cannot classify.
- **`describe`'s signature changed.** It is absent from `api.__all__` (ADR 0029 places the
  server policy boundary outside the supported reusable Python API), so this is not a
  public-contract break; the one production caller and the tests move with it.
- **The report costs one attribute read per registered tool.** `hmc_effective_permissions`
  is a `read` tool called by an operator, not on any dispatch path, and it already walks the
  registry once to classify names.
- No new dependency, no new field, and no change to any enforcement path — `authorized`
  gains one `setattr` on an object it was already constructing.

## Considered & rejected

- **Issue #254's literal proposal: `declared_only_dimensions` unconditional whenever a
  policy is selected, `enforced_dimensions` still gated on `ceiling_enforced`.** Rejected
  because `DECLARED_ONLY_DIMENSIONS` has been `()` since #223, so it is a no-op that leaves
  the reproduced misreport untouched. Its reasoning — that whether a dimension is enforced
  is not a fact about whether one registry drifted — is what this record implements.
- **Gate the connection and target labels on "a policy is selected" alone**, without
  reading the callables. One line, and it fixes the direction #254 reports. Rejected
  because it swaps the safe error for the unsafe one: it would assert connection
  enforcement over a registry with an unwrapped connection-bearing tool, which is the
  claim ADR 0037 wrote `ceiling_enforced` to prevent and which ADR 0038 flagged as open.
  A self-report about a security boundary should not be fixed by moving where it lies.
- **Recognise the wrapper by `__wrapped__` plus a code object named `guarded`**, the shape
  `tests/app/test_connection_authorization.py` already asserts. Rejected for production
  use because it reads an implementation accident — rename the inner function and the
  report silently stops claiming enforcement. The test keeps its own independent
  recogniser deliberately: two derivations mean one defect cannot satisfy both checks,
  the same reason R12 pins the reported set against a policy-derived expectation.
- **Add a third field, splitting "never enforced by this build" from "escaped by this
  registry".** More precise, and it would keep `declared_only_dimensions`' original
  meaning intact. Rejected as unrequested surface for a distinction `ceiling_enforced`
  already carries, on a payload R17 constrains as a closed allowlist — a new field is a new
  thing to justify there, and this one would be empty in every state a shipped deployment
  reaches.
- **Rename `declared_only_dimensions` to match its widened meaning.** Rejected: it is a
  field of a shipped tool's output schema, the rename buys nothing a docstring cannot, and
  ADR 0012's stable-public-contract posture makes a gratuitous output rename expensive.
- **Keep the tuples coupled and fix only the silence, by reporting all three as
  declared-only under drift.** Rejected because it is false in the other direction: the
  connection dimension provably *is* enforcing under drift — the reproduction's live denial
  is the evidence — so labelling it "declared only" understates the server's actual
  constraints and would tell an operator that a `connections` grant they rely on is inert.
- **Supersede ADR 0037 rather than amending it.** Rejected because only one paragraph of
  it is wrong. Its ceiling decision, its registration-gate contract, its warning placement,
  and its disclosure analysis are all still in force, and superseding a record whose
  substance governs would make every reference to it ambiguous.
