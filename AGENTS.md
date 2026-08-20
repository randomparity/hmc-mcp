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
  - **Documentation-only** means every file changed in the PR matches one of:
    `*.md`, `docs/**`, `*.txt`. If any changed file falls outside those patterns
    the PR is not documentation-only and must be merged with `--merge`.
  - To check before merging:
    `gh pr diff <PR> --name-only | grep -Ev '\.(md|txt)$|^docs/'`
    An empty result confirms documentation-only; any output means use `--merge`.

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
3. Verify syntax before staging: `uv run python -c "import hmc_mcp.server"`
   (or the relevant module) must succeed **before** `git add`.

Never call `git add <file>` and `git rebase --continue` in the same step unless
you have already confirmed the file is syntax-clean.

## Worktree venv hygiene

After any commit lands on `main` that changes installed package code, worktrees
created before that commit have a stale `.venv`. Run `uv sync` inside the
worktree before running tests or the smoke script.

**Symptom:** `SyntaxError` at a `.venv/lib/…/site-packages/` path while the
source file is clean. The fix is always `uv sync`, not editing source.

## Pre-existing test failures

When running the guardrail suite and discovering a failing test that predates the
current branch, **fix it in the same PR**. Do not leave it failing and do not
defer it to a follow-up. A failing test is either wrong (the test must be
corrected to match the current contract) or right (a latent bug that must be
fixed). Determine which, apply the minimum targeted fix, and include it in the
commit history for this branch. Documenting a known-broken test as "pre-existing"
and shipping over it is not acceptable.

Common causes worth checking first:

- **Local `.env` file supplying credentials** — `pydantic_settings` reads
  `env_file=".env"` at construction time, independent of `os.environ` patches.
  Tests that need a credential-free `HMCConfig` must pass `_env_file=None` to
  suppress `.env` loading: `HMCConfig(_env_file=None)` or
  `HMCConfig(host="h", user="u", _env_file=None)`. Do not use
  `monkeypatch.delenv` for this — it clears env vars but cannot prevent
  `pydantic_settings` from reading the `.env` file. When `_env_file=None` is
  applied, the `monkeypatch.delenv` calls are redundant and should be removed.
- **Import name drift** — a tool or function renamed in source but still
  referenced by the old name in a test or fixture.

## Guardrail commands

Use the quiet recipes by default so successful checks do not consume agent
context. Failures still replay their complete diagnostics.

```sh
just test          # tests + exact coverage gate; one-line success
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
