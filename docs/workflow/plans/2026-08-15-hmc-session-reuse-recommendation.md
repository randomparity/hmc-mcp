# HMC Session Reuse Research Plan

**Goal:** Record an evidence-backed decision for issue #155 without changing
runtime behavior.

**Architecture:** The recommendation separates process-local HMC session-token
state from per-call, event-loop-local HTTP transports. ADR 0028 governs only a
future implementation. This change is Markdown documentation.

**Tech stack:** Markdown, existing Python repository guardrails.

## Global Constraints

- No runtime implementation, migration, dependency, configuration, or ADR index
  change.
- Use only repository evidence, authoritative IBM documentation, and the raw
  operator-supplied measurement.
- Do not disclose credentials, tokens, hostnames, or IP addresses.
- Guardrails: `just verify`; `UV_NO_SYNC=1 uv run prek run --all-files`.
- Host: arm64; target architectures: none declared; relationship:
  no-target-declared.

## Task 1: Record the evidence and recommendation

**Files:**

- Create `docs/workflow/specs/2026-08-15-hmc-session-reuse-recommendation.md`.
- Create `docs/adr/0028-process-local-hmc-session-token-reuse.md`.

**Interfaces:** Consumes issue #155, its measurement comment, current client
lifecycle code, ADRs 0008/0009, and IBM session documentation. A future runtime
issue consumes ADR 0028's cache, invalidation, and replay invariants.

1. Recalculate the posted median and p95 from all 20 raw samples and compare
   them with the reported summary.
2. Record the measurement procedure, cleanup confirmation, environment class,
   missing-script caveat, and authoritative session facts.
3. State the proceed recommendation and link ADR 0028.
4. Record the approved cache key, cross-loop ownership, invalidation, retry,
   shutdown, and mutation-safety decisions in ADR 0028.
5. Run `UV_NO_SYNC=1 uv run prek run --all-files`; expect exit 0 and no modified
   files after formatter hooks settle.
6. Run `just verify`; expect exit 0 and `verify: all groups load OK`.
7. Commit with a Conventional Commits subject no longer than 72 characters.

**Acceptance:** Both documents cover every issue criterion, contain no runtime
change, distinguish verified evidence from inference, and pass both guardrails.

**Rollback:** Revert the documentation commit; no runtime or external state
requires cleanup.

