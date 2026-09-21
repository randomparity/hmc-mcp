# ADR 0121: Resource adapter domain ownership

## Status

Accepted (2026-09-05)

## Context

`server_tools/system_resources.py` combines PCIe, I/O-slot, processor-compatibility,
and memory-pool adapters despite established `virtualization` and `systems` packages.
The CLI likewise keeps memory-pool commands outside its `systems` package. That layout
obscures ownership and makes domain-local changes require a catch-all module.

## Decision

Place PCIe and I/O-slot MCP tools in `server_tools.virtualization.pcie`. Place
processor-compatibility and memory-pool MCP tools in a systems-owned module, and place
memory-pool CLI commands in `cli_commands.systems`. Keep registered MCP tool names,
signatures, effects, and CLI command paths unchanged. Remove the old catch-all modules
without forwarding imports; this pre-release repository does not retain compatibility
layers for internal module paths.

## Consequences

Direct importers move to their owning package, while consumers using registered tools or
the CLI see unchanged behavior. Boundary tests make the new owners explicit and verify
that the removed modules cannot be imported.

## Considered & rejected

- **Keep `system_resources` as a mixed catch-all.** verified: its handlers import both
  `operations.virtualization.pcie` and managed-system SSH helpers, as shown by
  `sed -n '1,260p' src/hmc_mcp/server_tools/system_resources.py` on 2026-09-05.
- **Leave forwarding modules at the old paths.** judgment: pre-release internal imports
  may break immediately, and a forwarding layer would preserve the ownership ambiguity.
