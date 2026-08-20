# 0048 — Qualify the server instructions with the tools the ceiling withholds

## Status

Accepted (2026-08-19)

## Context

`server.create_mcp(policy)` filters tool *registration* by the capability ceiling
(ADR 0037), so `tools/list` is exactly what the policy admits. It builds that registry on
`_app.create_mcp()`, whose `FastMCP(instructions=...)` string was fixed at module import
and shipped whole to every client in the `initialize` result, unfiltered. The prose
recommends specific tools by name — "## Composite tools — prefer these for common tasks",
"## Recommended workflows" — so under a narrow ceiling the server's own self-description
directs a client at tools it has just withheld.

**Reproduced before this record was written**, against `main` at `4a633a9`, on one
composed application read through one `fastmcp` client session so that the instructions
and the tool list come from the same `initialize` exchange:

```
policy grant:   tools = ["hmc_list_systems"], connections = ["lab"], targets = "all-targets"
tools/list:     ["hmc_list_systems"]                                  (1 tool)
instructions:   5314 characters, naming 18 tools in the authoritative index
withheld and still recommended:                                       17 of those 18
hmc_effective_permissions registered:                                 False
```

Three measurements from that run matter for the decision, and two of them correct the
issue's account:

- **#255 names 8 tools; 18 indexed names are actually recommended.** The eight it lists
  are the bullet headings of the composite section. Counting every mention: 12 names are
  first recommended inside "## Composite tools" — the four extra ones
  (`hmc_list_adapters`, `hmc_list_lpars`, `hmc_list_systems`, `hmc_list_vios`) appear in
  the "Use instead of X + Y" cross-references inside the bullets, not as bullets — and 6
  more are first recommended *outside* that section entirely: `hmc_get_job`,
  `hmc_migrate_lpar` and `hmc_wait_for_job` under "## Recommended workflows", and
  `hmc_deploy_partition_template`, `hmc_get_lpar_description` and
  `hmc_set_lpar_description` under "## Multi-agent ownership protocol".
- **`hmc_effective_permissions` is withheld by this policy**, confirming the issue's
  reading that a `tools`-only grant leaves a client with the instructions as its only
  self-description.
- **No shipped policy shape triggers the defect for free.** The legacy-equivalent policy
  and every grant of the `read` effect class reach every tool the composite section
  recommends; under the legacy policy the withheld-and-recommended set is empty.

No security boundary is crossed. The withheld tools stay absent from `tools/list` and
undispatchable, which is what ADR 0037 guarantees and what
`test_a_withheld_tool_cannot_be_called_by_name` pins. This is a self-description defect,
and it is the same defect ADR 0047 recorded for a different self-description: a report
about the server's own limits derived from something other than the limits themselves.
ADR 0047 fixed that by deciding each claim on evidence about the thing being claimed. This
record applies the same rule to the instructions — the correction is derived from the
ceiling and from the prose, not restated beside either.

## Decision

**The instructions are qualified at composition by a suffix naming the recommended tools
the ceiling withholds, and are returned byte-identical when it withholds none.**

`_app` gains `INSTRUCTIONS` (the prose, unchanged) and
`ceiling_aware_instructions(permits, index)`; `_app.create_mcp` takes the string to ship,
defaulting to `INSTRUCTIONS`. `server.create_mcp` passes
`ceiling_aware_instructions(permits, TOOL_SECURITY)`. The composition is decided in
`server.py` because that is the only place holding both the policy and the authoritative
tool index; `_app` owns the text because a correction and the prose it corrects drift the
moment they live apart.

**The withheld set is read off the prose, intersected with the authoritative index.** A
`\bhmc_[a-z0-9_]+\b` scan over `INSTRUCTIONS`, intersected with the `TOOL_SECURITY` keys,
then filtered by `permits`. Two properties follow, and both were the point:

- Rewriting the prose cannot leave a hand-maintained roster stale, which is the failure
  mode of every list-beside-the-text arrangement. It also catches the 6 names outside the
  composite section that a curated list assembled from #255 would have missed.
- The intersection is load-bearing rather than defensive: the conventions block names
  `hmc_timeout_minutes`, a configuration setting that matches the same pattern. Reporting
  it as a withheld *tool* would send a client looking for something that never existed.

**The suffix names the withheld tools inline, and points at `tools/list`.** A client
reading only the instructions gets the correction without a second call. `tools/list` is
named as the authoritative set because it is the one self-description no policy can
withhold — every MCP client already has it, and it is what ADR 0037 makes exact.

**`hmc_effective_permissions` is recommended only when the ceiling grants it.** It is the
tool that explains *why* a name is missing, which `tools/list` cannot, so the suffix adds
one sentence pointing at it — but only when `permits` admits it. The policy shape #255
identifies as worst is exactly the one that withholds it, so a remedy that recommended it
unconditionally would reproduce this very defect inside its own fix.

**The gate is the ceiling predicate, not the served registry.** `permits` is what
`create_mcp` filters registration by, and the registry does not exist yet when the
instructions must be fixed. The two diverge only for `hmc_run_command`, which needs
`--enable-arbitrary-command` in addition to the grant. The prose never names it, and the
divergence can only under-report a withheld name, never invent one.

**The correction is appended, not prepended.** Considered and taken deliberately: the
whole string is delivered as one blob in `initialize`, so the correction is in context
either way, and appending leaves the prose a reader of the repository can still diff
against its own history. A `##` heading is used so a client rendering the markdown gives
it the same weight as the sections it corrects.

## Consequences

- **Every deployment that grants the `read` effect class or more sees no change at all.**
  Measured on the reproduction: legacy-equivalent policy, 5314 characters, no suffix,
  identical to the string shipped before this record.
- **A narrow ceiling pays for the correction in tokens.** The `tools`-only policy above
  goes from 5314 to 5987 characters (17 names); a `read`-only policy goes to 5824 (5
  names: `hmc_create_lpar`, `hmc_deploy_partition_template`, `hmc_migrate_lpar`,
  `hmc_provision_lpar`, `hmc_set_lpar_description`). The suffix is bounded above by the
  18 names the prose can mention, so it cannot grow with the tool count.
- **The suffix corrects the prose and is silent about the rest of the ceiling.** A policy
  withholding `hmc_delete_lpar` produces no suffix, because nothing in the instructions
  recommended it. Restating the whole ceiling here would duplicate `tools/list` and bury
  the correction it exists to make.
- **`_app.create_mcp` gains one optional parameter and keeps its zero-argument meaning.**
  The three test call sites that use it as an empty base application
  (`tests/unit/test_tool_registry.py`, `tests/app/test_application_boundaries.py`,
  `tests/unit/test_mcp_instructions.py`) are unchanged, and still receive the unqualified
  prose — which is the honest string for an application with no ceiling.
- **A prose rewrite that renames a recommendation must move
  `_RECOMMENDED` in `tests/unit/test_mcp_instructions.py` with it.** That list is
  deliberately independent of the scan, so a scan that stopped matching cannot agree with
  itself; the cost is one list to maintain, paid in a test rather than in production.
- **This changes no enforcement path.** Registration filtering, dispatch authorization,
  and `hmc_effective_permissions` are untouched. The gap ADR 0047 surfaced and #297 tracks
  — connection-less tools registering unwrapped under a `targets` table — is unaffected in
  both directions: it is about which calls are denied, this is about what the server says
  about the calls it never registered.

## Considered & rejected

- **Trim the composite list against the ceiling** (#255's second suggestion). Rejected on
  the measurement above, not on principle: 6 of the 18 recommended names are first
  mentioned outside the composite section, and 4 more appear inside the composite bullets
  as "Use instead of `hmc_list_lpars` + `hmc_list_adapters`" cross-references rather than
  as bullets. Trimming the list of bullets would therefore leave 10 of 17 misdirections in
  place under the reproduced policy, and correcting the rest is sentence surgery on
  narrative prose, not list filtering. It also buys the coupling #255 warns about —
  registry-conditional prose — for a partial fix.
- **A fully policy-templated instructions block**, assembled per policy from per-tool
  fragments. Rejected for the reason #255 gives and one more it does not: the prose is
  narrative (workflows are ordered chains, the ownership protocol is a procedure), so
  templating it means every tool owning a fragment of several unrelated paragraphs, and a
  ceiling that removes one link would produce a workflow chain with a hole rather than a
  shorter chain. The machinery exceeds the confusion it removes.
- **A generic suffix — "some tools named above may not be exposed; call
  `hmc_effective_permissions` or read `tools/list`"** — which is close to the wording #255
  proposes. Rejected because it fails the case the issue itself identifies as worst: under
  a `tools`-only policy `hmc_effective_permissions` is withheld (verified above), so half
  the advice is dead, and a client told only that "some" tools may be missing must
  cross-check 18 names against `tools/list` by hand. Naming the withheld tools costs at
  most a few hundred characters and removes the cross-check.
- **Maintain the recommended-tool names as a constant beside the prose** instead of
  scanning it. Rejected: it is the arrangement that produced this defect one level up —
  two things that must agree, with nothing making them agree. The scan has one real cost,
  the false positive on `hmc_timeout_minutes`, and the index intersection removes it.
- **Rewrite the prose to stop naming tools**, leaving the instructions ceiling-independent.
  Rejected: the names are the value. "Prefer `hmc_lpar_summary` over `hmc_list_lpars` +
  `hmc_list_adapters`" is guidance a client can act on; the same sentence without names is
  not. Removing them would degrade every deployment to fix the narrow ones.
- **Mutate `application.instructions` after composition** rather than passing the string
  in. Rejected as a second way to set a value that has a first way — and it would leave a
  window in which the composed application carries the wrong self-description, which is
  the class of defect this record is closing.
- **Filter against the served registry rather than the ceiling predicate**, by composing
  first and setting the instructions from `list_tools()`. Marked as considered but
  **unverified**: it would be more exact for `hmc_run_command` under
  `--enable-arbitrary-command`, and it was not built or measured. It is rejected on
  ordering rather than on accuracy — `configure_arbitrary_command_tool` runs after
  `create_mcp` returns, in `_serve_application`, so the registry is still not final at the
  point the instructions are fixed, and chasing it would mean re-setting the string after
  the toggle. The prose names no arbitrary-command tool, so the exactness buys nothing
  today.
