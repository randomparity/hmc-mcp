# Configuration

[Documentation index](index.md) · [Quick start](../README.md#configure)

## Configure

Configuration priority (highest to lowest): **CLI flags > `HMC_*` env vars > TOML profile**.

## Clean cutover from the former name

This pre-release changes the distribution, command, Python package, and configuration
directory from `hmc-mcp`/`hmc_mcp` to `hmcpctl`. There is no compatibility alias or
configuration fallback. Stop every old CLI or MCP server process before moving the directory.
Each transaction below refuses to merge into an existing destination.

On Linux (including a custom `XDG_CONFIG_HOME`):

```bash
old_dir="${XDG_CONFIG_HOME:-$HOME/.config}/hmc-mcp"
new_dir="${XDG_CONFIG_HOME:-$HOME/.config}/hmcpctl"
if [ -e "$new_dir" ]; then echo "refusing existing destination: $new_dir" >&2; exit 1; fi
if [ -d "$old_dir" ]; then mv -- "$old_dir" "$new_dir"; fi
```

On macOS:

```bash
old_dir="$HOME/Library/Application Support/hmc-mcp"
new_dir="$HOME/Library/Application Support/hmcpctl"
if [ -e "$new_dir" ]; then echo "refusing existing destination: $new_dir" >&2; exit 1; fi
if [ -d "$old_dir" ]; then mv -- "$old_dir" "$new_dir"; fi
```

On Windows PowerShell:

```powershell
$oldDir = Join-Path $env:APPDATA 'hmc-mcp'
$newDir = Join-Path $env:APPDATA 'hmcpctl'
if (Test-Path -LiteralPath $newDir) { throw "Refusing existing destination: $newDir" }
if (Test-Path -LiteralPath $oldDir -PathType Container) {
    Move-Item -LiteralPath $oldDir -Destination $newDir
}
```

Then remove the former tool and install this checkout explicitly:

```bash
uv tool uninstall hmc-mcp
cd /absolute/path/to/hmcpctl-checkout
uv tool install --python 3.11 '.[app]'
hmcpctl --help
hmcpctl config show
test -f "${XDG_CONFIG_HOME:-$HOME/.config}/hmcpctl/access-policy.toml"  # Linux
if command -v hmc-mcp >/dev/null 2>&1; then echo "old command remains on PATH" >&2; exit 1; fi
```

For macOS, check `$HOME/Library/Application Support/hmcpctl/access-policy.toml` instead.
For PowerShell, run the equivalent verification:

```powershell
uv tool uninstall hmc-mcp
Set-Location -LiteralPath 'C:\absolute\path\to\hmcpctl-checkout'
uv tool install --python 3.11 '.[app]'
hmcpctl --help
hmcpctl config show
if (-not (Test-Path -LiteralPath (Join-Path $newDir 'access-policy.toml'))) {
    throw 'access-policy.toml was not moved'
}
if (Get-Command hmc-mcp -ErrorAction SilentlyContinue) { throw 'old command remains on PATH' }
```

Update every MCP client entry from command `hmc-mcp` to `hmcpctl`, preserving its arguments and
environment, and only then restart the client or server.

Repository hosting and publication are separate operator actions and have **not** been performed
by this source change. Before a public release, the operator must:

1. Rename the GitHub repository and verify redirects and documentation links.
2. Update package project URLs to the final repository slug.
3. Configure the `hmcpctl` PyPI project and its trusted publisher or release token.
4. Build clean artifacts, publish them, and verify a fresh `hmcpctl[app]` installation.

### TOML profile (recommended for multi-HMC setups)

Create `~/.config/hmcpctl/config.toml` (Linux / macOS `~/Library/Application Support/hmcpctl/config.toml` / Windows `%APPDATA%/hmcpctl/config.toml`):

```toml
default_profile = "prod"

[profiles.prod]
host = "hmc.example.com"
port = 443                         # optional; omit to allow legacy fallback
user = "admin"
password_env = "HMC_PROD_PASSWORD"   # resolved from the environment at runtime  # pragma: allowlist secret

[profiles.dev]
host = "hmc-dev.example.com"
user = "devadmin"
password = "devpassword"              # or store inline for non-production  # pragma: allowlist secret
```

Select a profile with `--profile <name>` or `HMC_PROFILE=<name>`.
`password_env` keeps secrets out of the file; `password` is accepted for convenience.

#### Profile nicknames (friendly names)

When you run more than one HMC, remember the exact `[profiles]` key for every
`--profile` / `HMC_PROFILE` call. A top-level `nicknames` table maps a friendly
name to an existing profile key, so a memorable name resolves to a profile:

```toml
[nicknames]
big-iron = "prod"
staging  = "stg-hmc-03"
```

`big-iron` and `staging` now work anywhere a profile name does — `--profile
big-iron`, `HMC_PROFILE=staging`, and even `default_profile = "big-iron"` —
because resolution is a single name-selection step inside the profile loader, so
the CLI and every MCP tool inherit it with no per-tool change.

Rules:

- **One level deep.** A nickname resolves to a *profile key*; it never resolves
  to another nickname (no chains, no cycles).
- **Case-sensitive.** `big-iron` does not match `BIG-IRON`.
- **Profile key wins on collision.** A name that is both a profile key and a
  nickname key selects the profile.
- **Clear failures.** A nickname whose target is not a profile, an unknown name,
  or a malformed `nicknames` table raises a `ConfigError` naming the available
  profiles and nicknames.

Nicknames are *surfaced, not hidden*. `config list` prints each nickname as
`nick -> target` (flagging a dangling target), `config show --profile <nick>` shows the
resolved profile with a `resolved_from` field naming the nickname, and
`hmc_list_configured_hosts` reports each nickname and its target-existence — none
of which resolves a secret. `config init` scaffolds a commented `nicknames`
example. A guardrail (`just nicknames`, in `just verify`) validates a committed
fixture: every nickname target exists, no nickname collides with a profile key,
and no target is itself a nickname.

### Environment variables (single-HMC / MCP server)

| Setting           | Env var              | CLI flag          | Default   |
|-------------------|----------------------|-------------------|-----------|
| Profile           | `HMC_PROFILE`        | `--profile`       | —         |
| HMC host / IP     | `HMC_HOST`           | `--host`          | —         |
| REST port         | `HMC_PORT`           | —                 | `443`     |
| User              | `HMC_USER`           | `--user, -u`      | —         |
| Password          | `HMC_PASSWORD`       | `--password, -p`  | —         |
| Verify TLS        | `HMC_VERIFY_SSL`     | `--verify-ssl`    | `false`   |
| HTTP timeout (s)  | `HMC_TIMEOUT`        | —                 | `60.0`    |
| SSH timeout (s)   | `HMC_SSH_TIMEOUT`    | —                 | `300.0`   |
| SSH key file      | `HMC_SSH_KEY_FILE`   | —                 | —         |
| Audit memento     | `HMC_AUDIT_MEMENTO`  | —                 | `hmcpctl` |
| Schema version    | `HMC_SCHEMA_VERSION` | —                 | _(unset)_ |

When the REST port is omitted, hmcpctl tries port 443 and retries logon once on
legacy port 12443 only if the first attempt fails at the transport layer. Setting
`port` in TOML or `HMC_PORT` selects that port explicitly: a connection failure
is returned immediately and never falls back. On an older HMC, leaving the port
unset can therefore add the duration of the failed 443 attempt. `HMC_TIMEOUT`
applies to each HTTP timeout phase rather than to the combined two-attempt wall
clock time. If a 443 logon response is lost, that unreachable attempt may leave
a server-side session until the HMC expires it.

See [Environment variables](environment-variables.md) for the
full reference, including descriptions and usage notes.

SSH-backed operations use port 22 and either the configured password or
`HMC_SSH_KEY_FILE`. They verify the HMC's host key against the process user's
`~/.ssh/known_hosts` by default. Provision independently verified keys before
running these operations; see [SSH trust setup](HMC_HINTS.md#ssh-host-key-trust).

HMCs ship self-signed certificates, so TLS verification is off by default and
`hmcpctl` emits `TLSVerificationDisabledWarning` once per HMC host and
`verify_ssl` setting source per process while it stays off. Reusable Python
consumers can import that category from `hmcpctl.api` and filter it without
suppressing unrelated `UserWarning`s. To verify the HMC certificate, install
its CA locally and set `HMC_VERIFY_SSL=true` (`--verify-ssl`) — otherwise the
HMC credentials are at risk of man-in-the-middle interception.
