## Problem

`docs/refs/` (vendored HMC API reference) and `AGENTS.local.md` are gitignored
(`.gitignore:31-32`), single-host, and absent from sibling worktrees under
`../hmc-mcp-worktrees/`, so the corpus reads as *never existed* rather than *look in the main
checkout*. PR #800 shipped a fixture invented without consulting it for exactly this reason.

## Scope

One pure function, `sync_reference_corpus(worktree_root: Path, main_root: Path) -> list[str]`,
in `scripts/link_reference_corpus.py`. Per path in `docs/refs`, `AGENTS.local.md`: skip if it
already exists or is a symlink (idempotent, never clobbers); no-op when `worktree_root ==
main_root`; else relative-symlink to the main checkout's copy if present, else return an
"unavailable" announcement string. `main()` resolves `worktree_root` via `git rev-parse
--show-toplevel` and `main_root` via `git -C "$(git rev-parse --git-common-dir)/.." rev-parse
--show-toplevel` (used at `docs/workflow/plans/2026-08-22-vios-update-contract.md:200`), prints
every message, and always exits 0 — including on a git-call failure — so `just setup` never
fails here. Wired into `justfile`'s `setup` as a third line, after `prek install`.

One `AGENTS.md` section (near "Worktree venv hygiene"): the local-only companion exists, its
absence means the corpus is unavailable, and the citation rule — cite `docs/refs/<path>:<line>`
and report what a search returns, never reproduce its prose into a tracked file.

New script, no caller migration, no obsolete path. Excludes `.gitignore` (already correct),
issues #789/#790/#791, and any `docs/refs/` content or citation-tooling change beyond the one
`AGENTS.md` line.

### Failure model

- Actors: a developer or agent running `just setup` in a linked worktree or the main checkout,
  host may or may not carry the corpus. Invariants: both paths stay gitignored/untracked;
  `just setup` never fails here; an existing real path is never replaced.
- Accepted: a main-side target changed after linking isn't re-synced until re-run (read-only
  material); a later-broken symlink is left in place, not repaired — setup still exits 0 and a
  dead link tells the same "unavailable" story as a fresh host.
- Covered elsewhere: the corpus's own content and citation enforcement beyond the one
  `AGENTS.md` line — out of scope per the issue.

## Success

- Corpus present: both paths are readable from the worktree root after `just setup`, as
  relative symlinks; corpus absent: setup succeeds, creates neither, prints an unavailable
  message for each. Main checkout: creates nothing, prints no reference-corpus message.
- Re-run is idempotent: an existing symlink or real path is left untouched; `git status
  --porcelain` is empty after the step in every case (both targets already gitignored).
- `AGENTS.md` states the companion's existence and the citation rule.

## Validation

- Contract: no-op on main checkout; symlink when present; announce-without-failing when absent;
  skip an existing symlink or real path. Mode: focused-test; cases:
  `test_main_checkout_is_a_no_op`, `test_links_present_targets_from_main_checkout`,
  `test_announces_missing_targets_without_failing`, `test_skips_existing_symlink`,
  `test_skips_existing_real_path`; red: `scripts/link_reference_corpus.py` absent; green:
  `uv run --no-sync pytest tests/scripts/test_link_reference_corpus.py -q --no-cov`.
- Contract: `just setup` wiring. Mode: task-test-not-applicable; reason: a one-line justfile
  addition invoking an already-tested, always-exit-0 script; no executable harness for recipe
  bodies here.
- Contract: `AGENTS.md` documentation line. Mode: task-test-not-applicable; reason: prose with
  no machine-checkable structure or doc-content gate in this repository.
