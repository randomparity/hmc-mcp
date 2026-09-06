# ADR 0122: Document-domain ownership

## Status

Accepted (2026-09-05)

## Context

`hmc_mcp.documents.common` contains LogicalPartition, managed-system, storage,
access, and boot vocabulary alongside the XML envelope helper. Domain modules import
their own definitions through that mixed module, obscuring ownership and expanding the
shared module whenever a domain adds a builder.

## Decision

Keep only `UOM_NS` and `document_envelope` in `documents.common`. Move each
domain-owned vocabulary, model, helper, and envelope to the domain module that owns its
builder. The package facade imports every public name from that owner and preserves its
object identity. Remove moved names from `documents.common` without forwarding aliases;
this pre-release branch intentionally breaks direct imports of those internal paths.
Boot builds its LogicalPartition envelope through `document_envelope` directly rather
than importing an LPAR-domain helper.

## Consequences

Domain-local changes no longer require a mixed common module, and direct imports reveal
their ownership. Package-level consumers retain unchanged behavior and names. Consumers
of moved submodule paths must update immediately, rather than relying on a compatibility
layer that would retain the unclear layout.

## Considered & rejected

- **Keep the mixed common module.** verified: `lpar.py`, `system.py`, `storage.py`,
  `access.py`, and `boot.py` each import domain-specific definitions from it, as shown by
  `rg -n "from \\.common" src/hmc_mcp/documents` on 2026-09-05.
- **Leave forwarding aliases in `common.py`.** judgment: they preserve the very
  ambiguity this refactor removes, while the approved pre-release policy permits an
  immediate internal import break.
