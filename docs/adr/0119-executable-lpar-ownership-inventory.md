# ADR 0119: Executable LPAR ownership inventory

## Status

Accepted (2026-09-05)

## Context

ADR 0092 classifies ownership-sensitive operations, but its table and citation
test do not fail when a new operation is absent from the classification.

## Decision

Test the selected public operation modules with an AST inventory. Each discovered
public operation must be classified as guarded, operational, creation-only, or
outside LPAR mutation. Guarded entries must statically reach an existing
ownership helper. ADR 0092 remains the policy source.

## Consequences

Adding an operation requires updating the inventory and ADR 0092 in the same
change. The check creates no runtime authorization layer.

## Considered & rejected

- **Facade-only enumeration.** verified: ADR 0092 covers internal, MCP, and CLI
  delegation; the public facade deliberately exports only core library types.
- **Runtime registration inspection.** judgment: registration does not expose
  internal operations or prove an ownership helper is reached.
