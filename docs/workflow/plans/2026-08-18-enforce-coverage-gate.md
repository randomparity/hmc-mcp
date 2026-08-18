# Enforced coverage gate implementation plan

**Goal:** Make the package coverage gate fail when the total is below its declared floor, and
raise real coverage above that floor so the gate passes on merit rather than on rounding.

**Architecture:** Two independent changes land in a fixed order. First, characterization tests
raise measured coverage from 89.78% to a margin above 90%, leaving the existing (lenient) gate
green throughout. Second, `pyproject.toml` moves the floor and its precision into
`[tool.coverage.report]`, which makes the comparison exact, and five tests in
`tests/test_ci_pipeline.py` lock that behavior — two behavioral (an under-floor total fails, a
total exactly on the floor passes) and three guarding the configuration's shape, its invocation
sites, and which file coverage.py reads. The order matters: tightening the gate before raising
coverage would leave a commit whose guardrails are red.

**Tech stack:** Python 3.11–3.14, pytest 9.1.1, pytest-cov 7.1.0, coverage 7.15.4, Typer +
`typer.testing.CliRunner`, `just` recipes, `uv`.

## Global constraints

- The declared floor is `90`. It is not lowered. [ADR 0034](../../adr/0034-exact-coverage-gate.md)
  governs this and the configuration shape below.
- The gate's final configuration is exactly this — comment included, since the existing comment's
  first sentence ("Keep the package-wide gate at the rounded full-suite baseline") describes the
  defect and stops being true here:
  ```toml
  [tool.pytest.ini_options]
  pythonpath = ["tests"]
  # Package-wide coverage gate lives in [tool.coverage.report]. Run a focused
  # subset with `--no-cov` when package-wide coverage is not meaningful.
  addopts = "--cov=hmc_mcp --cov-report=term-missing"

  [tool.coverage.report]
  fail_under = 90
  precision = 2
  ```
  `--cov-fail-under` must not appear in `addopts`: a command-line floor silently overrides a
  configured one (verified — configured `95` with `--cov-fail-under=50` reports "Required test
  coverage of 50% reached" and exits `0`).
- Target coverage is **at least 90.50%** on CPython 3.11, i.e. **no more than 567 missed
  statements** of 5977. Baseline is 611 missed (89.78%).
- No file under `src/hmc_mcp/` is modified. Coverage rises only by exercising shipped code. A
  statement that appears genuinely unreachable is reported in the pull request, not deleted.
- **A defect found while characterizing existing behavior is filed as a GitHub issue in the same
  turn and referenced from the test's docstring — never fixed here**, since `src/hmc_mcp/` is
  out of scope. The characterization test pins current behavior, so the issue is what stops a
  later fix from looking like a regression.
- New CLI tests extend `tests/app/test_cli_commands.py` and reuse its `FakeHMC` class and
  `fake_hmc` fixture; no second CLI-testing idiom is introduced.
- Branch `feat/enforce-coverage-gate-240`; base `main`. Host architecture `arm64`; no target
  architecture is declared by repository policy; relationship `no-target-declared`.
- Guardrails: focused `uv run --no-sync pytest -q --no-cov <paths>` during development;
  `just test`; `just verify`; `UV_NO_SYNC=1 uv run prek run --all-files`.
- Every commit leaves `just verify` green.

## Task 1: Cover the `cli_storage` command bodies

**Files:** Modify `tests/app/test_cli_commands.py`.

**Interfaces:** Consumes the existing module-level `RUNNER = CliRunner()`, the `FakeHMC` class,
the `fake_hmc` fixture, and the constants `VIOS_UUID`, `VG_UUID`, `LPAR_UUID`. Produces new test
functions only; it defines nothing that later tasks consume.

`src/hmc_mcp/cli_storage.py` is the largest single gap: 110 missed statements of 199 (45%). Its
commands come in four shapes, and each shape has a different injection point. Getting the
injection point wrong produces a test that passes without executing the command body, so the
shape table below is load-bearing.

| Shape | Commands | Patch target |
|---|---|---|
| A — `_with_client(lambda hmc: op(...))`, operation imported at module top | `list-vgs`, `delete-disk`, `create-media-repo`, `create-media`, `delete-media-repo`, `delete-media`, `get-media-repo`, `list-optical-media` | `hmc_mcp.cli_storage.<operation>` |
| B — `_run(_go)` with `load_profile()` + `HMCClient(config)`, operation imported **inside** the function | `list-mappings`, `detach-mapping` | `hmc_mcp.cli_storage.load_profile`, `hmc_mcp.cli_storage.HMCClient`, and `hmc_mcp.operations_storage.<operation>` |
| C — `_run(_go)` with `load_profile()` + `HMCClient(config)`, operation imported at module top | `upload-iso` | `hmc_mcp.cli_storage.load_profile`, `hmc_mcp.cli_storage.HMCClient`, `hmc_mcp.cli_storage.upload_iso` |
| D — `_run(_go)` with `async with _client()`, operation imported at module top | `map` | `hmc_mcp.cli_storage.map_storage`, with the `fake_hmc` fixture supplying the client |

Shape B imports its operation inside `_go`, so patching `hmc_mcp.cli_storage.list_storage_mappings`
has no effect — the name does not exist on that module. It must be patched on
`hmc_mcp.operations_storage`.

Shape D goes through `_client()` rather than `_with_client`, so the `fake_hmc` fixture covers the
client seam (both funnel through `cli_app.client_from_env`), but the command unpacks its result:
`lpar_uuid, result = _run(_go)`. A fake returning a dict or a scalar raises `ValueError` *after*
`_run` returns — outside its `except Exception` handler — so it surfaces through `CliRunner` as an
unhandled unpacking error that says nothing about the CLI. The fake must return a two-element
`(lpar_uuid, result)` tuple.

1. Add a shared helper for shapes B and C directly above the storage tests:

   ```python
   class _FakeClientContext:
       """Async context manager standing in for HMCClient in cli_storage._go bodies."""

       def __init__(self) -> None:
           self.entered = False

       async def __aenter__(self):
           self.entered = True
           return self

       async def __aexit__(self, *exc_info) -> None:
           return None


   @pytest.fixture
   def direct_client(monkeypatch):
       """Neutralise load_profile()/HMCClient() for the commands that build their own client."""
       client = _FakeClientContext()
       monkeypatch.setattr("hmc_mcp.cli_storage.load_profile", lambda: None)
       monkeypatch.setattr("hmc_mcp.cli_storage.HMCClient", lambda _config: client)
       return client
   ```

2. Write one test per row of the coverage table below. Each asserts the exit code, at least one
   rendered string unique to that branch, and — where the command mutates — that the patched
   operation received the arguments the CLI parsed.

   **Assert on the right stream.** click 8.4.2 removed `mix_stderr`, so `result.stdout` does
   **not** contain stderr. Output written through `console` is on `result.stdout`; output written
   through `err_console`, and click's own `Aborted.`, are on `result.stderr`. The module already
   uses `result.stderr` in 55 places — follow it. Asserting an abort message on `result.stdout`
   produces a red test with a confusing diagnostic, and `result.output` is not a safe blanket
   substitute because it is only correct for some of these rows.

   **Getting past a confirmation prompt.** Most storage commands take `--yes`/`-y`;
   `detach-mapping` takes `--confirm`/`-y` instead. `map` also prompts, so its success-path test
   needs `--yes`. To exercise a *decline* instead, pass `input="n\n"` and assert
   `result.exit_code == 1` with `"Aborted."` in `result.stderr`.

   Complete code for one test of each shape:

   ```python
   def test_storage_list_vgs_renders_a_table(fake_hmc, monkeypatch):
       async def fake_list(_hmc, vios):
           assert vios == VIOS_UUID
           return [{"UUID": VG_UUID, "Resource": {"GroupName": "rootvg",
                                                  "FreeSpaceInMBytes": "5120",
                                                  "GroupCapacity": "102400"}}]

       monkeypatch.setattr("hmc_mcp.cli_storage.list_volume_groups", fake_list)

       result = RUNNER.invoke(cli.app, ["storage", "list-vgs", VIOS_UUID])

       assert result.exit_code == 0
       assert "rootvg" in result.stdout
       assert "Volume Groups on" in result.stdout


   def test_storage_list_mappings_renders_backing_storage(direct_client, monkeypatch):
       async def fake_mappings(_hmc, vios, lpar):
           assert (vios, lpar) == (VIOS_UUID, None)
           return [{"ElementID": "map-1",
                    "AssociatedLogicalPartition": {"PartitionName": "lpar1"},
                    "Storage": {"VirtualDisk": {"DiskName": "bootvol"}}}]

       monkeypatch.setattr(
           "hmc_mcp.operations_storage.list_storage_mappings", fake_mappings
       )

       result = RUNNER.invoke(cli.app, ["storage", "list-mappings", VIOS_UUID])

       assert result.exit_code == 0
       assert "bootvol" in result.stdout
       assert "VirtualDisk" in result.stdout
       assert direct_client.entered


   def test_storage_upload_iso_reports_an_existing_duplicate(direct_client, monkeypatch):
       async def fake_upload(_hmc, vios, vg, media_name, iso_source):
           assert (vios, vg, media_name) == (VIOS_UUID, VG_UUID, "aix.iso")
           assert iso_source == "/tmp/aix.iso"
           return {"status": "existing", "media_name": "aix.iso",
                   "media_size_bytes": 1048576, "sha256": "abc123",
                   "existing_name": "aix-old.iso", "media": {"MediaName": "aix.iso"}}

       monkeypatch.setattr("hmc_mcp.cli_storage.upload_iso", fake_upload)

       result = RUNNER.invoke(
           cli.app,
           ["storage", "upload-iso", VIOS_UUID, VG_UUID, "aix.iso", "/tmp/aix.iso"],
       )

       assert result.exit_code == 0
       assert "Upload status: existing" in result.stdout
       assert "aix-old.iso" in result.stdout
       assert "1,048,576 bytes" in result.stdout
   ```

3. Cover these rows. Each is one test unless a count is given.

   | Command | Branches to cover | Shape |
   |---|---|---|
   | `list-vgs` | table render; `--json` | A |
   | `delete-disk` | confirmed delete; declined confirmation aborts with exit 1 and no call | A |
   | `map` | success path renders "Mapped" and calls `map_storage`; the fake returns `("lpar-uuid", {...})`. An invalid `--kind` already has a test asserting exit 2, so do not duplicate it | D |
   | `create-media-repo` | confirmed create; declined confirmation aborts | A |
   | `create-media` | confirmed create; declined confirmation aborts | A |
   | `delete-media-repo` | confirmed delete; declined confirmation aborts | A |
   | `delete-media` | confirmed delete; declined confirmation aborts | A |
   | `get-media-repo` | found (renders Name/Size); empty (renders "No media repository found"); `--json` | A |
   | `list-optical-media` | non-empty table; empty ("No optical media found"); `--json` | A |
   | `list-mappings` | table with `VirtualDisk`; table with `PhysicalVolume`; `--json` | B |
   | `detach-mapping` | `--confirm` deletes; a raising operation exits 1 and puts `Failed to delete storage mapping` on **stdout** while the real diagnostic goes to stderr — see the note below | B |
   | `upload-iso` | duplicate-existing render; `--json` | C |
   | `attach-disk` | `--json` with an incomplete workflow exits 1 | none — see below |

   **`attach-disk` patches nothing.** It is not a shape-A command. Its `--json` branch
   (`cli_storage.py:175-179`) calls `dataclasses.asdict(result)`, and the real return type is the
   frozen dataclass `AttachDiskResult` (`operations_provision.py:96-103`). A fake returning a dict
   or a `SimpleNamespace` makes `asdict()` raise `TypeError` before line 176 runs; `CliRunner`
   catches it and reports `exit_code == 1`, so an exit-code-only assertion passes while the
   branch stays uncovered. Instead reuse the setup of the existing
   `test_storage_attach_disk_partial_failure_is_visible_and_nonzero`
   (`tests/app/test_cli_commands.py:1450`) — `fake_hmc.fail_on = "add_vscsi_adapter"`, the real
   `attach_disk_to_lpar` running against `FakeHMC` — with `--json` added. Assert on the JSON
   content, not only the exit code: `json.loads(result.stdout)["workflow_completed"] is False`.
   A `TypeError` cannot satisfy that.

   Decline a confirmation by passing `input="n\n"` to `RUNNER.invoke`; `typer.Abort` renders
   `Aborted.` **to stderr** and exits 1, so assert on `result.stderr`.

   **Known defect on the `detach-mapping` failure path — issue #242.** `storage_detach_mapping`
   wraps `_run(_go)` in `except Exception`, but `_run` already reports the failure through `_fail`
   and raises `typer.Exit(1)` — and `typer.Exit` subclasses `RuntimeError`, so the handler catches
   its own framework's exit sentinel. The observed result is
   `Failed to delete storage mapping: ` on stdout with an empty value, alongside the real
   `Error: <exc>` on stderr. Characterize what it does today: assert the substring
   `Failed to delete storage mapping` and `exit_code == 1`, and put a docstring line on the test
   naming issue #242 and stating that the empty trailing value is the defect, not the contract.
   Do not assert the full line, or the fix for #242 will redden a test that looks protective.

4. Confirm the new tests bite. Pick `test_storage_get_media_repo_reports_empty`, temporarily change
   `cli_storage.storage_get_media_repo`'s `else` branch message, run the focused suite, and
   confirm that test fails; then revert the source edit. Record both results. This step is the
   substitute for TDD's confirm-it-fails, since these tests characterize code that already exists.

5. Run `uv run --no-sync pytest -q --no-cov tests/app/test_cli_commands.py`. Expect all tests to
   pass.

6. Measure: `uv run --no-sync pytest -q 2>&1 | tail -3`. Record the TOTAL line. Expect
   `src/hmc_mcp/cli_storage.py` to move from 45% toward full coverage and the package total to be
   at or above 90.50% (567 or fewer missed statements).

7. Run `just verify`. Expect green — the gate is still the lenient one at this point, so this
   confirms no regression rather than confirming the gate.

8. Commit: `test: cover the cli_storage command bodies (#240)`.

**Acceptance criteria:** `tests/app/test_cli_commands.py` gains tests for every row above; the
focused suite passes; the package total is at or above 90.50% on CPython 3.11; no file under
`src/hmc_mcp/` is modified; `just verify` is green.

## Task 2: Cover the `cli_systems` render paths — only if Task 1 fell short

**Files:** Modify `tests/app/test_cli_commands.py`.

**Interfaces:** Consumes the same `RUNNER`, `FakeHMC`, `fake_hmc` fixture, and the `SYSTEM_UUID`
constant as Task 1. Produces test functions, plus one new `search_uom` method on `FakeHMC` — the
only change this plan makes to that class.

**Trigger:** Run this task whenever any measurement reports below 90.50% — Task 1 step 6, Task 3
step 6, Task 4 step 1, or any CI leg in Task 4 step 4. If Task 1 already reached the target, skip
it on the first pass and record the measured total as the reason; a later shortfall re-opens it.

**Bound:** after two remediation rounds that still leave a measurement below 90.50%, stop and
raise the shortfall with the operator rather than looping. The enforced floor is 90 and the
half-point margin is a landing target the operator chose, so continuing to chase it is their call
— and `src/hmc_mcp/` stays off-limits either way.

`src/hmc_mcp/cli_systems.py` has 43 missed statements of 141 (70%), all in render paths. Every one
of them is reached through `_client()` / `_with_client`, so the `fake_hmc` fixture does cover the
*client* seam — but that does not make `hmc_mcp.cli_systems` the patch target. Three of these five
commands import their operation **inside** the command body, exactly as Task 1's shape B, and one
does not go through an operation at all:

| Command | Import site | Patch target |
|---|---|---|
| `health` | module top (`cli_systems.py:22`) | `hmc_mcp.cli_systems.fleet_health` |
| `list --state` | none — calls a **client method** | no operation exists; extend `FakeHMC` (below) |
| `summary` | inside the body (`cli_systems.py:189`) | `hmc_mcp.operations_composite.system_summary` |
| `capacity` | inside the body (`cli_systems.py:223`) | `hmc_mcp.operations_capacity.capacity_report` |
| `find-placement` | inside the body (`cli_systems.py:273`) | `hmc_mcp.operations_capacity.find_placement` |

`monkeypatch.setattr("hmc_mcp.cli_systems.capacity_report", ...)` raises
`AttributeError: module 'hmc_mcp.cli_systems' has no attribute 'capacity_report'` — monkeypatch
raises on a missing attribute by default, so this fails loudly rather than silently, but it fails.

Two further traps:

- **`health` must be faked with a dataclass.** `cli_systems.py:37` is `asdict(_run(_go))`, so the
  fake must return a `FleetHealthResult` (`operations_health.py:26-34`; fields `systems`, `vios`,
  `lpars`, `failed_jobs`, `warnings`) — construct the real one, do not hand back a dict.
- **`list --state` has no operation to patch.** `cli_systems.py:67-69` is
  `_with_client(lambda hmc: hmc.search_uom("ManagedSystem", "State", state))`, and `FakeHMC`
  defines no `search_uom`. Add one to `FakeHMC` returning `[self.system]`; this is the single
  place in Tasks 1–2 where extending `FakeHMC` is the right move rather than patching.

1. Add tests covering:

   | Branch | Lines | Stream |
   |---|---|---|
   | `health` empty estate — `No fleet health exceptions found` | 41–42 | stdout |
   | `health` non-empty category table | 43–53 | stdout |
   | `health` warnings loop | 54–55 | **stderr** |
   | `list --state` server-side search | 67–69 | stdout |
   | `summary` table render | 200–215 | stdout |
   | `capacity` table render | 236–263 | stdout |
   | `capacity` empty report — `No managed systems found` | 233–235 | **stderr** |
   | `find-placement` table render | 286–297 | stdout |
   | `find-placement` empty — `No systems with sufficient free capacity` | 283–285 | **stderr** |

   The two `health` rows are separate tests with different fixture data, not one: line 42 is
   guarded by `if not any(result.values())`, so it fires only when every category is empty, which
   is mutually exclusive with the table build at 47–53. The warnings loop needs a non-empty
   `warnings` tuple and is a third data shape.

2. **Task 1's "Assert on the right stream" rule applies here unchanged.** The stream column above
   is not decoration: `cli_systems.py:55`, `:234` and `:284` write through `err_console`, so those
   assertions go on `result.stderr`. Each test invokes through `RUNNER.invoke(cli.app, [...])`,
   asserts exit code 0, and asserts on a rendered value unique to the branch:

   ```python
   def test_systems_capacity_renders_a_table(fake_hmc, monkeypatch):
       async def fake_report(_hmc):
           # snake_case keys, not the column titles: cli_systems.py:252-261 reads
           # r.get("system_name"), r.get("free_memory_mb"), and so on. A row keyed
           # by the displayed headings renders "-" and "0" in every cell, so the
           # assertion below would still pass while proving nothing about the data.
           return [{"system_name": "sys1", "system_uuid": SYSTEM_UUID,
                    "total_memory_mb": 8192, "assigned_memory_mb": 4096,
                    "free_memory_mb": 4096, "total_proc_units": 4.0,
                    "assigned_proc_units": 1.5, "free_proc_units": 2.5,
                    "running_lpars": 2, "total_lpars": 3}]

       monkeypatch.setattr("hmc_mcp.operations_capacity.capacity_report", fake_report)

       result = RUNNER.invoke(cli.app, ["systems", "capacity"])

       assert result.exit_code == 0
       assert "System Capacity" in result.stdout


   def test_systems_capacity_reports_an_empty_estate(fake_hmc, monkeypatch):
       async def fake_report(_hmc):
           return []

       monkeypatch.setattr("hmc_mcp.operations_capacity.capacity_report", fake_report)

       result = RUNNER.invoke(cli.app, ["systems", "capacity"])

       assert result.exit_code == 0
       assert "No managed systems found" in result.stderr
   ```

   Match the fake's return shape to what the render path reads; where the rendered column set is
   derived from the data (as in `health`), a single representative entry is enough.

   **Assert on the table title and a short cell value, never a UUID.** Under `CliRunner` stdout is
   not a terminal, so rich renders at its 80-column default; `capacity` puts ten columns in that
   width, and a 36-character UUID is truncated with an ellipsis. `"System Capacity"` and `"sys1"`
   survive; `SYSTEM_UUID` does not.

3. Run `uv run --no-sync pytest -q --no-cov tests/app/test_cli_commands.py`. Expect all pass.

4. Measure again as in Task 1 step 6. Expect at or above 90.50%.

5. Run `just verify`. Expect green.

6. Commit: `test: cover the cli_systems render paths (#240)`.

**Acceptance criteria:** package total at or above 90.50% on CPython 3.11; `just verify` green;
no file under `src/hmc_mcp/` modified.

## Task 3: Make the coverage comparison exact and lock it

**Files:** Modify `pyproject.toml` and `tests/test_ci_pipeline.py`.

**Interfaces:** Consumes `ROOT`, `json`, `os`, `subprocess`, `sys`, `tomllib`, and `Path`, all
already imported at the top of `tests/test_ci_pipeline.py`. Produces four module-level helpers,
five test functions, and the final gate configuration. Nothing
later consumes them.

This task is genuine TDD: the behavioral test fails against the current configuration precisely
because the gate is broken, and passes once it is fixed.

1. Add all five tests to `tests/test_ci_pipeline.py`. Complete code:

   ```python
   GATE_STATEMENTS = 1000


   def _project_toml() -> dict:
       with (ROOT / "pyproject.toml").open("rb") as file:
           return tomllib.load(file)


   def _coverage_gate() -> tuple[float, dict]:
       """Return the configured floor and the whole [tool.coverage.report] table.

       float, not int: int(90.9) truncates to 90, which would let the assertions
       below pass while the real floor -- and the diagnostic the child emits --
       had moved.
       """
       report = _project_toml()["tool"]["coverage"]["report"]
       return float(report["fail_under"]), report


   def _write_gate_project(root: Path, report: dict, covered: int) -> None:
       """Build a package of exactly GATE_STATEMENTS statements, `covered` executed."""
       total = GATE_STATEMENTS
       assert 0 < covered < total
       package = root / "gatepkg"
       package.mkdir()
       package.joinpath("covered.py").write_text(
           "".join(f"v{i} = {i}\n" for i in range(1, covered - 1))
       )
       package.joinpath("uncovered.py").write_text(
           "def never_called():\n"
           + "".join(f"    w{i} = {i}\n" for i in range(1, total - covered + 1))
       )
       package.joinpath("__init__.py").write_text("from . import covered, uncovered\n")
       tests = root / "tests"
       tests.mkdir()
       tests.joinpath("test_gate.py").write_text(
           "def test_touch():\n    import gatepkg.covered\n    assert gatepkg.covered.v1 == 1\n"
       )
       # json.dumps, not repr: repr(True) is "True", which is not valid TOML.
       report_toml = "\n".join(
           f"{key} = {json.dumps(value)}" for key, value in sorted(report.items())
       )
       root.joinpath("pyproject.toml").write_text(
           "[project]\n"
           'name = "gatepkg"\n'
           'version = "0.0.0"\n'
           "\n"
           "[tool.pytest.ini_options]\n"
           'pythonpath = ["."]\n'
           'addopts = "--cov=gatepkg --cov-report=term"\n'
           "\n"
           "[tool.coverage.report]\n" + report_toml + "\n"
       )


   def _run_gate_project(root: Path) -> "subprocess.CompletedProcess[str]":
       # An exported PYTEST_ADDOPTS=--no-cov (or a COVERAGE_RCFILE left over from
       # debugging) would otherwise decide the child's coverage instead of the
       # generated project, reddening this test for an unrelated reason.
       environment = {
           key: value
           for key, value in os.environ.items()
           if key not in {"PYTEST_ADDOPTS", "COVERAGE_RCFILE", "COVERAGE_FILE"}
       }
       return subprocess.run(
           [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
           cwd=root,
           capture_output=True,
           check=False,
           env=environment,
           text=True,
           timeout=180,
       )


   def test_coverage_gate_declares_one_exact_floor() -> None:
       floor, report = _coverage_gate()
       project = _project_toml()
       addopts = project["tool"]["pytest"]["ini_options"]["addopts"]

       assert floor == 90
       assert report["precision"] >= 2
       # Without a measured source nothing consults fail_under at all. Token, not
       # substring: "--cov=hmc_mcp/config.py" contains "--cov=hmc_mcp" and would
       # narrow the measured source to one file, giving a total near 100%.
       assert "--cov=hmc_mcp" in addopts.split()
       # Each of these silently disarms the gate: a command-line floor or precision
       # overrides the configured one, --no-cov switches measurement off, and
       # --cov-config sends coverage.py to a different file entirely.
       for flag in ("--cov-fail-under", "--no-cov", "--cov-precision", "--cov-config"):
           assert flag not in addopts, flag
       # An omit/exclude key shrinks the denominator instead: adding
       # omit = ["*/uncovered.py"] to the probe package reports 100.00% and exits 0.
       assert set(report) == {"fail_under", "precision"}
       run_config = project["tool"]["coverage"].get("run", {})
       for key in run_config:
           assert key not in {"omit", "include"} and not key.startswith("exclude"), key


   def test_coverage_gate_is_not_defeated_at_the_invocation_sites() -> None:
       """The floor can be overridden from any pytest invocation, not just addopts.

       Scans every workflow and the whole justfile rather than one recipe, so a new
       recipe or a new workflow that runs pytest is covered too.

       --no-cov is held to a weaker rule than the other three, because it is the one
       flag with a legitimate use here: the addopts comment and this repository's
       own development guardrail both direct `pytest --no-cov <paths>` for a focused
       subset. Forbidding it outright would redden this test the first time someone
       adds a `just test-fast` recipe -- a false alarm whose natural remedy is
       deleting the assertion. So it is rejected only where no test path accompanies
       it, which is the package-wide run the gate exists to protect. That is a
       heuristic on the word "tests"; the other three flags need none, since no
       invocation in this repository has a reason to carry them.
       """
       sources = {"justfile": (ROOT / "justfile").read_text()}
       workflows = ROOT / ".github" / "workflows"
       for pattern in ("*.yml", "*.yaml"):
           for path in sorted(workflows.glob(pattern)):
               sources[path.name] = path.read_text()

       assert len(sources) >= 2
       for name, text in sources.items():
           for flag in ("--cov-fail-under", "--cov-precision", "--cov-config"):
               assert flag not in text, f"{name}: {flag}"
           for number, line in enumerate(text.splitlines(), start=1):
               if "--no-cov" in line:
                   assert "tests" in line, f"{name}:{number}: --no-cov on a package-wide run"


   def test_pyproject_is_the_coverage_configuration_source() -> None:
       """Guard which file coverage.py reads, not just what pyproject.toml says.

       coverage.py tries .coveragerc, .coveragerc.toml, setup.cfg, tox.ini,
       pyproject.toml in order and stops at the first that reads; for the two
       .coveragerc forms merely existing is enough. An empty .coveragerc at the
       root therefore disarms the gate with pyproject.toml byte-identical, and
       with no FAIL banner either, because fail_under falls back to 0.
       """
       for name in (".coveragerc", ".coveragerc.toml"):
           assert not (ROOT / name).exists(), name
       for name in ("setup.cfg", "tox.ini"):
           candidate = ROOT / name
           if candidate.exists():
               assert "[coverage:" not in candidate.read_text(), name


   def test_coverage_gate_fails_a_total_that_rounds_up_to_the_floor(tmp_path: Path) -> None:
       """A total just under the floor must fail, not round up into passing.

       Built from the configured floor rather than fixed constants: with 1000
       statements and 10 * floor - 1 covered, the true total is floor - 0.1 percent,
       which rounds to the floor at coverage.py's default precision of 0.
       """
       floor, report = _coverage_gate()
       _write_gate_project(tmp_path, report, covered=round(10 * floor) - 1)

       result = _run_gate_project(tmp_path)

       # EXIT_TESTSFAILED exactly. A non-zero check would also pass on a syntax
       # error (1 or 2), on no tests collected (5), or on an unimportable pytest --
       # every way this harness can break -- so it would prove nothing about the gate.
       assert result.returncode == 1, result.stdout + result.stderr
       # Pins the measured total, the floor, and the precision in one string.
       # Formatted from the configured precision, not hardcoded: at precision = 3
       # pytest-cov emits "total of 89.900 is less than fail-under=90.000", and a
       # hardcoded string would redden this test for a change that strengthened the gate.
       digits = report["precision"]
       assert (
           f"Coverage failure: total of {floor - 0.1:.{digits}f} "
           f"is less than fail-under={floor:.{digits}f}" in result.stdout
       ), result.stdout


   def test_coverage_gate_passes_a_total_exactly_on_the_floor(tmp_path: Path) -> None:
       """Control: without it, a permanently broken harness reads as a working gate."""
       floor, report = _coverage_gate()
       _write_gate_project(tmp_path, report, covered=round(10 * floor))

       result = _run_gate_project(tmp_path)

       assert result.returncode == 0, result.stdout + result.stderr
       assert "Coverage failure" not in result.stdout
   ```

   The statement arithmetic: `covered.py` contributes `covered - 2` statements, `uncovered.py`
   contributes `1 + (total - covered)` of which `total - covered` are missed, and `__init__.py`
   contributes 1 — giving `total` statements with `covered` of them executed. Both cases are
   verified against the pinned toolchain: `covered=899` reports `TOTAL 1000 101 89.90%` and exits
   `1`, `covered=900` reports `TOTAL 1000 100 90.00%` and exits `0`.

2. Run `uv run --no-sync pytest -q --no-cov tests/test_ci_pipeline.py -k "coverage_gate or coverage_configuration_source"`. Expect
   exactly **five collected, three red, two green**:
   - red — `test_coverage_gate_declares_one_exact_floor`,
     `test_coverage_gate_fails_a_total_that_rounds_up_to_the_floor`, and
     `test_coverage_gate_passes_a_total_exactly_on_the_floor`, all raising `KeyError` on
     `["tool"]["coverage"]` because no `[tool.coverage.report]` section exists yet;
   - green — `test_coverage_gate_is_not_defeated_at_the_invocation_sites` and
     `test_pyproject_is_the_coverage_configuration_source`, which read only the justfile, the
     workflow, and the filesystem, and are already satisfied.

   Anything other than `3 failed, 2 passed` means the step did not run as designed — stop rather
   than continue. Preserve this result in the forge ledger.

3. Apply the configuration from *Global constraints* to `pyproject.toml`: delete
   `--cov-fail-under=90` from `addopts`, correct the comment's first sentence, and add the
   `[tool.coverage.report]` section with `fail_under = 90` and `precision = 2`.

4. Re-run `uv run --no-sync pytest -q --no-cov tests/test_ci_pipeline.py -k "coverage_gate or coverage_configuration_source"`. Expect
   `5 passed`.

5. Prove each gate test bites, recording every result. Revert each mutation and confirm the tests
   are green again before applying the next:
   - Delete `precision = 2` from `pyproject.toml` → the behavioral test fails.
   - Append `--cov-precision=0` to `addopts` → `..._declares_one_exact_floor` fails.
   - Delete `--cov=hmc_mcp` from `addopts` → `..._declares_one_exact_floor` fails.
   - Add `omit = ["*/cli_storage.py"]` to `[tool.coverage.report]` →
     `..._declares_one_exact_floor` fails on the exact-key-set assertion. (This is the vector
     that otherwise reports 100.00% and exits 0.)
   - Append `--cov-fail-under=0` to the justfile `test` recipe →
     `..._is_not_defeated_at_the_invocation_sites` fails.
   - Append `--no-cov` to the justfile `test` recipe →
     `..._is_not_defeated_at_the_invocation_sites` fails on the line rule. Then instead add a
     recipe body `uv run --no-sync pytest -q --no-cov tests/app` → the test stays **green**.
     Both halves are needed: the first proves the rule bites, the second proves it does not
     redden a legitimate focused-subset recipe. Revert both.
   - Create an empty `.coveragerc` at the repository root →
     `test_pyproject_is_the_coverage_configuration_source` fails. Delete it. (This is the vector
     that disarms the gate with `pyproject.toml` unchanged and prints no banner at all.)

6. Run `just test`. Expect exit 0 and a `Required test coverage of 90.0% reached` line — note the
   trailing `.0`, which is new: the floor now comes from coverage.py's float-typed config rather
   than an int-parsed flag. Expect a total at or above 90.50%. If it exits non-zero, coverage
   regressed below the floor: return to Task 1 or 2 rather than lowering the floor.

7. Prove the gate now aborts the suite. Temporarily set `fail_under = 99` in `pyproject.toml` and
   run `just verify`. A non-zero exit is **not** sufficient evidence on its own: raising the floor
   also makes `test_coverage_gate_declares_one_exact_floor` fail its `floor == 90` assertion, and
   `just verify` aborts on any failing dependency, so both a non-zero exit and an absent `smoke`
   stage are fully explained without the coverage gate ever firing. Require instead:
   - the output contains the literal line
     `ERROR: Coverage failure: total of <n> is less than fail-under=99.00`, which only the gate
     emits; and
   - no `smoke` output appears after it.

   `test_coverage_gate_declares_one_exact_floor` is *expected* red for the duration of this probe
   — that is the probe working, not a defect. Restore `fail_under = 90`, confirm `just verify` is
   green, and record every result. This is the direct evidence for acceptance criteria 1 and 2.

8. Run `just verify` and `UV_NO_SYNC=1 uv run prek run --all-files`. Expect both green.

9. Commit: `build: enforce the package coverage floor exactly (#240)`.

**Acceptance criteria:** `pyproject.toml` matches *Global constraints* exactly; all five gate
tests pass; each of the six mutations in step 5 turns the expected test red and is reverted;
`fail_under = 99` makes `just verify` exit non-zero before `smoke`; `just test` passes at or above
90.50%; `just verify` green.

## Task 4: Confirm the margin on a second interpreter, and let CI confirm it on the rest

**Files:** None — this task only measures.

**Interfaces:** Consumes the finished state of Task 3.

A local run cannot confirm the margin. The eight CI legs vary by interpreter **and** by platform —
`config.py` and `cli_config.py` carry five `sys.platform` branches, worth roughly four statements
or 0.067 points — and a local run measures at most two interpreters on an architecture no leg
uses. The binding measurement is therefore the branch's own CI run; this task is the cheaper
pre-push check that the target is plausibly met, so CI is not the first thing to discover a
shortfall.

1. Run the suite under CPython 3.14:
   `uv run --frozen --python 3.14 --extra app --group dev pytest -q --cov-fail-under=0 2>&1 |
   tail -3`. Record the TOTAL line. Expect a total at or above 90.50%, within roughly 0.05 points
   of the 3.11 figure.

   `--frozen` is required: without it `uv run` may re-resolve and rewrite `uv.lock` for the
   requested interpreter, and that churn would ride into the branch under a task that declares it
   modifies nothing. `just setup` uses `uv sync --locked`, and `just verify` would not catch a
   lock rewritten to match the current `pyproject.toml`.

   The command deliberately omits `--cov=hmc_mcp --cov-report=term`: `addopts` already supplies
   both, and pytest appends command-line arguments to it, so passing them again would duplicate
   the source and the report and measure a differently-configured run than `just test` does. Only
   `--cov-fail-under=0` is added, to read the total without the gate stopping the run.

   **If CPython 3.14 cannot be obtained on this host** (no interpreter installed and no network
   for uv's downloads), record that fact and treat the branch CI run in step 4 as the sole margin
   measurement. Do not block on it — it is a pre-push check, not the binding one.

2. **Restore the project environment before doing anything else:** `uv run --python 3.14` replaces
   `.venv` with a 3.14 environment, which makes every subsequent `--no-sync` recipe run against
   the wrong interpreter. Run `just setup`, then confirm `.venv/bin/python -V` reports 3.11 and
   `just verify` is green. Skipping this step silently invalidates every later guardrail run.

   Then run `git status --short --untracked-files=all` and confirm it reports only the intended
   test and configuration changes — in particular that `uv.lock` is unmodified. This also catches
   any residue left by Task 3 step 5's six-mutation battery.

3. If the 3.14 total is below 90.50%, return to Task 1 or 2 and raise coverage further, subject to
   Task 2's two-round bound. Do not lower the floor.

4. After the pull request opens, read the coverage total from each of the eight CI legs. A leg
   below 90.00% is a red gate and blocks — return to Task 1 or 2. A leg between 90.00% and 90.50%
   passes CI but has eaten the margin, so raise coverage rather than accept it.

   If Task 2's two-round bound is exhausted and a leg still sits between 90.00% and 90.50%, the
   branch is not blocked: the merge gate is `fail_under = 90` and that leg is green. Record the
   shortfall in the pull request and let the operator decide whether to accept the reduced margin.
   The half point is a landing target the operator chose, not an enforced invariant — ADR 0034
   says so, and `src/hmc_mcp/` stays off-limits either way, so there is nothing further to try.

**Acceptance criteria:** the CPython 3.14 total is recorded and at or above 90.50%, or its
unavailability on this host is recorded instead; `.venv` is restored to 3.11; `git status` shows
no unintended change, `uv.lock` included; `just verify` green; every CI leg reports at or above
90.00%, and at or above 90.50% unless the shortfall is recorded and accepted by the operator.

## Self-review against the spec

| Spec requirement | Task |
|---|---|
| `[tool.coverage.report]` carries `fail_under = 90` and `precision = 2` | 3 |
| `--cov-fail-under` removed from `addopts` | 3 |
| Total below the floor fails `just test` | 3 step 7 |
| `just verify` aborts before `smoke` | 3 step 7 |
| No `FAIL` printed on a successful run | 3 step 6 |
| Behavioral test asserting `returncode == 1` and the literal diagnostic | 3 step 1 |
| Control test proving the gate passes exactly on the floor | 3 step 1 |
| Configuration test rejecting all four gate-disabling `addopts` edits | 3 step 1 |
| Exact `[tool.coverage.report]` key set; no `run` omit/include/exclude keys | 3 step 1 |
| Invocation-site test over the whole justfile and every workflow | 3 step 1 |
| Config-source test: no `.coveragerc`/`.coveragerc.toml`, no `[coverage:*]` elsewhere | 3 step 1 |
| Gate subprocess run with `PYTEST_ADDOPTS`/`COVERAGE_*` scrubbed | 3 step 1 |
| Coverage at or above 90.50% on 3.11 | 1, 2 |
| Coverage at or above 90.50% on 3.14 (pre-push check) | 4 |
| Every CI leg at or above 90.00%, and 90.50% unless the operator accepts less | 4 step 4 |
| `cli_storage` as the primary vehicle | 1 |
| No `src/hmc_mcp/` runtime change | 1, 2 constraints |
