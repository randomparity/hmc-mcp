# Implement VIOS FC label administration

**Goal:** Add bounded MCP and CLI administration for individual VIOS FC-port labels and vFC
group labels, backed by the documented POWER10/POWER11 HMC commands.

**Architecture:** A new SSH module owns `lslabelvios`/`labelvios` validation, construction,
parsing, and dispatch receipts. A thin operation module resolves public managed-system UUIDs to
CLI names. MCP and CLI presentation adapters call that operation module; the MCP security catalog
and generated tool documentation remain the authoritative composition surfaces.

**Tech stack:** Python 3.11, asyncio, existing `asyncssh` transport, FastMCP tool registry, Typer,
pytest, Ruff, ty, and repository `just` recipes. No dependency is added.

## Global constraints

- Python 3.11 remains the floor.
- Declared targets are amd64 and arm64; the x86_64 host is included through the amd64 alias.
- Support the identical documented POWER10 and POWER11 command grammar. Live output vocabulary,
  diagnostics, and read-after-write behavior remain owned by #559 and are not invented here.
- Add no dependency, schema migration, persisted format, reusable `hmc_mcp.api` export, other
  label family, default override, bulk deletion, or label-driven adapter mutation.
- Reject blank identifiers, incompatible selector combinations, non-positive IDs, duplicate
  members, HMC record delimiters where values enter records, and ASCII controls before dispatch.
  Preserve nonblank strings byte-for-byte and shell-quote every completed argument.
- Lists preserve the HMC's dynamic `-F --header` fields and reject malformed output. Mutation
  receipts prove dispatch only and include every operation-relevant input.
- Branch: `feat/vios-label-admin-556`; base: `main`.
- Focused checks precede every commit. Final checks are `just verify` and
  `uv run --no-sync prek run --all-files`, both bare.

## File map

- Create `src/hmc_mcp/ssh/vios_labels.py`: validation, dynamic header parser, exact command
  construction, SSH dispatch, and receipts.
- Create `src/hmc_mcp/operations/vios_labels.py`: managed-system selector resolution and delegation.
- Create `src/hmc_mcp/server_tools/vios_labels.py`: seven typed MCP presentation adapters.
- Modify `src/hmc_mcp/server_tools/catalog.py`: register the new tool module.
- Create `src/hmc_mcp/cli_commands/vios_labels.py`: matching Typer commands and confirmations.
- Modify `src/hmc_mcp/cli.py`: register the CLI module under `vios_app`.
- Create `tests/vios/test_vios_labels.py`: SSH, operation, MCP, validation, parsing, and receipt
  behavior.
- Modify `tests/app/test_cli_commands.py`: CLI registration, output, option, and confirmation behavior.
- Modify `tests/app/test_tool_security.py`: pin the seven new tool classifications and selector use
  where the existing exhaustive tables require explicit expected names.
- Modify `CHANGELOG.md`: document the user-facing MCP/CLI feature with an unchanged facade manifest.
- Regenerate `docs/tools/*.md` with `just tool-docs`; never hand-edit them.

## Task 1: Implement the evidence-bounded SSH command contract

### Interfaces

Produces these interfaces for Task 2:

```python
ViosGroupUpdateAction = Literal["rename", "add-members", "remove-members"]

async def list_vios_fc_port_labels(
    config: HMCConfig,
    system_name: str,
    *,
    vios_name: str | None = None,
    vios_id: int | None = None,
) -> list[dict[str, str]]: ...

async def set_vios_fc_port_label(
    config: HMCConfig,
    system_name: str,
    label: str,
    port_name: str,
    *,
    vios_name: str | None = None,
    vios_id: int | None = None,
) -> dict[str, object]: ...

async def remove_vios_fc_port_label(
    config: HMCConfig,
    system_name: str,
    port_name: str,
    *,
    vios_name: str | None = None,
    vios_id: int | None = None,
) -> dict[str, object]: ...

async def list_vios_vfc_group_labels(
    config: HMCConfig, system_name: str
) -> list[dict[str, str]]: ...

async def create_vios_vfc_group_label(
    config: HMCConfig,
    system_name: str,
    label: str,
    *,
    vios_names: Sequence[str] | None = None,
    vios_ids: Sequence[int] | None = None,
) -> dict[str, object]: ...

async def update_vios_vfc_group_label(
    config: HMCConfig,
    system_name: str,
    label: str,
    action: ViosGroupUpdateAction,
    *,
    new_name: str | None = None,
    vios_names: Sequence[str] | None = None,
    vios_ids: Sequence[int] | None = None,
) -> dict[str, object]: ...

async def remove_vios_vfc_group_label(
    config: HMCConfig, system_name: str, label: str
) -> dict[str, object]: ...
```

Consumes existing `HMCConfig`, `HMCCLIError`, `run_hmc_command`, `build_attribute_record`, and
`build_filter`. `build_attribute_record(pairs, quoted={attribute})` already supports a final
comma-bearing list pair and `+`/`-` suffixes. `shlex.quote` owns only the remote-shell layer.

### Steps

1. Create `tests/vios/test_vios_labels.py` with `pytest.mark.asyncio` command-boundary tests that
   monkeypatch `hmc_mcp.ssh.vios_labels.run_hmc_command`. Assert these exact forms for both selector
   families and every operation:

   ```text
   lslabelvios -r fcport -m system-a -F --header
   lslabelvios -r fcport -m system-a --filter vios_names=vios-a -F --header
   lslabelvios -r group -m system-a --filter resources=vfc -F --header
   labelvios -m system-a -o s -l port-label -i resource=fcport,port_name=fcs0,vios_ids=2
   labelvios -m system-a -o r -i resource=fcport,port_name=fcs0,vios_names=vios-a
   labelvios -m system-a -o a -l group-a -i 'resource=vfc,"vios_names=vios-a,vios-b"'
   labelvios -m system-a -o s -l group-a -i '"vios_ids+=2,3"'
   labelvios -m system-a -o s -l group-a -i '"vios_names-=vios-a,vios-b"'
   labelvios -m system-a -o s -l group-a -i new_name=group-b
   labelvios -m system-a -o r -l group-a
   ```

   Compare the actual single command string, not substrings. Expect import/collection failure
   before implementation. Run `uv run --no-sync pytest tests/vios/test_vios_labels.py -q` and
   retain the failing output in the forge ledger.

2. In the same test module, add parser cases for dynamic headers, reordered/unknown columns,
   quoted CSV values, blank output, exact `No results were found.`, blank/duplicate headers,
   malformed CSV, and row-width drift. Expected valid result:

   ```python
   [{"name": "fabric-a", "vios_names": "vios-a,vios-b", "future": "kept"}]
   ```

   Invalid output raises `HMCCLIError` naming the list operation and malformed condition; it never
   returns partial rows.

3. Add table-driven validation tests covering whitespace-only `system_name`, `label`, `port_name`,
   `new_name`, and VIOS names; neither/singly selected FC-port list filters and refusal of both;
   exactly-one selector enforcement for FC-port set/removal; both/neither/empty group-member
   families; duplicate names/IDs; non-positive IDs; comma, equals, double quote, ASCII controls in
   record values; invalid update actions/argument combinations; and preservation plus shell
   quoting of nonblank surrounding spaces and shell metacharacters. Assert `run_hmc_command` was
   not awaited on every refusal.

4. Add receipt assertions for every mutation. FC-port receipts include operation, resolved
   `system_name`, port, the selected VIOS name/ID, label when setting, and stripped output. Group
   create/member receipts include the exact selected list; rename includes old label and
   `new_name`; named removal includes its label. Irrelevant keys are absent.

5. Implement `src/hmc_mcp/ssh/vios_labels.py`. Keep private helpers local until a third caller
   exists: `_nonblank`, `_single_vios_selector`, `_member_selector`, `_parse_label_rows`,
   `_receipt`, and `_run_mutation`. Use `build_attribute_record` for every `-i` value, passing the
   one list attribute through `quoted={attribute}` and keeping it final. Use `build_filter` for
   list filters. Validate label/system standalone values for blank/control input, and record-bound
   values through the existing builder. Construct fixed tokens in code and `shlex.quote` every
   caller-derived standalone argument, filter, and completed record.

6. Run `uv run --no-sync pytest tests/vios/test_vios_labels.py -q`. Expect all Task 1 tests green.
   Make one controlled fault by changing the expected FC-port set operation from `-o s` to `-o a`,
   run the focused test and require one failure, then restore it and rerun green.

7. Run `just lint`, `just typecheck`, and `uv run --no-sync pytest tests/vios/test_vios_labels.py
   -q`. Commit the explicit Task 1 paths with `feat: add VIOS label SSH commands`.

### Acceptance criteria

- Every documented in-scope command has an exact red-then-green test.
- Invalid or ambiguous input dispatches nothing.
- Dynamic list rows preserve HMC field names without inventing a fixed vocabulary.
- Receipts retain every dispatched value and claim no post-state.

## Task 2: Resolve selectors and expose the authorized MCP surface

### Interfaces

Consumes all Task 1 functions. Produces public MCP functions with these exact names:

```python
hmc_list_vios_fc_port_labels
hmc_set_vios_fc_port_label
hmc_remove_vios_fc_port_label
hmc_list_vios_vfc_group_labels
hmc_create_vios_vfc_group_label
hmc_update_vios_vfc_group_label
hmc_remove_vios_vfc_group_label
```

Each takes `system_name_or_uuid: str` and `profile: str | None = None`. Other parameters match the
Task 1 function after the system selector. The operation module mirrors Task 1 names but accepts
`system_name_or_uuid`, resolves it with existing
`hmc_mcp.ssh.selectors.resolve_system_name(config, system_name_or_uuid)`, requires the non-optional
result, and delegates with the resolved CLI name.

### Steps

1. Extend `tests/vios/test_vios_labels.py` with operation tests that monkeypatch
   `resolve_system_name` and the SSH function. Prove a UUID becomes `system-a`, a CLI name remains
   usable, every argument is preserved, and resolution failure prevents dispatch. Run the focused
   module and expect failures because `operations/vios_labels.py` does not exist.

2. Add MCP adapter tests in the same module using existing `HMCConfig.from_mapping`, `patch`, and
   direct handler calls. Assert list rows and complete receipts pass through; `profile` selects the
   existing configuration boundary; and all seven handler signatures expose
   `system_name_or_uuid` exactly so target extraction can bind them.

3. Implement `src/hmc_mcp/operations/vios_labels.py` as seven thin async functions plus one private
   `_system_name` helper. Do not duplicate validation or command construction.

4. Implement `src/hmc_mcp/server_tools/vios_labels.py` with `tool, register_tools, tool_security =
   tool_module()`. Use these classifications:

   ```text
   read:        vios_label.list_fc_ports, vios_label.list_vfc_groups
   mutate:      vios_label.set_fc_port, vios_label.create_vfc_group,
                vios_label.update_vfc_group
   destructive: vios_label.remove_fc_port, vios_label.remove_vfc_group
   target_kind: managed_system for all seven
   ```

   Every handler calls `with_config`; no raw command, resource-family selector, or bulk flag is
   exposed. Docstrings state that labels guide migration/remote restart and do not alter adapter
   add/delete behavior.

5. Modify `src/hmc_mcp/server_tools/catalog.py` to import `vios_labels` and add it once to
   `TOOL_MODULES`. Add focused expectations to `tests/app/test_tool_security.py` only where its
   explicit exhaustive sets require the new tool names; do not modify frozen legacy snapshots.
   Assert read/mutate/destructive effects, operations, target selector
   `managed_system.system_name_or_uuid`, and exhaustive authorization.

6. Add an `[Unreleased]` `### Added` bullet to `CHANGELOG.md` naming the seven bounded MCP tools,
   SSH requirement, POWER10/POWER11 documented scope, and #559's live-evidence boundary. Leave
   `### Facade manifest` unchanged because no `hmc_mcp.api.__all__` export moves. Run `just
   tool-docs` to regenerate `docs/tools/*.md`, then inspect the generated diff for only the seven
   new tools and expected count changes.

7. Run:

   ```text
   uv run --no-sync pytest tests/vios/test_vios_labels.py tests/app/test_tool_security.py -q
   just tool-docs-check
   just doc-freshness
   ```

   Every command must pass. Never hand-edit generated pages.

8. Run `just lint` and `just typecheck`. Commit source, tests, changelog, and generated docs with
   `feat: expose VIOS label MCP tools`.

### Acceptance criteria

- UUID resolution occurs before SSH construction.
- All seven tools register once with exact effect, operation, and managed-system selector metadata.
- Policy target extraction sees and handlers use `system_name_or_uuid`.
- Existing adapter signatures and security records are unchanged.

## Task 3: Add the CLI adapters and release documentation

### Interfaces

Consumes Task 2 operation functions. Adds these commands to `hmc-mcp vios`:

```text
list-fc-port-labels
set-fc-port-label
remove-fc-port-label
list-vfc-group-labels
create-vfc-group-label
update-vfc-group-label
remove-vfc-group-label
```

List commands accept `--json`. Mutation commands accept `--yes`; group member inputs use repeated
`--vios-name` or `--vios-id`; update accepts `--action`, optional `--new-name`, and member options.

### Steps

1. Add direct `CliRunner` tests to `tests/app/test_cli_commands.py`. Patch the Task 2 operation
   functions at the CLI module boundary. Assert command registration/help, repeated member option
   preservation, list JSON, mutation receipt JSON, confirmation refusal without dispatch, `--yes`
   dispatch, and prompts/confirmation echoes on stderr rather than JSON stdout. Run only the new
   tests by their node IDs and expect import/command failures.

2. Implement `src/hmc_mcp/cli_commands/vios_labels.py`. Reuse the established `run`, `ssh_config`,
   `output`, `print_json`, and stderr confirmation pattern from `cli_commands/vnic.py`; do not add a
   second validator. Each mutation prompt names the system, operation, and one label/port target
   without echoing credentials. `register_commands(vios_app)` installs exactly the seven names.

3. Modify `src/hmc_mcp/cli.py` to import `vios_labels` and register it once with `vios_app`.
   Re-run the new CLI node IDs and `uv run --no-sync pytest tests/scripts/test_smoke_cli_groups.py
   tests/app/test_application_boundaries.py -q`; expect green.

4. Extend Task 2's `[Unreleased]` changelog bullet with the matching `hmc-mcp vios` command names;
   do not add a second feature bullet. Run:

   ```text
   uv run --no-sync pytest tests/vios/test_vios_labels.py tests/app/test_cli_commands.py \
     tests/app/test_tool_security.py tests/scripts/test_smoke_cli_groups.py \
     tests/app/test_application_boundaries.py tests/unit/test_changelog.py -q
   just lint
   just typecheck
   just tool-docs-check
   just doc-freshness
   ```

   Expect every command green. Commit explicit Task 3 paths with
   `feat: add VIOS label CLI commands`.

### Acceptance criteria

- CLI and MCP call the same operation functions and return the same structured evidence.
- Every mutation requires confirmation unless `--yes` is supplied, with clean JSON stdout.
- Changelog and generated tool documentation describe only implemented/installable behavior.
- No public Python facade export changes.

## Final verification and cleanup

1. Review the complete diff from the merge base:

   ```text
   git --no-pager diff "$(git merge-base HEAD origin/main)"
   ```

   Remove duplication, stale names, speculative helpers, and any surface outside ADR 0105.

2. Run `just verify` bare. It must pass static checks, exact-coverage tests, smoke handshake, build,
   artifact verification, and CLI-group loading. If it fails, diagnose the current artifact before
   changing code; do not advance red.

3. Run `uv run --no-sync prek run --all-files` bare. It must pass every hook that CI runs after
   `just verify`.

4. Record observed durations and exact successful commands in the forge ledger. The local host
   proves Python 3.11/x86_64 only; GitHub CI remains responsible for amd64/arm64 across Python
   3.11–3.14.

5. Commit only any final behavior-preserving cleanup, using a separate conventional commit. Leave
   the worktree clean for the quest review phase.
