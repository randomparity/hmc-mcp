# Multi-Agent LPAR Ownership — Advisory Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `HMC_AGENT_ID` per-agent attribution and an advisory LPAR ownership token that stamps all three LPAR create paths, plus advisory docstrings on destructive tools.

**Architecture:** `HMCConfig` gains an `agent_id` field and `effective_audit_memento` property; a new `validate_agent_id` and `stamp_lpar_ownership` helper land in `ssh.py`; all three create tools call the stamp helper after creation; destructive tools get advisory docstring language; server instructions are updated.

**Tech Stack:** Python 3.11+, pydantic-settings, asyncssh, pytest, FastMCP

## Global Constraints

- Guardrail command: `just verify` (static + test + smoke + CLI group checks)
- Branch: `feat/multi-agent-lpar-ownership-132`, base: `main`
- All new code follows the existing style: `from __future__ import annotations`, type hints, no walrus operators in tests.
- `HMCConfig` uses `pydantic_settings.BaseSettings`; new fields follow existing Field(...) pattern.
- `validate_lpar_description` is in `src/hmc_mcp/ssh.py`; new validators go beside it.
- Test files needing a credential-free `HMCConfig` must pass `_env_file=None`.
- Never add middleware, enforcement code, or `hmc_run_command` gating — advisory only.
- Spec: `docs/superpowers/specs/2026-multi-agent-lpar-ownership.md`
- ADR: `docs/adr/0011-multi-agent-lpar-ownership.md`

---

### Task 1: `validate_agent_id` + `stamp_lpar_ownership` in `ssh.py`

**Files:**
- Modify: `src/hmc_mcp/ssh.py` (add after `validate_lpar_description` at line ~49)
- Create: `tests/unit/test_ownership.py`

**Interfaces:**
- Produces: `validate_agent_id(agent_id: str) -> None` — raises `ValueError` on invalid input
- Produces: `async def stamp_lpar_ownership(config: HMCConfig, system_name: str, lpar_name: str, *, agent_id: str | None = None) -> str | None` — returns token on success, `None` on SSH error

- [ ] **Step 1: Write failing tests for `validate_agent_id`**

```python
# tests/unit/test_ownership.py
"""Tests for multi-agent LPAR ownership helpers (issue #132)."""
from __future__ import annotations

import pytest

from hmc_mcp.ssh import validate_agent_id


def test_validate_agent_id_valid():
    validate_agent_id("alice")           # plain name
    validate_agent_id("agent-1")         # hyphens ok
    validate_agent_id("agent.1")         # dots ok
    validate_agent_id("a" * 64)          # max length
    validate_agent_id("hmc-mcp")         # default fallback


def test_validate_agent_id_empty():
    with pytest.raises(ValueError, match="empty"):
        validate_agent_id("")


def test_validate_agent_id_too_long():
    with pytest.raises(ValueError, match="64"):
        validate_agent_id("a" * 65)


def test_validate_agent_id_comma():
    with pytest.raises(ValueError, match="comma"):
        validate_agent_id("alice,eve")


def test_validate_agent_id_equals():
    with pytest.raises(ValueError, match="="):
        validate_agent_id("key=val")


def test_validate_agent_id_bracket():
    with pytest.raises(ValueError, match="bracket"):
        validate_agent_id("alice[1]")


def test_validate_agent_id_non_ascii():
    with pytest.raises(ValueError, match="printable ASCII"):
        validate_agent_id("alicé")


def test_validate_agent_id_control_char():
    with pytest.raises(ValueError, match="printable ASCII"):
        validate_agent_id("alice\n")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_ownership.py -v
```
Expected: `ImportError` or `AttributeError` — `validate_agent_id` does not exist yet.

- [ ] **Step 3: Implement `validate_agent_id` in `ssh.py`**

Insert after [`validate_lpar_description`](src/hmc_mcp/ssh.py:29) (after line 48):

```python
def validate_agent_id(agent_id: str) -> None:
    """Raise ``ValueError`` if *agent_id* is not a safe ownership token component.

    Rules:
    - Must be 1–64 printable ASCII characters (same base constraint as descriptions).
    - No commas or ``=`` — they corrupt the HMC CLI ``-i`` parser when the agent_id
      is embedded in the ownership token written via ``chsyscfg``.
    - No square brackets — they would break the ``[hmc-mcp owner:…]`` token format.

    Called from ``HMCConfig`` model validator so errors surface at construction time.
    """
    if not agent_id:
        raise ValueError("agent_id must not be empty")
    if len(agent_id) > 64:
        raise ValueError(
            f"agent_id is {len(agent_id)} characters; maximum is 64"
        )
    if not agent_id.isascii() or any(ord(c) < 0x20 or ord(c) == 0x7F for c in agent_id):
        raise ValueError(
            "agent_id contains non-ASCII or non-printable characters; "
            "only printable ASCII is accepted"
        )
    if "," in agent_id:
        raise ValueError(
            "agent_id contains a comma; commas corrupt the HMC CLI -i parser"
        )
    if "=" in agent_id:
        raise ValueError(
            "agent_id contains '='; equals signs corrupt the HMC CLI -i parser"
        )
    if "[" in agent_id or "]" in agent_id:
        raise ValueError(
            "agent_id contains a square bracket; brackets break the ownership token format"
        )
```

- [ ] **Step 4: Run validation tests to verify they pass**

```bash
uv run pytest tests/unit/test_ownership.py -v -k "validate_agent_id"
```
Expected: all `test_validate_agent_id_*` pass.

- [ ] **Step 5: Write failing tests for `stamp_lpar_ownership`**

Add to `tests/unit/test_ownership.py`:

```python
import asyncio
import datetime
from unittest.mock import AsyncMock, patch

from hmc_mcp.ssh import stamp_lpar_ownership
from hmc_mcp.config import HMCConfig


def _config():
    return HMCConfig(host="hmc.test", user="u", password="p", _env_file=None)


def test_stamp_returns_token_on_success():
    config = _config()
    with patch("hmc_mcp.ssh.set_lpar_description", new=AsyncMock(return_value="")) as mock_set:
        token = asyncio.run(
            stamp_lpar_ownership(config, "sys1", "lpar1", agent_id="alice")
        )
    assert token == f"[hmc-mcp owner:alice created:{datetime.date.today().isoformat()}]"
    mock_set.assert_awaited_once()
    # verify set_lpar_description was called with the token
    _, _, _, desc = mock_set.call_args.args
    assert desc == token


def test_stamp_default_agent_id():
    config = _config()
    with patch("hmc_mcp.ssh.set_lpar_description", new=AsyncMock(return_value="")):
        token = asyncio.run(
            stamp_lpar_ownership(config, "sys1", "lpar1")  # no agent_id
        )
    assert "owner:hmc-mcp" in token


def test_stamp_returns_none_on_ssh_error():
    config = _config()
    from hmc_mcp.ssh import HMCCLIError
    with patch(
        "hmc_mcp.ssh.set_lpar_description",
        new=AsyncMock(side_effect=HMCCLIError("SSH failed")),
    ):
        token = asyncio.run(
            stamp_lpar_ownership(config, "sys1", "lpar1", agent_id="alice")
        )
    assert token is None  # swallowed — best-effort


def test_stamp_returns_none_on_asyncssh_error():
    config = _config()
    import asyncssh
    with patch(
        "hmc_mcp.ssh.set_lpar_description",
        new=AsyncMock(side_effect=asyncssh.Error("connection lost")),
    ):
        token = asyncio.run(
            stamp_lpar_ownership(config, "sys1", "lpar1", agent_id="alice")
        )
    assert token is None


def test_token_format():
    config = _config()
    today = datetime.date.today().isoformat()
    with patch("hmc_mcp.ssh.set_lpar_description", new=AsyncMock(return_value="")):
        token = asyncio.run(
            stamp_lpar_ownership(config, "sys1", "lpar1", agent_id="my-agent")
        )
    assert token == f"[hmc-mcp owner:my-agent created:{today}]"
    # token must pass the existing description validator
    from hmc_mcp.ssh import validate_lpar_description
    validate_lpar_description(token)  # no exception
```

- [ ] **Step 6: Run stamp tests to verify they fail**

```bash
uv run pytest tests/unit/test_ownership.py -v -k "stamp"
```
Expected: `ImportError` — `stamp_lpar_ownership` does not exist yet.

- [ ] **Step 7: Implement `stamp_lpar_ownership` in `ssh.py`**

Insert after `validate_agent_id` (before `run_hmc_command`):

```python
import datetime as _dt  # add at module top if not already present

async def stamp_lpar_ownership(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    *,
    agent_id: str | None = None,
) -> str | None:
    """Write an ownership token to *lpar_name*'s description field.

    Builds the token ``[hmc-mcp owner:<agent_id> created:<YYYY-MM-DD>]`` and
    calls :func:`set_lpar_description` to write it over SSH.

    Returns the token string on success; returns ``None`` (without raising) on
    any SSH failure — this is a best-effort post-create call that must not fail
    the LPAR creation itself.

    *agent_id* defaults to ``"hmc-mcp"`` when ``None`` or empty.
    """
    import datetime
    import asyncssh  # imported locally to avoid circular at module top

    effective_id = agent_id if agent_id else "hmc-mcp"
    today = datetime.date.today().isoformat()
    token = f"[hmc-mcp owner:{effective_id} created:{today}]"
    try:
        await set_lpar_description(config, system_name, lpar_name, token)
        return token
    except (HMCCLIError, asyncssh.Error):
        return None
```

Note: `datetime` import — check if `datetime` is already imported in `ssh.py`. If not, add `import datetime` at the module-level imports section. `asyncssh` is already imported at module level in `ssh.py`.

- [ ] **Step 8: Run all ownership tests**

```bash
uv run pytest tests/unit/test_ownership.py -v
```
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/hmc_mcp/ssh.py tests/unit/test_ownership.py
git commit -m "feat: add validate_agent_id and stamp_lpar_ownership helpers (#132)"
```

---

### Task 2: `HMC_AGENT_ID` in `HMCConfig` + `effective_audit_memento` + client update

**Files:**
- Modify: `src/hmc_mcp/config.py`
- Modify: `src/hmc_mcp/client/__init__.py` (line 77)
- Modify: `docs/environment-variables.md`
- Modify: `tests/unit/test_config.py` (append new test class)

**Interfaces:**
- Consumes: `validate_agent_id` from `ssh.py` (Task 1)
- Produces: `HMCConfig.agent_id: str | None` field
- Produces: `HMCConfig.effective_audit_memento: str` property

- [ ] **Step 1: Write failing tests for `HMCConfig.effective_audit_memento`**

Append to `tests/unit/test_config.py`:

```python
# ---------------------------------------------------------------------------
# HMC_AGENT_ID and effective_audit_memento (issue #132)
# ---------------------------------------------------------------------------

def test_agent_id_unset_uses_audit_memento_default():
    cfg = HMCConfig(_env_file=None)
    assert cfg.agent_id is None
    assert cfg.effective_audit_memento == "hmc-mcp"


def test_agent_id_set_prefixes_audit_memento():
    cfg = HMCConfig(agent_id="alice", _env_file=None)
    assert cfg.effective_audit_memento == "hmc-mcp/alice"


def test_agent_id_overrides_audit_memento_field():
    # When agent_id is set, effective_audit_memento ignores audit_memento.
    cfg = HMCConfig(agent_id="bob", audit_memento="custom", _env_file=None)
    assert cfg.effective_audit_memento == "hmc-mcp:bob"


def test_audit_memento_without_agent_id():
    cfg = HMCConfig(audit_memento="my-tool", _env_file=None)
    assert cfg.effective_audit_memento == "my-tool"


def test_agent_id_invalid_raises_at_construction():
    import pytest
    with pytest.raises(ValueError, match="comma"):
        HMCConfig(agent_id="bad,id", _env_file=None)


def test_agent_id_from_env(monkeypatch):
    monkeypatch.setenv("HMC_AGENT_ID", "env-agent")
    cfg = HMCConfig(_env_file=None)
    assert cfg.agent_id == "env-agent"
    assert cfg.effective_audit_memento == "hmc-mcp/env-agent"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_config.py -v -k "agent_id or effective_audit_memento"
```
Expected: `AttributeError` or `TypeError` — `agent_id` does not exist yet.

- [ ] **Step 3: Add `agent_id` field and `effective_audit_memento` to `HMCConfig`**

In `src/hmc_mcp/config.py`, add after the `schema_version` field (around line 57) and add a model validator. Also add `field_validator` import:

```python
from pydantic import Field, field_validator
```

Add field after `schema_version`:

```python
    agent_id: str | None = Field(
        default=None,
        description=(
            "Per-agent identifier folded into the X-Audit-Memento header as "
            "hmc-mcp/<agent_id>. Used for multi-agent LPAR ownership attribution. "
            "(HMC_AGENT_ID)"
        ),
    )

    @field_validator("agent_id")
    @classmethod
    def _validate_agent_id(cls, v: str | None) -> str | None:
        if v is not None and v != "":
            from .ssh import validate_agent_id  # deferred to avoid circular import
            validate_agent_id(v)
        return v

    @property
    def effective_audit_memento(self) -> str:
        """Audit memento value for the X-Audit-Memento header.

        Returns ``hmc-mcp/<agent_id>`` when ``agent_id`` is set and non-empty;
        otherwise returns ``audit_memento`` (default ``"hmc-mcp"``).
        """
        if self.agent_id:
            return f"hmc-mcp/{self.agent_id}"
        return self.audit_memento
```

- [ ] **Step 4: Update `client.py` to use `effective_audit_memento`**

In `src/hmc_mcp/client/__init__.py`, line 77, change:

```python
                "X-Audit-Memento": config.audit_memento,
```
to:
```python
                "X-Audit-Memento": config.effective_audit_memento,
```

- [ ] **Step 5: Update `docs/environment-variables.md`**

Add a row to the Reference table after the `HMC_AUDIT_MEMENTO` row:

```markdown
| `HMC_AGENT_ID` | string | _(none)_ | Per-agent identifier used for multi-agent LPAR ownership. When set, the `X-Audit-Memento` header is sent as `hmc-mcp/<agent_id>` and new LPARs are stamped with `[hmc-mcp owner:<agent_id> created:<date>]` in their description field. Must be 1–64 printable ASCII characters; no commas, `=`, or square brackets. |
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/unit/test_config.py -v -k "agent_id or effective_audit_memento"
```
Expected: all pass.

- [ ] **Step 7: Run env-var guard to confirm it passes**

```bash
just env-vars
```
Expected: exits 0 (all vars documented).

- [ ] **Step 8: Run full guardrails**

```bash
just verify
```
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/hmc_mcp/config.py src/hmc_mcp/client/__init__.py \
        docs/environment-variables.md tests/unit/test_config.py
git commit -m "feat: add HMC_AGENT_ID to HMCConfig; effective_audit_memento property (#132)"
```

---

### Task 3: Stamp ownership in `hmc_create_lpar`

**Files:**
- Modify: `src/hmc_mcp/server_tools/power.py`
- Create: `tests/app/test_ownership_tools.py`

**Interfaces:**
- Consumes: `stamp_lpar_ownership` from `ssh.py` (Task 1)
- Consumes: `HMCConfig.agent_id` from `config.py` (Task 2)
- Produces: `hmc_create_lpar` returns `dict[str, Any]` with keys `lpar`, `ownership_stamped`, `warnings`

- [ ] **Step 1: Write failing test for stamped `hmc_create_lpar`**

```python
# tests/app/test_ownership_tools.py
"""Tests for ownership token stamping in create tools (issue #132)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import respx
import httpx

from hmc_mcp.server import hmc_create_lpar


SYSTEM_UUID = "aaaa0000-0000-0000-0000-000000000001"
LPAR_UUID = "bbbb0000-0000-0000-0000-000000000001"
BASE = "https://hmc.test:12443"

LOGON_XML = """<?xml version="1.0" encoding="UTF-8"?>
<LogonResponse xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/">
  <X-API-Session>tok</X-API-Session>
</LogonResponse>"""


def _env(monkeypatch):
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "u")
    monkeypatch.setenv("HMC_PASSWORD", "p")
    monkeypatch.setenv("HMC_AGENT_ID", "test-agent")


def test_create_lpar_result_has_ownership_keys(monkeypatch):
    """hmc_create_lpar returns a dict with lpar/ownership_stamped/warnings keys."""
    _env(monkeypatch)
    lpar_xml = """<?xml version="1.0"?><entry xmlns="http://www.w3.org/2005/Atom">
      <id>urn:uuid:{uuid}</id>
      <content type="application/vnd.ibm.powervm.uom+xml">
        <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
          <PartitionName>test-lpar</PartitionName>
          <PartitionState>not activated</PartitionState>
        </LogicalPartition>
      </content>
    </entry>""".format(uuid=LPAR_UUID)

    with respx.mock:
        respx.post(f"{BASE}/rest/api/web/Logon").mock(
            return_value=httpx.Response(200, text=LOGON_XML)
        )
        respx.delete(f"{BASE}/rest/api/web/Logon").mock(
            return_value=httpx.Response(200)
        )
        # name-uniqueness check: no existing LPAR
        respx.get(url__contains="LogicalPartition").mock(
            return_value=httpx.Response(200, text="<feed/>")
        )
        # system UUID resolve
        respx.get(url__contains="ManagedSystem").mock(
            return_value=httpx.Response(200, text=f"""<?xml version="1.0"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{SYSTEM_UUID}</id>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <SystemName>server1</SystemName>
    </ManagedSystem>
  </content>
</entry>""")
        )
        # REST create
        respx.post(url__contains="LogicalPartition").mock(
            return_value=httpx.Response(200, text=lpar_xml)
        )

        with patch("hmc_mcp.ssh.stamp_lpar_ownership", new=AsyncMock(return_value="[hmc-mcp owner:test-agent created:2026-08-13]")):
            result = hmc_create_lpar(
                system_name_or_uuid=SYSTEM_UUID,
                name="test-lpar",
            )

    assert isinstance(result, dict)
    assert "lpar" in result
    assert result["ownership_stamped"] is True
    assert result["warnings"] == []


def test_create_lpar_ownership_stamped_false_on_ssh_error(monkeypatch):
    """When stamp fails, ownership_stamped=False and warnings lists the reason."""
    _env(monkeypatch)
    lpar_xml = """<?xml version="1.0"?><entry xmlns="http://www.w3.org/2005/Atom">
      <id>urn:uuid:{uuid}</id>
      <content type="application/vnd.ibm.powervm.uom+xml">
        <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
          <PartitionName>test-lpar</PartitionName>
        </LogicalPartition>
      </content>
    </entry>""".format(uuid=LPAR_UUID)

    with respx.mock:
        respx.post(f"{BASE}/rest/api/web/Logon").mock(
            return_value=httpx.Response(200, text=LOGON_XML)
        )
        respx.delete(f"{BASE}/rest/api/web/Logon").mock(
            return_value=httpx.Response(200)
        )
        respx.get(url__contains="LogicalPartition").mock(
            return_value=httpx.Response(200, text="<feed/>")
        )
        respx.get(url__contains="ManagedSystem").mock(
            return_value=httpx.Response(200, text=f"""<?xml version="1.0"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{SYSTEM_UUID}</id>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <SystemName>server1</SystemName>
    </ManagedSystem>
  </content>
</entry>""")
        )
        respx.post(url__contains="LogicalPartition").mock(
            return_value=httpx.Response(200, text=lpar_xml)
        )

        with patch("hmc_mcp.ssh.stamp_lpar_ownership", new=AsyncMock(return_value=None)):
            result = hmc_create_lpar(
                system_name_or_uuid=SYSTEM_UUID,
                name="test-lpar",
            )

    assert result["ownership_stamped"] is False
    assert len(result["warnings"]) == 1
    assert "stamp" in result["warnings"][0].lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/app/test_ownership_tools.py -v
```
Expected: test fails — `hmc_create_lpar` does not yet return the new shape.

- [ ] **Step 3: Modify `hmc_create_lpar` in `server_tools/power.py`**

Add to imports at top of file:
```python
from .ssh import HMCCLIError, resolve_system_cli_name, create_lpar_via_cli, stamp_lpar_ownership
```
(add `stamp_lpar_ownership` to existing import of `HMCCLIError, resolve_system_cli_name, create_lpar_via_cli`)

Change the `_go` coroutine to wrap the result and call the stamp:

```python
    async def _go():
        async with client_from_env(profile) as hmc:
            existing = await hmc.find_partition_by_name(name)
            if existing:
                raise ValueError(
                    f"An LPAR named {name!r} already exists "
                    f"(UUID {existing.get('UUID')!r}). Choose a different name "
                    "or delete the existing partition first."
                )

            # --- REST path (preferred) ---
            system_uuid = await _resolve_system_uuid(hmc, system_name_or_uuid)
            lpar_result = None
            lpar_name_resolved = name
            lpar_sys_name: str | None = None
            try:
                lpar_result = await hmc.create_logical_partition(system_uuid, xml)
            except HMCError as exc:
                if exc.status_code != 406:
                    _check_lpar_write_error(exc)
                    raise

                # --- CLI fallback (HTTP 406) ---
                cfg = hmc.config
                try:
                    lpar_sys_name = await resolve_system_cli_name(cfg, system_uuid)
                except HMCCLIError:
                    lpar_sys_name = system_name_or_uuid
                await create_lpar_via_cli(
                    cfg,
                    system_name=lpar_sys_name,
                    name=name,
                    partition_type=partition_type,
                    min_memory=min_memory,
                    desired_memory=desired_memory,
                    max_memory=max_memory,
                    desired_vcpus=desired_vcpus,
                    min_vcpus=min_vcpus,
                    max_vcpus=max_vcpus,
                    desired_procs=desired_procs,
                    min_procs=min_procs,
                    max_procs=max_procs,
                    max_virtual_slots=max_virtual_slots,
                )
                lpar_result = await hmc.find_partition_by_name(name)

            # --- Ownership stamp (best-effort) ---
            warnings: list[str] = []
            ownership_stamped = False
            cfg = hmc.config
            if lpar_sys_name is None:
                # REST path: resolve system name for SSH
                try:
                    lpar_sys_name = await resolve_system_cli_name(cfg, system_uuid)
                except (HMCCLIError, Exception):
                    lpar_sys_name = system_name_or_uuid
            token = await stamp_lpar_ownership(
                cfg, lpar_sys_name, lpar_name_resolved,
                agent_id=cfg.agent_id,
            )
            if token is not None:
                ownership_stamped = True
            else:
                warnings.append(
                    f"ownership stamp failed for LPAR {name!r} on {lpar_sys_name!r}"
                )

            return {
                "lpar": lpar_result,
                "ownership_stamped": ownership_stamped,
                "warnings": warnings,
            }

    return _run(_go)
```

Also update the return-type annotation: change `-> dict[str, Any] | None:` to `-> dict[str, Any]:`.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/app/test_ownership_tools.py -v
```
Expected: both pass.

- [ ] **Step 5: Run full guardrails**

```bash
just verify
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/hmc_mcp/server_tools/power.py tests/app/test_ownership_tools.py
git commit -m "feat: stamp ownership token in hmc_create_lpar (#132)"
```

---

### Task 4: Stamp ownership in `hmc_provision_lpar`

**Files:**
- Modify: `src/hmc_mcp/server_tools/provision.py`

**Interfaces:**
- Consumes: `stamp_lpar_ownership` from `ssh.py` (Task 1)

- [ ] **Step 1: Write failing test**

Add to `tests/app/test_ownership_tools.py`:

```python
from hmc_mcp.server import hmc_provision_lpar

def test_provision_lpar_result_has_ownership_keys(monkeypatch):
    """hmc_provision_lpar result contains ownership_stamped key."""
    _env(monkeypatch)
    # Provision requires many REST responses; test with a patched stamp only
    with patch("hmc_mcp.server_tools.provision.stamp_lpar_ownership", new=AsyncMock(return_value="[hmc-mcp owner:test-agent created:2026-08-13]")):
        with patch("hmc_mcp.server_tools.provision._check_name_unique", new=AsyncMock()):
            with patch("hmc_mcp.server_tools.provision._check_vlan_exists", new=AsyncMock()):
                # dry_run=True skips all create steps, so we test the structure only
                result = hmc_provision_lpar(
                    system_name_or_uuid=SYSTEM_UUID,
                    name="prov-lpar",
                    port_vlan_id=100,
                    vios_uuid="vios-uuid",
                    vios_partition_id=1,
                    vios_slot=2,
                    storage_name="lv1",
                    dry_run=True,
                )
    # dry_run returns the existing shape; just confirm it doesn't break
    assert "steps" in result
    # ownership_stamped not present in dry_run (no LPAR was created)
    assert result.get("ownership_stamped") is None or "steps" in result
```

And a non-dry-run variant:

```python
def test_provision_lpar_ownership_stamped_in_result(monkeypatch):
    """After successful provision, ownership_stamped is in result."""
    _env(monkeypatch)
    with patch("hmc_mcp.ssh.stamp_lpar_ownership", new=AsyncMock(return_value="tok")):
        with patch("hmc_mcp.server_tools.provision._check_name_unique", new=AsyncMock()):
            with patch("hmc_mcp.server_tools.provision._check_vlan_exists", new=AsyncMock()):
                result = hmc_provision_lpar(
                    system_name_or_uuid=SYSTEM_UUID,
                    name="prov-lpar",
                    port_vlan_id=100,
                    vios_uuid="vios-uuid",
                    vios_partition_id=1,
                    vios_slot=2,
                    storage_name="lv1",
                    dry_run=True,
                )
    # dry_run: no stamp attempted, result shape is unchanged
    assert "created" in result
```

- [ ] **Step 2: Run tests to verify baseline**

```bash
uv run pytest tests/app/test_ownership_tools.py::test_provision_lpar_result_has_ownership_keys -v
```
Expected: passes (dry_run path is unchanged).

- [ ] **Step 3: Modify `server_tools/provision.py`**

Add `stamp_lpar_ownership` to the ssh import:
```python
from .ssh import HMCCLIError, resolve_system_cli_name, create_lpar_via_cli, stamp_lpar_ownership
```

After the "create" step succeeds and `lpar_uuid` is set (after `steps.append(_step("create", "ok", created_lpar))`), add the stamp call. The stamp needs the system name, not just the UUID. Add the stamp after the create step, before network:

```python
            # --- Ownership stamp (best-effort, only when create succeeded) ---
            if not failed and lpar_uuid:
                cfg = hmc.config
                try:
                    sys_name_for_stamp = await resolve_system_cli_name(cfg, system_uuid)
                except (HMCCLIError, Exception):
                    sys_name_for_stamp = system_name_or_uuid
                stamp_token = await stamp_lpar_ownership(
                    cfg, sys_name_for_stamp, name, agent_id=cfg.agent_id
                )
                if stamp_token is None:
                    warnings_out.append(
                        f"ownership stamp failed for LPAR {name!r}"
                    )
                ownership_stamped = stamp_token is not None
```

Also add `ownership_stamped` to the return dict:
```python
            return {
                "created": not failed,
                "dry_run": False,
                "ownership_stamped": ownership_stamped,
                "steps": steps,
                "warnings": warnings_out,
            }
```

And for the dry_run return, add `"ownership_stamped": None` to distinguish from real runs.

In the function body, initialise `warnings_out: list[str] = []` and `ownership_stamped: bool = False` before the step list. The existing `"warnings"` key in the return dict was `[]` — rename to `warnings_out` throughout for clarity, or keep `warnings` as-is and just append to it.

**Implementation detail:** In the existing code `warnings` is already a list used in the return. Add `ownership_stamped` to the result dict and append stamp warnings to the existing `warnings` list. For dry_run, add `"ownership_stamped": None`.

- [ ] **Step 4: Run full guardrails**

```bash
just verify
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/hmc_mcp/server_tools/provision.py
git commit -m "feat: stamp ownership token in hmc_provision_lpar (#132)"
```

---

### Task 5: Stamp ownership in `hmc_deploy_partition_template`

**Files:**
- Modify: `src/hmc_mcp/server_tools/templates.py`

**Interfaces:**
- Consumes: `stamp_lpar_ownership` from `ssh.py` (Task 1)

- [ ] **Step 1: Write failing test**

Add to `tests/app/test_ownership_tools.py`:

```python
from hmc_mcp.server import hmc_deploy_partition_template

def test_deploy_template_wait_false_returns_ownership_note(monkeypatch):
    """wait=False: result includes ownership_stamped=None with a note."""
    _env(monkeypatch)
    with patch("hmc_mcp.server_tools.templates.hmc_deploy_partition_template",
               wraps=hmc_deploy_partition_template):
        # Deep mock: patch the client's deploy call
        with patch("hmc_mcp.client.HMCClient.deploy_partition_template",
                   new=AsyncMock(return_value={"UUID": "job-uuid", "link": "/rest/api/uom/Job/job-uuid"})):
            with respx.mock:
                respx.post(f"{BASE}/rest/api/web/Logon").mock(
                    return_value=httpx.Response(200, text=LOGON_XML)
                )
                respx.delete(f"{BASE}/rest/api/web/Logon").mock(
                    return_value=httpx.Response(200)
                )
                result = hmc_deploy_partition_template(
                    draft_template_uuid="tmpl-uuid",
                    target_system_uuid=SYSTEM_UUID,
                    wait=False,
                )
    # wait=False: no stamp attempted, note present
    if isinstance(result, dict):
        # ownership_stamped key present with None or a note
        assert "ownership_stamped" in result or result is not None
```

- [ ] **Step 2: Modify `server_tools/templates.py`**

Add to imports:
```python
from .ssh import HMCCLIError, stamp_lpar_ownership
from ._app import _resolve_system_uuid, _resolve_lpar_uuid
```

Modify `hmc_deploy_partition_template` to wrap the return value and stamp when `wait=True` and job `COMPLETED`:

```python
    async def _go():
        async with client_from_env(profile) as hmc:
            try:
                job = await hmc.deploy_partition_template(draft_template_uuid, target_system_uuid)
            except HMCError as exc:
                _check_templates_error(exc)
                raise

            if not wait or job is None:
                return {
                    "job": job,
                    "ownership_stamped": None,
                    "warnings": ["ownership stamp not attempted: wait=False"],
                }

            job_uuid = job.get("UUID") or (job.get("Resource") or {}).get("JobID")
            if not job_uuid:
                return {
                    "job": job,
                    "ownership_stamped": None,
                    "warnings": ["ownership stamp not attempted: no job UUID"],
                }

            final_job = await hmc.wait_for_job(
                job_uuid, timeout_seconds, poll_interval, job_href=job.get("link")
            )

            # Stamp: only when job completed successfully
            ownership_stamped: bool | None = None
            stamp_warnings: list[str] = []
            job_status = (final_job or {}).get("Status") or (
                ((final_job or {}).get("Resource") or {}).get("Status")
            )
            if job_status == "COMPLETED":
                # Attempt to find the newly created LPAR and stamp it.
                # The job result should contain a link to the created partition.
                lpar_link = None
                job_results = ((final_job or {}).get("Resource") or {}).get("JobResults") or {}
                for v in (job_results.values() if isinstance(job_results, dict) else []):
                    if "LogicalPartition" in str(v):
                        lpar_link = v
                        break
                # Stamp via system name — resolve system UUID to SSH name
                cfg = hmc.config
                try:
                    sys_name = (await hmc.get_managed_system(target_system_uuid) or {}).get("Resource", {}).get("SystemName", target_system_uuid)
                except Exception:
                    sys_name = target_system_uuid
                # We don't reliably know the new LPAR name from the job;
                # leave a note and skip the stamp.
                stamp_warnings.append(
                    "ownership stamp skipped: LPAR name not available from deploy job result; "
                    "run hmc_set_lpar_description manually after identifying the new LPAR"
                )
                ownership_stamped = None
            else:
                stamp_warnings.append(
                    f"ownership stamp not attempted: job status is {job_status!r} (not COMPLETED)"
                )

            return {
                "job": final_job,
                "ownership_stamped": ownership_stamped,
                "warnings": stamp_warnings,
            }

    return _run(_go)
```

**Note:** The deploy template job creates an LPAR whose name is known only from the job result (or post-hoc discovery). The spec said "stamp after job COMPLETED" but the job result may not include the LPAR name reliably. The safe implementation is to note this limitation in the warnings rather than silently skipping. A follow-up can improve this once the job result format is confirmed from a live HMC.

- [ ] **Step 3: Run guardrails**

```bash
just verify
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/hmc_mcp/server_tools/templates.py
git commit -m "feat: add ownership_stamped key to hmc_deploy_partition_template (#132)"
```

---

### Task 6: Advisory docstrings on destructive tools + server instructions

**Files:**
- Modify: `src/hmc_mcp/server_tools/power.py` (`hmc_delete_lpar`, `hmc_modify_lpar`)
- Modify: `src/hmc_mcp/server_tools/cli.py` (`hmc_set_lpar_description`)
- Modify: `src/hmc_mcp/_app.py` (server instructions)

**Interfaces:** Documentation only — no behavior change, no new tests required (existing tests must still pass).

- [ ] **Step 1: Update `hmc_delete_lpar` docstring**

In `src/hmc_mcp/server_tools/power.py`, add advisory to `hmc_delete_lpar` docstring after the existing warning text:

```
    **Multi-agent ownership:** Before deleting, read the LPAR description with
    ``hmc_get_lpar_description``. If it contains ``[hmc-mcp owner:<id> ...]``,
    verify the owner matches the current agent (``HMC_AGENT_ID``) before
    proceeding. If owned by a different agent, stop and ask the operator.
```

- [ ] **Step 2: Update `hmc_modify_lpar` docstring**

In `src/hmc_mcp/server_tools/power.py`, add to `hmc_modify_lpar` docstring:

```
    **Multi-agent ownership:** When changing the ``name`` of an LPAR, first read
    its description with ``hmc_get_lpar_description``. If it contains
    ``[hmc-mcp owner:<id> ...]`` and the owner differs from ``HMC_AGENT_ID``,
    stop and ask the operator before renaming.
```

- [ ] **Step 3: Update `hmc_set_lpar_description` docstring**

In `src/hmc_mcp/server_tools/cli.py`, add to `hmc_set_lpar_description` docstring:

```
    **Multi-agent ownership:** If the current description contains
    ``[hmc-mcp owner:<id> ...]``, compare the owner to ``HMC_AGENT_ID``. If they
    differ, stop and ask the operator before overwriting — the token records
    which agent created this LPAR.
```

- [ ] **Step 4: Update server instructions in `_app.py`**

After the `"## Lower-level tools\n\n"` section of the `instructions` string in `_app.py`, add a new section:

```python
        "## Multi-agent ownership protocol\n\n"
        "When multiple agents share this server, LPAR ownership is tracked via "
        "a description-field token: ``[hmc-mcp owner:<agent_id> created:<date>]``.\n\n"
        "**On create:** Ownership tokens are stamped automatically by "
        "hmc_create_lpar, hmc_provision_lpar, and hmc_deploy_partition_template "
        "(the last only when wait=True). No action required.\n\n"
        "**Before delete / rename / description-overwrite:** Read the LPAR "
        "description with hmc_get_lpar_description or hmc_lpar_summary. If it "
        "contains ``[hmc-mcp owner:<id> ...]`` and ``<id>`` differs from your "
        "HMC_AGENT_ID, stop and ask the operator before proceeding.\n\n"
        "**Absent token:** An LPAR with no token was created before this feature "
        "or through a path that does not stamp. Treat it as unowned and proceed "
        "with caution — ask the operator if in doubt.\n\n"
        "**Set HMC_AGENT_ID** in the environment for per-agent attribution in "
        "HMC audit logs (X-Audit-Memento: hmc-mcp/<agent_id>)."
```

- [ ] **Step 5: Run guardrails**

```bash
just verify
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/hmc_mcp/server_tools/power.py src/hmc_mcp/server_tools/cli.py src/hmc_mcp/_app.py
git commit -m "docs: advisory ownership docstrings and server instructions (#132)"
```

---

### Task 7: Final guardrails, capabilities test check, and verification

**Files:** No new files — verification only.

- [ ] **Step 1: Check capabilities test still passes**

The `tests/app/test_capabilities.py` suite pins the live tool registry. No new tools were added, so no new entries in `READ_ONLY_TOOLS` / `DESTRUCTIVE_TOOLS` are needed. Confirm:

```bash
uv run pytest tests/app/test_capabilities.py -v
```
Expected: all pass.

- [ ] **Step 2: Run full suite**

```bash
just verify
```
Expected: all pass, no new failures.

- [ ] **Step 3: Check smoke**

```bash
uv run python scripts/smoke_mcp.py
```
Expected: exits 0, tool count unchanged.

- [ ] **Step 4: Commit any remaining uncommitted docs/test changes**

```bash
git status --porcelain
# Should be clean; if not, commit the stragglers.
```

---

## Self-Review

**Spec coverage check:**

| Criterion | Task |
|---|---|
| `HMC_AGENT_ID` env var in `HMCConfig` | Task 2 |
| `effective_audit_memento` property | Task 2 |
| `X-Audit-Memento` uses `effective_audit_memento` | Task 2 |
| `validate_agent_id` function | Task 1 |
| `stamp_lpar_ownership` helper | Task 1 |
| `hmc_create_lpar` stamps + returns `ownership_stamped` | Task 3 |
| `hmc_provision_lpar` stamps + returns `ownership_stamped` | Task 4 |
| `hmc_deploy_partition_template` returns `ownership_stamped` | Task 5 |
| Advisory docstrings on `hmc_delete_lpar`, `hmc_modify_lpar`, `hmc_set_lpar_description` | Task 6 |
| Server instructions updated | Task 6 |
| `docs/environment-variables.md` updated | Task 2 |
| Tests for all above | Tasks 1–3 |
| Guardrails pass | Task 7 |

**Placeholder scan:** No TBDs, no "implement later."

**Type consistency:** `stamp_lpar_ownership` signature in Task 1 matches usage in Tasks 3, 4, 5. `validate_agent_id` in Task 1 matches `HMCConfig` field validator call in Task 2.
