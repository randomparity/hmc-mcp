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
- `connections` is enforced when a policy is selected and every reported tool that *routes*
  a connection carries the dispatch wrapper. A tool declaring no connection argument opens
  no HMC connection, so this dimension has nothing to say about it — ADR 0037 already
  records both such tools as local-only by construction.
- `targets` is enforced when a policy is selected and no reported tool escapes a target
  constraint a grant declares. That is *not* the same question as the connection one, and
  an earlier draft of this record that decided the two together was wrong: `authorized`
  keys its wrapper on the connection argument alone, so a tool declaring none registers
  unwrapped and no target check runs for it, while the connection dimension is genuinely
  vacuous for that same tool. `target_scope.targets_permitted` says when the skipped check
  would have decided something — it denies a non-exhaustive tool under a `targets` table,
  and it denies any tool whose extracted selector value is unreadable even under
  `all-targets` — so an unwrapped tool costs this label when it declares selectors, or when
  a table grant reaches it.

  > **Amended by #297** (2026-08-19). **The rule stated above is right; the mechanism it was
  > written against is gone, and the implementation that read `policy` no longer exists.**
  > `authorized` wraps every tool since #297, so nothing escapes the target check and the
  > question reduces to the same one `connections` asks — does every reported tool carry the
  > wrapper — with no exemption, because a tool that opens no connection still acts on
  > resources a table either can or cannot bound. `_targets_enforced` therefore no longer
  > takes the policy and no longer inspects a grant's targets to decide the label. The rule
  > and the connection rule still differ, and the difference is still the one this record
  > found: `_connections_enforced` skips a tool declaring no connection argument and
  > `_targets_enforced` does not, so an unwrapped connection-less tool costs the target label
  > alone. `tests/app/test_capability_ceiling.py` pins that with a direct `describe` call —
  > no composed application reaches the state any more.

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

**A name the authoritative index does not carry withholds both dispatch labels**, since it
could need the wrapper and be missing it with nothing here able to tell — the same
fail-closed default `_permission` already applies to `exhaustive_targets`.

**A tool declaring target selectors but no connection argument withholds the target
label.** No such tool is in the index today and a guard test pins that; the branch is what
keeps the label honest if one is ever added.

> **Amended by #297** (2026-08-19). **This paragraph and its guard test are removed, because the
> state they defend against is no longer dangerous.** Such a tool is wrapped now and its selectors
> *are* checked at dispatch, so declaring selectors without a connection argument costs nothing and
> needs no special branch — the surviving rule, "an unwrapped tool withholds the target label",
> subsumes it. The guard test in `tests/app/test_capability_ceiling.py` went with it rather than
> being left asserting an index property that no longer guards anything.

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
- **A table-only policy reports `targets` as declared-only, because it is — and the
  underlying enforcement gap is made visible here, not closed.** Writing this record's
  target rule surfaced a live gap in ADR 0039's design: `hmc_effective_permissions` and
  `hmc_list_configured_hosts` declare no connection argument, so they register unwrapped
  and no target check runs for them, while `targets_permitted` would deny both under a
  `targets` table since neither is `exhaustive_targets`. Reproduced: a read grant carrying
  `targets = {lpar = ["db-01"]}` composes an application on which both tools are called
  successfully, disclosing the policy and every configured host and user to a client whose
  operator believed the policy bounded to one LPAR. This entry changes no enforcement path
  — it stops the report claiming the target dimension is enforced in that state, which is
  the whole of what #254 owns. The gap itself is ADR 0039's and is tracked as #297, which
  the test pinning the permit above gives a witness already in the suite.

  > **Amended by #297** (2026-08-19). **The consequence above no longer holds: a table-only
  > policy reports `targets` as *enforced*, and the gap it made visible is closed rather than
  > only reported.** `authorized` wraps every tool, so `targets_permitted`'s refusal reaches the
  > two connection-less tools and the table denies them — which is what this bullet said it
  > should do. The witness test flipped with it: it asserted the permit and now asserts the
  > denial. What this entry claimed for itself is unchanged and was correct while it stood — it
  > described the state honestly rather than closing it, and the honest description is what made
  > the gap findable. The one reading to retire is "a table-only policy reports `targets` as
  > declared-only": that state is now unreachable through `create_mcp`.
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
- **Decide `connections` and `targets` together, on the presence of the one wrapper that
  runs both checks.** This record's first draft did exactly that, and it was wrong in a
  state a shipped policy reaches: the two connection-less tools make the connection
  dimension vacuous and the target dimension unenforced *at the same time*, so one answer
  cannot be right for both. Rejected on the evidence above.

  > **Amended by #297** (2026-08-19). The witness changed and the rejection stands. No shipped
  > policy reaches that state now — both tools are wrapped — but the two questions still differ,
  > because `_connections_enforced` exempts a tool declaring no connection argument and
  > `_targets_enforced` exempts nothing. An unwrapped connection-less tool, which only a direct
  > caller of `describe` can now produce, still separates the two answers.
- **Close the table-grant gap here** by wrapping every tool rather than only the
  connection-bearing ones. Rejected as out of #254's scope and as a change to an
  enforcement path, not a report: it would alter which calls are denied under existing
  policies, which belongs in an entry that can price that against ADR 0039's decision to
  key the wrapper on the connection argument. Reporting the state honestly is what makes
  the gap findable in the meantime.

  > **Taken by #297** (2026-08-19). Deferred rather than refused, and this is where it landed:
  > ADR 0039's amendment prices the change against its own decision, as this bullet asked. The
  > rejection's reasoning is unchanged — it was about scope, not about the mechanism.
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
