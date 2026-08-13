# hmc_list_configured_hosts + TOML-first Docs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `hmc_list_configured_hosts` read-only MCP tool and clean up the repo-root `.env` documentation artifacts.

**Architecture:** Single tool function in `server_system.py` that reads the platform-native TOML config file (raw `tomllib.loads`, key-presence-only for secrets), registers itself in `READ_ONLY_TOOLS`, and returns structured profile metadata. README and `.env.example` updates complete the configuration contract.

**Tech Stack:** Python 3.12+, tomllib (stdlib), FastMCP, pytest, uv

**Branch:** `feat/list-configured-hosts-128`  
**BASE_BRANCH:** `main`  
**Guardrail:** `just verify` (runs `ruff check`, `ty check`, `detect-secrets-hook`, `zizmor`, `check_env_vars.py`, `pytest -q`, `smoke_mcp.py`, and CLI help groups)

## Global Constraints

- All imports and code follow existing module conventions (see `server_system.py` for tool pattern)
- No new environment variables; `HMC_*` field count stays at 10
- Secret values never appear in tool output; only key-presence booleans
- `HMCConfig` defaults for `port` (12443) and `verify_ssl` (False) are read from `HMCConfig.model_fields`, not hardcoded
- All tests use `tmp_path` / `monkeypatch` — no test touches the real user home or real config file
- `just verify` must stay green after every commit
- The `.secrets.baseline` must be regenerated after `.env.example` removal

---

## Task 1: Add `hmc_list_configured_hosts` to `server_system.py` and `_app.py`

**Files:**
- Modify: `src/hmc_mcp/server_system.py` — add the tool function
- Modify: `src/hmc_mcp/_app.py` — add tool name to `READ_ONLY_TOOLS`
- Modify: `src/hmc_mcp/server.py` — export the new function

**Interfaces:**
- Produces: `hmc_list_configured_hosts() -> dict` with shape:
  ```python
  {
      "profiles": [
          {
              "name": str,
              "host": str,
              "user": str,
              "port": int,       # HMCConfig default: 12443
              "verify_ssl": bool,  # HMCConfig default: False
              "is_default": bool,
              "has_password": bool,
              "has_ssh_key": bool,
          },
          ...
      ],
      "config_file": str | None,  # absolute path string or None
  }
  ```

- [ ] **Step 1: Locate the insertion point in `server_system.py`**

  Read `src/hmc_mcp/server_system.py` lines 1–30 to understand the import block, then find `hmc_console_info` — the new tool goes after it.

  ```bash
  grep -n "hmc_console_info\|^@mcp.tool\|^def hmc_\|^import\|^from" src/hmc_mcp/server_system.py | head -30
  ```

- [ ] **Step 2: Add required imports to `src/hmc_mcp/server_system.py`**

  The current import block in `server_system.py` does NOT include `tomllib`, `Path`, `HMCConfig`, or `resolve_config_path`. Add these three lines to the standard-library / relative-import section at the top of the file (after the existing `from __future__ import annotations` line, before the first `from ._app import` line):

  ```python
  import tomllib
  from pathlib import Path
  ```

  And add `HMCConfig, resolve_config_path` to the existing relative imports section:
  ```python
  from .config import HMCConfig, resolve_config_path
  ```

  Verify the imports are present before continuing:
  ```bash
  head -20 src/hmc_mcp/server_system.py
  ```

  Then confirm the module still imports cleanly:
  ```bash
  uv run python -c "import hmc_mcp.server_system"
  ```

  Expected: exits 0 with no output.

- [ ] **Step 3: Add the tool function to `src/hmc_mcp/server_system.py`**

  Insert after the `hmc_console_info` function body:

  ```python
  @mcp.tool(annotations=_READ_ONLY)
  def hmc_list_configured_hosts() -> dict:
      """List all configured HMC profiles from the platform-native TOML config.

      Returns profile names, hostnames, users, ports, TLS settings, default
      status, and credential-presence booleans. Never returns passwords, resolved
      password_env values, or SSH key contents — only has_password / has_ssh_key
      presence indicators.

      No network calls are made. When no config file exists, returns an empty
      profile list.
      """
      config_path = resolve_config_path()
      if config_path is None:
          return {"profiles": [], "config_file": None}

      try:
          raw_text = config_path.read_text(encoding="utf-8")
      except OSError as exc:
          raise ValueError(f"{config_path}: cannot read config file: {exc}") from exc

      try:
          doc = tomllib.loads(raw_text)
      except tomllib.TOMLDecodeError as exc:
          raise ValueError(f"{config_path}: TOML parse error: {exc}") from exc

      default_profile = doc.get("default_profile")
      profiles_raw: dict = doc.get("profiles", {})

      # Read the HMCConfig field defaults once — port and verify_ssl come from
      # the model, not hardcoded constants, so they stay in sync if the model changes.
      _fields = HMCConfig.model_fields
      _default_port = int(_fields["port"].default)
      _default_verify_ssl = bool(_fields["verify_ssl"].default)

      profiles = []
      for name, entry in profiles_raw.items():
          # Build each profile dict from named fields only.
          # NEVER spread entry directly — it may contain a literal "password" key.
          profiles.append({
              "name": name,
              "host": entry.get("host", ""),
              "user": entry.get("user", ""),
              "port": int(entry.get("port", _default_port)),
              "verify_ssl": bool(entry.get("verify_ssl", _default_verify_ssl)),
              "is_default": (name == default_profile),
              "has_password": bool(entry.get("password") or entry.get("password_env")),
              "has_ssh_key": bool(entry.get("ssh_key_file")),
          })

      return {"profiles": profiles, "config_file": str(config_path)}
  ```

- [ ] **Step 3: Add to `READ_ONLY_TOOLS` in `src/hmc_mcp/_app.py`**

  Find the `READ_ONLY_TOOLS = frozenset({` block and add `"hmc_list_configured_hosts"` in alphabetical order within the set (it belongs near `"hmc_list_adapters"`):

  ```python
  # existing entries around the insertion point:
  "hmc_list_adapters",
  "hmc_list_configured_hosts",  # ADD THIS LINE
  "hmc_list_clusters",
  ```

- [ ] **Step 4: Export from `src/hmc_mcp/server.py`**

  Find the `from .server_system import (` block. Add `hmc_list_configured_hosts as hmc_list_configured_hosts,` in alphabetical order:

  ```python
  from .server_system import (
      hmc_capacity_report as hmc_capacity_report,
      hmc_console_info as hmc_console_info,
      hmc_find_placement as hmc_find_placement,
      hmc_find_system as hmc_find_system,
      hmc_get_job as hmc_get_job,
      hmc_list_configured_hosts as hmc_list_configured_hosts,  # ADD
      hmc_list_resources as hmc_list_resources,
      ...
  ```

- [ ] **Step 5: Run the smoke script to verify the tool registers correctly**

  ```bash
  uv run python scripts/smoke_mcp.py 2>&1 | grep -E "hmc_list_configured|error|Error"
  ```

  Expected: `hmc_list_configured_hosts` appears in the tool list, no errors.

- [ ] **Step 6: Run static checks**

  ```bash
  just static
  ```

  Expected: `All checks passed!` on lint and typecheck; no new secrets or workflow findings; env-vars count stays at 10.

- [ ] **Step 7: Commit**

  ```bash
  git add src/hmc_mcp/server_system.py src/hmc_mcp/_app.py src/hmc_mcp/server.py
  git commit -m "feat(mcp): add hmc_list_configured_hosts read-only tool (#128)"
  ```

---

## Task 2: Unit tests for `hmc_list_configured_hosts`

**Files:**
- Create: `tests/unit/test_server_hosts.py`

**Interfaces:**
- Consumes: `hmc_list_configured_hosts` from `hmc_mcp.server_system`; `resolve_config_path` from `hmc_mcp.config`

- [ ] **Step 1: Write the test file**

  Create `tests/unit/test_server_hosts.py`:

  ```python
  """Tests for hmc_list_configured_hosts (issue #128).

  All tests use tmp_path and monkeypatch — no test touches the real user home
  or the real platform-native config file.
  """

  from __future__ import annotations

  from pathlib import Path
  from unittest.mock import patch

  import pytest

  from hmc_mcp.server_system import hmc_list_configured_hosts


  # ---------------------------------------------------------------------------
  # Helpers
  # ---------------------------------------------------------------------------

  def _write_toml(path: Path, content: str) -> Path:
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_text(content, encoding="utf-8")
      return path


  def _patch_config_path(tmp_path, content: str | None):
      """Context manager: patch resolve_config_path to return a tmp file or None."""
      if content is None:
          return patch("hmc_mcp.server_system.resolve_config_path", return_value=None)
      cfg = _write_toml(tmp_path / "config.toml", content)
      return patch("hmc_mcp.server_system.resolve_config_path", return_value=cfg)


  # ---------------------------------------------------------------------------
  # Test 1: No config file
  # ---------------------------------------------------------------------------

  def test_no_config_file(tmp_path):
      """Returns empty profiles list when no config file exists."""
      with patch("hmc_mcp.server_system.resolve_config_path", return_value=None):
          result = hmc_list_configured_hosts()
      assert result == {"profiles": [], "config_file": None}


  # ---------------------------------------------------------------------------
  # Test 2: Single profile, is default
  # ---------------------------------------------------------------------------

  SINGLE_PROFILE_TOML = """\
  default_profile = "prod"

  [profiles.prod]
  host = "hmc.example.com"
  user = "admin"
  password = "secret"  # pragma: allowlist secret
  """


  def test_single_profile_is_default(tmp_path):
      """Returns correct fields; is_default=True for the default profile."""
      with _patch_config_path(tmp_path, SINGLE_PROFILE_TOML):
          result = hmc_list_configured_hosts()

      assert result["config_file"] is not None
      assert len(result["profiles"]) == 1
      p = result["profiles"][0]
      assert p["name"] == "prod"
      assert p["host"] == "hmc.example.com"
      assert p["user"] == "admin"
      assert p["is_default"] is True
      assert p["port"] == 12443      # HMCConfig default
      assert p["verify_ssl"] is False  # HMCConfig default


  # ---------------------------------------------------------------------------
  # Test 3: Two profiles, one default
  # ---------------------------------------------------------------------------

  TWO_PROFILE_TOML = """\
  default_profile = "prod"

  [profiles.prod]
  host = "hmc.example.com"
  user = "admin"
  password = "prodpass"  # pragma: allowlist secret

  [profiles.dev]
  host = "hmc-dev.example.com"
  user = "devadmin"
  password = "devpass"  # pragma: allowlist secret
  """


  def test_two_profiles_one_default(tmp_path):
      """Both profiles returned; only the default has is_default=True."""
      with _patch_config_path(tmp_path, TWO_PROFILE_TOML):
          result = hmc_list_configured_hosts()

      assert len(result["profiles"]) == 2
      by_name = {p["name"]: p for p in result["profiles"]}
      assert by_name["prod"]["is_default"] is True
      assert by_name["dev"]["is_default"] is False


  # ---------------------------------------------------------------------------
  # Test 4: Password literal present — no password value in output
  # ---------------------------------------------------------------------------

  PASSWORD_TOML = """\
  [profiles.prod]
  host = "hmc.example.com"
  user = "admin"
  password = "supersecret"  # pragma: allowlist secret
  """


  def test_password_literal_has_password_true_no_value(tmp_path):
      """has_password=True; the literal password value must not appear in output."""
      with _patch_config_path(tmp_path, PASSWORD_TOML):
          result = hmc_list_configured_hosts()

      p = result["profiles"][0]
      assert p["has_password"] is True
      # The raw profile dict must never be forwarded; verify no password key leaks
      assert "password" not in p
      assert "supersecret" not in str(result)  # paranoid check


  # ---------------------------------------------------------------------------
  # Test 5: password_env present — env var NOT resolved
  # ---------------------------------------------------------------------------

  PASSWORD_ENV_TOML = """\
  [profiles.prod]
  host = "hmc.example.com"
  user = "admin"
  password_env = "MY_PROD_PW"  # pragma: allowlist secret
  """


  def test_password_env_has_password_true_not_resolved(tmp_path, monkeypatch):
      """has_password=True when password_env key present; env var is never read."""
      monkeypatch.delenv("MY_PROD_PW", raising=False)
      with _patch_config_path(tmp_path, PASSWORD_ENV_TOML):
          result = hmc_list_configured_hosts()

      p = result["profiles"][0]
      assert p["has_password"] is True
      assert "password_env" not in p


  # ---------------------------------------------------------------------------
  # Test 6: No credentials — has_password=False, has_ssh_key=False
  # ---------------------------------------------------------------------------

  NO_CRED_TOML = """\
  [profiles.test]
  host = "hmc.example.com"
  user = "admin"
  """


  def test_no_credentials_false_booleans(tmp_path):
      """has_password=False and has_ssh_key=False when no credential keys present."""
      with _patch_config_path(tmp_path, NO_CRED_TOML):
          result = hmc_list_configured_hosts()

      p = result["profiles"][0]
      assert p["has_password"] is False
      assert p["has_ssh_key"] is False


  # ---------------------------------------------------------------------------
  # Test 7: ssh_key_file present — has_ssh_key=True, no key content
  # ---------------------------------------------------------------------------

  SSH_KEY_TOML = """\
  [profiles.prod]
  host = "hmc.example.com"
  user = "admin"
  ssh_key_file = "/home/user/.ssh/id_rsa"
  """


  def test_ssh_key_has_ssh_key_true_no_content(tmp_path):
      """has_ssh_key=True; key path and content must not appear in output."""
      with _patch_config_path(tmp_path, SSH_KEY_TOML):
          result = hmc_list_configured_hosts()

      p = result["profiles"][0]
      assert p["has_ssh_key"] is True
      assert "ssh_key_file" not in p
      assert "/home/user/.ssh" not in str(result)


  # ---------------------------------------------------------------------------
  # Test 8: TOML parse error → ValueError with config path
  # ---------------------------------------------------------------------------

  def test_toml_parse_error(tmp_path):
      """TOML parse error → ValueError whose message includes the config path."""
      cfg = tmp_path / "config.toml"
      cfg.write_text("this is [[not valid toml]]\n", encoding="utf-8")
      with patch("hmc_mcp.server_system.resolve_config_path", return_value=cfg):
          with pytest.raises(ValueError, match="TOML parse error"):
              hmc_list_configured_hosts()


  # ---------------------------------------------------------------------------
  # Test 9: PermissionError reading config → ValueError with path and OS error
  # ---------------------------------------------------------------------------

  def test_permission_error_reading_config(tmp_path):
      """PermissionError reading config file → ValueError with path and OS error."""
      cfg = tmp_path / "config.toml"
      cfg.write_text("[profiles.x]\nhost = 'h'\nuser = 'u'\n", encoding="utf-8")
      with patch("hmc_mcp.server_system.resolve_config_path", return_value=cfg), \
           patch.object(Path, "read_text", side_effect=PermissionError("Permission denied")):
          with pytest.raises(ValueError, match="cannot read config file"):
              hmc_list_configured_hosts()


  # ---------------------------------------------------------------------------
  # Test 10: port and verify_ssl defaults from HMCConfig
  # ---------------------------------------------------------------------------

  CUSTOM_PORT_TOML = """\
  [profiles.prod]
  host = "hmc.example.com"
  user = "admin"
  password = "p"  # pragma: allowlist secret
  port = 9999
  verify_ssl = true
  """

  DEFAULTS_TOML = """\
  [profiles.prod]
  host = "hmc.example.com"
  user = "admin"
  password = "p"  # pragma: allowlist secret
  """


  def test_port_verify_ssl_explicit_values(tmp_path):
      """Explicit port and verify_ssl in TOML are used."""
      with _patch_config_path(tmp_path, CUSTOM_PORT_TOML):
          result = hmc_list_configured_hosts()
      p = result["profiles"][0]
      assert p["port"] == 9999
      assert p["verify_ssl"] is True


  def test_port_verify_ssl_defaults_from_hmcconfig(tmp_path):
      """When port and verify_ssl are absent, defaults come from HMCConfig.model_fields."""
      from hmc_mcp.config import HMCConfig
      expected_port = int(HMCConfig.model_fields["port"].default)
      expected_verify_ssl = bool(HMCConfig.model_fields["verify_ssl"].default)

      with _patch_config_path(tmp_path, DEFAULTS_TOML):
          result = hmc_list_configured_hosts()

      p = result["profiles"][0]
      assert p["port"] == expected_port
      assert p["verify_ssl"] == expected_verify_ssl
  ```

- [ ] **Step 2: Run the new tests**

  ```bash
  uv run pytest tests/unit/test_server_hosts.py -v
  ```

  Expected: all tests pass. If a test fails because the implementation isn't quite right, fix the implementation in `server_system.py` and re-run.

- [ ] **Step 3: Run the full test suite**

  ```bash
  uv run pytest -q
  ```

  Expected: all tests pass (no regressions).

- [ ] **Step 4: Commit**

  ```bash
  git add tests/unit/test_server_hosts.py
  git commit -m "test: unit tests for hmc_list_configured_hosts (#128)"
  ```

---

## Task 3: Update capability test

**Files:**
- Modify: `tests/app/test_capabilities.py` — add `"hmc_list_configured_hosts"` to `READ_ONLY_TOOLS`

**Interfaces:**
- Consumes: `READ_ONLY_TOOLS` from `hmc_mcp.server`

- [ ] **Step 1: Add to READ_ONLY_TOOLS in the capability test**

  In `tests/app/test_capabilities.py`, find the import:
  ```python
  from hmc_mcp.server import (
      DESTRUCTIVE_TOOLS,
      READ_ONLY_TOOLS,
      ...
  )
  ```

  The test `test_every_registered_tool_matches_its_category` checks that every tool in `READ_ONLY_TOOLS` carries `readOnlyHint=True` at runtime. Adding the tool name to `_app.py`'s `READ_ONLY_TOOLS` frozenset (done in Task 1) is what this test validates — no additional change to the test file is needed beyond confirming the test imports the constant from `server.py`.

  Verify by running:
  ```bash
  uv run pytest tests/app/test_capabilities.py -v
  ```

  Expected: all capability tests pass, including `test_every_registered_tool_matches_its_category` which now covers `hmc_list_configured_hosts`.

- [ ] **Step 2: Commit if any change was needed**

  ```bash
  git add tests/app/test_capabilities.py
  git commit -m "test: verify hmc_list_configured_hosts capability classification (#128)"
  ```

  (Only commit if a file changed; if the test already passes from Task 1's `READ_ONLY_TOOLS` update, skip this commit.)

---

## Task 4: Update README and remove `.env.example`

**Files:**
- Modify: `README.md` — add tool to table; remove `.env` aside in Hermes Agent section; fix Layout comment
- Delete: `.env.example` — `git rm .env.example`
- Modify: `.secrets.baseline` — regenerate after `.env.example` removal

**Interfaces:**
- (documentation only)

- [ ] **Step 1: Add `hmc_list_configured_hosts` to the README tool table**

  Find the `**Read-only / inventory**` tool table in `README.md`. It starts with:
  ```
  | Tool                  | Description |
  |-----------------------|-------------|
  | `hmc_console_info`    | ...
  ```

  Add a row (keep the table alphabetically ordered by tool name within the inventory section):
  ```
  | `hmc_list_configured_hosts` | List configured HMC profiles from the platform-native TOML config; returns name, host, user, port, TLS setting, default flag, and credential-presence booleans. No network calls. |
  ```

  Place it after `hmc_get_proc_compat_modes` (or near alphabetically equivalent tools) — review the existing table order and insert appropriately.

- [ ] **Step 2: Remove the `.env` aside in the Hermes Agent section**

  Find the paragraph:
  ```
  (or point your MCP client's env at `HMC_HOST`/`HMC_USER`/`HMC_PASSWORD`).
  ```

  Remove that parenthetical line entirely. After the change the section should read:
  ```bash
  hermes mcp add hmc -- uv run --directory ~/src/hmc-mcp hmc-mcp serve
  ```
  with no following parenthetical.

- [ ] **Step 3: Fix the Layout section comment**

  Find:
  ```
    config.py      # pydantic-settings config (env/.env/flags)
  ```

  Replace with:
  ```
    config.py      # pydantic-settings config (TOML profile + env vars + CLI flags)
  ```

- [ ] **Step 4: Remove `.env.example`**

  ```bash
  git rm .env.example
  ```

- [ ] **Step 5: Regenerate `.secrets.baseline`**

  The baseline scan needs to be updated since `.env.example` is gone:

  ```bash
  git ls-files -z | xargs -0 uv run detect-secrets scan --no-verify > .secrets.baseline.new
  mv .secrets.baseline.new .secrets.baseline
  ```

  Then verify the new baseline is clean:
  ```bash
  git ls-files -z | xargs -0 uv run detect-secrets-hook --baseline .secrets.baseline --no-verify --
  ```

  Expected: exits 0 with no output (or a clean message).

  If `detect-secrets scan` isn't available, use the approach that matches the existing baseline format. Check the current baseline to see the generator command:
  ```bash
  head -5 .secrets.baseline
  ```

- [ ] **Step 6: Run full static + tests**

  ```bash
  just verify
  ```

  Expected: all checks pass.

- [ ] **Step 7: Confirm staged changes and commit**

  ```bash
  git status --short
  ```

  Expected: `D .env.example` (deleted/staged by `git rm`), `M README.md`, `M .secrets.baseline`.
  If `.env.example` does not show as deleted/staged, run `git rm .env.example` again.

  ```bash
  git add README.md .secrets.baseline
  git commit -m "docs: add hmc_list_configured_hosts to README; remove .env.example (#128)"
  ```

---

## Self-Review

**Spec coverage check:**

| Spec section | Task covering it |
|---|---|
| §2.1 `hmc_list_configured_hosts` per-profile fields | Task 1 + Task 2 (tests 1–3) |
| §2.2 Implementation location (`server_system.py`) | Task 1 |
| §2.3 Secret-redaction: raw dict forwarding forbidden | Task 1 (build), Task 2 test 4 (paranoid check) |
| §2.3 `port`/`verify_ssl` from `HMCConfig.model_fields` | Task 1, Task 2 test 10 |
| §2.3 `PermissionError` → `ValueError` | Task 1, Task 2 test 9 |
| §2.4 Registration (`READ_ONLY_TOOLS`, `_READ_ONLY`, `server.py`) | Task 1 |
| §4.1 Test 1 (no config file) | Task 2 test 1 |
| §4.1 Test 2 (single profile, is default) | Task 2 test 2 |
| §4.1 Test 3 (two profiles, one default) | Task 2 test 3 |
| §4.1 Test 4 (password literal, no leak) | Task 2 test 4 |
| §4.1 Test 5 (password_env, not resolved) | Task 2 test 5 |
| §4.1 Test 6 (no credentials, false booleans) | Task 2 test 6 |
| §4.1 Test 7 (ssh_key_file, no content) | Task 2 test 7 |
| §4.1 Test 8 (TOML parse error) | Task 2 test 8 |
| §4.1 Test 9 (PermissionError) | Task 2 test 9 |
| §4.1 Test 10 (port/verify_ssl defaults) | Task 2 test 10 |
| §4.2 Capability test update | Task 3 |
| §3.1 README tool table | Task 4 step 1 |
| §3.1 Remove `.env` aside | Task 4 step 2 |
| §3.1 Fix Layout comment | Task 4 step 3 |
| §3.2 `.env.example` removal | Task 4 step 4 |
| §3.2 `.secrets.baseline` regeneration | Task 4 step 5 |

**Placeholder scan:** None found. All code blocks are complete.

**Type consistency:** `hmc_list_configured_hosts` returns `dict` consistently across all references.
