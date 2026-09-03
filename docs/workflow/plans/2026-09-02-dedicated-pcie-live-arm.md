# Dedicated PCIe live-assignment arm — implementation plan

Spec: [2026-09-02-dedicated-pcie-live-arm-design.md](../specs/2026-09-02-dedicated-pcie-live-arm-design.md) ·
Decision: [ADR 0115](../../adr/0115-dedicated-pcie-live-evidence-arm.md) ·
Issue: [#217](https://github.com/randomparity/hmc-mcp/issues/217)

**Goal.** Add the dedicated PCIe slot arm to the live-HMC test harness, with cleanup guards
that refuse to mutate anything this run cannot prove it owns, and unit tests that prove the
guards refuse.

**Architecture.** One new orchestrator in `scripts/live_test/pcie.py`
(`exercise_dedicated_pcie_assignment`), registered as live-runner subtask 24 and subtask
group `dedicated`, recording rows under step labels ST29–ST34. It follows the SR-IOV arm's
existing shape in the same file: frozen config, mutable fixture record, a state snapshot with
a summary formatter, one function per step, and an orchestrator that always reaches cleanup.
Assignment is issued through the ADR 0053-documented profile grammar over `hmc_run_command`,
because the admitted operations fail closed (ADR 0055, ADR 0115).

**Tech stack.** Python 3.11+, `dataclasses`, `pytest` with `pytest-asyncio`, `fastmcp` client
types for annotation only.

Expected implementation size: 800–950 changed lines (L) — derived from the file map below:
~470 new lines in `scripts/live_test/pcie.py`, ~8 in `scripts/live_test_runner.py`, and
~450 in the new `tests/scripts/test_pcie.py`.

## Global Constraints

Transcribed from `AGENTS.md` and the repository configuration; every task's requirements
include this section.

- **Bootstrap only with `just setup`.** Never a bare `uv sync`, `uv run`, or `uv add`. Every
  command runs as `uv run --no-sync …` or through a `just` recipe.
- **Guardrails.** `just verify` is the pre-push gate. Narrow a `static` failure with
  `just lint` / `typecheck` / `secrets` / `workflow-security` / `env-vars` / `nicknames` /
  `tool-docs-check` / `adr-numbering` / `doc-freshness`. CI additionally runs
  `uv run --no-sync prek run --all-files`, which `just verify` does not; run it before
  pushing.
- **Run gates bare.** No `| tail`, no `>/dev/null`, no `|| true` — a pipeline returns the
  last exit code and hides the failure.
- **Ruff 0.16.4** under the `[tool.ruff.lint]` config in `pyproject.toml`. `ruff check .` is
  clean on `main` and must stay clean.
- **Line length 100.**
- **Python floor 3.11**; CI is {amd64, arm64} × {3.11, 3.12, 3.13, 3.14}. Do not use syntax
  or stdlib behaviour newer than 3.11.
- **One test module per `scripts/` file**, named `tests/scripts/test_<name>.py`.
- **Diff against the merge base:** `git --no-pager diff "$(git merge-base HEAD origin/main)"`.
- **Non-interactive shell:** `GIT_EDITOR=true`, `git --no-pager`.
- **Do not modify** the SR-IOV arm (`_SriovState` through `exercise_sriov_assignment`),
  anything under `src/`, or `src/hmc_mcp/ssh/transport.py`.
- **No HMC contact.** This implementation is never run against a live or test HMC. Unit
  tests use in-process fakes only.
- `BASE_BRANCH` is `main`; the branch is `feat/dedicated-pcie-arm-217`.

### Verified external names

Each confirmed at `main@51d190c7` with the signature the tasks assume.

| Name | Source | Signature |
|---|---|---|
| `build_attribute_record` | `hmc_mcp.ssh.commands` | `(pairs: Sequence[tuple[str, object]], *, quoted=(), surface="-i") -> str` |
| `build_filter` | `hmc_mcp.ssh.commands` | `(pairs: Sequence[tuple[str, object]]) -> str` — used by `ssh/profiles.py:read_lpar_profile_record` |
| `_ADMITTED_HMC_RELEASE` / `_ADMITTED_SYSTEM_MODEL` | `hmc_mcp.operations.pcie` | `"V10R3 M1060"` / `"8375-42A"`; the envelope `require_admitted_environment` enforces |
| `parse_lpar_ownership_caller_token` | `hmc_mcp.operations.ownership` | `(description: str) -> str \| None` |
| `RunState.record_expected_or_real` | `scripts/live_test_runner.py:278` | `(subtask, tool, status, data, expected_fail_substrings: list[str], skip_reason: str) -> None` |
| `hmc_list_dedicated_pcie_slots` | MCP tool | `(system_name_or_uuid, profile=None) -> dict` with `items[]` of `{system, drc_index, description, owner_lpar, availability}` |
| `hmc_create_lpar` | MCP tool | `(system_name_or_uuid, name, resources=…, …, caller_token=None, assignments=LparPcieAssignments(), profile=None)` returning `{resource_created, workflow_completed, lpar, ownership_stamped, steps, warnings}` |
| `hmc_get_lpar` | MCP tool | `(lpar_name_or_uuid, profile=None, system_name_or_uuid=None) -> dict \| None` |
| `hmc_get_lpar_description` | MCP tool | `(system_name_or_uuid, lpar_name_or_uuid, profile=None) -> str` |
| `hmc_delete_lpar` | MCP tool | `(system_name_or_uuid, lpar_name_or_uuid, ownership_override=False, profile=None) -> str` |
| `hmc_assign_dedicated_pcie_slot` | MCP tool | `(system_name_or_uuid, lpar_name_or_uuid, profile_name, drc_index, ownership_override=False, profile=None)` — raises `PcieAssignmentUnavailableError` |
| `hmc_run_command` | MCP tool | `(cmd: str, profile=None) -> str` |

`caller_token` grammar (`validate_caller_token`): 1–64 printable ASCII, no whitespace and
none of `, = " [ ] \`. The run marker `pcie-<8 lowercase hex>` satisfies it.

## File map

| File | Action | Answerable for |
|---|---|---|
| `scripts/live_test/pcie.py` | modify (append only) | The dedicated arm: config, fixture, state reads, ST29–ST34, orchestrator |
| `scripts/live_test_runner.py` | modify | Registering subtask 24 and the `dedicated` group |
| `tests/scripts/test_pcie.py` | create | Behavioural tests for the arm's orchestration and cleanup guards |

## Task 1 — Configuration, fixture identity, and state reads

Creates the vocabulary every later task uses. Ends at something testable: configuration
resolution and state parsing can be exercised without any orchestration.

**Modifies:** `scripts/live_test/pcie.py` (append below the existing SR-IOV code; change
nothing above it).

**Interfaces this task provides to later tasks:**

```python
_DEDICATED_ENV_SYSTEM = "HMC_LIVE_PCIE_SYSTEM"
_DEDICATED_ENV_PREFIX = "HMC_LIVE_PCIE_LPAR_PREFIX"
_DEDICATED_ENV_PROFILE = "HMC_LIVE_PCIE_PROFILE"
_DEDICATED_ENV_DRC = "HMC_LIVE_PCIE_DRC_INDEX"
_DEFAULT_DEDICATED_PROFILE = "default_profile"

@dataclass(frozen=True)
class _DedicatedConfig:
    system_name: str
    lpar_prefix: str
    profile_name: str
    drc_index: str | None

@dataclass
class _DedicatedFixture:
    config: _DedicatedConfig
    run_marker: str
    lpar_name: str
    probe_lpar_name: str
    lpar_uuid: str | None = None
    drc_index: str | None = None
    baseline_io_slots: str | None = None
    applied_io_slots: str | None = None
    slot_assigned: bool = False   # reporting only; no cleanup guard reads it
    created: bool = False
    probe_created: bool = False

@dataclass(frozen=True)
class _DedicatedState:
    slot_owner: str | None
    profile_io_slots: str | None
    lpar_uuid: str | None
    caller_token: str | None

def _dedicated_config(environ: Mapping[str, str]) -> _DedicatedConfig | None: ...
def _new_run_marker() -> str: ...
def _dedicated_state_summary(s: _DedicatedState) -> str: ...
def _environment_admitted(version: str, model: str) -> bool: ...
async def _read_profile_io_slots(client, state, fixture) -> str | None: ...
async def _read_dedicated_state(client, state, fixture) -> _DedicatedState: ...
def _profile_io_slots_command(fixture: _DedicatedFixture) -> str: ...
def _change_io_slots_command(fixture: _DedicatedFixture, *, add: bool) -> str: ...
```

### Steps

1. Add imports at the top of `scripts/live_test/pcie.py`, beside the existing ones:
   `import os`, `import shlex`, `import uuid`, `from collections.abc import Mapping`, and
   `from hmc_mcp.operations.ownership import parse_lpar_ownership_caller_token`,
   `from hmc_mcp.ssh.commands import build_attribute_record, build_filter`. Keep the existing
   `from __future__ import annotations`, `from dataclasses import dataclass`,
   `from typing import TYPE_CHECKING`, and `from fastmcp import Client`.

   Also import the admitted-environment envelope from the operation that owns it:
   `from hmc_mcp.operations.pcie import (PCIE_ASSIGNMENT_UNAVAILABLE_REASON,
   _ADMITTED_HMC_RELEASE, _ADMITTED_SYSTEM_MODEL)`. The two underscore-prefixed names are
   imported rather than restated so the arm's SKIP envelope cannot drift from the one
   `require_admitted_environment` enforces for the SR-IOV path; a copied literal would go
   stale silently the first time the admitted release moves. Add a one-line comment saying
   exactly that at the import.

2. Append a section banner and the four env-var constants plus
   `_DEFAULT_DEDICATED_PROFILE`, exactly as in the Interfaces block above.

3. Append `_DedicatedConfig`, `_DedicatedFixture`, and `_DedicatedState`, exactly as in the
   Interfaces block above. `_DedicatedConfig` and `_DedicatedState` are
   `@dataclass(frozen=True)`; `_DedicatedFixture` is a plain `@dataclass` because the arm
   fills it in as it goes.

4. Append `_dedicated_config`:

   ```python
   def _dedicated_config(environ: Mapping[str, str]) -> _DedicatedConfig | None:
       """Resolve the arm's explicit configuration, or None when it is absent.

       Never falls back to ``LiveTestContext.system_name``: issue #217 requires an
       explicitly configured managed system and forbids running against an
       arbitrary one.
       """
       system_name = (environ.get(_DEDICATED_ENV_SYSTEM) or "").strip()
       lpar_prefix = (environ.get(_DEDICATED_ENV_PREFIX) or "").strip()
       if not system_name or not lpar_prefix:
           return None
       profile_name = (
           environ.get(_DEDICATED_ENV_PROFILE) or ""
       ).strip() or _DEFAULT_DEDICATED_PROFILE
       drc_index = (environ.get(_DEDICATED_ENV_DRC) or "").strip() or None
       return _DedicatedConfig(system_name, lpar_prefix, profile_name, drc_index)
   ```

5. Append `_new_run_marker` and `_dedicated_state_summary`:

   ```python
   def _new_run_marker() -> str:
       """Return a run-unique ADR 0064 caller token for this invocation."""
       return f"pcie-{uuid.uuid4().hex[:8]}"


   def _dedicated_state_summary(s: _DedicatedState) -> str:
       return (
           f"slot_owner={s.slot_owner!r} profile_io_slots={s.profile_io_slots!r} "
           f"lpar_uuid={s.lpar_uuid!r} caller_token={s.caller_token!r}"
       )


   def _environment_admitted(version: str, model: str) -> bool:
       """Whether this HMC release and system model are the ADR 0053-admitted pair.

       The same normalized comparison ``operations/pcie.py`` applies in
       ``require_admitted_environment``: the arm mutates through raw profile
       grammar rather than through that operation, so nothing else enforces the
       envelope on this path.
       """
       normalized = " ".join(version.split()).lower()
       admitted = _ADMITTED_HMC_RELEASE.lower() in normalized or all(
           marker in normalized
           for marker in ("version: 10", "release: 3", "service pack: 1060")
       )
       return admitted and model == _ADMITTED_SYSTEM_MODEL
   ```

6. Append the two command builders. The profile record goes through
   `build_attribute_record`, which is what `ssh/profiles.py:_change_profile_io_slot` uses and
   which refuses characters the `-i` parser treats as structure:

   ```python
   def _profile_io_slots_command(fixture: _DedicatedFixture) -> str:
       """Return the exact `io_slots` profile read admitted by ADR 0053.

       The `--filter` expression goes through `build_filter` for the same reason
       the record goes through `build_attribute_record`: a delimiter inside
       `profile_name` — which arrives from the environment with only `.strip()`
       applied — would otherwise rewrite the filter and answer about a profile
       the arm did not name, while Guard B's exact-match comparisons still
       reported success. `shlex.quote` protects the remote shell, not the HMC's
       own record parser, and does not substitute for it.
       """
       config = fixture.config
       filters = build_filter(
           [
               ("lpar_names", fixture.lpar_name),
               ("profile_names", config.profile_name),
           ]
       )
       return (
           f"lssyscfg -r prof -m {shlex.quote(config.system_name)} "
           f"--filter {shlex.quote(filters)} -F io_slots"
       )


   def _change_io_slots_command(fixture: _DedicatedFixture, *, add: bool) -> str:
       """Return the documented profile mutation, without --force (ADR 0055)."""
       config = fixture.config
       record = build_attribute_record(
           [
               ("name", config.profile_name),
               ("io_slots+" if add else "io_slots-", f"{fixture.drc_index}//0"),
               ("lpar_name", fixture.lpar_name),
           ]
       )
       return (
           f"chsyscfg -r prof -m {shlex.quote(config.system_name)} "
           f"-i {shlex.quote(record)}"
       )
   ```

7. Append the two state readers:

   ```python
   async def _read_profile_io_slots(
       client: Client, state: RunState, fixture: _DedicatedFixture
   ) -> str | None:
       """Return the profile's exact `io_slots` value, or None when unreadable.

       A response that is not exactly one non-empty line is refused, matching
       `ssh/profiles.py:read_lpar_profile_record`: the guards compare exact
       strings, so a multi-record answer — the filter selected more than the arm
       named — must read as unreadable rather than as its first line.
       """
       st, data = await state.call(
           client, "hmc_run_command", cmd=_profile_io_slots_command(fixture)
       )
       if st != "PASS" or not isinstance(data, str):
           return None
       records = [line for line in data.splitlines() if line.strip()]
       if len(records) != 1:
           # Includes the empty answer. A profile with no slots prints `none`,
           # so an empty response is a failed read, and reading it as "no
           # slots" would hand the guards a baseline nothing established.
           return None
       return records[0].strip()


   async def _read_dedicated_state(
       client: Client, state: RunState, fixture: _DedicatedFixture
   ) -> _DedicatedState:
       """Read live slot ownership, profile io_slots, LPAR UUID, and caller token."""
       config = fixture.config
       slot_owner = None
       st, data = await state.call(
           client,
           "hmc_list_dedicated_pcie_slots",
           system_name_or_uuid=config.system_name,
       )
       if st == "PASS" and isinstance(data, dict):
           for item in data.get("items") or []:
               if (
                   isinstance(item, dict)
                   and item.get("drc_index") == fixture.drc_index
               ):
                   slot_owner = item.get("owner_lpar") or None
                   break

       profile_io_slots = await _read_profile_io_slots(client, state, fixture)

       lpar_uuid = None
       st_lpar, data_lpar = await state.call(
           client,
           "hmc_get_lpar",
           lpar_name_or_uuid=fixture.lpar_name,
           system_name_or_uuid=config.system_name,
       )
       if st_lpar == "PASS" and isinstance(data_lpar, dict):
           lpar_uuid = data_lpar.get("UUID") or data_lpar.get("uuid")

       caller_token = None
       st_desc, data_desc = await state.call(
           client,
           "hmc_get_lpar_description",
           system_name_or_uuid=config.system_name,
           lpar_name_or_uuid=fixture.lpar_name,
       )
       if st_desc == "PASS" and isinstance(data_desc, str):
           caller_token = parse_lpar_ownership_caller_token(data_desc)

       return _DedicatedState(slot_owner, profile_io_slots, lpar_uuid, caller_token)
   ```

**Verify:** `uv run --no-sync ruff check scripts/live_test/pcie.py` — expect
`All checks passed!`. Then
`uv run --no-sync python -c "import sys; sys.path.insert(0, 'scripts'); import live_test.pcie"`
— expect no output and exit 0.

**Acceptance:** the module imports; `_dedicated_config({})` returns `None`;
`_dedicated_config({"HMC_LIVE_PCIE_SYSTEM": "s", "HMC_LIVE_PCIE_LPAR_PREFIX": "p-"})`
returns a config whose `profile_name` is `default_profile` and whose `drc_index` is `None`.

**Do not** modify any line above the new section banner.

## Task 2 — ST29 baseline and ST30 fixture creation

Ends at a testable unit: the arm can decide to SKIP, or produce a created fixture, without
any of the mutation steps existing.

**Modifies:** `scripts/live_test/pcie.py`.

**Consumes from Task 1:** `_DedicatedConfig`, `_DedicatedFixture`, `_DedicatedState`,
`_dedicated_config`, `_new_run_marker`, `_dedicated_state_summary`,
`_read_profile_io_slots`, `_DEDICATED_ENV_SYSTEM`, `_DEDICATED_ENV_PREFIX`.

**Provides to later tasks:**

```python
async def capture_dedicated_baseline(client, state) -> _DedicatedFixture | None: ...
async def create_dedicated_fixture(client, state, fixture) -> bool: ...
```

### Steps

1. Append `_admit_dedicated_environment`. It reads the two facts
   `ssh/network.py:read_sriov_environment` reads, records them so the results file is
   self-labelling, and answers whether the arm may mutate here.

   ```python
   async def _admit_dedicated_environment(
       client: Client, state: RunState, config: _DedicatedConfig
   ) -> bool:
       """Record the HMC release and system model; admit only the ADR 0053 pair."""
       st_v, version = await state.call(client, "hmc_run_command", cmd="lshmc -V")
       st_m, model = await state.call(
           client,
           "hmc_run_command",
           cmd=(
               "lssyscfg -r sys -m "
               f"{shlex.quote(config.system_name)} -F type_model"
           ),
       )
       if st_v != "PASS" or st_m != "PASS":
           state.skip(
               29,
               "dedicated admitted environment",
               "could not read the HMC release or the managed-system type-model; "
               "the dedicated arm mutates through raw profile grammar and will "
               "not do so on an unidentified environment — SKIP dedicated arm",
           )
           return False
       version_text = str(version).strip()
       model_text = str(model).strip()
       admitted = _environment_admitted(version_text, model_text)
       state.record(
           29,
           "dedicated admitted environment",
           "PASS" if admitted else "SKIP",
           f"hmc_release={version_text!r} system_model={model_text!r} "
           f"admitted={_ADMITTED_HMC_RELEASE!r}/{_ADMITTED_SYSTEM_MODEL!r}",
       )
       if not admitted:
           state.skip(
               29,
               "dedicated admitted environment (envelope)",
               f"HMC release {version_text!r} / model {model_text!r} is outside "
               "the ADR 0053-admitted envelope for dedicated profile grammar; "
               "issuing io_slots mutations here would use a grammar this "
               "repository has not probed — SKIP dedicated arm",
           )
       return admitted
   ```

2. Append `capture_dedicated_baseline`. It resolves configuration, SKIPs the arm when it is
   absent, admits the environment, lists dedicated slots, and selects a slot. Selection
   rules: with a configured `drc_index`, that exact slot must appear in the inventory with
   an empty `owner_lpar`, otherwise SKIP; without one, take the first inventory row whose
   `owner_lpar` is empty, and SKIP when there is none.

   ```python
   async def capture_dedicated_baseline(
       client: Client, state: RunState
   ) -> _DedicatedFixture | None:
       """Resolve configuration and select an unassigned dedicated slot.

       Returns the fixture to create, or None when the arm must be skipped.
       """
       print("\n=== ST29: Dedicated PCIe Baseline (issue #217) ===")
       config = _dedicated_config(os.environ)
       if config is None:
           state.skip(
               29,
               "dedicated pcie configuration",
               f"{_DEDICATED_ENV_SYSTEM} and {_DEDICATED_ENV_PREFIX} are not both "
               "set; the dedicated arm requires an explicitly configured managed "
               "system and LPAR name prefix and never falls back to a default "
               "system — SKIP dedicated arm",
           )
           return None

       if not await _admit_dedicated_environment(client, state, config):
           return None

       st, data = await state.call(
           client,
           "hmc_list_dedicated_pcie_slots",
           system_name_or_uuid=config.system_name,
       )
       state.record(29, "hmc_list_dedicated_pcie_slots (baseline)", st, data)
       if st != "PASS" or not isinstance(data, dict):
           state.skip(
               29,
               "dedicated slot inventory",
               "dedicated-slot inventory failed — SKIP dedicated arm",
           )
           return None
       # No `capability == "capability-unavailable"` branch: `list_dedicated_slots`
       # returns the literal "available" unconditionally, so such a branch could
       # never execute and would advertise a SKIP path that does not exist. A
       # failing read is already covered above.

       rows = [item for item in data.get("items") or [] if isinstance(item, dict)]
       unassigned = [row for row in rows if not (row.get("owner_lpar") or "").strip()]
       if config.drc_index is not None:
           selected = next(
               (row for row in unassigned if row.get("drc_index") == config.drc_index),
               None,
           )
           if selected is None:
               state.skip(
                   29,
                   "dedicated slot selection",
                   f"configured drc_index {config.drc_index!r} is absent from the "
                   "inventory or already owned — SKIP dedicated arm rather than "
                   "mutate a slot this run did not select",
               )
               return None
       elif unassigned:
           selected = unassigned[0]
       else:
           state.skip(
               29,
               "dedicated slot selection",
               f"no unassigned dedicated PCIe slot on {config.system_name!r} "
               f"({len(rows)} slot(s) inventoried, all owned) — SKIP dedicated arm",
           )
           return None

       run_marker = _new_run_marker()
       lpar_name = f"{config.lpar_prefix}{run_marker}"
       fixture = _DedicatedFixture(
           config=config,
           run_marker=run_marker,
           lpar_name=lpar_name,
           probe_lpar_name=f"{lpar_name}-createtime",
           drc_index=str(selected.get("drc_index")),
       )
       state.record(
           29,
           "dedicated slot selection",
           "PASS",
           f"selected drc_index={fixture.drc_index!r} "
           f"description={selected.get('description')!r} on "
           f"{config.system_name!r}; fixture lpar={fixture.lpar_name!r} "
           f"run_marker={run_marker!r}",
       )
       return fixture
   ```

3. Append `create_dedicated_fixture`. It records the create-time capability boundary, then
   creates the fixture without assignments, resolves the UUID, and captures the baseline
   `io_slots`.

   ```python
   async def create_dedicated_fixture(
       client: Client, state: RunState, fixture: _DedicatedFixture
   ) -> bool:
       """Create the run-unique owner-stamped fixture LPAR.

       Returns True when the fixture exists and its UUID was resolved, which is
       the only state in which the arm may mutate hardware.
       """
       config = fixture.config
       print("\n=== ST30: Dedicated PCIe Fixture Create (issue #217) ===")

       # Create-time assignment: `prevalidate_lpar_pcie_assignments` refuses this
       # before `create_and_stamp_lpar` runs, so today nothing is created. That
       # refusal is the only reason, and it is exactly the gate this arm's
       # evidence exists to lift — so the probe does not assume it holds.
       st, data = await state.call(
           client,
           "hmc_create_lpar",
           system_name_or_uuid=config.system_name,
           name=fixture.probe_lpar_name,
           caller_token=fixture.run_marker,
           assignments={
               "dedicated": [
                   {
                       "profile_name": config.profile_name,
                       "drc_index": fixture.drc_index,
                   }
               ]
           },
       )
       state.record_expected_or_real(
           30,
           "hmc_create_lpar (create-time dedicated assignment)",
           st,
           data,
           expected_fail_substrings=[
               "PcieAssignmentUnavailableError",
               PCIE_ASSIGNMENT_UNAVAILABLE_REASON,
           ],
           skip_reason=(
               "create-time dedicated assignment is capability-unavailable "
               "(ADR 0055 fails closed before any mutating command, pending "
               "exact io_slots readback under ADR 0053) — SKIP this path; the "
               "refusal happens in prevalidation, ahead of partition creation"
           ),
       )
       if st == "PASS":
           # The gate has been lifted since this arm was written. A partition now
           # exists that nothing else in this run tracks.
           fixture.probe_created = True
           state.record(
               30,
               "create-time dedicated assignment unexpectedly succeeded",
               "FAIL",
               "MANUAL RECOVERY REQUIRED (if cleanup below does not clear it): "
               f"the create-time probe created partition "
               f"{fixture.probe_lpar_name!r} on {config.system_name!r} with "
               f"dedicated slot {fixture.drc_index!r} assigned. ADR 0055's gate "
               "no longer refuses, so this arm's probe and ADR 0115 both need "
               "revisiting alongside the ADR 0053 capability update.",
           )

       st, data = await state.call(
           client,
           "hmc_create_lpar",
           system_name_or_uuid=config.system_name,
           name=fixture.lpar_name,
           caller_token=fixture.run_marker,
       )
       state.record(30, "hmc_create_lpar (fixture)", st, data)
       if st != "PASS":
           state.skip(
               30,
               "dedicated fixture create",
               "fixture LPAR create failed — SKIP dedicated arm; no partition to "
               "clean up",
           )
           return False
       fixture.created = True

       stamped = None
       if isinstance(data, dict):
           body = data.get("lpar")
           if isinstance(body, dict):
               fixture.lpar_uuid = body.get("UUID") or body.get("uuid")
           stamped = data.get("ownership_stamped")
           state.record(
               30,
               "fixture ownership stamp",
               "PASS" if stamped is True else "FAIL",
               f"ownership_stamped={stamped!r} warnings={data.get('warnings')!r}",
           )

       if stamped is not True:
           # The caller token is the half of the identity Guard A checks first
           # and refuses on unconditionally. Without it, assigning a slot here
           # does not risk a stranded slot on a partition cleanup may not touch
           # — it guarantees one. `False` means the stamp and the caller segment
           # were both lost; `None` means the stamp was skipped.
           state.skip(
               30,
               "fixture identity (ownership stamp)",
               f"fixture {fixture.lpar_name!r} was created but its ADR 0064 "
               f"ownership stamp did not land (ownership_stamped={stamped!r}), "
               "so cleanup could never prove this run owns it; no hardware will "
               "be mutated — proceeding directly to cleanup",
           )
           return False

       if not fixture.lpar_uuid:
           st_get, data_get = await state.call(
               client,
               "hmc_get_lpar",
               lpar_name_or_uuid=fixture.lpar_name,
               system_name_or_uuid=config.system_name,
           )
           if st_get == "PASS" and isinstance(data_get, dict):
               fixture.lpar_uuid = data_get.get("UUID") or data_get.get("uuid")

       if not fixture.lpar_uuid:
           state.skip(
               30,
               "fixture identity",
               f"fixture {fixture.lpar_name!r} was created but its UUID could not "
               "be resolved; no hardware will be mutated without a captured "
               "identity — proceeding directly to cleanup",
           )
           return False

       fixture.baseline_io_slots = await _read_profile_io_slots(client, state, fixture)
       state.record(
           30,
           "fixture profile io_slots (baseline)",
           "PASS" if fixture.baseline_io_slots is not None else "FAIL",
           f"lpar_uuid={fixture.lpar_uuid!r} "
           f"baseline io_slots={fixture.baseline_io_slots!r}",
       )
       return fixture.baseline_io_slots is not None
   ```

`PCIE_ASSIGNMENT_UNAVAILABLE_REASON` is already imported by Task 1 step 1, alongside the two
admitted-environment constants.

**Verify:** `uv run --no-sync ruff check scripts/live_test/pcie.py` — expect
`All checks passed!`.

**Acceptance:** with no `HMC_LIVE_PCIE_*` set, `capture_dedicated_baseline` returns `None`
and records exactly one SKIP row without calling any tool. With configuration set, an
admitted environment, and an inventory containing one unassigned slot, it returns a fixture
whose `lpar_name` starts with the configured prefix and whose `probe_lpar_name` is that name
plus `-createtime`. With configuration set and a non-admitted environment, it returns `None`
after exactly the two environment reads and no `hmc_list_dedicated_pcie_slots` call.

## Task 3 — ST31–ST33 assign, verify, unassign, reassign

**Modifies:** `scripts/live_test/pcie.py`.

**Consumes from Task 2:** `_DedicatedFixture`, `_read_profile_io_slots`,
`_read_dedicated_state`, `_change_io_slots_command`, `_dedicated_state_summary`,
`PCIE_ASSIGNMENT_UNAVAILABLE_REASON`.

**Provides to later tasks:**

```python
async def assign_dedicated_slot(client, state, fixture) -> bool: ...
async def verify_dedicated_assigned(client, state, fixture) -> bool: ...
async def unassign_dedicated_slot(client, state, fixture) -> bool: ...
async def reassign_dedicated_slot(client, state, fixture) -> bool: ...
```

### Steps

1. Append `assign_dedicated_slot`. It records the admitted operation's refusal, then issues
   the documented grammar and confirms by readback before setting `slot_assigned`.

   ```python
   async def assign_dedicated_slot(
       client: Client, state: RunState, fixture: _DedicatedFixture
   ) -> bool:
       """Assign the selected slot to the fixture profile and confirm by readback."""
       config = fixture.config
       print("\n=== ST31: Dedicated PCIe Assign (issue #217) ===")

       st, data = await state.call(
           client,
           "hmc_assign_dedicated_pcie_slot",
           system_name_or_uuid=config.system_name,
           lpar_name_or_uuid=fixture.lpar_name,
           profile_name=config.profile_name,
           drc_index=fixture.drc_index,
       )
       state.record_expected_or_real(
           31,
           "hmc_assign_dedicated_pcie_slot",
           st,
           data,
           expected_fail_substrings=[
               "PcieAssignmentUnavailableError",
               PCIE_ASSIGNMENT_UNAVAILABLE_REASON,
           ],
           skip_reason=(
               "the admitted dedicated assignment operation is "
               "capability-unavailable (ADR 0055); this run gathers the exact "
               "io_slots evidence ADR 0053 names as the precondition for lifting "
               "it, through the documented profile grammar below"
           ),
       )

       st, data = await state.call(
           client,
           "hmc_run_command",
           cmd=_change_io_slots_command(fixture, add=True),
       )
       state.record(31, "chsyscfg io_slots+ (assign)", st, data)

       # Read back whichever way the command reported. `RunState.call` returns
       # FAIL for any raised exception, and the SSH transport raises when its
       # timeout expires — after the HMC has already executed chsyscfg. Returning
       # early on a FAIL would leave the run believing it had not written
       # something it had, which is the belief cleanup must never hold.
       applied = await _read_profile_io_slots(client, state, fixture)
       assigned = applied is not None and str(fixture.drc_index) in applied
       if assigned:
           fixture.applied_io_slots = applied
           fixture.slot_assigned = True
       state.record(
           31,
           "profile io_slots readback (post-assign)",
           "PASS" if assigned else "FAIL",
           f"io_slots={applied!r} expected to contain drc_index="
           f"{fixture.drc_index!r} (command status {st})",
       )
       return st == "PASS" and assigned
   ```

2. Append `verify_dedicated_assigned`:

   ```python
   async def verify_dedicated_assigned(
       client: Client, state: RunState, fixture: _DedicatedFixture
   ) -> bool:
       """Verify the assignment through profile and inventory readback."""
       print("\n=== ST32: Dedicated PCIe Post-Assign Verify (issue #217) ===")
       observed = await _read_dedicated_state(client, state, fixture)
       profile_ok = (
           observed.profile_io_slots is not None
           and str(fixture.drc_index) in observed.profile_io_slots
       )
       state.record(
           32,
           "dedicated post-assign profile verify",
           "PASS" if profile_ok else "FAIL",
           _dedicated_state_summary(observed),
       )
       # Inventory ownership is informational: a profile-only assignment is not
       # expected to move the effective layer for a partition that has never
       # activated, the same asymmetry the SR-IOV arm records at ST25.
       state.record(
           32,
           "dedicated post-assign inventory owner (informational)",
           "PASS",
           f"inventory owner_lpar={observed.slot_owner!r} "
           "(a profile assignment does not change effective slot ownership until "
           "the partition activates)",
       )
       return profile_ok
   ```

3. Append `unassign_dedicated_slot`, which requires **exact** restoration of the captured
   baseline, not merely the DRC index's absence:

   ```python
   async def unassign_dedicated_slot(
       client: Client, state: RunState, fixture: _DedicatedFixture
   ) -> bool:
       """Remove the slot and require the exact captured baseline to return."""
       print("\n=== ST33: Dedicated PCIe Unassign (issue #217) ===")
       st, data = await state.call(
           client,
           "hmc_run_command",
           cmd=_change_io_slots_command(fixture, add=False),
       )
       state.record(33, "chsyscfg io_slots- (unassign)", st, data)

       # Same reason as the assign: a removal whose response was lost has still
       # removed, and the run must not believe otherwise.
       observed = await _read_profile_io_slots(client, state, fixture)
       restored = observed is not None and observed == fixture.baseline_io_slots
       if restored:
           fixture.slot_assigned = False
       state.record(
           33,
           "profile io_slots exact baseline restore",
           "PASS" if restored else "FAIL",
           f"io_slots={observed!r} baseline={fixture.baseline_io_slots!r} "
           f"(command status {st})",
       )
       return st == "PASS" and restored
   ```

4. Append `reassign_dedicated_slot`, which proves the round trip on the existing LPAR:

   ```python
   async def reassign_dedicated_slot(
       client: Client, state: RunState, fixture: _DedicatedFixture
   ) -> bool:
       """Reassign the same slot to the existing fixture, proving the round trip."""
       print("\n=== ST33: Dedicated PCIe Reassign (issue #217) ===")
       st, data = await state.call(
           client,
           "hmc_run_command",
           cmd=_change_io_slots_command(fixture, add=True),
       )
       state.record(33, "chsyscfg io_slots+ (reassign)", st, data)

       # The reassign is the LAST mutation before cleanup, so a lost response or
       # a lost confirming read here is the most dangerous of the three; read
       # back regardless.
       applied = await _read_profile_io_slots(client, state, fixture)
       ok = applied is not None and str(fixture.drc_index) in applied
       if ok:
           fixture.applied_io_slots = applied
           fixture.slot_assigned = True
       state.record(
           33,
           "profile io_slots readback (post-reassign)",
           "PASS" if ok else "FAIL",
           f"io_slots={applied!r} expected to contain drc_index="
           f"{fixture.drc_index!r} (command status {st})",
       )
       return st == "PASS" and ok
   ```

**Verify:** `uv run --no-sync ruff check scripts/live_test/pcie.py` — expect
`All checks passed!`.

**Acceptance:** `assign_dedicated_slot` sets `fixture.slot_assigned` only after the readback
observes the DRC index; `unassign_dedicated_slot` returns `False` when the readback does not
equal `fixture.baseline_io_slots` exactly; and each of the three mutating steps performs its
readback even when the command it just issued reported `FAIL`, so `applied_io_slots` records
what the profile actually holds rather than what the command claimed.

## Task 4 — ST34 cleanup guards and the orchestrator

The safety boundary. Ends at the complete arm.

**Modifies:** `scripts/live_test/pcie.py`, `scripts/live_test_runner.py`.

**Consumes from Task 3:** every function above.

**Provides:**

```python
async def cleanup_dedicated(client, state, fixture) -> None: ...
async def exercise_dedicated_pcie_assignment(client, state) -> None: ...
```

### Steps

1. Append `cleanup_dedicated`. Every refusal records a `MANUAL RECOVERY REQUIRED:` row and
   returns without attempting any further mutation.

   ```python
   async def cleanup_dedicated(
       client: Client, state: RunState, fixture: _DedicatedFixture
   ) -> None:
       """Remove the slot, then delete the fixture — each only on an exact match."""
       config = fixture.config
       print("\n=== ST34: Dedicated PCIe Cleanup (issue #217) ===")

       # Guard A — fixture identity, re-read immediately before any mutation.
       observed = await _read_dedicated_state(client, state, fixture)
       state.record(34, "dedicated pre-cleanup state", "PASS", _dedicated_state_summary(observed))
       recovery = (
           f"MANUAL RECOVERY REQUIRED: fixture {fixture.lpar_name!r} on "
           f"{config.system_name!r} could not be confirmed as this run's; no "
           "mutation was attempted. Inspect it and, once you have confirmed it "
           f"is this run's fixture, remove slot {fixture.drc_index!r} with "
           f"`{_change_io_slots_command(fixture, add=False)}` and then delete the "
           "partition."
       )
       if observed.caller_token != fixture.run_marker:
           state.record(
               34,
               "dedicated cleanup: run-marker mismatch",
               "FAIL",
               f"{recovery} Expected caller token {fixture.run_marker!r}, read "
               f"{observed.caller_token!r}.",
           )
           return
       if fixture.lpar_uuid is not None and observed.lpar_uuid != fixture.lpar_uuid:
           state.record(
               34,
               "dedicated cleanup: uuid mismatch",
               "FAIL",
               f"{recovery} Expected UUID {fixture.lpar_uuid!r}, read "
               f"{observed.lpar_uuid!r}.",
           )
           return

       # Guard B — remove the slot before the partition, decided on LIVE state.
       #
       # Deliberately not `if fixture.slot_assigned:`. That flag is false on three
       # reachable paths where the profile really does carry the DRC index — an
       # assign or reassign whose response was lost to a timeout, and one whose
       # confirming read failed — and on each of them a flag-gated branch skips
       # removal and Guard C deletes a partition with a slot still on it. The
       # profile is the fact; the flag is only a memory of it. An unreadable
       # value (None) is also unequal to the baseline, so it enters the branch
       # and is refused inside it.
       drifted = (
           fixture.baseline_io_slots is not None
           and observed.profile_io_slots != fixture.baseline_io_slots
       )
       if drifted:
           if (
               fixture.applied_io_slots is None
               or observed.profile_io_slots != fixture.applied_io_slots
           ):
               state.record(
                   34,
                   "dedicated cleanup: profile drift",
                   "FAIL",
                   "MANUAL RECOVERY REQUIRED: the fixture profile's io_slots is "
                   f"{observed.profile_io_slots!r}, which is neither the captured "
                   f"baseline ({fixture.baseline_io_slots!r}) nor the value this "
                   f"run applied ({fixture.applied_io_slots!r}), so this run "
                   "cannot prove the deviation is its own. No mutation attempted "
                   "and the partition was NOT deleted — deleting it would strand "
                   "whatever is assigned. Recover with "
                   f"`{_change_io_slots_command(fixture, add=False)}`.",
               )
               return
           st, data = await state.call(
               client,
               "hmc_run_command",
               cmd=_change_io_slots_command(fixture, add=False),
           )
           state.record(34, "chsyscfg io_slots- (cleanup)", st, data)
           if st != "PASS":
               state.record(
                   34,
                   "dedicated cleanup: slot removal failed",
                   "FAIL",
                   "MANUAL RECOVERY REQUIRED: slot removal failed, so the "
                   "partition was not deleted — deleting it now would strand "
                   f"slot {fixture.drc_index!r}. Run "
                   f"`{_change_io_slots_command(fixture, add=False)}` then delete "
                   f"{fixture.lpar_name!r}. Error: {str(data)[:400]}",
               )
               return
           after = await _read_profile_io_slots(client, state, fixture)
           if after != fixture.baseline_io_slots:
               state.record(
                   34,
                   "dedicated cleanup: baseline not restored",
                   "FAIL",
                   "MANUAL RECOVERY REQUIRED: io_slots is "
                   f"{after!r} after removal, not the captured baseline "
                   f"{fixture.baseline_io_slots!r}; the partition was not deleted. "
                   f"Reconcile {fixture.lpar_name!r} by hand.",
               )
               return
           fixture.slot_assigned = False
           state.record(
               34,
               "dedicated cleanup: slot removed",
               "PASS",
               f"io_slots restored to the captured baseline {after!r}",
           )

       # Guard C — re-read identity once more, immediately before the delete.
       final = await _read_dedicated_state(client, state, fixture)
       if final.caller_token != fixture.run_marker or (
           fixture.lpar_uuid is not None and final.lpar_uuid != fixture.lpar_uuid
       ):
           state.record(
               34,
               "dedicated cleanup: identity changed before delete",
               "FAIL",
               "MANUAL RECOVERY REQUIRED: the fixture's identity changed between "
               "slot removal and deletion — read caller_token="
               f"{final.caller_token!r} uuid={final.lpar_uuid!r}, expected "
               f"{fixture.run_marker!r} / {fixture.lpar_uuid!r}. The partition was "
               "NOT deleted; remove it by hand after confirming what it is.",
           )
           return

       # The probe partition first, when the create-time gate has been lifted and
       # the probe actually created one. Same identity rules, same override-off
       # rule: it carries this run's caller token, so the operation layer's own
       # ownership check passes without help.
       if fixture.probe_created:
           st_probe, data_probe = await state.call(
               client,
               "hmc_delete_lpar",
               system_name_or_uuid=config.system_name,
               lpar_name_or_uuid=fixture.probe_lpar_name,
           )
           state.record(
               34,
               "hmc_delete_lpar (create-time probe partition)",
               st_probe,
               data_probe,
           )
           if st_probe != "PASS":
               state.record(
                   34,
                   "dedicated cleanup: probe partition delete failed",
                   "FAIL",
                   "MANUAL RECOVERY REQUIRED: the create-time probe partition "
                   f"{fixture.probe_lpar_name!r} on {config.system_name!r} still "
                   f"exists, with dedicated slot {fixture.drc_index!r} assigned, "
                   f"and must be removed by hand. Error: {str(data_probe)[:400]}",
               )

       # Act on the identity Guard C just verified. `hmc_delete_lpar` accepts a
       # UUID or a name; deleting by name would re-resolve the name and reopen
       # the window the guard closed. `ownership_override` stays off: the fixture
       # is stamped by this run, so the tool's own description-token check passes
       # on every intended path, and it is the one operation-layer check that
       # survives the escape hatch.
       st, data = await state.call(
           client,
           "hmc_delete_lpar",
           system_name_or_uuid=config.system_name,
           lpar_name_or_uuid=fixture.lpar_uuid or fixture.lpar_name,
       )
       state.record(34, "hmc_delete_lpar (fixture cleanup)", st, data)
       if st != "PASS":
           state.record(
               34,
               "dedicated cleanup: fixture delete failed",
               "FAIL",
               f"MANUAL RECOVERY REQUIRED: fixture {fixture.lpar_name!r} on "
               f"{config.system_name!r} still exists and must be deleted by hand. "
               f"Its slot assignment was already removed. Error: {str(data)[:400]}",
           )
   ```

2. Append the orchestrator. Cleanup runs whenever a fixture was created, whatever happened
   after:

   ```python
   async def exercise_dedicated_pcie_assignment(
       client: Client, state: RunState
   ) -> None:
       """Orchestrate the dedicated-slot assign/verify/unassign/reassign/cleanup arm."""
       print("\n============================")
       print("=== Dedicated PCIe Live Test (issue #217) ===")
       print("============================")

       fixture = await capture_dedicated_baseline(client, state)
       if fixture is None:
           print("  Dedicated baseline SKIP — halting dedicated arm")
           return

       usable = await create_dedicated_fixture(client, state, fixture)
       if not usable:
           # A partition that was created but has no resolved identity or baseline
           # still has to be cleaned up; cleanup decides for itself whether it can
           # prove ownership. A create that never happened has nothing to clean up,
           # and calling cleanup on it would emit a manual-recovery row for a
           # partition that does not exist.
           if fixture.created:
               await cleanup_dedicated(client, state, fixture)
           return

       assign_ok = await assign_dedicated_slot(client, state, fixture)
       verify_ok = await verify_dedicated_assigned(client, state, fixture) if assign_ok else False
       if not assign_ok:
           state.skip(
               32,
               "dedicated post-assign verify",
               f"skipping verify: assign_ok={assign_ok}",
           )

       if assign_ok and verify_ok:
           unassign_ok = await unassign_dedicated_slot(client, state, fixture)
       else:
           state.skip(
               33,
               "chsyscfg io_slots- (unassign)",
               f"skipping unassign: assign_ok={assign_ok} verify_ok={verify_ok}",
           )
           unassign_ok = False

       if unassign_ok:
           await reassign_dedicated_slot(client, state, fixture)
       else:
           state.skip(
               33,
               "chsyscfg io_slots+ (reassign)",
               f"skipping reassign: unassign_ok={unassign_ok}",
           )

       await cleanup_dedicated(client, state, fixture)
   ```

3. In `scripts/live_test_runner.py`, extend the existing import to
   `from live_test.pcie import (exercise_dedicated_pcie_assignment, exercise_sriov_assignment)`,
   add `24: exercise_dedicated_pcie_assignment,` to `SUBTASKS`, add
   `"dedicated": [24],` to `SUBTASK_GROUPS`, and change `"all"` from `list(range(24))` to
   `list(range(25))`.

4. Replace the module docstring of `scripts/live_test/pcie.py` so it covers both arms and
   documents the four environment variables. Keep the existing SR-IOV ST23–ST28 paragraph
   verbatim and append a dedicated ST29–ST34 paragraph plus the configuration table. State
   in that paragraph that the arm SKIPs outside the ADR 0053-admitted HMC release and system
   model, since an operator reading the variable table is exactly the person who will point
   it at the wrong machine. **The
   first line must remain ordinary prose** — `just doc-freshness` reads every tracked
   Markdown file's first line looking for a generation banner, and while a `.py` docstring is
   not in its scope, keeping the file's opening conventional avoids surprising the guard's
   siblings.

**Verify:**

```sh
uv run --no-sync ruff check .
uv run --no-sync ty check
uv run --no-sync python -c "import sys; sys.path.insert(0, 'scripts'); import live_test_runner"
```

Expect `All checks passed!`, `All checks passed!`, and no output respectively.

**Acceptance:** `live_test_runner.SUBTASKS[24] is live_test.pcie.exercise_dedicated_pcie_assignment`;
`live_test_runner.SUBTASK_GROUPS["dedicated"] == [24]`; `"all"` covers 0–24.

**Rollback:** the arm is additive; reverting the commit removes it without touching the
SR-IOV arm.

## Task 5 — Behavioural tests for the orchestration and cleanup guards

**Creates:** `tests/scripts/test_pcie.py`.

**Consumes:** every public function from Tasks 2–4.

### Steps

1. Create the module with the header and the state seam. It follows
   `tests/scripts/test_inventory.py`'s `ScenarioState` pattern, extended with a **per-call**
   status map and an ordered call list so a test can assert that a mutation **did not**
   happen.

   The per-call form is required, not a convenience: `hmc_create_lpar` is called twice
   (probe, then fixture) and `hmc_run_command` carries the environment reads, every
   `lssyscfg` and every `chsyscfg`, so a run-wide status on either tool aborts the arm at a
   strictly earlier step than the one a test is named for and the assertion then passes over
   the wrong path. A `statuses` value may therefore be a plain string **or** a callable
   `(tool, kwargs, call_index) -> str`:

   ```python
   """Behavioural tests for the dedicated PCIe live-assignment arm (issue #217)."""

   from __future__ import annotations

   import sys
   from pathlib import Path
   from types import SimpleNamespace
   from typing import Any

   import pytest

   LIVE_TEST_ROOT = Path(__file__).parents[2] / "scripts"
   sys.path.insert(0, str(LIVE_TEST_ROOT))
   from live_test import pcie  # noqa: E402

   _ENV = {
       "HMC_LIVE_PCIE_SYSTEM": "sys-one",
       "HMC_LIVE_PCIE_LPAR_PREFIX": "live-",
   }


   class ScenarioState:
       """State seam recording every tool call in order, with per-tool outcomes."""

       def __init__(
           self,
           responses: dict[str, Any],
           statuses: dict[str, Any] | None = None,
       ) -> None:
           self.responses = responses
           self.statuses = statuses or {}
           self.calls: list[tuple[str, dict[str, Any]]] = []
           self.results: list[tuple[int, str, str, Any]] = []
           self.context = SimpleNamespace(system_name="unused", lp3_name="unused")
           self.tool_counts: dict[str, int] = {}

       async def call(
           self, _client: object, tool: str, **kwargs: Any
       ) -> tuple[str, Any]:
           index = self.tool_counts.get(tool, 0)
           self.tool_counts[tool] = index + 1
           self.calls.append((tool, kwargs))
           response = self.responses.get(tool)
           if callable(response):
               response = response(kwargs, len(self.calls))
           status = self.statuses.get(tool, "PASS")
           if callable(status):
               status = status(tool, kwargs, index)
           return status, response

       def record(
           self, subtask: int, tool: str, status: str, data: Any, note: str = ""
       ) -> None:
           # `RunState.record` puts the note in `note` and the payload in `data`;
           # both are retained here so an assertion can read either.
           self.results.append((subtask, tool, status, data if data is not None else note))

       def skip(self, subtask: int, tool: str, reason: str) -> None:
           self.results.append((subtask, tool, "SKIP", reason))

       def record_expected_or_real(
           self,
           subtask: int,
           tool: str,
           status: str,
           data: Any,
           expected_fail_substrings: list[str],
           skip_reason: str,
       ) -> None:
           if status == "FAIL" and any(
               text.lower() in str(data).lower() for text in expected_fail_substrings
           ):
               self.skip(subtask, tool, skip_reason)
               return
           self.record(subtask, tool, status, data)

       def commands(self) -> list[str]:
           return [
               kwargs["cmd"] for tool, kwargs in self.calls if tool == "hmc_run_command"
           ]

       def tools(self) -> list[str]:
           return [tool for tool, _ in self.calls]

       def statuses_for(self, needle: str) -> list[str]:
           return [status for _, tool, status, _ in self.results if needle in tool]
   ```

2. Add helpers building a working scenario, so each guard test differs from the happy path
   in exactly one respect:

   ```python
   def _slot_inventory(owner: str = "") -> dict[str, Any]:
       return {
           "capability": "available",
           "items": [
               {"drc_index": "553713664", "description": "PCIe adapter", "owner_lpar": owner}
           ],
       }


   _ADMITTED_VERSION = "Version: 10\nRelease: 3\nService Pack: 1060"
   _ADMITTED_MODEL = "8375-42A"


   def _happy_responses(
       marker_holder: dict[str, str],
       *,
       description: str | None = None,
       uuid_value: str | None = "fixture-uuid",
       ownership_stamped: bool | None = True,
       hmc_version: str = _ADMITTED_VERSION,
       system_model: str = _ADMITTED_MODEL,
       model: dict[str, str] | None = None,
   ) -> dict[str, Any]:
       """Responses for a run in which every step succeeds.

       ``io_slots`` is served from a small mutable model so a ``chsyscfg`` in the
       call sequence actually changes what the next ``lssyscfg`` reads — a static
       map would let a cleanup that issues no command still look correct. Tests
       that need to diverge from the command sequence (a mutation that applied
       while its response was lost) pass their own ``model``.
       """
       slots = model if model is not None else {"io_slots": "none"}

       def run_command(kwargs: dict[str, Any], _n: int) -> str:
           cmd = kwargs["cmd"]
           if cmd == "lshmc -V":
               return hmc_version
           if "-r sys " in cmd and "type_model" in cmd:
               return system_model
           if "io_slots+" in cmd:
               slots["io_slots"] = "553713664//0"
               return ""
           if "io_slots-" in cmd:
               slots["io_slots"] = "none"
               return ""
           return slots["io_slots"]

       def get_description(_kwargs: dict[str, Any], _n: int) -> str:
           if description is not None:
               return description
           token = marker_holder.get("marker", "")
           return f"[hmc-mcp owner:hmc-mcp created:2026-09-02] [caller {token}]"

       return {
           "hmc_list_dedicated_pcie_slots": lambda _k, _n: _slot_inventory(),
           "hmc_create_lpar": lambda _k, _n: {
               "resource_created": True,
               "lpar": {"UUID": uuid_value} if uuid_value else None,
               "ownership_stamped": ownership_stamped,
               "warnings": [],
           },
           "hmc_get_lpar": lambda _k, _n: {"UUID": uuid_value} if uuid_value else None,
           "hmc_get_lpar_description": get_description,
           "hmc_run_command": run_command,
           "hmc_delete_lpar": lambda _k, _n: "deleted",
           "hmc_assign_dedicated_pcie_slot": lambda _k, _n: None,
       }


   def _first_call_fails() -> Any:
       """A status callable failing only a tool's first call.

       The failing call's *payload* comes from the responses map, which can
       branch on `kwargs["name"]` to tell the create-time probe from the fixture
       create — the response callable's second argument is the run-wide call
       index, not the per-tool one.
       """

       def status(_tool: str, _kwargs: dict[str, Any], index: int) -> str:
           return "FAIL" if index == 0 else "PASS"

       return status


   def _command_fails(needle: str) -> Any:
       """A status callable failing only the `hmc_run_command` calls matching *needle*."""

       def status(_tool: str, kwargs: dict[str, Any], _index: int) -> str:
           return "FAIL" if needle in kwargs.get("cmd", "") else "PASS"

       return status


   async def _run_arm(
       monkeypatch: pytest.MonkeyPatch,
       responses: dict[str, Any],
       marker_holder: dict[str, str],
       statuses: dict[str, str] | None = None,
       env: dict[str, str] | None = None,
   ) -> ScenarioState:
       for name in (
           "HMC_LIVE_PCIE_SYSTEM",
           "HMC_LIVE_PCIE_LPAR_PREFIX",
           "HMC_LIVE_PCIE_PROFILE",
           "HMC_LIVE_PCIE_DRC_INDEX",
       ):
           monkeypatch.delenv(name, raising=False)
       for key, value in (env if env is not None else _ENV).items():
           monkeypatch.setenv(key, value)

       real_marker = pcie._new_run_marker

       def capture() -> str:
           marker = real_marker()
           marker_holder["marker"] = marker
           return marker

       monkeypatch.setattr(pcie, "_new_run_marker", capture)
       state = ScenarioState(responses, statuses)
       await pcie.exercise_dedicated_pcie_assignment(None, state)
       return state
   ```

3. Write the twenty tests the spec's Testing section lists, in that order, each named for
   the guard it exercises. Every guard test asserts **both** the recorded row and the
   absence of the forbidden call. Four written out — the three shapes every other test
   follows, plus the live-state regression that a flag-gated Guard B fails:

   ```python
   @pytest.mark.asyncio
   async def test_missing_configuration_skips_without_any_tool_call(
       monkeypatch: pytest.MonkeyPatch,
   ) -> None:
       holder: dict[str, str] = {}
       state = await _run_arm(monkeypatch, {}, holder, env={})
       assert state.calls == []
       assert [status for _, _, status, _ in state.results] == ["SKIP"]


   @pytest.mark.asyncio
   async def test_foreign_caller_token_blocks_every_cleanup_mutation(
       monkeypatch: pytest.MonkeyPatch,
   ) -> None:
       holder: dict[str, str] = {}
       responses = _happy_responses(
           holder,
           description="[hmc-mcp owner:hmc-mcp created:2026-09-02] [caller someone-else]",
       )
       state = await _run_arm(monkeypatch, responses, holder)

       assert "hmc_delete_lpar" not in state.tools()
       assert not [cmd for cmd in state.commands() if "io_slots-" in cmd]
       assert any(
           status == "FAIL" and "MANUAL RECOVERY REQUIRED" in str(data)
           for _, _, status, data in state.results
       )


   @pytest.mark.asyncio
   async def test_slot_removal_precedes_partition_delete(
       monkeypatch: pytest.MonkeyPatch,
   ) -> None:
       holder: dict[str, str] = {}
       state = await _run_arm(monkeypatch, _happy_responses(holder), holder)

       order = [
           index
           for index, (tool, kwargs) in enumerate(state.calls)
           if tool == "hmc_delete_lpar"
           or (tool == "hmc_run_command" and "io_slots-" in kwargs["cmd"])
       ]
       assert order, "expected a cleanup removal and a delete"
       assert state.calls[order[-1]][0] == "hmc_delete_lpar"
       delete = next(k for t, k in state.calls if t == "hmc_delete_lpar")
       assert delete["lpar_name_or_uuid"] == "fixture-uuid"
       assert "ownership_override" not in delete


   @pytest.mark.asyncio
   async def test_cleanup_removes_a_slot_whose_assign_response_was_lost(
       monkeypatch: pytest.MonkeyPatch,
   ) -> None:
       """The assign applied but reported FAIL, so `slot_assigned` is never set.

       A Guard B gated on that flag skips the removal and deletes the partition
       with the slot still on it. This is the test that distinguishes the two.
       """
       holder: dict[str, str] = {}
       state = await _run_arm(
           monkeypatch,
           _happy_responses(holder),
           holder,
           statuses={"hmc_run_command": _command_fails("io_slots+")},
       )

       cleanup_removals = [c for c in state.commands() if "io_slots-" in c]
       assert cleanup_removals, "cleanup must remove a slot the profile still holds"
       order = [
           index
           for index, (tool, kwargs) in enumerate(state.calls)
           if tool == "hmc_delete_lpar"
           or (tool == "hmc_run_command" and "io_slots-" in kwargs["cmd"])
       ]
       assert state.calls[order[-1]][0] == "hmc_delete_lpar"
   ```

   The remaining sixteen follow the same shape against the scenario variation the spec
   names. Their fault injections, where not obvious:

   | Spec test | Injection |
   |---|---|
   | 2 environment outside envelope | `_happy_responses(holder, system_model="9080-M9S")` |
   | 3 no unassigned slot | `_slot_inventory(owner="someone")` |
   | 4 configured DRC absent | `env={**_ENV, "HMC_LIVE_PCIE_DRC_INDEX": "999"}` |
   | 5 create-time refusal | `statuses={"hmc_create_lpar": _first_call_fails()}`, response branching on `kwargs["name"]` to return `"PcieAssignmentUnavailableError: …"` for the probe |
   | 6 probe unexpectedly succeeds | default statuses — every `hmc_create_lpar` PASSes |
   | 7 assign-tool refusal | `statuses={"hmc_assign_dedicated_pcie_slot": "FAIL"}` with a matching payload |
   | 9 UUID drift | `hmc_get_lpar` returning a different UUID after the create |
   | 10 foreign token | `description="… [caller someone-else]"` |
   | 11 unreadable identity | `statuses={"hmc_get_lpar_description": "FAIL"}` |
   | 12 profile drift | a `model` the test mutates to a third value before cleanup |
   | 13 removal does not restore | `_command_fails` variant whose `io_slots-` leaves the model unchanged |
   | 15 lost confirming read | `statuses={"hmc_run_command": _command_fails("-F io_slots")}` scoped to the post-assign read |
   | 16 identity drift before delete | `hmc_get_lpar_description` returning this run's token first and a foreign one afterwards |
   | 17 nothing ever assigned | `_command_fails("io_slots+")` **with** a `model` that does not apply it |
   | 19 no resolvable UUID | `uuid_value=None` |
   | 20 stamp did not land | `ownership_stamped=False` |

**Verify:**

```sh
uv run --no-sync pytest tests/scripts/test_pcie.py -q
uv run --no-sync ruff check .
```

Expect 20 passed and `All checks passed!`.

**Acceptance:** every guard test asserts an absent call, not only a recorded row. To confirm
the tests bite, introduce each controlled fault below in turn, observe the named tests go
red, and revert before the next one. A guard whose neutralization reddens nothing is not
covered, whatever its tests appear to assert.

| Fault | Tests that must fail |
|---|---|
| Guard A's identity comparison → `if False:` | 9, 10, 11 |
| Guard B's entry condition → `if fixture.slot_assigned:` | 14, 15 |
| Guard B's `applied_io_slots` exact-match → `if False:` | 12, 13 |
| Guard C's re-read comparison → `if False:` | 16 |
| `_admit_dedicated_environment` → always return `True` | 2 |

**Cleanup:** revert every deliberate fault before committing.

## Task 6 — Guardrails

### Steps

1. `just verify` — bare, no pipeline. Expect a clean run; note its wall-clock duration.
2. `uv run --no-sync prek run --all-files` — bare. Expect every hook `Passed`.
3. `git --no-pager diff "$(git merge-base HEAD origin/main)" --stat` and read the diff back
   for naming and complexity before committing.
4. Only once 1 and 2 are green, flip ADR 0115's `## Status` from `Proposed on 2026-09-02.`
   to the `Accepted on <date> after <what passed>` form ADRs 0053 and 0055 use, naming the
   guard tests and the two gates. The record is deliberately not Accepted before that: an
   Accepted ADR is settled ground in this repository's review workflow, and leaving it
   Accepted while unreviewed would arm a suppression rule against findings about the very
   guards it describes. Re-run `just adr-numbering` after the edit.

**Acceptance:** both gates exit 0 with no `| tail`, `>/dev/null`, or `|| true` anywhere in
the invocation, and ADR 0115's status names the evidence it was accepted on.

## Deferrals

None. The design review (`$trial-loop`/`$gauntlet`, iteration 1, 9 findings / 3 blocking)
raised no finding that was deferred, rejected, or blocked: all nine were dispositioned
`accepted-fixed` in the design set. In summary, what changed:

- **Guard B no longer reads `fixture.slot_assigned`.** It is entered on live state — the
  re-read `io_slots` differing from the captured baseline — because the flag is false on
  three reachable paths where the profile really does carry the DRC index, and on each of
  them a flag-gated guard deleted a partition with a slot still assigned.
- **The three mutating steps read back even when the command reported `FAIL`**, so the run
  never believes it did not write something it did.
- **`ownership_stamped` is not `True` now stops the arm before any mutation**, joining the
  unresolved-UUID rule under "No identity, no mutation": without the caller token, Guard A
  refuses every cleanup mutation, so mutating past that point guarantees a stranded slot.
- **An admitted-environment gate at ST29** reads the HMC release and system type-model,
  records both so the results file is self-labelling, and SKIPs outside the ADR 0053
  envelope. The dead `capability == "capability-unavailable"` branch is removed.
- **The delete acts on the verified UUID with `ownership_override` off**, in both the
  cleanup and the assign-refusal call.
- **The `--filter` expression goes through `build_filter`**, and `_read_profile_io_slots`
  refuses an answer that is not exactly one record.
- **The create-time probe partition is tracked and cleaned up**, and its skip reason no
  longer asserts "no partition was created" — a claim that stops being true on the day the
  gate this arm's evidence exists to lift is lifted.
- **The test seam takes per-call statuses**, two previously inexpressible tests are
  restated, five new tests are added, and the controlled-fault check covers all three
  guards plus the environment gate rather than Guard A alone.
- **ADR 0115 is `Proposed` until the guardrails are green** (Task 6 step 4), and its
  "before issuing any command" claim is narrowed to "before any mutating command".

The dedicated arm is not exercised against hardware by this change; the live PASS/SKIP/FAIL
matrix is the operator's, per the frozen charter's exclusions.
