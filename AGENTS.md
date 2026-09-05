# AGENTS.md

## Non-interactive shell discipline

All shell commands must be non-interactive. A command that opens an editor, a
pager, or a prompt will stall the agent with no recovery path.

**git**

- Always set `GIT_EDITOR=true` when a git command may open an editor:
  ```sh
  GIT_EDITOR=true git rebase --continue
  GIT_EDITOR=true git commit --amend
  GIT_EDITOR=true git merge --continue
  ```
- Prefer `--no-edit` on `commit --amend` and `merge` when the message is unchanged.
- Never run `git rebase --continue` without `GIT_EDITOR=true` — the continue
  step opens the editor to confirm the commit message even when there are no
  remaining conflicts.
- Suppress the pager: `git --no-pager <subcommand>` or set `GIT_PAGER=cat`.
- **Never squash-merge** (`gh pr merge --squash` / `git merge --squash`) unless
  the PR is documentation-only. Squash merges collapse commit history and break
  `git bisect`. For all code, test, config, and script changes, use either
  `--merge` (merge commit) or `--rebase` (rebase merge).
  - **Documentation-only** means **no changed file's content is asserted by a
    test or a `static` gate.** *Content*, specifically: repo-wide scans like
    `just secrets` read every tracked file, and counting those would make the
    exception dead.
  - **Decide it by reading, not by a pattern or a grep.** Both have been tried
    here and both are unsound, because the guards reach their files three
    different ways: by literal path, by basename
    (`tests/test_project_metadata.py` opens
    `ROOT / "docs" / "hmc-cli-cheatsheet.md"`), and by directory —
    `scripts/gen_tool_reference.py` writes `_REPO_ROOT / "docs" / "tools"` and
    `scripts/check_adr_numbering.py` globs `docs/adr/*.md`, so no search for a
    path or a filename can see those at all. Work from this floor instead:
    - **Content-asserted:** `docs/tools/` (diffed by `just tool-docs-check`),
      every page carrying a generation banner (regenerated and diffed by
      `just doc-freshness`), `docs/environment-variables.md` (checked field by
      field by `just env-vars`), `docs/hmc-cli-cheatsheet.md` and
      `docs/authorization-audit.md` (opened by `tests/`), ADR 0029's inventory
      block (parsed by `tests/unit/test_public_api.py`), and `CHANGELOG.md`,
      `CONTRIBUTING.md`, `README.md`, `SECURITY.md`.
    - **Name-asserted only:** `just adr-numbering` checks an ADR's *filename*
      and H1, never its body — so editing an ADR's prose is not caught by that
      gate. Most ADRs are in this class, which is why a prose-only ADR edit is
      one of the few real documentation-only changes here.
    - **Content-unasserted** today: the `docs/plan-*`, `docs/spec-*`,
      `docs/scorecard-*`, `docs/workflow/` and `docs/superpowers/` pages. Test
      modules cite some of those specs in module docstrings, which asserts
      nothing about the file. `just doc-freshness` does read every tracked
      Markdown file's *first line*, looking for a generation banner — so a page
      here can still redden it by opening with something banner-shaped, but its
      body is unchecked.
  - **When the answer is not obvious, use `--merge`.** It costs one merge
    commit. A wrong `--squash` is not reversible once it is on `main`.
  - Two shell mechanics, for whatever check you do write over
    `gh pr diff <PR> --name-only`: `grep -Ev` **exits 1 when it filters every
    line out**, so a naive `… | grep -Ev '…'` reports failure on exactly the
    empty-result case and is fatal under `set -e` or inside an `&&` chain; and a
    failed `gh pr diff` — wrong number, expired auth — prints nothing, which
    looks identical to a clean pass. Check its exit status before reading its
    output, and treat an unanswered query as "not documentation-only".
  - The policy is prose, not a gate: **nothing in the repo enforces it** —
    `rg -ni squash` over `.github/`, `tests/`, `scripts/`, `justfile`, and
    `.pre-commit-config.yaml` returns nothing. The maintainer has intentionally
    retained squash-merge availability and selected this documented human
    pre-merge check as the sufficient control; your own reading before the merge
    is the only control.
  - **A single-parent commit on `main` is not evidence of a squash.** This repo
    has also landed PRs with `--rebase`, which replays each commit onto `main`
    and preserves the per-commit history the policy protects — seven commits
    from PR #455 landed that way as `aec6125^..f528e94`. To tell a squash from a
    rebase, compare the commit's own diff with the PR's: on a squash they are
    equal, on a rebase the commit carries only its own slice. This paragraph is
    forensic — how to read history after the fact. It does not prefer one
    permitted non-squash strategy over the other: both `--merge` and `--rebase`
    preserve the per-commit history this policy protects.

**Other common interactive traps**

| Command | Safe form |
|---------|-----------|
| `git log` | `git --no-pager log --oneline` |
| `git diff` | `git --no-pager diff` |
| `gh pr create` | always supply `--title` and `--body` (or `--body-file`) |
| `gh issue create` | always supply `--title` and `--body` |
| `uv add` / `pip install` | non-interactive, but never bare — see *Worktree venv hygiene* |

## Merge-conflict resolution

**Resolve conflicts with precise, targeted edits — not regex over the whole file.**

A regex that matches `<<<<<<< HEAD … >>>>>>> sha (message)` across the entire
file is brittle: it fails silently when the conflict block spans a Python
f-string boundary (the closing `"""` is inside the conflict region and gets
consumed), leaving a syntax error that only surfaces at import time.

Preferred approach:

1. After a failed rebase, read the exact conflict block with `grep -n` and
   `read_file` (specifying the conflict line range).
2. Write a targeted Python script that replaces the exact literal string
   (conflict markers included) with the resolved text — or use `apply_diff` /
   `search_and_replace` with the conflict markers escaped.
3. Verify syntax before staging:
   `uv run --no-sync python -c "import hmc_mcp.server"` (or the relevant
   module) must succeed **before** `git add`.

Never call `git add <file>` and `git rebase --continue` in the same step unless
you have already confirmed the file is syntax-clean.

## Worktree venv hygiene

**Bootstrap a new worktree with `just setup`, and use nothing else.** It is the
repo's only sync recipe (`justfile`):

```sh
just setup   # uv sync --locked --extra app --link-mode copy; then prek install
```

**Never run a bare `uv sync`.** `pyproject.toml` declares no `[tool.uv]` table
and no `default-extras`, so a bare `uv sync` installs `[project] dependencies`
plus the default `dev` group and then **prunes the `app` extra** — `typer` and
fastmcp's server-extra transitive dependencies (`cyclopts`, `openapi-pydantic`,
`websockets`, `watchfiles`, `shellingham`, and more). `uv sync --dry-run` prints
the exact list for the current lock and writes nothing, so check there rather
than trusting a list in this file. Losing `typer` alone breaks `just typecheck`,
which covers every `src/hmc_mcp/cli_*.py` module, in a way whose cause is
nowhere near the error. A bare `uv sync` also drops `--locked` and can silently
rewrite `uv.lock`.

**`uv add` syncs by default and prunes the same way.** Always pass `--no-sync`,
and then `just setup` — the exact form depends on where the dependency goes,
below. `pip install` into this venv is worse: the next `uv sync --locked`
reverts it silently. When `uv sync --locked` refuses because `uv.lock` has
fallen behind `pyproject.toml`, refresh the lock with `uv lock` — that is the
one sync-adjacent command the rule above does not cover, because it resolves
without touching the environment.

**Where a new dependency goes decides how it must be written**, and
`uv add`'s default `>=` floor is wrong for both cases. `tests/test_supply_chain.py`
enforces this in `just test`, before any CI job runs:

- A **runtime** dependency goes in `[project] dependencies` and needs both a
  floor and a cap: `uv add --no-sync "<pkg>>=x,<y"`, writing the range out
  rather than letting `uv add` pick a bare floor. It must also join
  `LIBRARY_DEPENDENCIES` in
  `tests/test_supply_chain.py` and ADR 0068's policy notes — the exhaustiveness
  test compares the two sets and says so in its own failure message. CI's
  `library-range-floors` job re-checks the range shape, long after and far from
  the `uv add` that caused it.
- A **development tool** goes in the `dev` group and must be **exactly pinned**:
  `uv add --dev --no-sync "<pkg>==<version>"`. The `app` extra and
  `[build-system] requires` are pinned the same way, and a pin that does not
  equal the locked version fails too.

**Never run a bare `uv run` either.** Every `uv run` in the `justfile` passes
`--no-sync`, and `tests/test_ci_pipeline.py` asserts that as an invariant. A
bare `uv run` may re-sync and undo the extras state `just setup` established —
the same breakage as above, arrived at sideways.

Package code in an older worktree is **not** stale: the project is installed
editable (`.venv/lib/python3.11/site-packages/_editable_impl_hmc_mcp.pth`), so
`src/` is what imports. Only dependency and extra state drifts, and only
`just setup` restores it.

**Reading a `SyntaxError`.** Under an editable install, a `hmc_mcp` syntax
error surfaces at a `src/` path — that one is yours to fix in source. A
`SyntaxError` at a `.venv/lib/…/site-packages/` path is third-party code, so it
means the extras or lock state is wrong, or the interpreter does not match what
the environment was built for; `just setup` is the fix for that one.

## Pre-existing test failures

When running the guardrail suite and discovering a failing test that predates the
current branch, **fix it in the same PR**. Do not leave it failing and do not
defer it to a follow-up. A failing test is either wrong (the test must be
corrected to match the current contract) or right (a latent bug that must be
fixed). Determine which, apply the minimum targeted fix, and include it in the
commit history for this branch. Documenting a known-broken test as "pre-existing"
and shipping over it is not acceptable.

Common causes worth checking first:

- **Ambient `HMC_*` variables supplying credentials** — `HMCConfig` is a
  pydantic-settings `BaseSettings` with `env_prefix="HMC_"`, so **every field a
  test leaves unset resolves from the developer's own environment**. A test that
  passes `host=` and asserts on `agent_id` or `ssh_key_file` passes in CI and
  fails on a workstation with those exported.

  Tests that need a credential-free `HMCConfig` should construct it with
  `HMCConfig.from_mapping({...})`, which reads no environment variable and no
  dotenv file and gives every omitted field its declared default (ADR 0096):

  ```python
  HMCConfig.from_mapping({})                      # every field at its default
  HMCConfig.from_mapping({"host": "h", "user": "u"})
  ```

  **`_env_file=None` is not this.** It is a private pydantic-settings parameter
  that suppresses a dotenv source and never touches `os.environ`; and
  `HMCConfig` declares no `env_file` at all, so it currently does nothing
  whatsoever. Existing call sites that pass it are inert. Do not add new ones,
  and do not delete a `monkeypatch.delenv` on the strength of one — the
  `delenv` is what is actually isolating that test. Where a test needs to
  exercise the environment-reading constructor on purpose (`load_profile`'s
  env-over-TOML precedence, the CLI path), keep using
  `monkeypatch.setenv`/`delenv`; `from_mapping` is the wrong tool there,
  because the environment is the behaviour under test.

  The earlier version of this note produced real breakage. Verify a fix with
  the environment a workstation actually has, not an empty one. `HMCConfig`
  declares **thirteen** fields, so thirteen `HMC_*` names can leak in; this
  covers the eleven that no fixture already pins:

  ```sh
  HMC_AGENT_ID=a HMC_AUDIT_MEMENTO=m HMC_HOST=h \
  HMC_ISO_URL_ALLOWLIST=iso.example.test HMC_PASSWORD=p HMC_PORT=12443 \
  HMC_SCHEMA_VERSION=V1_0 HMC_SSH_KEY_FILE=/k HMC_SSH_TIMEOUT=111 \
  HMC_TIMEOUT=17 HMC_USER=u \
    uv run --no-sync pytest tests/ -q
  ```

  Every value differs from the field's declared default, deliberately.
  `HMC_PORT`, `HMC_TIMEOUT` and `HMC_SSH_TIMEOUT` default to 443, 60 and 300; a
  probe set to the default is invisible to a test that asserts the default, so
  it would prove nothing. A leak has to surface as a wrong-value assertion.

  **The failures this probe produces are its output, not pre-existing failures
  to fix under the rule at the top of this section.** It is a diagnostic, and it
  reddens tests on purpose — `tests/conftest.py`'s `make_config()` pins only
  host, user, password and `verify_ssl`, so `HMC_PORT=12443` alone re-points
  every config it builds away from the `https://hmc.test:443` the respx routes
  expect. Read the failures as a list of tests that are not isolated, fix the
  isolation, and re-run. A green `just verify` — with no `HMC_*` exported — is
  what says the branch is shippable.

  `HMC_VERIFY_SSL` and `HMC_AUTHORIZE_POWER_OPERATIONS` are deliberately absent:
  autouse fixtures in `tests/conftest.py` already pin both for every test, so
  exporting them here would prove nothing.

  Two names in play here leak in a second way, and neither shows up as a
  wrong-value assertion:

  - **`HMC_PROFILE` is not an `HMCConfig` field at all.** `load_profile` reads
    it from `os.environ` directly to choose which profile in `config.toml` to
    load, so an exported value selects a profile a test's fixture config may not
    define, and the test fails with a `ConfigError`.
  - **`HMC_HOST` reroutes the authorization path**, on top of being a field.
    `connection_scope.selected_connection` returns `None` — collapsing every
    `connection=` token to the environment connection — whenever `HMC_HOST` is
    set; `connection_denial` adds a clause to the ADR 0038 denial text on the
    same condition; and `build_config` skips its whole TOML/profile branch. So
    an authorization or profile test failing under the probe above is reporting
    a *rerouted decision*, not a leaked value. Do not "fix" an access-policy
    assertion to match it — unset `HMC_HOST` and re-run that test first.

  #461 tracks the tests outside `tests/unit/test_config.py` that still fail
  this way, and the suite-wide fixture that should stop it recurring.
- **Import name drift** — a tool or function renamed in source but still
  referenced by the old name in a test or fixture.

## Guardrail commands

Use the quiet recipes by default so successful checks do not consume agent
context. `just test` buffers pytest's output and replays the whole capture when
the run exits non-zero (`scripts/run_tests.py`), so a failure loses nothing.
`just smoke` does no buffering at all — it is already terse, and there is
nothing held back to replay.

```sh
just test          # tests + exact coverage gate; compact success summary
just smoke         # MCP handshake; tool count only
just verify        # full pre-push guardrail
```

Use verbose recipes only when live progress or expanded diagnostics are needed:

```sh
just test-verbose   # live pytest output + missing-lines coverage
just smoke-verbose  # list every exposed MCP tool
```

Before pushing, run `just verify` inside the branch worktree. If pytest fails
during collection, run `just smoke`; it imports `hmc_mcp.server` directly and
can expose an import-time syntax error that collection obscures.

`just verify` is `static test smoke build verify-artifacts` plus a CLI-group
load check. Running a `static` sub-recipe by name is how you narrow a `static`
failure to its cause instead of re-reading the umbrella output:

```sh
just lint              # ruff check .
just typecheck         # ty check
just secrets           # detect-secrets-hook against .secrets.baseline
just workflow-security # zizmor over .github/workflows/, no online audits
just env-vars          # every HMC_* field in HMCConfig is documented
just nicknames         # the committed config fixture's nicknames table is well-formed
just tool-docs-check   # docs/tools/ still matches the MCP tool registry
just adr-numbering     # every docs/adr/ record's number is unique and matches its H1
just doc-freshness     # every generated document matches its declared command
```

Two of those have a **regenerator, not a hand fix**. When `tool-docs-check`
fails, run `just tool-docs` and commit the result; editing `docs/tools/*.md` by
hand only makes the next run fail again. `doc-freshness` is the same pattern
generalized: it re-runs the recipe each banner-carrying document names and
diffs the output, so the fix is always to run that recipe.

`build` and `verify-artifacts` are the other two `verify` members: `build`
produces a fresh wheel and sdist into `dist/`, and `verify-artifacts` validates
what is already there without rebuilding.

## What a green `just verify` does not cover

**A green local run does not predict a green CI run.** Locally `just verify`
uses the worktree's one `.venv`, built on the `3.11` that `.python-version`
pins. CI's `ci` job is **eight legs** — {amd64 `ubuntu-24.04`, arm64
`ubuntu-24.04-arm`} × {3.11, 3.12, 3.13, 3.14} — and `library-wheel-smoke`,
`library-range-floors`, and a `wheel-smoke` matrix of the same eight legs all
depend on it. This has bitten a change that was locally green
and red on 3.12+, because `inspect` renders `Annotated` differently across
versions. When a change touches signature introspection, generated
documentation, or anything whose output is a rendered type, expect the version
legs to disagree with your machine and read the CI matrix rather than re-running
locally.

**CI runs the hooks after `just verify`, and `just verify` does not.** The last
step of every `ci` leg is `UV_NO_SYNC=1 uv run prek run --all-files`. CI also
invokes `just tool-docs-check` and `just doc-freshness` as named steps ahead of
`just verify`, so a stale generated document is reported as its own failed check
rather than as a line inside the umbrella. To cover the hook step before
pushing, run `uv run --no-sync prek run --all-files` yourself. Run it that way
and not as a bare `prek`: the dev group pins a `prek` version, and a globally
installed one on `PATH` is a different binary — which is the same
green-here-red-there hazard this section is about. `just setup` has installed
the git hook script, not a `prek` on `PATH`.

## Repository conventions

**A new `static` sub-recipe needs a matching prek hook.** `tests/test_ci_pipeline.py`
asserts a 1:1 correspondence between `static`'s dependency list and the hook ids
in `.pre-commit-config.yaml`: the sets must be equal, each hook's `id` must equal
the recipe its `entry: just <recipe>` names, and each block must carry
`pass_filenames: false` and `language: system` and must not narrow with `stages`,
`exclude`, `files`, `types`, `types_or`, or `always_run`. Add a gate to `static`
without its hook and a test in a file about CI shape goes red for a reason that
looks unrelated.

**Adding a name to `hmc_mcp.api.__all__` is a four-part duty**, and none of it is
automatic:

1. the export itself in `__all__`;
2. the module inventory in
   `docs/adr/0029-supported-reusable-python-api-contract.md`, between its
   `<!-- ADR-0029-INVENTORY:BEGIN -->` and `<!-- ADR-0029-INVENTORY:END -->`
   markers — `tests/unit/test_public_api.py` parses that block and compares
   every clause against the facade's own imports;
3. the contract tests, including the transitive type-export closure — an
   exported model's fields, a `TypedDict`'s keys, and an exported error's or
   `HMCClient`'s constructor parameters all pull further package-owned types into
   the supported surface;
4. a **coded** bullet under `[Unreleased]`'s `### Facade manifest` section in
   `CHANGELOG.md`. `tests/unit/test_changelog.py` asserts the manifest against
   `__all__`; a prose mention does not satisfy it. Per ADR 0029 any addition,
   removal, or rename there requires a minor release during `0.x`.
   `CONTRIBUTING.md` carries the full changelog rules.

**ADR conventions.** `just adr-numbering` (`scripts/check_adr_numbering.py`)
enforces that each record's number is unique, that the filename matches
`NNNN-lowercase-kebab-slug.md`, and that the H1 the record opens with announces
the same number as its filename. **Renaming an ADR is therefore a rename plus a
heading edit** — a rename that lands without the heading edit fails the gate.
Number **gaps are deliberately legal** and several exist (0032, 0085, 0095); do
not renumber to close one. There is **no ADR index**: navigation is by filename,
so give a new record a slug that reads as its subject.

**One test module per `scripts/` file**, named `tests/scripts/test_<name>.py`.
Two predate the convention and are exceptions to know about rather than a
pattern to copy: `scripts/check_env_vars.py` is tested by
`tests/test_env_var_guard.py`, and `scripts/live_test_runner.py` by
`tests/test_live_runner.py`. Every other script follows it, and a new script
gets the convention.

**Diff a worktree against the merge base, not against `main`.** Local `main`
advances under merges while a branch is open, so `git diff main` shows other
people's landed work as if it were yours. Use:

```sh
git --no-pager diff "$(git merge-base HEAD origin/main)"
```
