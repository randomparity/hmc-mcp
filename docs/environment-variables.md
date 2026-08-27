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
| `HMC_AUTHORIZE_POWER_OPERATIONS` | bool | `false` | Enforce the ADR 0011 ownership guard on LPAR power operations. Off by default, so powering a partition another agent owns is permitted and ownership stays advisory on this path. When `true`, `power_lpar` (and everything that delegates to it: `hmc_power_on_lpar`, `hmc_power_off_lpar`, `hmc-mcp lpars power-on/power-off`) reads the ownership token before submitting the job, requires a managed-system selector, and refuses a foreign-owned partition unless the caller passes `ownership_override`. See the note below and ADR 0092 §4 |
| `HMC_ISO_URL_ALLOWLIST` | string | _(empty — refuses every URL)_ | Comma-separated hosts that `hmc_upload_iso` / `hmc-mcp storage upload-iso` may download an ISO from, each written as `host` or `host:port` (no scheme, no path) — e.g. `iso.example.internal,localhost:18765`. An entry without a port permits any port on that host. **Empty is fail-closed: every URL is refused**, because the download runs from the MCP server's network position and there is no safe default destination. See the note below and ADR 0050 |
| `HMC_SCHEMA_VERSION` | string | _(unset)_ | Pins the `X-HMC-Schema-Version` request header on `GET` requests only. **Leave unset for normal operation** — see note below. |

## Notes

- **REST port** (`HMC_PORT`): when omitted, logon starts on port 443 and retries
  once on port 12443 only after a transport failure. Any configured value is an
  explicit choice and fails without fallback. The legacy retry can add the
  duration of the failed 443 attempt; `HMC_TIMEOUT` applies per HTTP timeout
  phase, not across both attempts. A lost 443 logon response may leave an
  unreachable server-side session until the HMC expires it.

<!-- The `source` values below are read by tests/test_authorization_audit_doc.py and held
     to `client.VERIFY_SSL_SOURCES`. Keep them a comma-and-`or` run introduced by the
     words "where the setting came from"; that clause is the anchor, and this note must
     stay one `- **` bullet. -->
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

- **Power ownership guard** (`HMC_AUTHORIZE_POWER_OPERATIONS`): ADR 0011 ownership
  is advisory by default on the power path, and ADR 0092 §4 records why. The guard
  costs **one SSH login plus two REST GETs** per guarded call —
  `authorize_lpar_mutation` reads the token over SSH, and
  `resolve_lpar_ownership_names` performs both REST reads unconditionally to turn
  UUIDs into the CLI names the SSH command takes. `ownership_override=True` skips
  the SSH read, **not** the two REST reads: they run first, because the audit
  record for an approved override names the system and the partition. A
  power-cycling orchestrator is the highest-frequency caller of this operation, and
  power is the one mutation class whose inverse is a single call with no prior
  state to reconstruct, so the cost is opt-in rather than default. Turn it on when
  the HMC is shared with other agents or human operators and that cost is
  acceptable.

  Turning it on changes two things beyond the ownership check. A partition another
  agent owns is refused with a `PermissionError`; retry it as a deliberate, audited
  exception with `ownership_override` (`--ownership-override` on the CLI).
  `provision_lpar` passes that override on its own activation leg, because the
  partition it powers is the one the same workflow just created and stamped.

  And a call that omits the managed-system selector pays a bounded fleet walk: the
  ownership token is read per managed system, so the guard derives the owning one
  by scanning partition feeds (ADR 0094, capped at 100 systems with a timeout).
  Supplying `system_name_or_uuid` — `--system` on the CLI — replaces that walk with
  one read, which is worth doing for a power-cycling orchestrator.

  And **power operations gain a dependency on the HMC's SSH interface.** The
  ownership read runs the HMC CLI over SSH, so with the guard on a power operation
  fails with `HMCCLIError` when SSH is unreachable, refuses the credentials, or
  hangs. That includes `power-off --immediate`, the call an operator most wants
  during an incident. It is fail-closed by design — an ownership token that cannot
  be read has not been checked — and `ownership_override` is the escape, because it
  skips the read.

  **Size the worst case at two SSH commands, not one.** `HMC_SSH_TIMEOUT` bounds
  each command separately, not the operation, and a guarded call can run two: the
  name resolution falls back to an SSH lookup when the REST read of the managed
  system fails or returns no `SystemName`, and that fallback *swallows* its timeout
  and carries on to the ownership read, which then burns a second one. At the
  300-second default that is roughly ten minutes before the failure surfaces, not
  five. The override path pays only the first, because it skips the ownership read
  — so it is not an unconditional SSH-free path either. A deployment whose HMC
  credentials work for REST but not for SSH should leave this setting off.

  **Set the environment variable, not the TOML key, to make the guard hold
  everywhere.** The value is read from the resolved config, so a TOML
  `authorize_power_operations = true` applies only to the profile that carries it —
  every other profile stays unguarded, including a second profile pointing at the
  same HMC, and both the MCP tools and the CLI take a caller-supplied profile
  selector. `HMC_AUTHORIZE_POWER_OPERATIONS` overrides every profile's TOML value,
  so it is the setting that cannot be selected around — in any casing, see
  [Variable names are matched without regard to
  case](#variable-names-are-matched-without-regard-to-case).

  **Check that it actually took — ask the server, not the shell.** This setting
  fails **open**, and a mistyped profile key or environment variable is dropped
  silently — indistinguishable from a correct `false`. Call
  `hmc_effective_permissions` against the running server and read
  `power_ownership_guards`: one entry per connection the access policy's grants
  name, each carrying the effective post-precedence `authorize_power_operations`
  value — `true` means the ownership guard is **enforced** — and the `source` that
  supplied it: `environment`, `profile`, or `default`. `default` is the answer
  that means *nothing you wrote arrived*, which is the case a bare
  `false` cannot distinguish. It also covers one case that is not your memory's
  fault: a `config.toml` that exists but cannot be read, parsed, or resolved to a
  profile is discarded on the default connection with no error and no log line
  from this report, and every setting in it reverts to its built-in default. **If
  you have a `config.toml` and the default connection reads `default`, suspect the
  file itself** before you go looking for a typo in the key. A read or parse
  failure is not silent everywhere: `hmc_list_configured_hosts` surfaces it by
  name, with the line and column, and the same `read` grant that reaches this
  report reaches that tool. Only a file that parses but resolves to no profile —
  a missing `default_profile`, an `HMC_PROFILE` naming nothing — is silent on
  every channel.

  A fourth value, `ambiguous`, means a **case variant** of
  `HMC_AUTHORIZE_POWER_OPERATIONS` is exported: only the exact upper-case
  spelling is dropped from a profile's keys before the config is built, so a
  variant loses to a profile there and wins where no profile is read — and nothing
  in the server can tell which happened. Fix the spelling.

  **With `HMC_HOST` set, expect fewer rows than your policy has connections.** Every
  connection token collapses to the default one at dispatch, so the report carries at
  most the `<default>` row — the named ones vanish because nothing can reach them. An
  empty `power_ownership_guards` means the policy grants no connection any call can
  reach, not that the report found nothing to say.

  A connection whose config cannot be built reports
  `authorize_power_operations: null` with `source: unresolved` and a `detail`
  classifying the failure — `ConfigError`, or
  `ValidationError` with the field names it rejected. The `detail` is deliberately
  closed. For a `ConfigError` the full message goes to the server's log instead,
  because it names every profile and nickname key in your `config.toml` — **once
  per process**, on the first call that hits that failure, since the tool's call
  rate belongs to the MCP client; restart the server to see it again. That line is
  written outside the bounded stderr sink of ADR 0043 (#534). For a
  `ValidationError` there is no fuller message anywhere, in the report or the log:
  pydantic quotes the value it rejected, and a bad `password` would then be in
  your log, so you get the field name and read the value from the config source
  yourself. Beyond the connection names your policy already declares, the entries
  carry no host, user, or credential.

  Because the report is resolved inside the process being asked, it answers where
  `hmc-mcp config show` cannot. `config show` requires a `config.toml` and exits 1
  without one, so it cannot answer for an env-var-only setup at all. It reads the
  environment of the shell that invoked it, not of the `hmc-mcp serve` process an
  MCP host launched with its own environment block. And when `HMC_HOST` (or an
  explicit `--host`) is set, a tool run skips the profile entirely and builds its
  config from environment variables alone — so a TOML-only
  `authorize_power_operations = true` is shown by `config show` as enabled while
  the runtime resolves it to `false`. That last one is the fail-open direction; the
  server's own report resolves it the same way a tool call does and says `default`,
  and it is another reason to set `HMC_AUTHORIZE_POWER_OPERATIONS` rather than the
  TOML key.

  One limit remains, and it is a tradeoff rather than advice: an access policy that
  does not grant `hmc_effective_permissions` withholds the tool, and then neither
  route answers for the running process — but the tool also discloses the whole
  policy to the MCP client (ADR 0037). Weigh those against each other for your
  deployment. There is no second in-process channel for the value today: with the
  tool withheld, `config show` and its three limits above are all that is left.
  #533 tracks announcing the effective value at `serve` startup, which would not
  depend on the tool being granted.

  When the setting is off, `power_lpar` reads no ownership token and opens no SSH
  connection — the call path is exactly what it was before this setting existed.
  A caller that wants ownership facts without the guard can read them in one REST
  call with `list_lpar_ownership` / `hmc_list_lpar_ownership` (ADR 0071).

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

### Variable names are matched without regard to case

`HMCConfig` leaves pydantic-settings' `case_sensitive` at its `False` default, so
`hmc_host=…` and `Hmc_Host=…` reach the `host` field exactly as `HMC_HOST=…`
does. Every precedence statement on this page holds for any casing, including
the `HMC_AUTHORIZE_POWER_OPERATIONS` claim in the [Notes](#notes): a case variant
beats the profile's TOML key for every field in the [Reference](#reference)
table, and a case variant of `HMC_HOST` skips the TOML profile in the same way
the canonical spelling does. The names are written in upper case throughout
because that is the convention, not because the loader requires it. Setting two
casings of the same variable at once resolves to the **last** of them in the
process environment's own order — pydantic-settings folds the environment into
one case-blind mapping, so the later entry overwrites the earlier. Do not rely
on that ordering: export one spelling.

Three readers do **not** fold case, and all three are worth knowing:

- **`HMC_PROFILE` is matched exactly** on POSIX. It is not an `HMCConfig` field;
  `load_profile()` reads it directly to pick a profile, so no case-insensitive
  settings loader is involved. A lower-case `hmc_profile` export selects no
  profile; selection falls back to `default_profile`, or to environment
  variables alone when the file names none. On Windows this does not apply: the
  OS folds every environment variable name to upper case, so `hmc_profile` *is*
  `HMC_PROFILE` there and selects the profile it names.
- **The authorization audit record's `attribution` reads `HMC_AGENT_ID`
  exact-case** ([#543](https://github.com/randomparity/hmc-mcp/issues/543)).
  `audit.py` imports nothing from the package by design, so it carries its own
  read and has not been folded yet. Under a case-variant export the two halves
  of the trail disagree: the ownership stamp and the `X-Audit-Memento` header
  carry the variant's value, while the access-policy decision record shows no
  claimant.
- **A profile's `password_env` value names a variable read exact-case.**
  `load_profile()` looks the name up in `os.environ` directly, and correctly so:
  `password_env` points at an operator-chosen variable rather than at an
  `HMCConfig` field, so there is no field name to fold it onto. Unlike the two
  above, this one **fails hard** instead of degrading — a name that is not
  present exactly as written raises `password_env=… is not set`, and the
  connection never opens. The templates in this repository always give it an
  `HMC_*` name, so a case-variant export of that name is the likely way to hit
  it.

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
- **A key that names no field is dropped silently**, so a column-name drift
  (`hostname` for `host`) surfaces later as a missing setting rather than at the
  call. `HMCConfig.validate_credentials()` reports that as
  `host (HMC_HOST / --host)` — its hints name the operator's knobs, and on this
  path neither the variable nor the flag applies. Read the parenthesised name as
  the field, and check your keys against `HMCConfig.model_fields` if a value you
  supplied did not arrive.

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
