# Portable LPAR snapshot contract plan

Goal: approve and publish the version-1 portable LPAR snapshot contract without adding runtime
behavior. The accepted ADR selects a strict JSON envelope; the design specification defines every
member, validation boundary, replay rule, and compatibility rule needed by later implementation.

## Global constraints

- This issue changes documentation only; no production model, serializer, command, dependency,
  migration, or live-HMC behavior is added.
- The format discriminator is `hmc-mcp.lpar-snapshot` and the only accepted version is integer 1.
- Configuration is replayable; observations, including current and predicted scores, never are.
- Native HMC profile data is replay authority and the normalized projection must agree with it.
- Reserved CLI commands live under `hmc-mcp snapshot`; MCP identifiers live under `snapshot.*`.
- Host is `x86_64`; targets are `amd64`, `arm64`, and `ppc64le`; the host is included. The design
  is architecture-independent.
- Run `just test`, `just smoke`, and `just verify`.

## Task 1: Record the complete contract

Files created: `docs/adr/0082-portable-lpar-snapshot-contract.md`,
`docs/workflow/specs/2026-08-23-portable-lpar-snapshot-contract-design.md`, and this plan.

This single documentation task is the complete deliverable. Splitting identity, payload, and
compatibility would permit independently accepted fragments that do not form a valid format.

Interfaces: later implementation consumes the exact format/version pair, JSON member names,
identity precedence, capability vocabulary rules, configuration/observation boundary, reserved
command namespace, diagnostics contract, and evolution rules from the specification. This task
produces no executable interface.

1. Write ADR 0082 with the context, selected JSON-envelope decision, consequences, and grounded
   rejected alternatives. Confirm the status is `Accepted (2026-08-23)` and the filename is the
   campaign-assigned number.
2. Write the design specification with one complete version-1 example and normative sections for
   identity, capabilities, replayable configuration, non-replayable observations, command names,
   malformed input, and compatibility.
3. Cross-check every issue criterion against a named specification section. Confirm current and
   predicted scores appear only under observations, native HMC profile data remains replay
   authority, and no command is described as currently installable.
4. Run `git diff --check`; expect exit 0 with no output.
5. Run `just test`, `just smoke`, and `just verify`; expect exit 0 and zero warnings from each.
6. Commit the three documentation artifacts together with subject
   `docs: define portable LPAR snapshot contract`.

Acceptance: the ADR is accepted; the specification independently answers every persistence-format
criterion from issue #313; future implementers can produce deterministic valid/invalid format
fixtures without another schema decision; and the repository guardrails pass. Runtime replay and
conversion policies retain their own design checkpoints. Rollback is a normal revert because no
runtime or persisted state is changed.
