# Implementation Plan: Consolidate Read-Only List/Get MCP Tools (Issue #51)

**Branch:** feat/consolidate-readonly-tools-51
**BASE_BRANCH:** main
**Guardrails:** `just verify` (runs ruff + ty + detect-secrets + zizmor + pytest + smoke_mcp.py + CLI groups)
**Spec:** docs/superpowers/specs/2026-08-09-consolidate-readonly-tools-spec.md
**ADR:** docs/adr/0003-consolidate-list-get-tool-pairs.md

## Context

This plan implements the consolidation of 6 read-only list/get tool pairs into
single tools with optional identifiers. The work touches 4 server modules, `_app.py`,
`server.py`, associated tests, and README. All changes preserve read-only capability
annotations.

## Tasks

### Task 1: Write failing tests for `hmc_systems`, `hmc_lpars`, `hmc_vios`

**What:** Add tests for the three new tools that replace the tools in `server_tools/system.py`.
The tests must fail before the implementations exist (red phase of TDD).

**Files touched:**
- `tests/system/test_system_tools.py` — replace tests for `hmc_list_systems`, `hmc_get_system`, `hmc_list_lpars`, `hmc_get_lpar`, `hmc_lpar_state`, `hmc_list_vios`, `hmc_vios_mappings` with tests for `hmc_systems` and `hmc_lpars` and `hmc_vios`

**Tests to write:**

For `hmc_systems`:
- `test_systems_no_arg_lists_all()` — `hmc_systems()` returns a list (calls ManagedSystem collection)
- `test_systems_with_uuid_gets_one()` — `hmc_systems(system_uuid=UUID)` returns a dict
- `test_systems_with_uuid_404_propagates()` — 404 propagates as HMCError

For `hmc_lpars`:
- `test_lpars_no_arg_lists_all()` — `hmc_lpars()` returns a list
- `test_lpars_system_uuid_scopes()` — `hmc_lpars(system_uuid=...)` uses scoped URL
- `test_lpars_lpar_uuid_gets_one()` — `hmc_lpars(lpar_uuid=...)` returns a dict
- `test_lpars_name_finds_by_name()` — `hmc_lpars(name=...)` searches by PartitionName
- `test_lpars_name_not_found_returns_none()` — returns None when not found
- `test_lpars_state_only_returns_string()` — `hmc_lpars(lpar_uuid=..., state_only=True)` returns string
- `test_lpars_state_only_without_lpar_uuid_raises()` — ValueError when state_only=True but no lpar_uuid

For `hmc_vios`:
- `test_vios_no_arg_lists_all()` — `hmc_vios()` returns a list
- `test_vios_with_uuid_returns_mappings()` — `hmc_vios(vios_uuid=...)` returns storage-detail dict

**Acceptance criteria:** All new tests fail with `ImportError` or `AttributeError`
because the new function names don't exist yet.

**Repo conventions:** Tests use `respx` (mock_hmc fixture), `monkeypatch` for env
vars, `httpx.Response`, the `_feed()` helper from the existing test file.

---

### Task 2: Write failing tests for storage/templates/users unified tools

**What:** Add tests for `hmc_shared_storage_pools`, `hmc_partition_templates`, `hmc_users`.

**Files touched:**
- `tests/storage/test_storage_tools.py` — update SSP tests to use `hmc_shared_storage_pools`
- `tests/app/test_template_tools.py` — update list/get template tests
- `tests/security/test_users.py` — update `hmc_list_users`/`hmc_get_user` tests

**Tests to write:**

For `hmc_shared_storage_pools`:
- `test_shared_storage_pools_no_arg_lists()` — returns list
- `test_shared_storage_pools_with_uuid_gets_one()` — returns dict

For `hmc_partition_templates`:
- `test_partition_templates_no_arg_lists()` — returns list
- `test_partition_templates_with_uuid_gets_one()` — returns dict
- `test_partition_templates_with_uuid_error_propagates()` — HMCError on 404

For `hmc_users`:
- `test_users_no_arg_lists_all()` — returns list
- `test_users_with_name_gets_one()` — returns dict
- `test_users_with_name_empty_returns_none()` — None on 204

**Acceptance criteria:** All new tests fail because new function names don't exist yet.

---

### Task 3: Update `_app.py` READ_ONLY_TOOLS

**What:** Remove the 8 old tool names from `READ_ONLY_TOOLS` and add the 6 new names.

**File touched:** `src/hmc_mcp/_app.py`

**Changes:**
Remove from `READ_ONLY_TOOLS`:
- `"hmc_list_systems"`, `"hmc_get_system"` → replaced by `"hmc_systems"`
- `"hmc_list_lpars"`, `"hmc_get_lpar"`, `"hmc_find_lpar"`, `"hmc_lpar_state"` → replaced by `"hmc_lpars"`
- `"hmc_list_vios"`, `"hmc_vios_mappings"` → replaced by `"hmc_vios"`
- `"hmc_list_shared_storage_pools"`, `"hmc_get_shared_storage_pool"` → replaced by `"hmc_shared_storage_pools"`
- `"hmc_list_partition_templates"`, `"hmc_get_partition_template"` → replaced by `"hmc_partition_templates"`
- `"hmc_list_users"`, `"hmc_get_user"` → replaced by `"hmc_users"`

Add to `READ_ONLY_TOOLS`:
- `"hmc_systems"`, `"hmc_lpars"`, `"hmc_vios"`, `"hmc_shared_storage_pools"`, `"hmc_partition_templates"`, `"hmc_users"`

**Acceptance criteria:** The frozenset has the 6 new names and none of the 8 old names.

---

### Task 4: Implement `hmc_systems` and `hmc_lpars` and `hmc_vios` in `server_tools/system.py`

**What:** Replace the 7 existing tools with 3 unified tools.

**File touched:** `src/hmc_mcp/server_tools/system.py`

**Replace:**
- `hmc_list_systems` + `hmc_get_system` → `hmc_systems(system_uuid: str | None = None) -> Any`
- `hmc_list_lpars` + `hmc_get_lpar` + `hmc_find_lpar` + `hmc_lpar_state` → `hmc_lpars(system_uuid=None, lpar_uuid=None, name=None, state_only=False) -> Any`
- `hmc_list_vios` + `hmc_vios_mappings` → `hmc_vios(system_uuid=None, vios_uuid=None) -> Any`

**Implementation:**

```python
@mcp.tool(annotations=_READ_ONLY)
def hmc_systems(system_uuid: str | None = None) -> Any:
    """List all managed systems or get one by UUID.
    ...
    """
    if system_uuid is None:
        return with_client(lambda hmc: hmc.list_managed_systems())
    return with_client(lambda hmc: hmc.get_managed_system(system_uuid))

@mcp.tool(annotations=_READ_ONLY)
def hmc_lpars(
    system_uuid: str | None = None,
    lpar_uuid: str | None = None,
    name: str | None = None,
    state_only: bool = False,
) -> Any:
    """List LPARs or get/find one.
    ...
    """
    if lpar_uuid is not None and state_only:
        return with_client(
            lambda hmc: hmc.get_quick_property("LogicalPartition", lpar_uuid, "PartitionState")
        )
    if lpar_uuid is not None:
        return with_client(lambda hmc: hmc.get_logical_partition(lpar_uuid))
    if name is not None:
        return with_client(lambda hmc: hmc.find_partition_by_name(name))
    if state_only:
        raise ValueError("state_only=True requires lpar_uuid")
    return with_client(lambda hmc: hmc.list_logical_partitions(system_uuid))

@mcp.tool(annotations=_READ_ONLY)
def hmc_vios(
    system_uuid: str | None = None,
    vios_uuid: str | None = None,
) -> Any:
    """List VIOS or get storage-detail mappings for one VIOS.
    ...
    """
    if vios_uuid is not None:
        return with_client(lambda hmc: hmc.get_vios_storage_detail(vios_uuid))
    return with_client(lambda hmc: hmc.list_vios(system_uuid))
```

**Acceptance criteria:** Task 1 tests pass. `just verify` stays green.

---

### Task 5: Implement `hmc_shared_storage_pools` in `server_tools/storage.py`

**What:** Replace `hmc_list_shared_storage_pools` + `hmc_get_shared_storage_pool` with one tool.

**File touched:** `src/hmc_mcp/server_tools/storage.py`

**Implementation:**

```python
@mcp.tool(annotations=_READ_ONLY)
def hmc_shared_storage_pools(ssp_uuid: str | None = None) -> Any:
    """List Shared Storage Pools or get one by UUID.
    ...
    """
    if ssp_uuid is not None:
        return with_client(lambda hmc: hmc.get_shared_storage_pool(ssp_uuid))
    return with_client(lambda hmc: hmc.list_shared_storage_pools())
```

**Acceptance criteria:** Task 2 SSP tests pass.

---

### Task 6: Implement `hmc_partition_templates` in `server_tools/templates.py`

**What:** Replace `hmc_list_partition_templates` + `hmc_get_partition_template` with one tool.

**File touched:** `src/hmc_mcp/server_tools/templates.py`

**Implementation:**

```python
@mcp.tool(annotations=_READ_ONLY)
def hmc_partition_templates(template_uuid: str | None = None) -> Any:
    """List partition templates or get one by UUID.
    ...
    """
    if template_uuid is not None:
        return with_client(lambda hmc: hmc.get_partition_template(template_uuid))
    return with_client(lambda hmc: hmc.list_partition_templates())
```

**Acceptance criteria:** Task 2 template tests pass.

---

### Task 7: Implement `hmc_users` in `server_tools/users.py`

**What:** Replace `hmc_list_users` + `hmc_get_user` with one tool.

**File touched:** `src/hmc_mcp/server_tools/users.py`

**Implementation:**

```python
@mcp.tool(annotations=_READ_ONLY)
def hmc_users(
    name: str | None = None,
    user_type: Literal["local", "kerberos", "all"] = "all",
) -> Any:
    """List HMC users or get one by username.
    ...
    """
    if name is not None:
        return with_client(lambda hmc: hmc.get_hmc_user(name))
    return with_client(lambda hmc: hmc.list_hmc_users(user_type))
```

**Acceptance criteria:** Task 2 user tests pass.

---

### Task 8: Update `server.py` re-exports

**What:** Update the public re-export list in `server.py`.

**File touched:** `src/hmc_mcp/server.py`

**Changes:**
In the `from .server_system import ...` block:
- Remove: `hmc_find_lpar`, `hmc_get_lpar`, `hmc_get_system`, `hmc_list_lpars`, `hmc_list_systems`, `hmc_list_vios`, `hmc_lpar_state`, `hmc_vios_mappings`
- Add: `hmc_systems`, `hmc_lpars`, `hmc_vios`

In the `from .server_storage import ...` block:
- Remove: `hmc_get_shared_storage_pool`, `hmc_list_shared_storage_pools`
- Add: `hmc_shared_storage_pools`

In the `from .server_templates import ...` block:
- Remove: `hmc_get_partition_template`, `hmc_list_partition_templates`
- Add: `hmc_partition_templates`

In the `from .server_users import ...` block:
- Remove: `hmc_get_user`, `hmc_list_users`
- Add: `hmc_users`

**Acceptance criteria:** `from hmc_mcp.server import hmc_systems, hmc_lpars, hmc_vios, hmc_shared_storage_pools, hmc_partition_templates, hmc_users` works; old names raise ImportError.

---

### Task 9: Fix test imports referencing old names

**What:** Update all remaining test files that import the old tool names.

**Files touched:**
- `tests/app/test_server_tools.py` — remove `hmc_find_lpar` import, add `hmc_lpars`; update `test_find_lpar_by_name` and `test_find_lpar_not_found_returns_none` to use `hmc_lpars(name=...)`
- `tests/app/test_capabilities.py` — no direct old-name imports; the test inspects the live registry which now has the new names; verify it still passes

**Acceptance criteria:** `just test` passes (no ImportError).

---

### Task 10: Update README tool tables

**What:** Update the README to reflect the 97-tool count and new tool names.

**File touched:** `README.md`

**Changes:** Find all references to the 8 removed tool names and the 6 pairs they
came from; add the 6 new tool names; update any tool-count references from 105 to 97.

**Acceptance criteria:** `grep -c "hmc_" README.md` reflects the reduced count; no
references to old tool names in the tool-table sections.

---

### Task 11: Run full guardrails and commit

**What:** Run `just verify` to confirm all guardrails are green. Fix any remaining
issues. Commit the final result with a descriptive message.

**Command:** `just verify`

**Acceptance criteria:** `just verify` exits 0 with no warnings.
