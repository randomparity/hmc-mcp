# Enforced coverage gate implementation plan

**Goal:** Make the package coverage gate fail when the total is below its declared floor, and
raise real coverage above that floor so the gate passes on merit rather than on rounding.

**Architecture:** Two independent changes land in a fixed order. First, characterization tests
raise measured coverage from 89.78% to a margin above 90%, leaving the existing (lenient) gate
green throughout. Second, `pyproject.toml` moves the floor and its precision into
`[tool.coverage.report]`, which makes the comparison exact, and two tests in
`tests/test_ci_pipeline.py` lock that behavior. The order matters: tightening the gate before
raising coverage would leave a commit whose guardrails are red.

**Tech stack:** Python 3.11–3.14, pytest 9.1.1, pytest-cov 7.1.0, coverage 7.15.4, Typer +
`typer.testing.CliRunner`, `just` recipes, `uv`.

## Global constraints

- The declared floor is `90`. It is not lowered. [ADR 0034](../../adr/0034-exact-coverage-gate.md)
  governs this and the configuration shape below.
- The gate's final configuration is exactly:
  ```toml
  [tool.pytest.ini_options]
  pythonpath = ["tests"]
  # Keep the package-wide gate at the rounded full-suite baseline. Run a focused
  # subset with `--no-cov` when package-wide coverage is not meaningful.
  addopts = "--cov=hmc_mcp --cov-report=term-missing"

  [tool.coverage.report]
  fail_under = 90
  precision = 2
  ```
  `--cov-fail-under` must not appear in `addopts`: a command-line floor silently overrides a
  configured one (verified — configured `95` with `--cov-fail-under=50` reports "Required test
  coverage of 50% reached" and exits `0`).
- The `addopts` comment is retained but its first sentence is corrected, since the gate is no
  longer "at the rounded baseline". Replace it with: `# Package-wide coverage gate lives in
  [tool.coverage.report]. Run a focused subset with` / `# --no-cov when package-wide coverage is
  not meaningful.`
- Target coverage is **at least 90.50%** on CPython 3.11, i.e. **no more than 567 missed
  statements** of 5977. Baseline is 611 missed (89.78%).
- No file under `src/hmc_mcp/` is modified. Coverage rises only by exercising shipped code. A
  statement that appears genuinely unreachable is reported in the pull request, not deleted.
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
commands come in three shapes, and each shape has a different injection point. Getting the
injection point wrong produces a test that passes without executing the command body, so the
shape table below is load-bearing.

| Shape | Commands | Patch target |
|---|---|---|
| A — `_with_client(lambda hmc: op(...))`, operation imported at module top | `list-vgs`, `delete-disk`, `create-media-repo`, `create-media`, `delete-media-repo`, `delete-media`, `get-media-repo`, `list-optical-media` | `hmc_mcp.cli_storage.<operation>` |
| B — `_run(_go)` with `load_profile()` + `HMCClient(config)`, operation imported **inside** the function | `list-mappings`, `detach-mapping` | `hmc_mcp.cli_storage.load_profile`, `hmc_mcp.cli_storage.HMCClient`, and `hmc_mcp.operations_storage.<operation>` |
| C — `_run(_go)` with `load_profile()` + `HMCClient(config)`, operation imported at module top | `upload-iso` | `hmc_mcp.cli_storage.load_profile`, `hmc_mcp.cli_storage.HMCClient`, `hmc_mcp.cli_storage.upload_iso` |

Shape B imports its operation inside `_go`, so patching `hmc_mcp.cli_storage.list_storage_mappings`
has no effect — the name does not exist on that module. It must be patched on
`hmc_mcp.operations_storage`.

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
   operation received the arguments the CLI parsed. Complete code for one test of each shape:

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
   | `map` | success path renders "Mapped" and calls `map_storage` | A |
   | `create-media-repo` | confirmed create; declined confirmation aborts | A |
   | `create-media` | confirmed create; declined confirmation aborts | A |
   | `delete-media-repo` | confirmed delete; declined confirmation aborts | A |
   | `delete-media` | confirmed delete; declined confirmation aborts | A |
   | `get-media-repo` | found (renders Name/Size); empty (renders "No media repository found"); `--json` | A |
   | `list-optical-media` | non-empty table; empty ("No optical media found"); `--json` | A |
   | `list-mappings` | table with `VirtualDisk`; table with `PhysicalVolume`; `--json` | B |
   | `detach-mapping` | `--confirm` deletes; operation raising renders "Failed to delete" and exits 1 | B |
   | `upload-iso` | duplicate-existing render; `--json` | C |
   | `attach-disk` | `--json` with an incomplete workflow exits 1 | A |

   Decline a confirmation by passing `input="n\n"` to `RUNNER.invoke`; `typer.Abort` renders
   "Aborted." and exits 1.

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

**Interfaces:** Consumes the same `RUNNER`, `FakeHMC`, and `fake_hmc` fixture as Task 1. Produces
test functions only.

**Trigger:** Run this task only when Task 1 step 6 measured a total **below 90.50%**. If Task 1
reached the target, skip this task and record the measured total as the reason. Do not run it for
extra margin; the target is already a chosen safety factor over the observed variance.

`src/hmc_mcp/cli_systems.py` has 43 missed statements of 141 (70%), all in render paths reached
through `_client()` / `_with_client`, so the existing `fake_hmc` fixture intercepts every one —
no shape table is needed here.

1. Add tests covering: `health` non-empty table render and its warnings loop (lines 42, 47–53);
   `list --state` server-side search branch (line 68); `summary` table render (200–215);
   `capacity` table render and its empty-report branch (233–263); `find-placement` table render
   and its empty branch (283–297).

2. Each test invokes through `RUNNER.invoke(cli.app, [...])`, asserts exit code 0, and asserts on
   a rendered value unique to the branch — for example, for `capacity`:

   ```python
   def test_systems_capacity_renders_a_table(fake_hmc):
       result = RUNNER.invoke(cli.app, ["systems", "capacity"])

       assert result.exit_code == 0
       assert "System Capacity" in result.stdout
   ```

   Where `FakeHMC` does not already return data shaped for the operation under test, patch the
   operation on `hmc_mcp.cli_systems` rather than extending `FakeHMC`, matching Task 1 shape A.

3. Run `uv run --no-sync pytest -q --no-cov tests/app/test_cli_commands.py`. Expect all pass.

4. Measure again as in Task 1 step 6. Expect at or above 90.50%.

5. Run `just verify`. Expect green.

6. Commit: `test: cover the cli_systems render paths (#240)`.

**Acceptance criteria:** package total at or above 90.50% on CPython 3.11; `just verify` green;
no file under `src/hmc_mcp/` modified.

## Task 3: Make the coverage comparison exact and lock it

**Files:** Modify `pyproject.toml` and `tests/test_ci_pipeline.py`.

**Interfaces:** Consumes `ROOT` and the `tomllib` import already present at the top of
`tests/test_ci_pipeline.py`. Produces two test functions and the final gate configuration. Nothing
later consumes them.

This task is genuine TDD: the behavioral test fails against the current configuration precisely
because the gate is broken, and passes once it is fixed.

1. Add both tests to `tests/test_ci_pipeline.py`. Complete code:

   ```python
   GATE_STATEMENTS = 1000


   def _project_toml() -> dict:
       with (ROOT / "pyproject.toml").open("rb") as file:
           return tomllib.load(file)


   def _coverage_gate() -> tuple[int, dict]:
       """Return the configured floor and the whole [tool.coverage.report] table."""
       report = _project_toml()["tool"]["coverage"]["report"]
       return int(report["fail_under"]), report


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
       report_toml = "\n".join(f"{key} = {value!r}" for key, value in sorted(report.items()))
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
       return subprocess.run(
           [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
           cwd=root,
           capture_output=True,
           check=False,
           text=True,
           timeout=180,
       )


   def test_coverage_gate_declares_one_exact_floor() -> None:
       floor, report = _coverage_gate()
       addopts = _project_toml()["tool"]["pytest"]["ini_options"]["addopts"]

       assert floor == 90
       assert report["precision"] >= 2
       # Without a measured source nothing consults fail_under at all.
       assert "--cov=hmc_mcp" in addopts
       # Each of these silently disarms the gate: a command-line floor or precision
       # overrides the configured one, and --no-cov switches measurement off.
       for flag in ("--cov-fail-under", "--no-cov", "--cov-precision"):
           assert flag not in addopts, flag


   def test_coverage_gate_fails_a_total_that_rounds_up_to_the_floor(tmp_path: Path) -> None:
       """A total just under the floor must fail, not round up into passing.

       Built from the configured floor rather than fixed constants: with 1000
       statements and 10 * floor - 1 covered, the true total is floor - 0.1 percent,
       which rounds to the floor at coverage.py's default precision of 0.
       """
       floor, report = _coverage_gate()
       _write_gate_project(tmp_path, report, covered=10 * floor - 1)

       result = _run_gate_project(tmp_path)

       # EXIT_TESTSFAILED exactly. A non-zero check would also pass on a syntax
       # error (1 or 2), on no tests collected (5), or on an unimportable pytest --
       # every way this harness can break -- so it would prove nothing about the gate.
       assert result.returncode == 1, result.stdout + result.stderr
       # Pins the measured total, the floor, and the precision in one string.
       assert (
           f"Coverage failure: total of {floor - 0.1:.2f} "
           f"is less than fail-under={floor:.2f}" in result.stdout
       ), result.stdout


   def test_coverage_gate_passes_a_total_exactly_on_the_floor(tmp_path: Path) -> None:
       """Control: without it, a permanently broken harness reads as a working gate."""
       floor, report = _coverage_gate()
       _write_gate_project(tmp_path, report, covered=10 * floor)

       result = _run_gate_project(tmp_path)

       assert result.returncode == 0, result.stdout + result.stderr
       assert "Coverage failure" not in result.stdout
   ```

   The statement arithmetic: `covered.py` contributes `covered - 2` statements, `uncovered.py`
   contributes `1 + (total - covered)` of which `total - covered` are missed, and `__init__.py`
   contributes 1 — giving `total` statements with `covered` of them executed. Both cases are
   verified against the pinned toolchain: `covered=899` reports `TOTAL 1000 101 89.90%` and exits
   `1`, `covered=900` reports `TOTAL 1000 100 90.00%` and exits `0`.

2. Run `uv run --no-sync pytest -q --no-cov tests/test_ci_pipeline.py -k coverage_gate`. Expect
   **all three to fail** with a `KeyError` on `["tool"]["coverage"]`, because no
   `[tool.coverage.report]` section exists yet. Preserve this red result in the forge ledger.

3. Apply the configuration from *Global constraints* to `pyproject.toml`: delete
   `--cov-fail-under=90` from `addopts`, correct the comment's first sentence, and add the
   `[tool.coverage.report]` section with `fail_under = 90` and `precision = 2`.

4. Re-run `uv run --no-sync pytest -q --no-cov tests/test_ci_pipeline.py -k coverage_gate`. Expect
   all three to pass.

5. Prove each gate test bites, recording every result:
   - Delete `precision = 2` from `pyproject.toml`; confirm
     `test_coverage_gate_fails_a_total_that_rounds_up_to_the_floor` fails. Restore it.
   - Append `--cov-precision=0` to `addopts`; confirm
     `test_coverage_gate_declares_one_exact_floor` fails. Remove it.
   - Delete `--cov=hmc_mcp` from `addopts`; confirm the same test fails. Restore it.
   Each mutation must be reverted and the tests confirmed green again before moving on.

6. Run `just test`. Expect exit 0 and a `Required test coverage of 90.0% reached` line — note the
   trailing `.0`, which is new: the floor now comes from coverage.py's float-typed config rather
   than an int-parsed flag. Expect a total at or above 90.50%. If it exits non-zero, coverage
   regressed below the floor: return to Task 1 or 2 rather than lowering the floor.

7. Prove the gate now aborts the suite: temporarily set `fail_under = 99` in `pyproject.toml`, run
   `just verify`, and confirm it exits non-zero **and** that no `smoke` output appears after the
   coverage failure. Restore `fail_under = 90` and confirm `just verify` is green. Record both
   results — this is the direct evidence for acceptance criteria 1 and 2.

8. Run `just verify` and `UV_NO_SYNC=1 uv run prek run --all-files`. Expect both green.

9. Commit: `build: enforce the package coverage floor exactly (#240)`.

**Acceptance criteria:** `pyproject.toml` matches *Global constraints* exactly; all three gate
tests pass; each of the three mutations in step 5 turns the expected test red and is reverted;
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
   `uv run --python 3.14 --extra app --group dev pytest -q --cov=hmc_mcp --cov-report=term
   --cov-fail-under=0 2>&1 | tail -3`. Record the TOTAL line. Expect a total at or above 90.50%,
   within roughly 0.05 points of the 3.11 figure.

2. **Restore the project environment before doing anything else:** `uv run --python 3.14` replaces
   `.venv` with a 3.14 environment, which makes every subsequent `--no-sync` recipe run against
   the wrong interpreter. Run `just setup`, then confirm `.venv/bin/python -V` reports 3.11 and
   `just verify` is green. Skipping this step silently invalidates every later guardrail run.

3. If the 3.14 total is below 90.50%, return to Task 1 or 2 and raise coverage further. Do not
   lower the floor.

4. After the pull request opens, read the coverage total from each of the eight CI legs. Every leg
   must be at or above 90.50%. A leg below 90.00% is a red gate and blocks; a leg between 90.00%
   and 90.50% passes CI but has eaten the margin, so raise coverage rather than accept it.

**Acceptance criteria:** the CPython 3.14 total is recorded and at or above 90.50%; `.venv` is
restored to 3.11; `just verify` green; every CI leg reports at or above 90.50%.

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
| Coverage at or above 90.50% on 3.11 | 1, 2 |
| Coverage at or above 90.50% on 3.14 (pre-push check) | 4 |
| Every CI leg at or above 90.50% (binding measurement) | 4 step 4 |
| `cli_storage` as the primary vehicle | 1 |
| No `src/hmc_mcp/` runtime change | 1, 2 constraints |
