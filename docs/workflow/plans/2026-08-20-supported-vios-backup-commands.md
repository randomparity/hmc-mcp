# Supported VIOS backup commands implementation plan

**Goal:** Replace the broken VIOS backup/list/restore command forms and public signatures with the
explicit HMC-supported contracts in ADR 0060.

**Architecture:** Keep the three existing tool entry points and SSH transport. Listing resolves a
VIOS UUID and filters `lsviosbk`; mutation tools resolve the explicit managed system name and VIOS
UUID before building one quoted `mkviosbk` or `rstviosbk` command.

**Tech stack:** Python 3.11+, FastMCP tool registration, existing REST client and async SSH,
pytest/ruff/ty through `uv` and `just`.

## Global constraints

- Python 3.11 remains the floor.
- Declared targets are amd64, arm64, and ppc64le; the arm64 host is included.
- Add no dependency, compatibility shim, live-HMC mutation, authorization change beyond the
  approved backup two-target migration, or full-image restore workflow.
- Preserve existing exports and listing/raw-output return types.
- Quote every dynamic remote-shell word and validate type/name inputs before external calls.
- Final guardrail: `just verify`.

## Task 1: Pin the replacement public and command contracts

**Files:** Modify `tests/vios/test_vios_backup.py`, `tests/unit/test_ssh_quoting.py`,
`tests/unit/test_ssh_profile_routing.py`, `tests/unit/test_destructive_scope.py`,
`tests/app/test_tool_security.py`, `tests/app/test_capabilities.py`, and
`tests/app/test_lifecycle_schema_descriptions.py`, and `tests/app/test_target_authorization.py`.
Search the remaining name-only inventory tests, but do not edit them unless their expected metadata
actually changes.

**Interfaces:** Tests consume the approved signatures:

```python
def hmc_backup_vios(
    system_name_or_uuid, vios_name_or_uuid, *, backup_name,
    backup_type="vios", profile=None,
): ...

def hmc_restore_vios(
    system_name_or_uuid, vios_name_or_uuid, backup_name, *, backup_type,
    restart_if_required=False, profile=None,
): ...
```

Later implementation must satisfy exact supported command strings and preserve
`hmc_list_vios_backups(vios_name_or_uuid, profile=None)`.

1. Replace list expectations with
   `lsviosbk --filter "vios_uuids=<uuid>" -F name,type --header`. Pin empty output and valid
   `name,type` CSV output, including CRLF plus a quoted comma and escaped quote in a valid name;
   assert the exact dictionaries. Also pin wrong/duplicate headers, empty values, and extra-column
   failures so a naive delimiter split cannot satisfy the contract. Include a valid quoted field
   containing an embedded newline and assert that the returned name preserves it byte-for-byte.
2. Replace backup calls with explicit system, VIOS, and backup name. Parameterize all three valid
   types and assert `mkviosbk -t TYPE -m SYSTEM --uuid UUID -f NAME`.
3. Replace restore calls with explicit system and required `viosioconfig`/`ssp` type. Assert
   `rstviosbk ...` both without and with `-r`, and assert `vios` is rejected before external calls.
   Add public Python binding regressions proving the old backup `(vios, type, profile)` and restore
   `(vios, backup_name, profile, system)` positional forms raise `TypeError` before external calls.
   Add MCP dispatch regressions submitting the old named backup and restore payloads and proving
   schema validation rejects them before REST or SSH.
4. Apply every catalog-name rejection case to both backup and restore; retain ordinary and hostile
   separator-free name cases to prove validation and quoting independently. Drive invalid type and
   name cases with selectors that would require REST, while asserting neither `HMCClient` nor
   `run_hmc_cli` is touched, so validation-before-I/O is directly proved.
5. Update profile-routing and destructive-scope calls to the approved signatures. Assert direct
   system names pass through, system UUIDs become MTMS even when names collide, missing/malformed
   MTMS fails before SSH, and VIOS-name resolution remains scoped to the explicit system. Cover
   every missing and blank nested `MachineType`, `Model`, and `SerialNumber` case. Pin dispatch
   authorization so backup requires matching managed-system and VIOS grants and denies a policy
   missing either target before its handler opens REST or SSH. `build_targets` already derives both
   required selectors from `REQUIRED_TARGET_ARGUMENTS`; do not add redundant `extra_targets`.
   Prove a direct system name plus VIOS UUID constructs no REST client. For a REST-assisted call,
   prove the REST client and SSH receive the exact same configuration object even if a later
   profile read would resolve differently.
   Exercise a hostile direct managed-system name for both backup and restore, split each rendered
   command with `shlex.split`, and prove `-m` remains one exact argument.
6. Run `uv run --no-sync pytest -q tests/vios/test_vios_backup.py tests/unit/test_ssh_quoting.py
   tests/unit/test_ssh_profile_routing.py tests/unit/test_destructive_scope.py
   tests/app/test_tool_security.py tests/app/test_capabilities.py
   tests/app/test_lifecycle_schema_descriptions.py tests/app/test_target_authorization.py`. Expect
   failures showing the old signatures, commands, schemas, descriptions, and authorization do not
   meet the new contract. Do not implement before this red proof is retained in the forge ledger.
   Run this identical file set after implementation.

**Acceptance:** Focused tests fail for the old command/signature behavior and cover every criterion
without making a real HMC call.

## Task 2: Implement resolution, validation, and supported commands

**Files:** Modify `src/hmc_mcp/server_vios.py`.

**Interfaces:** Consume `HMCClient`, `is_uuid`, `resolve_vios_uuid`, `run_hmc_cli`, and
`build_config`. Provide the exact public functions from Task 1. Keep
`BackupType = Literal["vios", "viosioconfig", "ssp"]` and add a restore-only literal/set.

1. Add a local system CLI identity resolver. Pass a direct name through. For a UUID, fetch the
   managed system. A flattened `MachineTypeModelSerialNumber` must parse into exactly three
   nonblank, unpadded components separated by the first `-` and `*`, then re-serialize
   byte-identically. A nested representation must contain nonblank `MachineType`, `Model`, and
   `SerialNumber`, composed as `tttt-mmm*sssssss`; otherwise raise an actionable `ValueError`
   before SSH. Tests cover a valid flattened value, valid nested mapping, malformed flattened
   value, and each missing or blank nested component. Replace `_run_vios_backup_command` with a
   helper accepting explicit system and VIOS selectors. Fast-path a direct system name plus VIOS
   UUID without REST; otherwise use one REST context to resolve only selectors that need it, then
   call the builder and SSH transport. Build one `HMCConfig` snapshot per call; when REST is needed,
   construct `HMCClient` from that object and pass that identical object to `run_hmc_cli`.
2. Replace the list parser with strict `csv.DictReader` over `io.StringIO(text, newline="")` for
   the explicit `name,type` header. Empty output returns `[]`; reject a wrong or duplicate header,
   empty value, or extra column. Change the list builder to:

   ```python
   lambda uuid: (
       f'lsviosbk --filter {shlex.quote(f"vios_uuids={uuid}")} '
       '-F name,type --header'
   )
   ```

3. Rename `_validate_backup_name` only if a command-neutral name improves clarity; call it before
   both backup and restore. Preserve its exact narrow rejection set and use operation-neutral
   repair guidance that does not tell creation callers to select an existing catalog entry.
4. Implement backup with the approved signature and:

   ```python
   f"mkviosbk -t {shlex.quote(backup_type)} "
   f"-m {shlex.quote(system_name)} --uuid {shlex.quote(vios_uuid)} "
   f"-f {shlex.quote(backup_name)}"
   ```

5. Implement restore with required restore type, reject values outside `viosioconfig` and `ssp`,
   build the corresponding `rstviosbk` command, and append ` -r` exactly when
   `restart_if_required` is true. Preserve the keyword-only boundaries from Task 1.
6. Set backup tool metadata to `exhaustive_targets=False`; both required selector grants remain,
   but SSP cluster scope means they do not exhaust every affected target. Pin this beside restore's
   existing non-exhaustive classification.
7. Rewrite docstrings to state supported command forms, required selectors, type limits, restart
   semantics, and validation failures. Remove every current-tense old command spelling from source.
8. Run the exact focused command from Task 1. Expect all selected tests to pass.

**Acceptance:** Validation occurs before REST/SSH, selector resolution is correctly scoped, command
arguments are single quoted words, and exact supported command tests pass.

## Task 3: Reconcile repository consumers and documentation

**Files:** Modify direct callers/tests returned by
`rg -n "hmc_(backup|restore|list)_vios|lsviosbackup|chviosbackup" src tests README.md docs`; modify
`README.md` and `docs/hmc-cli-cheatsheet.md` for the replacement calling and routing contracts.
Historical ADR/spec passages that explicitly record the former defect remain historical evidence.

**Interfaces:** Every live caller uses Task 2 signatures. Generated MCP schemas expose system,
VIOS, backup name, valid type, restart flag, and profile with the requiredness fixed by Task 2.

1. Update remaining direct test callers and exact schema/description expectations. Do not add an
   adapter for the old positional form.
2. Update the cheatsheet repository-use notes to describe the now-supported implementation. Keep
   its command examples aligned with ADR 0060. Document that backup policies now require matching
   managed-system and VIOS target grants, so existing VIOS-only narrow grants must be updated.
   Show named Python calls for backup `backup_name` and restore `backup_type` so the keyword-only
   boundary is explicit. Qualify README's generic SSH fallback note: VIOS backup/restore can bypass
   REST only with a direct system name and VIOS UUID; a system UUID requires REST MTMS resolution
   and has no `lssyscfg` fallback.
   Apply the same exception to generic SSH-routing claims in the cheatsheet and server help text so
   no current guidance promises universal name conversion or fallback for these tools. Document
   backup and restore as non-exhaustive for SSP cluster scope.
3. Run `rg -n "lsviosbackup|chviosbackup" src tests README.md docs/hmc-cli-cheatsheet.md`. Expect no
   match describing live code; any retained match must explicitly identify historical broken
   behavior in an immutable design record.
4. Run `just test`. Expect the compact passing suite and exact coverage gate.
5. Run `just smoke`. Expect a successful MCP handshake and tool count.
6. Commit one logical implementation change with Conventional Commit subject
   `fix: use supported VIOS backup commands`.

**Acceptance:** All live callers and user-facing docs use the replacement contracts, focused and
full tests pass, and no compatibility path remains.

## Task 4: Verify the complete branch

**Files:** No planned source changes; fix only evidence-backed failures attributable to this branch.

**Interfaces:** The completed branch is consumed by the quest review and delivery phases.

1. Run `just verify` bare. Expect every static, test, smoke, build, artifact, and CLI-group check to
   pass with zero warnings.
2. Run `UV_NO_SYNC=1 uv run prek run --all-files`. Expect every installed hook to pass.
3. Review `git diff main...HEAD` for the frozen scope, naming, line length, old command residue, and
   accidental unrelated edits. Expect only the approved source, tests, docs, ADR, spec, and plan.

**Acceptance:** Both guardrails pass and the diff is limited to issue #289.
