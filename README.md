# hmc-mcp

Manage IBM Power systems from your terminal or an AI assistant. hmc-mcp connects
to the IBM Hardware Management Console (HMC) to inventory systems, inspect LPARs
and VIOS partitions, manage resources, and run lifecycle operations.

Use it as a CLI, an MCP server, or a [Python library](https://github.com/randomparity/hmc-mcp/blob/main/docs/python-api.md).
MCP access policies control which tools, connections, and targets an assistant can use.

## Install

Requires **Python ≥3.11** and [uv](https://docs.astral.sh/uv/).
The project supports every stable, non-EOL CPython release at or above that floor.

Install the CLI and MCP server from source:

```bash
git clone https://github.com/randomparity/hmc-mcp.git
cd hmc-mcp
uv tool install --python 3.11 '.[app]'
hmc-mcp --help
```

If your shell cannot find `hmc-mcp`, run `uv tool update-shell` and open a new
terminal. The `app` extra includes both the CLI and MCP server; for development,
use [the contributor setup](CONTRIBUTING.md).

You need network access to an HMC and an HMC account with permissions for your
intended operations. The project targets HMC V8–V11; individual operations have
[version and firmware limits](https://github.com/randomparity/hmc-mcp/blob/main/docs/compatibility.md).

## Configure

For one HMC, set the connection environment in your shell:

```bash
export HMC_HOST=hmc.example.com
export HMC_USER=admin
export HMC_VERIFY_SSL=true
```

Set `HMC_PASSWORD` through your shell or secret manager before running commands.
Replace the example host and user with your own. TLS verification requires the
HMC's CA certificate to be trusted locally. Verification is off by default if
`HMC_VERIFY_SSL` is omitted, which exposes credentials to interception.

Check the connection:

```bash
hmc-mcp console info
```

This prints HMC information after a successful logon. Some operations also use
SSH; see [configuration](https://github.com/randomparity/hmc-mcp/blob/main/docs/configuration.md)
for SSH credentials, TOML profiles, friendly nicknames, and connection precedence.
For multiple consoles, start with `hmc-mcp config init` and edit the file it prints.

## CLI quick start

Explore your systems and partitions:

```bash
hmc-mcp systems list
hmc-mcp systems health --json
hmc-mcp lpars list --system <system-uuid>
hmc-mcp lpars show <lpar-uuid>
hmc-mcp vios list
hmc-mcp jobs list -n 5
```

Use UUIDs from the inventory output to identify resources. To change an LPAR,
for example:

```bash
hmc-mcp lpars modify <lpar-uuid> --mem 16384 --procs 2.0
hmc-mcp lpars power-on <lpar-uuid>
```

Memory is in MiB; power-on asks for confirmation. HMC permissions and operation
guards still apply. MCP access policies apply only to the MCP server; shell
commands run under your HMC credentials.

Use `hmc-mcp --help` or `hmc-mcp lpars --help` to explore.
The [CLI guide](https://github.com/randomparity/hmc-mcp/blob/main/docs/cli.md)
covers storage, adapters, snapshots, provisioning, and job output.

## MCP quick start

Create `access-policy.toml` in the platform's hmc-mcp configuration directory:

- Linux: `~/.config/hmc-mcp/` (or `$XDG_CONFIG_HOME/hmc-mcp/`)
- macOS: `~/Library/Application Support/hmc-mcp/`
- Windows: `%APPDATA%/hmc-mcp/`

Create the directory if needed. For an initial read-only server, add this policy
to the file, preserving any policies already there:

```toml
[[policies.readonly.grants]]
effects = ["read"]
connections = ["<default>"]
targets = "all-targets"
```

This permits read tools across the default HMC. Read tools can also disclose
local profile and policy metadata; see the
[policy scope guide](https://github.com/randomparity/hmc-mcp/blob/main/docs/mcp-server.md#what-the-policy-does-not-bound).

With the connection environment above available, start the server over stdio:

```bash
hmc-mcp serve --access-policy readonly
```

An MCP client normally launches this process for you. For clients that use an
`mcpServers` configuration, add:

```json
{
  "mcpServers": {
    "hmc": {
      "command": "hmc-mcp",
      "args": ["serve", "--access-policy", "readonly"],
      "env": {
        "HMC_HOST": "hmc.example.com",
        "HMC_USER": "admin",
        "HMC_PASSWORD": "<HMC-password>",
        "HMC_VERIFY_SSL": "true"
      }
    }
  }
}
```

Replace the placeholders and use your client's secret handling if available.
If it cannot find the executable, use its absolute path from `uv tool dir --bin`.
The client process must have access to the policy file and trusted HMC CA.

Try asking: “List my managed Power systems” or “Show fleet health.”
Call `hmc_effective_permissions` to inspect the active policy.

An access policy is required. For broader permissions, migration from older
deployments, audit logging, and HTTP setup, see the
[MCP server guide](https://github.com/randomparity/hmc-mcp/blob/main/docs/mcp-server.md).
HTTP is unauthenticated: keep it on loopback or put an authenticated proxy in front.

## Documentation

- [Documentation index](https://github.com/randomparity/hmc-mcp/blob/main/docs/index.md) — guides and reference.
- [MCP tool reference](https://github.com/randomparity/hmc-mcp/blob/main/docs/tools/index.md) — available tools, parameters, and effects.
- [Environment variables](https://github.com/randomparity/hmc-mcp/blob/main/docs/environment-variables.md) — all connection and operation settings.
- [Operation details](https://github.com/randomparity/hmc-mcp/blob/main/docs/operations.md) — selectors, units, limits, and firmware-specific behavior.
- [Development guide](https://github.com/randomparity/hmc-mcp/blob/main/docs/development.md) — stack, testing, source layout, and HMC API internals.

## Contributing, security, and license

See [Contributing](CONTRIBUTING.md) for the development and pull-request path.
Report suspected vulnerabilities through the private channel in the
[Security policy](SECURITY.md). This project is available under the [License](LICENSE).
