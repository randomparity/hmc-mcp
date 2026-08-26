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
  `git bisect`. Use `--merge` (merge commit) for all code, test, config, and
  script changes.
  - **Documentation-only** means **no changed file is asserted by a test or by
    a `static` gate.** Matching `*.md` or `docs/**` is necessary and not
    sufficient — plenty of Markdown here is gate-load-bearing:
    `CHANGELOG.md`'s `### Facade manifest` is asserted against
    `hmc_mcp.api.__all__` by `tests/unit/test_changelog.py`; `docs/tools/` is
    generated and diffed by `just tool-docs-check`;
    `docs/environment-variables.md` is checked field by field by
    `just env-vars`; ADR 0029's inventory block is parsed by
    `tests/unit/test_public_api.py`; and `README.md` is asserted by
    `tests/test_readme_tool_names.py` and `tests/test_readme_layout.py`. A PR
    touching any of those is changing a contract, and its per-commit history
    has to survive.
  - The check below encodes that criterion as a **floor, not a closed set**:
    its second half lists the asserted surfaces known today, and the set grows.
    Save it as a script and run it — the guards call `exit`, so it is not an
    interactive paste.
    ```sh
    files=$(gh pr diff <PR> --name-only) || { echo 'diff query failed' >&2; exit 1; }
    [ -n "$files" ] || { echo 'no files reported' >&2; exit 1; }
    disqualifying=$(
      printf '%s\n' "$files" | grep -Ev '\.md$|^docs/'
      printf '%s\n' "$files" | grep -E '^(CHANGELOG|README)\.md$'
      printf '%s\n' "$files" | grep -E '^docs/(tools|adr|workflow/specs)/'
      printf '%s\n' "$files" | grep -E '^docs/(environment-variables|authorization-audit)\.md$'
    ) || true
    if [ -z "$disqualifying" ]; then
      echo 'documentation-only: --squash permitted'
    else
      echo 'not documentation-only, use --merge:'; echo "$disqualifying"
    fi
    ```
    **The `|| true` is load-bearing.** `var=$(…)` takes the exit status of the
    command substitution, which is the status of its *last* command — a `grep`
    that matched nothing, which is exactly the documentation-only case. Without
    it the script dies at the assignment under `set -e` and prints nothing. And
    printing nothing is also what a failed `gh pr diff` looks like — wrong PR
    number, expired auth — which is why the two guards check its exit status
    first. Treat an unanswered query as "not documentation-only", never as a
    pass.
  - The policy is prose, not a gate: **nothing in the repo enforces it.** PR
    #455 landed as a single-parent merge (`f528e94`) while changing
    `src/hmc_mcp/py.typed`, `.github/workflows/ci.yml`, and five test files.
    Run the check above before every merge rather than trusting that a mistake
    would be caught.

**Other common interactive traps**

| Command | Safe form |
|---------|-----------|
| `git log` | `git --no-pager log --oneline` |
| `git diff` | `git --no-pager diff` |
| `gh pr create` | always supply `--title` and `--body` (or `--body-file`) |
| `gh issue create` | always supply `--title` and `--body` |
| `uv add` / `pip install` | non-interactive by default; fine as-is |

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
which covers the twelve `src/hmc_mcp/cli_*.py` modules, in a way whose cause is
nowhere near the error. A bare `uv sync` also drops `--locked` and can silently
rewrite `uv.lock`.

**`uv add` syncs by default and prunes the same way.** Use
`uv add --no-sync <pkg>` and then `just setup`. `pip install` into this venv is
worse: the next `uv sync --locked` reverts it silently. When `uv sync --locked`
refuses because `uv.lock` has fallen behind `pyproject.toml`, refresh the lock
with `uv lock` — that is the one sync-adjacent command the rule above does not
cover, because it resolves without touching the environment.

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
  HMC_ISO_URL_ALLOWLIST=iso.example.test HMC_PASSWORD=p HMC_PORT=443 \
  HMC_SCHEMA_VERSION=V1_0 HMC_SSH_KEY_FILE=/k HMC_SSH_TIMEOUT=300 \
  HMC_TIMEOUT=60 HMC_USER=u \
    uv run --no-sync pytest tests/ -q
  ```

  `HMC_VERIFY_SSL` and `HMC_AUTHORIZE_POWER_OPERATIONS` are deliberately absent:
  autouse fixtures in `tests/conftest.py` already pin both for every test, so
  exporting them here would prove nothing.

  `HMC_PROFILE` is a fourteenth hazard of a different kind — it is not an
  `HMCConfig` field. `load_profile` reads it from `os.environ` directly to
  choose which profile in `config.toml` to load, so an exported value selects a
  profile that a test's fixture config may not define and the test fails with a
  `ConfigError` rather than a wrong-value assertion. Check for it separately
  when a config test fails in a way the block above does not reproduce.

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
load check. `static` is nine sub-recipes, and running one by name is how you
narrow a `static` failure to its cause instead of re-reading the umbrella
output:

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
`ubuntu-24.04-arm`} × {3.11, 3.12, 3.13, 3.14} — and three further jobs depend
on it: `library-wheel-smoke`, `library-range-floors`, and a `wheel-smoke`
matrix of the same eight legs. This has bitten a change that was locally green
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
