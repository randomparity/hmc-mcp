# Configuration

[Documentation index](index.md) · [Quick start](../README.md#configure)

## Configure

Configuration priority (highest to lowest): **CLI flags > `HMC_*` env vars > TOML profile**.

### TOML profile (recommended for multi-HMC setups)

Create `~/.config/hmc-mcp/config.toml` (Linux / macOS `~/Library/Application Support/hmc-mcp/config.toml` / Windows `%APPDATA%/hmc-mcp/config.toml`):

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
| Audit memento     | `HMC_AUDIT_MEMENTO`  | —                 | `hmc-mcp` |
| Schema version    | `HMC_SCHEMA_VERSION` | —                 | _(unset)_ |

When the REST port is omitted, hmc-mcp tries port 443 and retries logon once on
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
`hmc-mcp` emits `TLSVerificationDisabledWarning` once per HMC host and
`verify_ssl` setting source per process while it stays off. Reusable Python
consumers can import that category from `hmc_mcp.api` and filter it without
suppressing unrelated `UserWarning`s. To verify the HMC certificate, install
its CA locally and set `HMC_VERIFY_SSL=true` (`--verify-ssl`) — otherwise the
HMC credentials are at risk of man-in-the-middle interception.
