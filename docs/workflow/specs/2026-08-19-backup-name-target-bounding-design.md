# `backup_name` and the `UNBOUNDED_ARGUMENTS` line

Issue: [#264](https://github.com/randomparity/hmc-mcp/issues/264)
Decision: [ADR 0044](../../adr/0044-unbounded-arguments-asks-what-a-call-acts-on.md)

The reasoning lives in ADR 0044 and is not restated here — this issue is about
one argument's classification being stated in two places that drifted, so a spec
paraphrasing the record would recreate exactly that. This document holds only
what the ADR does not: the requirements, the change list, and the test plan.

## Goal

Make the rule that decides `exhaustive_targets` say what is actually applied, and
classify `backup_name` under it, so the guardrail comment and the code can be read
together without inferring an unstated rule and neither can drift from the other
unnoticed.

## Requirements

- **R1** The rule text states the distinction actually applied — a resource the
  call *acts on* that the declared selectors do not contain — rather than which
  filesystem the name refers to.
- **R2** `backup_name`'s classification agrees with that rule text.
- **R3** No claim this repository cannot verify is load-bearing in the rule or in
  the classification.
- **R4** A test pins the classification, failing if the declaration flips or the
  classification is removed.
- **R5** No behaviour change: no tool gains or loses a grant, and no argument is
  newly refused.

## Changes

| File | Change |
|---|---|
| `docs/adr/0044-unbounded-arguments-asks-what-a-call-acts-on.md` | The decision record. |
| `tests/app/test_tool_security.py` | Guardrail comment rewritten to the acts-on rule; `backup_name` added to `_PAYLOAD_SOURCE_ARGUMENTS`; `hmc_restore_vios: (True, ["backup_name"])` added to the enumeration test's expected mapping. |
| `src/hmc_mcp/tool_registry.py` | `UNBOUNDED_ARGUMENTS` comment states the membership criterion and points at ADR 0044. |

`UNBOUNDED_ARGUMENTS` itself is unchanged — `backup_name` does not join it — and
no `src/` behaviour changes, which is what R5 means in practice.

## Out of scope, with owners

- Whether `chviosbackup` can address a file outside the declared VIOS's catalog —
  [#283](https://github.com/randomparity/hmc-mcp/issues/283).
- Whether restoring an `ssp`-type entry reaches the cluster, which would make
  `hmc_restore_vios`'s own declaration wrong —
  [#282](https://github.com/randomparity/hmc-mcp/issues/282).
- `file_path`'s classification on the profile pair, settled by ADR 0036 and
  ADR 0039.

## Testing

- `test_payload_source_arguments_are_out_of_the_target_dimension_by_decision`
  gains `hmc_restore_vios: (True, ["backup_name"])`. This is the pin for R4: it
  fails if `exhaustive_targets` flips to `False`, and it fails if `backup_name`
  is removed from `_PAYLOAD_SOURCE_ARGUMENTS`.
- `test_the_declared_set_is_exactly_what_the_check_finds` keeps `hmc_restore_vios`
  out of its expected mapping — the independent assertion that `backup_name` did
  not enter `UNBOUNDED_ARGUMENTS` by another route.
- The existing `assert not (_PAYLOAD_SOURCE_ARGUMENTS & UNBOUNDED_ARGUMENTS)`
  keeps the two sets disjoint, so `backup_name` cannot end up in both.
- Both pins must be watched biting rather than assumed: flip the declaration and
  remove the set member in turn, confirm each reddens a test, and revert.
- The full suite must stay green with no test changed beyond those additions —
  the check on R5, since any behaviour change would show up somewhere else.
