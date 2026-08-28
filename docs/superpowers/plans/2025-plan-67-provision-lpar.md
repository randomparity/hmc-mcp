# Implementation Plan: hmc_provision_lpar (Issue #67)

**Branch**: feat/provision-lpar-67
**BASE_BRANCH**: main
**Guardrails**: `just verify`
**ADR**: docs/adr/0005-hmc-provision-lpar-workflow.md

## Tasks

### 1. [x] Create branch, ADR, scope annotation
### 2. [ ] Write failing tests (TDD red)
  - File: `tests/lpar/test_provision_tool.py`
  - Cases:
    - `test_provision_lpar_full_workflow` — all 5 steps succeed; verify HTTP calls and result shape
    - `test_provision_lpar_dry_run_validates_only` — dry_run=True: only precondition GETs, no mutations
    - `test_provision_lpar_dry_run_name_conflict` — name already exists → dry_run raises/returns error
    - `test_provision_lpar_name_conflict_aborts` — name already exists → immediate error, no steps
    - `test_provision_lpar_vlan_not_found` — VLAN not in list → precondition error
    - `test_provision_lpar_vg_not_found` — VG UUID not found → precondition error
    - `test_provision_lpar_partial_failure_skips_remaining` — step 3 fails → step 3 "error", steps 4-5 "skipped"
    - `test_provision_lpar_power_on_step_skipped_when_off` — power_on=False → power_on step absent
### 3. [ ] Implement `src/hmc_mcp/server_tools/provision.py`
  - `hmc_provision_lpar(...)` function registered with `@mcp.tool`
  - Precondition helpers (name check, VLAN check, VG check)
  - Step runner: executes steps sequentially; stops on failure; records skips
### 4. [ ] Wire into `server.py` re-exports
### 5. [ ] Add `lpars provision` CLI command in `cli_commands/lpars.py`
### 6. [ ] Update README (tool table row + six-step example replacement)
### 7. [ ] Run guardrails (just verify) — all green
### 8. [ ] Commit and ship PR

## Function signature (from issue + ADR)

```python
def hmc_provision_lpar(
    system_name_or_uuid: str,
    name: str,
    # LPAR resources
    partition_type: PartitionType = "AIX/Linux",
    min_memory: int = 256,
    desired_memory: int = 4096,
    max_memory: int = 8192,
    desired_vcpus: int = 1,
    max_vcpus: int = 2,
    # Network adapter
    port_vlan_id: int,
    # vSCSI adapter
    vios_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
    # Storage mapping
    storage_name: str,
    storage_kind: str = "VirtualDisk",
    # Options
    power_on: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
```

## Result shape

```json
{
  "created": false,
  "dry_run": true,
  "warnings": [],
  "steps": [
    {"step": "create", "status": "ok", "result": {...}},
    {"step": "network", "status": "ok", "result": {...}},
    {"step": "vscsi", "status": "ok", "result": {...}},
    {"step": "storage", "status": "ok", "result": {...}},
    {"step": "power_on", "status": "ok", "result": {...}}
  ]
}
```

On dry-run: `created=False`, all steps are `{"status": "dry_run"}`.
On partial failure: failed step is `{"status": "error", "result": str(exc)}`,
subsequent steps are `{"status": "skipped"}`.
