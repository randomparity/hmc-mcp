# Resource adapter domain ownership design

Decision: [ADR 0121](../../adr/0121-resource-adapter-domain-ownership.md)

## Scope and outcome

Move the remaining PCIe, I/O-slot, processor-compatibility, and memory-pool adapters
out of the mixed resource modules into their existing domain packages. Registered MCP
tools and `hmc-mcp systems memory-pools` retain their behavior and signatures. Direct
imports of the removed modules are intentionally unsupported after the move.

## Design

`server_tools.virtualization.pcie` becomes the sole owner of PCIe, SR-IOV, and I/O-slot
MCP adapters. A systems submodule owns processor-compatibility and shared-memory-pool
MCP adapters. `cli_commands.systems` owns shared-memory-pool CLI commands. The catalog
and CLI composition root import those new owners, and the old catch-all modules are
deleted without compatibility exports.

No trust boundary, authorization rule, command construction, or operation implementation
changes. Existing `tool` metadata, calls to `with_client`/`ssh_with_client`, and Typer
registration remain intact.

## Verification

Focused server-boundary, PCIe inventory, processor-compatibility, memory-pool, SSH
routing/quoting, and CLI tests prove equivalent behavior and the removed-module imports.
Regenerate the tool documentation because module ownership changes the generated
reference. Run `just typecheck`, `just smoke`, and the focused tests before each commit.
