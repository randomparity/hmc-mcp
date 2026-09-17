# ADR 0158: The LPAR power job operation segment is a closed set

## Status

Accepted (2026-09-16)

## Context

ADR 0151 guards the LPM expression of the LogicalPartition `/do/{operation}`
path. Issue #845 covers the other expression in `operations/lpar/core.py`:
`power_lpar` chooses `PowerOn` or `PowerOff` locally, then submits directly.
Neither name is an LPM operation. The gap is latent, not an exploit: no caller
supplies the operation string.

## Decision

**The power operations module owns a separate `_LPAR_POWER_OPERATIONS`
frozenset containing `PowerOn` and `PowerOff`.** `power_lpar` checks membership
immediately after selecting the literal, before constructing XML or the path.
Refusal matches ADR 0151: `ValueError` before submission, naming the sorted
permitted set and not the rejected value, qualified as `LPAR power job operation`.
No operation argument or helper is added to make the local literal testable.

ADR 0151 and its five-member LPM set remain unchanged. Each of the two current
expressions is governed at its build site by the module that owns its operations.

## Consequences

Adding a power operation requires updating its closed set deliberately. Existing
power calls, authorization, already-running behavior, job documents and waiting
are unchanged. Tests remove each selected literal from the set to exercise the
otherwise unreachable refusal without widening the production interface.
Generic `submit_job` policy and the operations-wide AST inventory remain with
the existing client and operations owners, outside this issue.

## Considered & rejected

- **Shared path builder.** judgment: two distinct closed namespaces need separate
  policies regardless; moving this small check adds coupling without removing policy.
- **Guard at `submit_job`.** verified: `client/core.py:1437-1461` accepts paths for
  all job resource types. judgment: a generic waist policy is broader than this
  local residual and would change ADR 0151's build-site error ownership.
- **Add power names to `_LPAR_JOB_OPERATIONS`.** verified: ADR 0151 explicitly
  rejects `PowerOff` as outside LPM. judgment: widening it obscures the two owners.
- **Do nothing.** verified: the present operation is a local conditional over two
  literals (`operations/lpar/core.py:495`). judgment: that convention alone does
  not reject a future third literal, the exact residual #845 asks to govern.
