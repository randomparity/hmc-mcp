# Environment Variables

`hmc-mcp` reads configuration from a platform-native TOML profile file,
environment variables, or CLI flags (priority: CLI flags > environment variables > TOML profile).

See [Configure](#configure) in the README for the TOML profile format.
Use `HMC_HOST`, `HMC_USER`, and `HMC_PASSWORD` for single-HMC setups without a profile file.

## Reference

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `HMC_HOST` | string | _(required)_ | HMC hostname or IP address |
| `HMC_PORT` | integer | `12443` | HMC REST API port |
| `HMC_USER` | string | _(required)_ | HMC user name |
| `HMC_PASSWORD` | string | _(required)_ | HMC password |
| `HMC_PROFILE` | string | _(none)_ | Named profile to load from `~/.config/hmc-mcp/config.toml` (or platform equivalent). Selects the connection when no explicit `--host`/`HMC_HOST` is set |
| `HMC_SSH_KEY_FILE` | path | _(none)_ | Path to an SSH private key file; when set, SSH commands use key-based auth instead of password auth |
| `HMC_VERIFY_SSL` | bool | `false` | Verify the HMC TLS certificate. HMCs ship self-signed certs; set to `true` only after installing the HMC CA locally |
| `HMC_TIMEOUT` | float | `60.0` | HTTP request timeout in seconds |
| `HMC_SSH_TIMEOUT` | float | `300.0` | SSH command timeout in seconds. SSH-backed HMC CLI operations (e.g. `bkprofdata`/`rstprofdata`) are significantly slower than REST calls |
| `HMC_AUDIT_MEMENTO` | string | `hmc-mcp` | Value sent in the `X-Audit-Memento` request header; appears in HMC audit logs |
| `HMC_AGENT_ID` | string | _(none)_ | Per-agent identifier for multi-agent LPAR ownership. When set, the `X-Audit-Memento` header is sent as `hmc-mcp/<agent_id>` and new LPARs are stamped with `[hmc-mcp owner:<agent_id> created:<date>]` in their description field. Must be 1–64 printable ASCII characters; no commas, `=`, square brackets, or forward slashes. **Note:** when `HMC_AGENT_ID` is set, `HMC_AUDIT_MEMENTO` is ignored — the prefix `hmc-mcp` is always used. |
| `HMC_SCHEMA_VERSION` | string | _(unset)_ | Pins the `X-HMC-Schema-Version` request header on `GET` requests only. **Leave unset for normal operation** — see note below. |

## Notes

- **TLS verification** (`HMC_VERIFY_SSL`): HMCs ship self-signed certificates,
  so TLS verification is off by default. To verify the HMC certificate, install
  its CA locally and set `HMC_VERIFY_SSL=true` — otherwise credentials are at
  risk of man-in-the-middle interception.

- **SSH key file** (`HMC_SSH_KEY_FILE`): only used by SSH-passthrough commands
  (`hmc_run_command`, CLI subcommands backed by `ssh.py`). REST commands always
  use `HMC_PASSWORD`.

- **Schema version** (`HMC_SCHEMA_VERSION`): **do not set this for normal
  operation.** `hmc-mcp` omits `X-HMC-Schema-Version` from all write paths
  (`PUT`/`POST`) regardless of this setting — some HMC firmware versions return
  HTTP 406 on every UOM write endpoint when that header is present (confirmed on
  HMC V10R3 build 2408210051 and likely other V10 builds). The variable only
  affects `GET` requests. Set it only if you are debugging schema negotiation on
  a specific read path; it has no effect on LPAR creation, adapter
  configuration, storage operations, or any other mutating call.

## Adding a New Variable

Every new `HMC_*` env var added to
[`src/hmc_mcp/config.py`](../src/hmc_mcp/config.py) must be added to this
document before merging. The `just env-vars` guard (`scripts/check_env_vars.py`)
enforces this in pre-commit hooks and CI.
