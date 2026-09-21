# Plan: LPAR PowerOn activation parameters

**Goal.** Make `bootmode`, `LogicalPartitionProfile` and `OperationType` reachable on the
PowerOn job from the builder, the operations layer, the MCP tool and the CLI, without
changing the document a caller who passes none of them gets.

**Architecture.** `power_on_lpar_job` in `src/hmc_mcp/jobs/requests.py` owns two closed
vocabularies and refuses a non-member before `build_job_request` formats any XML.
`power_lpar` and `power_on_lpar` in `src/hmc_mcp/operations/lpar/core.py` forward the
values on the PowerOn arm only. `hmc_power_on_lpar` and `lpars power-on` present them.

**Stack.** Python 3.11 (`.python-version`), fastmcp, Typer 0.27.2, pytest, respx.

Expected implementation size: 180–280 changed lines (M) — derived from the file map
below: three source functions and two small vocabulary blocks, three test modules, one
recorded signature line, and one regenerated document.

Spec: `docs/workflow/specs/2026-09-21-lpar-poweron-activation-parameters-design.md`.
Decision: `docs/adr/0161-lpar-poweron-activation-parameters.md`.

## Global Constraints

- Run every command from the branch worktree. Never a bare `uv sync`, `uv run`, or
  `uv add`; every `uv run` carries `--no-sync` (`tests/test_ci_pipeline.py` asserts it).
- `src/hmc_mcp/jobs/__init__.py` must not gain an `__all__`
  (`tests/unit/test_public_api.py:65` forbids one in a package initializer).
- The `/do/{operation}` segment stays `PowerOn`; ADR 0158's `_LPAR_POWER_OPERATIONS`
  check at `src/hmc_mcp/operations/lpar/core.py:497-500` is not edited.
- Refusal style: `ValueError` naming the sorted permitted set, not the rejected value.
- `docs/tools/` is generated. When `just tool-docs-check` fails, run `just tool-docs`
  and commit the result; never hand-edit a file under `docs/tools/`. It publishes only
  each tool's **first** docstring line (`scripts/gen_tool_reference.py:154-169`), and
  no parameter text at all.
- **Every focused `pytest` command carries `--no-cov`.** `pyproject.toml:102` sets
  `addopts = "--cov=hmc_mcp --cov-report="` and `:108` sets `fail_under = 90.5`, so any
  run of a subset exits 1 on coverage even when every selected test passes. Without
  `--no-cov` a green focused run is indistinguishable from a red one.
- **`docs/capabilities/operations.json` records each tool's signature.** It stores
  `str(inspect.signature(handler))` and `scripts/check_capability_inventory.py:419-421`
  compares it by string equality, inside `just capability-inventory` → `just static` →
  `just verify` and inside the `capability-inventory` pre-commit hook. Any change to a
  tool's parameters must update that record's `signature` field in the same commit.
- **Every MCP tool parameter needs an `Args:` entry.** It is the only source of the
  rendered schema description, and
  `tests/app/test_lifecycle_schema_descriptions.py::test_core_lifecycle_parameters_have_rendered_descriptions`
  lists `hmc_power_on_lpar` (`:39`) and fails on any empty description.
- No new `HMC_*` environment variable, so `just env-vars` needs nothing.
- Guardrails: `just lint`, `just typecheck`, `just test` while iterating;
  `just verify` then `uv run --no-sync prek run --all-files` before pushing.

## File map

| File | Owns now | Owns after |
| --- | --- | --- |
| `src/hmc_mcp/jobs/requests.py` | Job document builders; `RemoteRestartOperation` vocabulary | Also `BootMode`, `BOOT_MODES`, `PowerOnOperationType`, `POWER_ON_OPERATION_TYPES`, and PowerOn parameter validation |
| `src/hmc_mcp/jobs/__init__.py` | Aggregated re-exports | Also the four new names |
| `src/hmc_mcp/operations/lpar/core.py` | LPAR power policy | Unchanged responsibility; three forwarded keyword-only parameters |
| `src/hmc_mcp/server_tools/lpar/lifecycle.py` | LPAR lifecycle MCP tools | Unchanged responsibility; three tool parameters and clarified docstring |
| `src/hmc_mcp/cli_commands/lpar/lifecycle.py` | LPAR power CLI commands | Unchanged responsibility; three options on `power-on` only |
| `tests/lpar/test_power.py` | Builder-level power job tests | Also the PowerOn vocabulary and emission tests |
| `tests/app/test_server_tools.py` | Tool-level power tests | Also the tool-threading test |
| `tests/app/test_cli_commands.py` | CLI tests | Also the flag-wiring test |
| `docs/capabilities/operations.json` | Recorded tool signatures | The `hmc_power_on_lpar` record's `signature` field only |
| `docs/tools/lpar.md` | Generated tool reference | Regenerated |

No file is created, moved, or removed. No caller migrates: every new parameter is optional,
so `power_lpar`'s three existing callers (`src/hmc_mcp/operations/lpar/provision.py:285`,
`src/hmc_mcp/server_tools/lpar/lifecycle.py:411`,
`src/hmc_mcp/cli_commands/lpar/lifecycle.py:109`) and `power_on_lpar`'s one
(`src/hmc_mcp/server_tools/lpar/lifecycle.py:358`) keep today's behaviour with no edit.

## Task 1 — Builder vocabularies, validation, conditional emission

Creates nothing. Modifies `src/hmc_mcp/jobs/requests.py`, `src/hmc_mcp/jobs/__init__.py`.
Tests in `tests/lpar/test_power.py`.

**Interfaces.** Consumes `build_job_request(operation: str, group: str, parameters: dict[str, str] | None = None) -> str`
from the same module. Provides, for Tasks 2 and 3:

```python
BootMode = Literal["norm", "dd", "ds", "of", "sms"]
PowerOnOperationType = Literal["activate"]
BOOT_MODES: frozenset[str]
POWER_ON_OPERATION_TYPES: frozenset[str]
def power_on_lpar_job(
    profile_uuid: str | None = None,
    bootmode: BootMode = "norm",
    operation_type: PowerOnOperationType | None = None,
) -> str: ...
```

### Verification

- **Contract: a no-argument call emits today's document unchanged.**
  Mode: `focused-test`. Observable: the exact return string. Test:
  `tests/lpar/test_power.py::test_power_on_lpar_job_default_document_is_unchanged`,
  which pins the literal document captured by step 1 before the builder is edited.
  Red: the pinned string differs once emission is reordered or a parameter leaks in.
  Green: `uv run --no-sync pytest tests/lpar/test_power.py -k default_document --no-cov -q`
  reports `1 passed` and exits 0.
- **Contract: optional parameters are emitted only when supplied.**
  Mode: `focused-test`. Test:
  `tests/lpar/test_power.py::test_power_on_lpar_job_emits_optional_parameters_when_supplied`,
  covering `None` and `""` for `profile_uuid` (both omit, neither validated) and `None`
  for `operation_type` (omits).
  Red: before the change the builder takes no arguments, so the call raises `TypeError`.
  Green: `uv run --no-sync pytest tests/lpar/test_power.py -k optional_parameters --no-cov -q`
  reports `1 passed`.
- **Contract: every documented boot mode is accepted.**
  Mode: `focused-test`. Test:
  `tests/lpar/test_power.py::test_power_on_lpar_job_accepts_every_boot_mode`,
  parametrized over `sorted(BOOT_MODES)`. Red: `ImportError` on `BOOT_MODES`.
  Green: `uv run --no-sync pytest tests/lpar/test_power.py -k every_boot_mode --no-cov -q`
  reports `5 passed`.
- **Contract: a non-member is refused before XML is built, with the documented message.**
  Mode: `focused-test`. Test:
  `tests/lpar/test_power.py::test_power_on_lpar_job_rejects_unknown_vocabulary`,
  parametrized over `bootmode` and `operation_type` and asserting the sorted permitted
  set appears in the message. The generated vocabulary cases in
  `tests/unit/test_xml_escaping.py` already prove *that* both are refused — `_unwrap`
  (`:96-106`) strips the optional before `_is_closed_vocabulary` (`:146`) tests it — but
  they assert nothing about the wording, which criterion 2 fixes. Red: `TypeError`
  before the change.
  Green: `uv run --no-sync pytest tests/lpar/test_power.py -k unknown_vocabulary --no-cov -q`
  reports `2 passed`.
- **Contract: the new parameters join the generated escaping cases.**
  Mode: `focused-test`. Test: generated by `tests/unit/test_xml_escaping.py` — into
  `STRING_CASES` for `profile_uuid` and `VOCABULARY_CASES` for `bootmode` and
  `operation_type`. No new test is written.
  Red: not applicable — the cases do not exist before the parameters do.
  Green: `uv run --no-sync pytest tests/unit/test_xml_escaping.py -k power_on_lpar_job --no-cov -v`
  passes and lists `requests.power_on_lpar_job.profile_uuid.str`,
  `requests.power_on_lpar_job.bootmode` and `requests.power_on_lpar_job.operation_type`
  among the ids. Use `-v`, not `-q`: `-q` prints no case ids.

### Steps

1. Before editing any source, capture the document to pin:
   `uv run --no-sync python -c "from hmc_mcp.jobs import power_on_lpar_job; print(repr(power_on_lpar_job()))"`.
   The builder is still `main`'s at this point, so this is `main`'s output. Then write
   the four tests named above in `tests/lpar/test_power.py`, pasting that value, and
   importing `BOOT_MODES`, `POWER_ON_OPERATION_TYPES` and `power_on_lpar_job` from
   `hmc_mcp.jobs`.
2. Run `uv run --no-sync pytest tests/lpar/test_power.py --no-cov -q`. Expect collection errors
   or failures naming `BOOT_MODES` and the builder's arity — the red observation.
3. In `src/hmc_mcp/jobs/requests.py`, add beside the existing aliases at `:15-18`:

   ```python
   BootMode = Literal["norm", "dd", "ds", "of", "sms"]
   PowerOnOperationType = Literal["activate"]
   BOOT_MODES = frozenset(get_args(BootMode))
   POWER_ON_OPERATION_TYPES = frozenset(get_args(PowerOnOperationType))
   ```

4. Replace `power_on_lpar_job` with:

   ```python
   def power_on_lpar_job(
       profile_uuid: str | None = None,
       bootmode: BootMode = "norm",
       operation_type: PowerOnOperationType | None = None,
   ) -> str:
       """Build a PowerOn request for one logical partition.

       ``bootmode`` and ``operation_type`` are the job's own closed vocabularies
       and are refused here, before any XML is built. ``profile_uuid`` is the UUID
       of the partition profile to activate against, not a connection profile.
       """
       if bootmode not in BOOT_MODES:
           allowed = ", ".join(sorted(BOOT_MODES))
           raise ValueError(f"PowerOn boot mode must be one of: {allowed}")
       if operation_type is not None and operation_type not in POWER_ON_OPERATION_TYPES:
           allowed = ", ".join(sorted(POWER_ON_OPERATION_TYPES))
           raise ValueError(f"PowerOn operation type must be one of: {allowed}")
       parameters = {"force": "false", "novsi": "true", "bootmode": bootmode}
       if profile_uuid:
           parameters["LogicalPartitionProfile"] = profile_uuid
       if operation_type:
           parameters["OperationType"] = operation_type
       return build_job_request("PowerOn", "LogicalPartition", parameters)
   ```

5. Add `BOOT_MODES`, `POWER_ON_OPERATION_TYPES`, `BootMode` and `PowerOnOperationType`
   to the `from .requests import (...)` block in `src/hmc_mcp/jobs/__init__.py`, keeping
   the existing alphabetical grouping and adding no `__all__`.
6. Run the four green commands in the Verification block. Each must report the stated
   count.
7. Run `just lint` and `just typecheck`. Expect `All checks passed!` from ruff and
   `All checks passed` from ty.
8. Commit: `feat(jobs): accept profile, boot mode and operation type on PowerOn`.

**Acceptance.** `power_on_lpar_job()` is byte-identical to `main`'s output; the five boot
modes and `activate` are accepted; any other value raises `ValueError` naming the sorted
set; the new names import from `hmc_mcp.jobs`.

## Task 2 — Thread the parameters through the operations layer

Creates nothing. Modifies `src/hmc_mcp/operations/lpar/core.py`. Tests in
`tests/lpar/test_power.py`.

**Interfaces.** Consumes Task 1's `BootMode`, `PowerOnOperationType` and the new
`power_on_lpar_job` signature. Provides, for Task 3:

```python
async def power_lpar(
    hmc, system_name_or_uuid, lpar_name_or_uuid, *, power_on: bool,
    immediate: bool = False, force: bool = False, wait: bool = False,
    timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    poll_interval: int = DEFAULT_JOB_POLL_INTERVAL, ownership_override: bool = False,
    boot_mode: BootMode = "norm", partition_profile_uuid: str | None = None,
    operation_type: PowerOnOperationType | None = None,
) -> LparPowerResult: ...

async def power_on_lpar(
    hmc, lpar_name_or_uuid, *, system_name_or_uuid: str | None = None,
    wait: bool = False, timeout_seconds: int = 300, poll_interval: int = 5,
    force: bool = False, affinity_assessment=None, ownership_override: bool = False,
    boot_mode: BootMode = "norm", partition_profile_uuid: str | None = None,
    operation_type: PowerOnOperationType | None = None,
) -> LparPowerOnOutcome: ...
```

### Verification

- **Contract: `power_lpar` forwards all three to the builder on the PowerOn arm.**
  Mode: `focused-test`. Test:
  `tests/lpar/test_power.py::test_power_lpar_forwards_activation_parameters`, an
  `AsyncMock` client whose `submit_job` captures the document; asserts
  `LogicalPartitionProfile`, `bootmode>sms` and `OperationType>activate` appear in it.
  Red: `TypeError: power_lpar() got an unexpected keyword argument 'boot_mode'`.
  Green: `uv run --no-sync pytest tests/lpar/test_power.py -k forwards_activation --no-cov -q`
  reports `1 passed`.
- **Contract: the PowerOff arm is unaffected.**
  Mode: `focused-test`. Test:
  `tests/lpar/test_power.py::test_power_lpar_power_off_document_is_unchanged`, asserting
  the PowerOff document contains none of the three parameter names. Red: fails if the
  values are forwarded unconditionally rather than on the PowerOn arm.
  Green: same module, `-k power_off_document --no-cov`, reports `1 passed`.
- **Contract: `power_on_lpar` passes them through.**
  Mode: `focused-test`. Test:
  `tests/lpar/test_power.py::test_power_on_lpar_passes_activation_parameters`, patching
  `power_lpar` and asserting the keyword arguments it receives. Red: `TypeError`.
  Green: same module, `-k passes_activation --no-cov`, reports `1 passed`.

### Steps

1. Write the three tests. Use `unittest.mock.AsyncMock` for the client and
   `unittest.mock.patch` for `power_lpar`, both already used in `tests/lpar/`.
2. Run `uv run --no-sync pytest tests/lpar/test_power.py --no-cov -q` and observe the three
   `TypeError` failures.
3. Add to `power_lpar`'s keyword-only block, after `ownership_override`:
   `boot_mode: BootMode = "norm"`, `partition_profile_uuid: str | None = None`,
   `operation_type: PowerOnOperationType | None = None`. Import `BootMode` and
   `PowerOnOperationType` from `...jobs` alongside the existing `power_on_lpar_job`
   import.
4. Replace the document selection with:

   ```python
   document = (
       power_on_lpar_job(
           profile_uuid=partition_profile_uuid,
           bootmode=boot_mode,
           operation_type=operation_type,
       )
       if power_on
       else power_off_lpar_job(immediate=immediate)
   )
   ```

5. Add the same three keyword-only parameters to `power_on_lpar` and forward them in its
   existing `await power_lpar(...)` call.
6. Extend `power_lpar`'s docstring with one sentence: the three activation parameters
   apply to PowerOn only and are ignored on the PowerOff path because it builds a
   different document.
7. Run the three green commands, then `just lint` and `just typecheck`.
8. Commit: `feat(lpar): forward activation parameters through the power path`.

**Acceptance.** A `power_lpar(..., power_on=True, boot_mode="sms", partition_profile_uuid=U,
operation_type="activate")` call submits a document carrying all three; the PowerOff
document is unchanged; `power_on_lpar` forwards its three to `power_lpar`.

## Task 3 — Surface on the MCP tool and the CLI, regenerate the docs

Creates nothing. Modifies `src/hmc_mcp/server_tools/lpar/lifecycle.py`,
`src/hmc_mcp/cli_commands/lpar/lifecycle.py`, `docs/tools/lpar.md`. Tests in
`tests/app/test_server_tools.py` and `tests/app/test_cli_commands.py`.

**Interfaces.** Consumes Task 2's `power_on_lpar` and `power_lpar` signatures and Task 1's
`BootMode` and `PowerOnOperationType`. Provides no name later tasks depend on.

### Verification

- **Contract: the tool forwards all three and names none of them `profile`.**
  Mode: `focused-test`. Test:
  `tests/app/test_server_tools.py::test_power_on_lpar_tool_forwards_activation_parameters`,
  modelled on `test_power_on_lpar_submits_job` at `:249`: same respx `mock_hmc` routes,
  calling `hmc_power_on_lpar(LPAR_UUID, boot_mode="sms", partition_profile_uuid=PROFILE_UUID,
  operation_type="activate")` and asserting all three appear in the captured body. The same
  test asserts
  `{"boot_mode", "partition_profile_uuid", "operation_type"} <= set(inspect.signature(hmc_power_on_lpar).parameters)`
  and that `profile` still means the connection profile by remaining a `str | None`.
  Red: `TypeError: hmc_power_on_lpar() got an unexpected keyword argument 'boot_mode'`.
  Green: `uv run --no-sync pytest tests/app/test_server_tools.py -k tool_forwards_activation --no-cov -q`
  reports `1 passed`.
- **Contract: the CLI flags reach the job document.**
  Mode: `focused-test`. Test:
  `tests/app/test_cli_commands.py::test_lpars_power_on_activation_flags_reach_the_job`,
  modelled on `test_lpars_power_on_submits_power_on_job` at `:933`: invokes
  `["lpars", "power-on", LPAR_UUID, "--force", "--yes", "--boot-mode", "sms",
  "--partition-profile", PROFILE_UUID, "--operation-type", "activate"]` and asserts the
  three parameters in `fake_hmc.calls[0][1][1]`. A second case invokes
  `["--boot-mode", "bogus"]` and asserts `result.exit_code == 2` — Typer 0.27.2 rejects a
  non-member `Literal` before the command body runs, confirmed by probe on this worktree.
  Red: `exit_code == 2` with `No such option: --boot-mode` on the first case.
  Green: `uv run --no-sync pytest tests/app/test_cli_commands.py -k activation_flags --no-cov -q`
  reports `2 passed`.
- **Contract: the generated tool reference matches the registry.**
  Mode: `focused-test`. Test: `just tool-docs-check`. Red: it exits non-zero listing
  `docs/tools/lpar.md` once the first docstring line changes and the tree is stale.
  Green: `just tool-docs-check` prints no diff and exits 0.
- **Contract: every new tool parameter carries a rendered schema description.**
  Mode: `focused-test`. Test:
  `tests/app/test_lifecycle_schema_descriptions.py::test_core_lifecycle_parameters_have_rendered_descriptions`,
  which lists `hmc_power_on_lpar` at `:39` and collects every parameter whose schema
  `description` is empty. The `Args:` block is that description's only source, so this is
  the executable consumer of the text. Red: it fails naming `boot_mode`,
  `partition_profile_uuid` and `operation_type` if step 3 lands before step 4.
  Green: `uv run --no-sync pytest tests/app/test_lifecycle_schema_descriptions.py --no-cov -q`
  passes.
- **Contract: the stored tool signature still matches live introspection.**
  Mode: `focused-test`. Test: `just capability-inventory`, which compares
  `docs/capabilities/operations.json`'s `signature` field for `hmc_power_on_lpar` to
  `str(inspect.signature(handler))` by equality. Red: it exits non-zero with
  `operation evidence hmc_power_on_lpar: signature does not match registry` as soon as
  step 3 lands. Green: it reports the inventory as structurally valid and exits 0.

### Steps

1. Write both new tests. Define `PROFILE_UUID = "00000000-0000-0000-0000-0000000000aa"` in
   each module beside the existing UUID constants.
2. Run both green commands and observe the two red failures named above.
3. In `src/hmc_mcp/server_tools/lpar/lifecycle.py`, import `BootMode` and
   `PowerOnOperationType` from `...jobs`, add to `hmc_power_on_lpar` after
   `ownership_override`:

   ```python
   boot_mode: BootMode = "norm",
   partition_profile_uuid: str | None = None,
   operation_type: PowerOnOperationType | None = None,
   ```

   and forward all three in the `power_on_lpar(...)` call.
4. Extend that docstring's `Args:` block with:

   ```text
   boot_mode: Boot mode to activate into: norm, dd, ds, of, or sms. Defaults to
       norm, which is what the partition activates into today.
   partition_profile_uuid: UUID of the *partition profile* to activate against.
       This is not the `profile` argument above, which selects a configured HMC
       connection; when omitted the partition activates against its current
       configuration.
   operation_type: PowerOn operation type; `activate` states the default
       explicitly. Omit it to send no OperationType parameter.
   ```

   and amend the existing `profile:` entry to read
   `profile: Optional configured HMC connection profile name — not the partition
   profile, which is partition_profile_uuid; uses the default when omitted.`
   Then replace the docstring's **first line**, which is the only text
   `docs/tools/lpar.md` publishes, with:

   ```text
   Submit a PowerOn job for a logical partition, optionally against a named partition profile.
   ```

   This is how completion criterion 6's `docs/tools/` half is met;
   `scripts/gen_tool_reference.py` renders no parameter text and is not changed.
5. In `src/hmc_mcp/cli_commands/lpar/lifecycle.py`, import `BootMode` and
   `PowerOnOperationType` from `...jobs`, add three options to `lpars_power_on` only:

   ```python
   boot_mode: BootMode = typer.Option(
       "norm", "--boot-mode", help="Boot mode to activate into"
   ),
   partition_profile: str | None = typer.Option(
       None,
       "--partition-profile",
       help="UUID of the partition profile to activate against; not the connection profile",
   ),
   operation_type: PowerOnOperationType | None = typer.Option(
       None, "--operation-type", help="PowerOn operation type"
   ),
   ```

6. Add three parameters to `_power_lpar` with the same defaults — it has no keyword-only
   marker today, so append them after `ownership_override` rather than introducing one —
   and forward them into its `power_lpar(...)` call. **The CLI option is named
   `partition_profile` and the operations parameter is named `partition_profile_uuid`**,
   so this is the one call site that renames: pass
   `partition_profile_uuid=partition_profile`. The other two names match.
   `lpars_power_off` calls `_power_lpar` without any of them, so the PowerOff path keeps
   today's behaviour through Task 2's PowerOn-arm-only forwarding.
7. Update `docs/capabilities/operations.json`: set the `hmc_power_on_lpar` record's
   `signature` field to the new value. Do not hand-write it — read it back from the
   handler:
   `uv run --no-sync python -c "import inspect; from hmc_mcp.server_tools.lpar.lifecycle import hmc_power_on_lpar as t; print(str(inspect.signature(t)))"`
   and paste that exact string. Touch no other record and no other field. Then run
   `just capability-inventory` and expect exit 0.
8. Run `just tool-docs`, then `git --no-pager diff --stat docs/tools/`. Expect
   `docs/tools/lpar.md` to change — the summary cell for `hmc_power_on_lpar` now carries
   the new first docstring line. Commit whatever it regenerates without editing it.
9. Run the green commands, then `just lint`, `just typecheck`, `just test`, and
   `just smoke` — `smoke` is the check that the fastmcp schema still builds with the new
   annotations; expect it to report the tool count without error.
10. Commit: `feat(lpar): expose activation parameters on the power-on tool and CLI`.

**Acceptance.** `hmc_power_on_lpar` and `hmc-mcp lpars power-on` each accept the three
parameters; `--boot-mode bogus` exits 2; every new tool parameter renders a non-empty
schema description; `docs/capabilities/operations.json` matches live introspection;
`docs/tools/lpar.md` is regenerated and its summary cell names the partition profile; no
new parameter is named `profile`.

## Final verification

Run in the branch worktree, bare, reading exit status:

1. `just verify` — expect `verify: all groups load OK`.
2. `uv run --no-sync prek run --all-files` — expect every hook `Passed`. CI runs this
   after `just verify` and `just verify` does not.
3. `git --no-pager diff --stat "$(git merge-base HEAD origin/main)" -- src tests docs/capabilities docs/tools`
   — expect only the files in the file map, and nothing else. Scoping the pathspec is
   deliberate: this branch also carries its own three design artifacts (the ADR, the spec
   and this plan), which the file map does not list because they are the lane's output
   rather than its subject. They land in the feature PR, as `docs/adr/0158-*` and
   `docs/workflow/plans/2026-09-16-*` did on the branch that became PR #862. An unscoped
   diff would report them every time and teach you to wave the step through.

`just verify` is locally green on one interpreter; CI is eight legs across Python
3.11-3.14 on two architectures. This change alters tool signatures that
`scripts/gen_tool_reference.py` introspects, which is the case AGENTS.md records as having
disagreed with a workstation before. Read the CI matrix rather than re-running locally if a
leg fails.

## Deferrals carried into the build

None. No finding from the design review was deferred.
