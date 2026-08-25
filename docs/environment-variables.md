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
  The `false` default is deliberate and stays until 1.0: self-signed
  certificates are the norm on HMCs, and this package has no certificate-trust
  story yet (no trust-store configuration, no per-host pinning, no fingerprint
  option), so flipping the default would break every existing operator's working
  configuration on upgrade with no migration path. Because the insecure-by-default
  state must still be observable, every client constructed with verification off
  emits a `tls-verification-disabled` record to the audit stream, naming the HMC
  host and where the setting came from (`explicit-argument`,
  `environment:HMC_VERIFY_SSL`, or `field-default`), in addition to the
  logon-time warning.

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

## Library Consumers

The variables above configure the `hmc-mcp` CLI and MCP server, which are
single-connection processes an operator owns end to end. A library consumer of
`hmc_mcp.api` is usually not that: a server that builds one connection per HMC in
a single process inherits the ambient environment on every field it does not set.

### Precedence

`HMCConfig` resolves each field independently, from the first source that has a
value for it:

1. **Constructor arguments** — `HMCConfig(host="a.example")`
2. **Environment variables** — `HMC_HOST`, and one per field in the
   [Reference](#reference) table
3. **A dotenv file** — not configured; see the warning below
4. **The declared field default** — `port` is `443`, `agent_id` is unset, and so on

Per field, not per source: `HMCConfig(host="a.example")` in a process where
`HMC_AGENT_ID` is exported gets the constructor's `host` **and** the
environment's `agent_id`.

`load_profile()` inserts TOML profile values below the environment
(constructor args > `HMC_*` > TOML profile > field default). That ordering is
deliberate — it is how an operator overrides a committed profile for one
invocation — and it applies to every field the profile omits as well.

### Isolated construction

Use `HMCConfig.from_mapping(values)` when a setting must come from `values` or
from the declared field default, and never from the process environment:

```python
from hmc_mcp.api import HMCConfig

# row is e.g. a database row: {"host": ..., "user": ..., "password": ...}
config = HMCConfig.from_mapping(row)
```

`from_mapping` reads no environment variable and no dotenv file. Every field the
mapping omits takes its declared default. Keys that name no field are ignored
(the same `extra="ignore"` the ordinary constructor uses), so a row carrying
`id`, `name`, or other columns can be passed as-is. Validation is unchanged —
field validators and the `HMC_AGENT_ID` grammar check still run.

Two properties worth knowing when the mapping is a database row:

- **A key present with a `None` value is applied, not treated as absent.** A
  nullable column arriving as SQL `NULL` is a validation error for every field
  except `ssh_key_file` and `agent_id`. Drop the key when the intent is "use the
  default": `{k: v for k, v in row.items() if v is not None}`.
- **`model_fields_set` reports the keys the mapping supplied**, so
  `config.model_dump(exclude_unset=True)` round-trips back to the settings the
  row actually named.

The **exhaustive** list of fields the environment can supply is the
[Reference](#reference) table above: every row except `HMC_PROFILE` names an
`HMCConfig` field, and every `HMCConfig` field has a row. `HMC_PROFILE` is read
by `load_profile()` to pick a profile, not by `HMCConfig`. Both halves of that
claim are enforced against `HMCConfig.model_fields` by
`tests/test_env_var_guard.py`, and the "every field has a row" half is enforced
again by `scripts/check_env_vars.py` (`just env-vars`) in the pre-commit hooks
and in CI. Neither can go stale.

### `_env_file=None` is not isolation

> **`_env_file=None` suppresses dotenv loading only.** It does not suppress
> environment variables. `HMCConfig(_env_file=None)` in a process where
> `HMC_HOST` is exported still returns that host.

It is worth being blunter still: `HMCConfig` declares no `env_file` in its
`model_config`, so no dotenv source is configured in the first place and
`_env_file=None` currently changes nothing whatsoever. It is a private
pydantic-settings parameter, it looks like isolation, and it is not. Use
`from_mapping`.

### What leaks, and what it costs

Three variables are worth naming because the failure is silent and the blast
radius is wide:

| Stray variable | Effect on a config that omitted the field |
|----------------|-------------------------------------------|
| `HMC_HOST` | The connection targets a different HMC than the caller named |
| `HMC_SSH_KEY_FILE` | SSH commands offer a private key the caller did not choose |
| `HMC_AGENT_ID` | Every LPAR the process stamps is attributed to the wrong agent, corrupting the ADR 0011 ownership token other agents authorize against |

None of these raise. See ADR 0096 for the full reasoning.

## Adding a New Variable

Every new `HMC_*` env var added to
[`src/hmc_mcp/config.py`](../src/hmc_mcp/config.py) must be added to this
document before merging. The `just env-vars` guard (`scripts/check_env_vars.py`)
enforces this in pre-commit hooks and CI.
