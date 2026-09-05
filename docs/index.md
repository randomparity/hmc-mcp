# Documentation

Start with the [README quick starts](../README.md) to install hmc-mcp, connect to
an HMC, and use the CLI or an MCP client.

## Guides

| Guide | Contents |
|-------|----------|
| [Configuration](configuration.md) | TOML profiles, nicknames, environment precedence, TLS, and REST ports |
| [CLI](cli.md) | Inventory, LPAR lifecycle, storage, adapters, and snapshots |
| [MCP server](mcp-server.md) | Access policies, client setup, HTTP transport, migration, and diagnostics |
| [Python library](python-api.md) | Installation, example usage, typing, and the supported API contract |
| [HMC compatibility](compatibility.md) | HMC versions and firmware write-path limitations |
| [Development](development.md) | Stack, setup, testing, source layout, and HMC REST API internals |

## Reference

- [MCP tools](tools/index.md): generated tool signatures, parameters, and security metadata.
- [Environment variables](environment-variables.md): all supported settings and operation guards.
- [Operation details](operations.md): units, selectors, collection limits, and capability restrictions.
- [Authorization audit](authorization-audit.md): events, reason codes, and log delivery.
- [HMC CLI cheatsheet](hmc-cli-cheatsheet.md): underlying IBM commands used over SSH.
- [HMC hints](HMC_HINTS.md): SSH access, host-key trust, and command troubleshooting.

## Project records

- [Architecture decisions](adr/): numbered decisions and compatibility contracts.
- [Workflow specifications](workflow/specs/): designs for individual changes.
- [Contributing](../CONTRIBUTING.md), [security policy](../SECURITY.md), and [license](../LICENSE).
