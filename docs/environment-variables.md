# Environment Variables

`hmc-mcp` reads configuration from a platform-native TOML profile file,
environment variables, or CLI flags (priority: CLI flags > environment variables > TOML profile).

See [Configure](#configure) in the README for the TOML profile format.
Use `HMC_HOST`, `HMC_USER`, and `HMC_PASSWORD` for single-HMC setups without a profile file.

## Reference

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `HMC_HOST` | string | _(required)_ | HMC hostname or IP address |
| `HMC_PORT` | integer | `443` | HMC REST API port. If unset, a transport failure during logon retries once on legacy port 12443; if set, connection failure is final and never falls back |
| `HMC_USER` | string | _(required)_ | HMC user name |
| `HMC_PASSWORD` | string | _(required)_ | HMC password |
| `HMC_PROFILE` | string | _(none)_ | Named profile to load from `~/.config/hmc-mcp/config.toml` (or platform equivalent); a value that is not a profile key is resolved through the top-level `nicknames` table. Selects the connection when no explicit `--host`/`HMC_HOST` is set |
| `HMC_SSH_KEY_FILE` | path | _(none)_ | Path to an SSH private key file; when set, SSH commands use key-based auth instead of password auth |
| `HMC_VERIFY_SSL` | bool | `false` | Verify the HMC TLS certificate. HMCs ship self-signed certs; set to `true` only after installing the HMC CA locally |
| `HMC_TIMEOUT` | float | `60.0` | HTTP request timeout in seconds |
| `HMC_SSH_TIMEOUT` | float | `300.0` | SSH command timeout in seconds. SSH-backed HMC CLI operations (e.g. `bkprofdata`/`rstprofdata`) are significantly slower than REST calls |
| `HMC_AUDIT_MEMENTO` | string | `hmc-mcp` | Value sent in the `X-Audit-Memento` request header; appears in HMC audit logs |
| `HMC_AGENT_ID` | string | _(none)_ | Per-agent identifier for multi-agent LPAR ownership. When set, the `X-Audit-Memento` header is sent as `hmc-mcp:<agent_id>` and new LPARs are stamped with `[hmc-mcp owner:<agent_id> created:<date>]` in their description field. Must be 1–64 printable ASCII characters; no commas, `=`, square brackets, forward slashes, colons, or spaces; must not be the reserved value `hmc-mcp` (the default fallback used when no agent_id is set). **Note:** when `HMC_AGENT_ID` is set, `HMC_AUDIT_MEMENTO` is ignored — the prefix `hmc-mcp` is always used. |
| `HMC_ISO_URL_ALLOWLIST` | string | _(empty — refuses every URL)_ | Comma-separated hosts that `hmc_upload_iso` / `hmc-mcp storage upload-iso` may download an ISO from, each written as `host` or `host:port` (no scheme, no path) — e.g. `iso.example.internal,localhost:18765`. An entry without a port permits any port on that host. **Empty is fail-closed: every URL is refused**, because the download runs from the MCP server's network position and there is no safe default destination. See the note below and ADR 0050 |
| `HMC_SCHEMA_VERSION` | string | _(unset)_ | Pins the `X-HMC-Schema-Version` request header on `GET` requests only. **Leave unset for normal operation** — see note below. |

## Notes

- **REST port** (`HMC_PORT`): when omitted, logon starts on port 443 and retries
  once on port 12443 only after a transport failure. Any configured value is an
  explicit choice and fails without fallback. The legacy retry can add the
  duration of the failed 443 attempt; `HMC_TIMEOUT` applies per HTTP timeout
  phase, not across both attempts. A lost 443 logon response may leave an
  unreachable server-side session until the HMC expires it.

- **TLS verification** (`HMC_VERIFY_SSL`): HMCs ship self-signed certificates,
  so TLS verification is off by default. To verify the HMC certificate, install
  its CA locally and set `HMC_VERIFY_SSL=true` — otherwise credentials are at
  risk of man-in-the-middle interception.

- **SSH key file** (`HMC_SSH_KEY_FILE`): only used by SSH-passthrough commands
  (`hmc_run_command`, CLI subcommands backed by `ssh.py`). REST commands always
  use `HMC_PASSWORD`.

- **ISO download allowlist** (`HMC_ISO_URL_ALLOWLIST`): `hmc_upload_iso` fetches
  the ISO from the MCP server's own network position, so the caller of the tool
  chooses a destination the server can reach and they may not be able to — cloud
  instance metadata, loopback services, hosts inside the server's segment (#303).
  Only an operator knows which ISO servers are legitimate, so the tool refuses
  every URL until one is named here, and refuses it before opening a connection.
  **Setting nothing means uploading nothing:** an installation that upgraded into
  this variable will see `hmc_upload_iso` refuse every call, with a message
  naming this variable, until it is set. Matching is on the URL's host and port
  only; a redirect away from that host is refused rather than followed.
  **Allowlisting a name trusts DNS for that name:** entries are matched as
  written, and nothing checks what the name resolves to, so whoever controls
  that resolution at fetch time — owning the record, poisoning the resolver this
  host uses — chooses where the download lands. That yields them a fetch from
  the MCP server's network position, the SHA-256 and exact size of whatever came
  back (both returned to the caller), and those bytes imported into the VIOS
  media repository as media; recovering the body needs an LPAR the caller can
  mount it in, as listing the repository returns metadata only. Prefer names
  whose resolution you control, served by a resolver you trust. An entry written
  as an IP literal (`192.0.2.10`, `[2001:db8::1]:443`) is matched as that
  literal, and no name resolving to it matches. ADR 0050 records the reasoning;
  #322 tracks the residual.

## Profile Nicknames

`--profile`, `HMC_PROFILE`, and `default_profile` all accept a nickname as well as a profile key. When a selected name is not a profile key, it is resolved through the top-level `nicknames` table (a friendly name → an existing profile key) before the not-found error, so the CLI and every MCP tool resolve a nickname identically. Resolution is one level deep (no chains/cycles), case-sensitive, and a profile key wins on a name collision. See the README [Configure](#configure) section and ADR 0030.

## Notes

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
