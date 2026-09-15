# Plan: LPAR ISO Installation CLI Recipe

Goal: make issue #776's full LPAR ISO-install lifecycle executable and verifiable through the installed
CLI. Thin presentation adapters call existing operations; one selector-level extraction lets MCP and
CLI share bounded console behavior. The operator recipe remains an explicit, resumable sequence.

Tech stack: Python 3.11–3.14, Typer/Click CLI, pytest, Markdown documentation.

Expected implementation size: 300–480 changed lines (M) — derived from three CLI adapters, one shared
selector operation, focused adapter and recipe-contract tests, and one operator recipe with two links.

## Global Constraints

- Support Python 3.11–3.14 on amd64 and arm64 CI; preserve the declared ppc64le target.
- Add no dependency and change no MCP public contract.
- Preserve existing optical ownership/error behavior and bounded console contention/release behavior.
- Never render captured console bytes as interpreted terminal control data.
- Every recipe command must resolve in the installed CLI and match its positional and option contract.
- Use `just setup` for the worktree; never run bare `uv sync` or bare `uv run`.
- Final guardrails are `just verify` and `uv run --no-sync prek run --all-files`.

## File map

- Create `src/hmc_mcp/operations/lpar/console.py`: shared selector resolution into bounded capture.
- Create `src/hmc_mcp/cli_commands/lpar/console.py`: `lpars capture-console` presentation adapter.
- Modify `src/hmc_mcp/server_tools/console.py`: call the shared selector operation without changing its
  signature or payload.
- Modify `src/hmc_mcp/cli.py`: register the LPAR console module.
- Modify `src/hmc_mcp/cli_commands/storage/resources.py`: optical command bodies and registration.
- Modify `tests/app/test_cli_commands.py`: optical and console body/help tests.
- Modify `tests/unit/test_console_capture.py`: migrate selector-resolution coverage to the shared
  operation and retain an MCP payload/profile-routing regression.
- Create `tests/app/test_lpar_iso_recipe.py`: structural validation of recipe commands.
- Create `docs/recipes/lpar-iso-install.md`: operator lifecycle.
- Modify `docs/cli.md` and `docs/index.md`: recipe navigation.

## Task 1: Expose optical mapping operations in the storage CLI

### Interfaces

Consumes existing signatures:

```python
async def mount_optical_media(
    hmc: HMCClient, vios_name_or_uuid: str, lpar_name_or_uuid: str, *,
    system_name_or_uuid: str | None = None, media_name: str,
    target_device: str | None = None, ownership_override: bool = False,
) -> dict[str, Any] | None: ...

async def unmount_optical_media(
    hmc: HMCClient, vios_name_or_uuid: str, lpar_name_or_uuid: str, *,
    system_name_or_uuid: str | None = None, media_name: str,
    ownership_override: bool = False,
) -> None: ...
```

Produces registered `storage mount-optical-media` and `storage unmount-optical-media` commands for
Task 3's recipe.

### Verification

- Mode: focused-test — forwarding and success output in
  `test_storage_mount_optical_media_forwards_selectors` and
  `test_storage_unmount_optical_media_forwards_selectors`; red is “No such command” or an uncalled
  mock; green command is `uv run --no-sync pytest tests/app/test_cli_commands.py -q -k optical_media`.
- Mode: focused-test — decline-before-mutation in
  `test_storage_mount_optical_media_decline_does_not_mutate` and
  `test_storage_unmount_optical_media_decline_does_not_mutate`; red is “No such command”; same green
  command.
- Mode: focused-test — help options in `test_storage_optical_media_command_help`; red is command not
  found; same green command.

### Steps

1. Add the focused tests using `AsyncMock`, `RUNNER.invoke(cli.app, ...)`, and explicit assertions for
   VIOS, LPAR, media, system, target device, ownership override, and confirmation flags.
2. Run the focused command and require the named tests to fail because the commands are absent.
3. Import both operations, add the two Typer functions exactly as specified, and register their two
   kebab-case names. Prompts must identify all affected resources; unmount must say the ISO remains.
4. Re-run the focused command and require all selected tests to pass.
5. Commit as `feat: expose optical media mapping in the CLI`.

## Task 2: Share and expose bounded LPAR console capture

### Interfaces

Produces:

```python
async def capture_lpar_console_by_selector(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    system_name_or_uuid: str,
    *,
    duration_seconds: float = 30.0,
    max_bytes: int = 65_536,
    idle_timeout_seconds: float = 10.0,
) -> ConsoleCapture: ...

def lpars_capture_console(
    lpar_name_or_uuid: str,
    system_name_or_uuid: str,
    duration_seconds: float = 30.0,
    max_bytes: int = 65_536,
    idle_timeout_seconds: float = 10.0,
    as_json: bool = False,
) -> None: ...
```

The MCP adapter relies on the first signature without changing its own function or result schema.
Task 3 relies on the registered `lpars capture-console` command with `--duration`, `--max-bytes`,
`--idle-timeout`, and `--json`.

### Verification

- Mode: focused-test — shared selector resolution is covered directly by
  `test_capture_lpar_console_by_selector_resolves_names` and
  `test_capture_lpar_console_by_selector_resolves_uuids`, asserting every resolver and the final
  bounded capture call; red is the missing shared function or an unmet mock assertion; green is
  `uv run --no-sync pytest tests/unit/test_console_capture.py -q -k 'by_selector'`.
- Mode: focused-test — unchanged MCP payload and profile routing are covered by
  `test_capture_tool_preserves_payload_and_profile`; red is a changed dict/base64 result or profile
  omission; green is `uv run --no-sync pytest tests/unit/test_console_capture.py -q -k 'preserves_payload'`.
- Mode: focused-test — CLI adapter bound forwarding is covered by
  `test_lpars_capture_console_forwards_bounds`; red is command absence; green is
  `uv run --no-sync pytest tests/app/test_cli_commands.py -q -k capture_console`.
- Mode: focused-test — JSON base64 shape in `test_lpars_capture_console_json_preserves_bytes`; red is
  command absence; same green command.
- Mode: focused-test — escaped terminal output and false-release warning in
  `test_lpars_capture_console_text_escapes_controls_and_warns`; red is command absence; same green
  command.
- Mode: focused-test — help contract in `test_lpars_capture_console_help`; red is command not found;
  same green command.

### Steps

1. Add focused CLI tests around a patched `capture_lpar_console_by_selector` returning `ConsoleCapture`
   with control bytes, invalid UTF-8, each stop field, and `released=False`.
2. Run the focused commands and require absence or symbol-migration failures.
3. Move only selector-to-name resolution from the MCP closure into the shared operation, preserving
   calls to `resolve_system_uuid`, `resolve_lpar_uuid`, `resolve_system_name`,
   `resolve_lpar_cli_name`, and `capture_lpar_console` with their current arguments.
4. Update the MCP adapter to call the shared operation and run its existing focused tests green.
5. Add the CLI module. Build the JSON mapping with base64 ASCII. For terminal output, render metadata
   and `ascii(capture.data.decode("utf-8", errors="backslashreplace"))` with markup/highlighting
   disabled; write an explicit warning when `released` is false.
6. Migrate the existing selector test in `tests/unit/test_console_capture.py` to the shared operation,
   keep the MCP wrapper regression for its unchanged payload and profile argument, and rerun both
   focused commands. Require all tests green before registering the module at the composition root.
7. Register the module at the composition root, rerun the CLI focused command, and require all tests green.
8. Commit as `feat: expose bounded LPAR console capture in the CLI`.

## Task 3: Publish and structurally verify the operator recipe

### Interfaces

Consumes the commands delivered by Tasks 1 and 2 and existing CLI contracts shown by each leaf
`--help`. Produces the page and two navigation links in the file map.

### Verification

- Mode: focused-test — `tests/app/test_lpar_iso_recipe.py` extracts `hmc-mcp` command lines, resolves
  every leaf command, validates positional arity and named options through Click parsing without
  invoking callbacks, and asserts the expected lifecycle command set. Red is missing recipe or absent
  new commands; green is `uv run --no-sync pytest tests/app/test_lpar_iso_recipe.py -q`.
- Mode: task-test-not-applicable — explanatory prose about prerequisites, recovery, ownership, and
  destructive intent has no executable semantic consumer; review checks it against issue #776 rather
  than snapshotting wording.
- Mode: focused-test — links from `docs/cli.md` and `docs/index.md` are checked in the recipe test; red
  is a missing link; same green command.

### Steps

1. Add the structural test with a repository-root path, fenced-shell extraction, `shlex.split`, Click
   command-tree traversal, numeric variable substitution, and link assertions. It must not execute a
   callback or network operation.
2. Run its focused command and require a missing-page failure.
3. Write the recipe in the nine specified sections. Use one placeholder table and shell assignments;
   include discovery, creation, network/vSCSI/disk, repository/ISO/mount, boot/job/state/capture,
   post-install, recovery, and visibly labelled optional destructive cleanup commands. In the
   unmount section, require serialized VIOS mapping writers plus fresh pre- and post-unmount
   `storage list-mappings` checks, with an explicit stop-and-reconcile branch for unexpected output.
4. Link the page once from the CLI guide and once from the documentation index.
5. Run every command leaf's installed `--help`, correct the examples to match it, then run the focused
   recipe test green.
6. Run `just verify` and `uv run --no-sync prek run --all-files`; require exit 0 from both.
7. Commit as `docs: add the LPAR ISO installation recipe`.

## Rollback and cleanup

Reverting the three implementation commits removes the commands, shared extraction, recipe, and
links while restoring the unchanged MCP path. Tests create no external resources. No dependency,
schema, persisted local data, or migration needs cleanup.
