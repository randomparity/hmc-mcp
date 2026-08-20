# Implementation plan — `backup_name` and the `UNBOUNDED_ARGUMENTS` line

**Goal.** Classify `backup_name` under a corrected `UNBOUNDED_ARGUMENTS` rule —
ADR 0039's containment question — enforce the containment the classification
depends on, and pin both with a test, so the guardrail comment and the code cannot
disagree again.

**Architecture.** `src/hmc_mcp/tool_registry.py` holds the tables of public
argument names: `REQUIRED_TARGET_ARGUMENTS` (target selectors) and
`UNBOUNDED_ARGUMENTS` (identities no `targets` allowlist can pin down). Neither is
read at runtime — a tool's `exhaustive_targets` boolean is what the authorizer
reads. `src/hmc_mcp/server_vios.py` holds the VIOS tools; `hmc_restore_vios` builds
an HMC CLI string and runs it over SSH via `_run_vios_backup_command`.
`tests/app/test_tool_security.py` is the guardrail suite that checks declarations
against those tables.

**Tech stack.** Python 3.11, pytest, uv, ruff, ty.

## Global Constraints

- Run guardrails with `just verify` and `uv run prek run --all-files`. Run both
  **bare** — no pipes, no `|| true`. The exit code is the verdict.
- A fresh worktree needs `uv sync --all-extras` before any test run. Plain
  `uv sync` omits the `cli` extra and `ty` then fails on `import typer`, which
  looks like a source defect and is not one.
- Line length 100. Zero warnings from any tool.
- Coverage gate: 90%. The branch baseline is 92.59%.
- Conventional commits, imperative mood, subject ≤72 characters, ending with the
  trailer `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- Never write `backup_name` into `UNBOUNDED_ARGUMENTS`. `tests/app/test_tool_security.py:1453`
  asserts that set disjoint from `_PAYLOAD_SOURCE_ARGUMENTS`, and ADR 0044 turns
  on `backup_name` being in neither.
- The refusal is the narrow one and must not grow into IBM's 1–40-character
  grammar. ADR 0044 rejects that explicitly; a stricter check can refuse a
  legitimate catalog entry.

## Task 1 — refuse a `backup_name` that could leave the catalog

**Creates:** nothing.
**Modifies:** `src/hmc_mcp/server_vios.py`, `tests/vios/test_vios_backup.py`,
`tests/unit/test_ssh_quoting.py`.
**Tests:** `tests/vios/test_vios_backup.py`, `tests/unit/test_ssh_quoting.py`.

**Interfaces.** Consumes nothing new. Defines nothing later tasks import; Task 2
asserts the *behaviour* this task adds (that `hmc_restore_vios` raises
`ValueError` on the refused shapes) by calling
`hmc_mcp.server_vios.hmc_restore_vios` directly. That name is the unwrapped
handler: the `@tool` decorator returns the function unchanged and the
authorization wrapper is applied at registration, not to the module global.

**Where this fits.** ADR 0044 classifies `backup_name` as bounded by containment.
This task is what makes that containment a property of this code rather than an
assumption about `chviosbackup`.

### Steps

1. Add the failing tests first. In `tests/vios/test_vios_backup.py`, after
   `test_restore_vios_returns_cli_output`, add:

   ```python
   @pytest.mark.parametrize(
       "backup_name",
       ["", "   ", "../other/x.tar", "/data/viosbackup/x.tar", "a\\b.tar", ".", ".."],
   )
   def test_restore_vios_refuses_a_name_that_could_leave_the_catalog(
       monkeypatch, backup_name
   ):
       """A backup_name is a name in the declared VIOS's catalog, not a path.

       ADR 0044 classifies backup_name as bounded because `-id` selects the
       catalog the name resolves in. That holds only while the name cannot
       address anything outside it, so the refusal is what the classification
       rests on rather than an assumption about what chviosbackup does.
       """
       _hmc_env(monkeypatch)
       with pytest.raises(ValueError, match="backup_name"):
           hmc_restore_vios(VIOS_UUID, backup_name)
   ```

2. Run it and **confirm it fails**:

   ```
   cd "/Volumes/Source Code Volume/src/hmc-mcp-worktrees/feat/backup-name-bounding-264"
   uv run --no-sync pytest tests/vios/test_vios_backup.py -q -k leave_the_catalog
   ```

   Expect: `7 failed` — each case reaching the SSH layer instead of raising.

3. In `src/hmc_mcp/server_vios.py`, add the refusal at the top of
   `hmc_restore_vios`'s body, before the `return _run(...)`:

   ```python
       if not backup_name.strip():
           raise ValueError("backup_name must not be empty")
       if "/" in backup_name or "\\" in backup_name or backup_name.strip(".") == "":
           raise ValueError(
               f"backup_name {backup_name!r} must be a backup name, not a path: it is "
               "resolved inside the declared VIOS's own backup catalog. Use a name "
               "from hmc_list_vios_backups."
           )
   ```

   `backup_name.strip(".") == ""` is what refuses `.` and `..` — and any
   all-dots value — without a separate membership test.

4. Run the new test and confirm it passes:

   ```
   uv run --no-sync pytest tests/vios/test_vios_backup.py -q -k leave_the_catalog
   ```

   Expect: `7 passed`.

5. Add the refusal to the docstring so the tool's contract states it. In
   `hmc_restore_vios`, change the `backup_name` line of the `Args:` block to:

   ```
           backup_name: Backup file name returned by hmc_list_vios_backups. A name
               within this VIOS's own catalog, not a path: a value containing a
               path separator, or consisting only of dots, is refused.
   ```

6. Move the hostile value in the quoting test, which the refusal now rejects. In
   `tests/unit/test_ssh_quoting.py`, replace both occurrences of
   `"/backups/vios;id"` in `test_restore_vios_quotes_hostile_backup_name` with
   `"vios;id"`, leaving the rest of the test unchanged:

   ```python
   def test_restore_vios_quotes_hostile_backup_name(monkeypatch):
       """hmc_restore_vios shell-quotes a hostile backup_name (no REST resolution)."""
       _hmc_env(monkeypatch)
       conn = _make_ssh_mock("")

       with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
           hmc_restore_vios(SYSTEM_UUID, "vios;id")

       cmd = _captured_cmd(conn)
       assert shlex.quote("vios;id") in cmd
   ```

   The value keeps a shell metacharacter, so the quoting property is still what
   is proven; it simply no longer also carries a separator, which is a different
   control.

7. Confirm the untouched restore tests still pass — the check that no legitimate
   name became unrestorable:

   ```
   uv run --no-sync pytest tests/vios/test_vios_backup.py tests/unit/test_ssh_quoting.py -q
   ```

   Expect: all pass, including `test_restore_vios_runs_correct_command`
   (`vios1_backup_001`) and `test_restore_vios_returns_cli_output` (`mybackup`)
   unmodified.

8. Run the guardrails bare and commit:

   ```
   just verify
   uv run prek run --all-files
   git add src/hmc_mcp/server_vios.py tests/vios/test_vios_backup.py tests/unit/test_ssh_quoting.py
   git commit
   ```

   Subject: `fix(vios): refuse a backup_name that could leave the catalog`.

**Acceptance criteria.** Each of the seven shapes raises `ValueError` naming
`backup_name`; `vios1_backup_001` and `mybackup` still reach the command
unchanged; the quoting test still proves `shlex.quote` is applied;
`just verify` exits 0.

## Task 2 — pin the classification to the guard

**Creates:** nothing.
**Modifies:** `tests/app/test_tool_security.py`.
**Tests:** `tests/app/test_tool_security.py`.

**Interfaces.** Consumes `TOOL_SECURITY` and `UNBOUNDED_ARGUMENTS`, both already
imported at the top of the file, and the refusal behaviour Task 1 added to
`hmc_mcp.server_vios.hmc_restore_vios`. `server_vios` is already reachable in
this module through `_TOOL_MODULES`; import it explicitly by name for the direct
call.

**Where this fits.** This is the pin #264 asks for. The classification is held by
omission — `tool()` defaults `exhaustive_targets` to `True` — so without this
test nothing at the tool shows a classification was made or fails when it
changes.

### Steps

1. Add the test at the end of `tests/app/test_tool_security.py`:

   ```python
   @pytest.mark.parametrize(
       "escape",
       ["", "   ", "../other/x.tar", "/data/viosbackup/x.tar", "a\\b.tar", ".", ".."],
   )
   def test_backup_name_is_bounded_only_because_the_catalog_guard_holds(escape):
       """ADR 0044: the classification and the guard stand or fall together.

       `hmc_restore_vios` keeps `exhaustive_targets=True` — by omission, since
       `tool()` defaults it — because `-file` names an entry in the catalog `-id`
       selects. That is true only while a `backup_name` cannot address anything
       outside that catalog, so asserting the classification without asserting the
       guard would pin a conclusion to nothing. Deleting the guard fails this test,
       which forces the classification to be argued again rather than drifting.
       """
       assert TOOL_SECURITY["hmc_restore_vios"].exhaustive_targets
       assert "backup_name" not in UNBOUNDED_ARGUMENTS
       with pytest.raises(ValueError, match="backup_name"):
           server_vios.hmc_restore_vios("2FC1D9D9-9D9D-4D9D-8D9D-9D9D9D9D9D9D", escape)
   ```

2. Add `server_vios` to the module imports at the top of the file if it is not
   already imported by name. Check first:

   ```
   cd "/Volumes/Source Code Volume/src/hmc-mcp-worktrees/feat/backup-name-bounding-264"
   grep -n "server_vios" tests/app/test_tool_security.py | head
   ```

   If only `_TOOL_MODULES` mentions it, add `server_vios` to the existing
   `from hmc_mcp import (...)` import list in alphabetical position.

3. Run the new test and confirm it passes:

   ```
   uv run --no-sync pytest tests/app/test_tool_security.py -q -k catalog_guard
   ```

   Expect: `7 passed`.

4. Prove the pin bites, in both directions, one at a time.

   First, remove the guard: comment out the two `raise ValueError` blocks added in
   Task 1 step 3 in `src/hmc_mcp/server_vios.py`, then run:

   ```
   uv run --no-sync pytest tests/app/test_tool_security.py -q -k catalog_guard
   ```

   Expect: `7 failed` — `DID NOT RAISE`. Revert with
   `git checkout -- src/hmc_mcp/server_vios.py` and re-run; expect `7 passed`.

   Second, flip the declaration: change the `hmc_restore_vios` decorator in
   `src/hmc_mcp/server_vios.py` to
   `@tool(effect="destructive", operation="vios.restore", target_kind="vios", exhaustive_targets=False)`
   and run the same command. Expect `7 failed` on the first assertion. Revert with
   `git checkout -- src/hmc_mcp/server_vios.py` and re-run; expect `7 passed`.

   Record in the commit message that both directions were watched failing.

5. Run the guardrails bare and commit:

   ```
   just verify
   uv run prek run --all-files
   git add tests/app/test_tool_security.py
   git commit
   ```

   Subject: `test(security): pin backup_name's classification to its guard`.

**Acceptance criteria.** The test asserts the declaration, the absence from
`UNBOUNDED_ARGUMENTS`, and the refusal together; removing the guard reddens it;
flipping the declaration reddens it; `just verify` exits 0.

## Task 3 — state the rule the classification is decided under

**Creates:** nothing.
**Modifies:** `tests/app/test_tool_security.py`, `src/hmc_mcp/tool_registry.py`.
**Tests:** the whole of `tests/app/test_tool_security.py` — comment-only changes
must move no assertion.

**Interfaces.** Consumes nothing, defines nothing. Both edits are comments; no
identifier changes.

**Where this fits.** Tasks 1 and 2 made the classification true and checkable.
This makes it readable, which is the half #264 is actually about.

### Steps

1. In `tests/app/test_tool_security.py`, replace the paragraph currently at lines
   1390-1394 — beginning "The line against UNBOUNDED_ARGUMENTS is *which side* the
   named thing lives on." — with:

   ```python
   # The line against UNBOUNDED_ARGUMENTS is not which side of the HMC the named
   # thing lives on: it is ADR 0039's containment question — can the identity
   # address a resource, read or written, that the declared selectors do not
   # contain? `file_path` fails it because `bkprofdata -f` and `rstprofdata -f`
   # take an absolute path anywhere on the console filesystem and the declared
   # `-m` system does not constrain it, which is why the read-only
   # `hmc_restore_lpar_profiles` is unbounded too and why "the file is written" is
   # not the rule. `backup_name` passes it: `-id` selects the catalog the name
   # resolves in, and `hmc_restore_vios` refuses any value that could leave it.
   # ADR 0044 records that, and the two questions it leaves open (#282, #283).
   #
   # Every name below is a remote host or a source outside the HMC, which no
   # `targets` table could reach under any design, so refusing the tool would buy
   # nothing.
   ```

2. Run the suite and confirm nothing changed behaviourally:

   ```
   cd "/Volumes/Source Code Volume/src/hmc-mcp-worktrees/feat/backup-name-bounding-264"
   uv run --no-sync pytest tests/app/test_tool_security.py -q
   ```

   Expect: the same passed count as after Task 2, no failures.

3. In `src/hmc_mcp/tool_registry.py`, extend the `UNBOUNDED_ARGUMENTS` comment.
   Immediately after the `job_href` bullet (ending "...so the value authorized and
   the value fetched are different values.") and before the paragraph beginning
   "This is not the complement of REQUIRED_TARGET_ARGUMENTS", insert:

   ```python
   #
   # Membership is ADR 0039's containment question, not which filesystem the value
   # refers to and not whether it is written: can the identity address a resource
   # the declared selectors do not contain? A name that resolves inside a
   # container a selector names is bounded — `backup_name` on `hmc_restore_vios`
   # is the HMC-side case that made that explicit, and ADR 0044 records why it is
   # absent here and what the tool does to keep it so.
   ```

4. Run the full guardrail suite bare:

   ```
   just verify
   uv run prek run --all-files
   ```

   Expect: both exit 0, `2070` passed or more, coverage at or above 92.59%.

5. Commit:

   ```
   git add tests/app/test_tool_security.py src/hmc_mcp/tool_registry.py
   git commit
   ```

   Subject: `docs(security): state the containment rule membership uses`.

**Acceptance criteria.** Neither comment claims anything about `chviosbackup`'s
HMC-side behaviour beyond `-id` scoping the operation. The guardrail comment,
`UNBOUNDED_ARGUMENTS`, and `hmc_restore_vios` agree about `backup_name`.
`UNBOUNDED_ARGUMENTS` is unchanged as a value. Both guardrail commands exit 0.

## Self-review against the spec

- **R1** (rule text states containment) — Task 3, steps 1 and 3.
- **R2** (classification agrees with the rule) — Task 2, step 1, asserting the
  declaration and the absence from `UNBOUNDED_ARGUMENTS` together.
- **R3** (containment enforced, not assumed) — Task 1, step 3.
- **R4** (a test pins classification and guard together) — Task 2, steps 1 and 4,
  both failure directions watched.
- **R5** (no legitimate entry refused) — Task 1, steps 6 and 7: the refusal covers
  only separator- and dot-shaped values, and the existing restore tests using
  ordinary names are left unmodified and must still pass.

Every identifier used is either already in the file being edited or added by the
step that uses it; no task references another by number.
