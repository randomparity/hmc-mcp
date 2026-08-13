# Spec: hmc_list_configured_hosts and TOML-first Configuration Documentation

**Issue:** #128  
**ADR:** [0010-list-configured-hosts.md](../../adr/0010-list-configured-hosts.md)  
**Branch:** feat/list-configured-hosts-128  
**BASE_BRANCH:** main  
**Guardrail:** `just verify`

---

## 1. Context

Issues #124–#127 built the platform-native TOML profile loader, CLI config
commands, and per-call profile routing for both REST-backed and SSH-backed MCP
tools. Issue #128 completes the configuration story with two deliverables:

1. **`hmc_list_configured_hosts`** — a read-only MCP tool that enumerates
   configured profiles without any network calls. Agents need this to discover
   which HMCs are configured before selecting a `profile=` argument.

2. **Documentation cleanup** — the README still references checkout-local `.env`
   instructions and the `.env.example` artifact. They are now incorrect: the
   config contract is platform-native TOML profiles (ADR 0006). These must be
   removed and replaced with TOML-first documentation.

---

## 2. `hmc_list_configured_hosts`

### 2.1 Behaviour contract

- **No network calls.** The tool reads only the TOML config file and relevant
  environment variables. It never opens an HTTP or SSH connection.
- **Secret-free.** The response never contains literal passwords, resolved
  `password_env` values, or the contents of SSH key files. Only presence
  booleans are emitted.
- **Per-profile fields returned:**

  | Field | Type | Source |
  |-------|------|--------|
  | `name` | string | TOML profile key |
  | `host` | string | profile `host` field |
  | `user` | string | profile `user` field |
  | `port` | int | profile `port` (default `12443`) |
  | `verify_ssl` | bool | profile `verify_ssl` (default `false`) |
  | `is_default` | bool | `true` when name == `default_profile` in TOML |
  | `has_password` | bool | `true` when `password` or `password_env` key is present in the raw TOML |
  | `has_ssh_key` | bool | `true` when `ssh_key_file` key is present |

- **No config file:** returns `{"profiles": [], "config_file": null}`.
- **Config file present:** returns `{"profiles": [...], "config_file": "<path>"}`.
- **TOML parse error:** raises `ValueError` with a message that includes the
  config path and the TOML error. The tool must not crash silently.

### 2.2 Implementation location

`hmc_list_configured_hosts` is implemented in the existing
`src/hmc_mcp/server_system.py` module, which already holds other lightweight
metadata and inventory tools. A new file is not warranted.

**Rationale:** `server_system.py` contains `hmc_console_info`, which is the
closest analogue — a lightweight HMC metadata query. Keeping the tool in an
existing module avoids a new import in `server.py` and a new test fixture file.

### 2.3 Secret-redaction approach

The tool reads the TOML file as a raw dict (`tomllib.loads`) — **not** through
`load_profile`, which resolves `password_env` values and would require the
secret to be set. The raw dict is inspected for key presence only. This matches
the pattern already used in `cli_config.py`'s `config_show` command.

### 2.4 Registration

- Added to `READ_ONLY_TOOLS` in `src/hmc_mcp/_app.py`.
- Tagged `_READ_ONLY` on its `@mcp.tool` decorator.
- Exported from `src/hmc_mcp/server.py` alongside other system tools.
- `tests/app/test_capabilities.py` updated to include `hmc_list_configured_hosts`
  in `READ_ONLY_TOOLS` coverage.

### 2.5 Threat model

**Trust boundary:** The tool reads the operator's own config file (owner-written,
platform-native path). No untrusted input is accepted — the tool takes zero
parameters. The TOML is parsed with `tomllib` (stdlib, read-only parser, no
code execution). The only output is a dict of safe metadata fields.

**Actor model:** The only actor is the local operator running the MCP server.
The MCP server is a single-user local deployment over stdio transport. No
remote actor can supply input to this tool.

**Controls:**
- Secret fields are never read from the TOML value — only key presence is checked.
- `password_env` is not resolved; the environment variable is not accessed.
- `ssh_key_file` path is not read or validated; only key presence is checked.
- The return schema is built from a fixed set of fields; no TOML key is passed
  through to the caller verbatim (except `host`, `user`, `port`, `verify_ssl`
  which are non-secret connection metadata).

**Out of scope:** File-system path traversal (config path is platform-derived,
not caller-supplied), TOML injection (stdlib parser, read-only), privilege
escalation (the process already has whatever access the operator's user does).

---

## 3. README and documentation cleanup

### 3.1 Changes required

- **Remove `.env.example`** from the repository root. It references the old
  repo-root `.env` credential pattern, which is superseded by ADR 0006 (TOML
  profiles). The starter TOML example in `cli_config.py` replaces it.

- **Update the README `## Configure` section** to lead with the TOML profile
  (already there) and remove the `(or point your MCP client's env at
  HMC_HOST/HMC_USER/HMC_PASSWORD)` aside in the *Use with Hermes Agent*
  paragraph — that aside implies the `.env` pattern is still valid.

- **Update the README `## Layout` section** to remove the stale comment:
  `config.py      # pydantic-settings config (env/.env/flags)` → describe the
  real contract: TOML profile + env vars + CLI flags.

- **Add `hmc_list_configured_hosts` to the README tool table** under
  *Read-only / inventory*.

- **Add a TOML example to the README** (already present at lines 28–46;
  verify it includes a `password_env` example). No change needed if it
  covers `password_env`.

### 3.2 `.env.example` removal

`.env.example` is tracked in git. It must be removed with `git rm`. The
`.secrets.baseline` will need a re-scan if the file contained any secrets
entries — run `just verify` to confirm.

---

## 4. Tests

### 4.1 Unit tests (no network)

New test file `tests/unit/test_server_hosts.py` (or tests added to
`tests/unit/test_config.py`) covering:

1. **No config file** → returns `{"profiles": [], "config_file": None}`.
2. **Single profile, is default** → correct fields, `is_default=True`.
3. **Two profiles, one default** → both returned; only one has `is_default=True`.
4. **Password literal present** → `has_password=True`, no password value in
   output.
5. **password_env present** → `has_password=True`, env var is NOT resolved.
6. **ssh_key_file present** → `has_ssh_key=True`, no key content in output.
7. **TOML parse error** → `ValueError` raised, message includes config path.
8. **port and verify_ssl defaults** → correct when not specified in profile.

### 4.2 Capability test update

`tests/app/test_capabilities.py`: add `"hmc_list_configured_hosts"` to
`READ_ONLY_TOOLS` (it already tests that every tool in the set carries
`readOnlyHint=True`).

---

## 5. Acceptance criteria (testable)

| # | Criterion | Test |
|---|-----------|------|
| AC1 | `hmc_list_configured_hosts` is in `READ_ONLY_TOOLS` and carries `readOnlyHint=True` | `test_every_registered_tool_matches_its_category` |
| AC2 | Returns correct fields for each profile without network calls | unit tests 1–3 |
| AC3 | `has_password=True` when `password` key present; never emits password value | unit test 4 |
| AC4 | `has_password=True` when `password_env` key present; env var NOT resolved | unit test 5 |
| AC5 | `has_ssh_key=True` when `ssh_key_file` key present; no path or content emitted | unit test 6 |
| AC6 | `ValueError` on TOML parse error | unit test 7 |
| AC7 | Empty list when no config file | unit test 1 |
| AC8 | README documents the tool | manual / smoke |
| AC9 | `.env.example` removed from repo | `git ls-files` |
| AC10 | `just verify` green with no new warnings | CI |

---

## 6. Out of scope

- Editing `HMCConfig` fields (no new env vars, no schema changes).
- Merging profiles with env-var overrides in the tool response (the tool shows
  the raw TOML config, not the merged runtime config).
- `password_env` resolution or validation.
- MCP profile routing for the new tool itself (it takes zero parameters and
  makes no network calls; no `profile=` parameter is needed).
