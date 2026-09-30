# ADR 0181: Gate ruff format over Python files, not Markdown

## Status

Accepted (2026-09-29)

## Context

`ruff format --check .` failed on 246 files on `main`, and no gate ran the
formatter. `static` ran `ruff check .` (lint) only, and no prek hook ran
`ruff format`. A contributor who ran the formatter reformatted unrelated code into
their diff, and a check nobody runs cannot say whether a file conforms (#1122).

Twelve of the 246 were Markdown: ruff 0.16 formats Python code blocks inside
Markdown. Some of those blocks are laid out by hand, such as the aligned comments
in `AGENTS.md`, and ADR bodies are amended rather than rewritten.

## Decision

1. `ruff format` is the project's Python formatter. `[tool.ruff.format]` in
   `pyproject.toml` sets `exclude = ["*.md"]`, so Markdown code blocks are not
   formatted or checked.
2. The tree was reformatted once, in a commit holding only the formatter's
   output. `.git-blame-ignore-revs` lists that commit's full SHA.
3. `just format-check` runs `uv run --no-sync ruff format --check .`. It is a
   `static` member with a matching prek hook of the same id, under the 1:1 rule
   `tests/test_ci_pipeline.py` enforces, and is one of the gates that test pins
   as not to be lost.

## Consequences

- An unformatted Python file fails `just static`, `just verify`, CI's hook step,
  and the local pre-commit hook. The fix is `uv run --no-sync ruff format .`.
- Markdown code blocks stay as written. Nothing checks their formatting.
- A future ruff upgrade that changes formatter output fails `format-check`; the
  upgrade PR carries the reformat as its own commit and adds it to
  `.git-blame-ignore-revs`.
- A `detect-secrets` allowlist pragma must sit on the line that holds the flagged
  value in the formatter's output. One call in `tests/unit/test_config.py` was
  rewritten into that shape before the reformat.

## Alternatives considered

- **Do not gate; record that `ruff format` is not the formatter.** Rejected by the
  maintainer: it leaves the churn hazard in place and gives no conformance signal.
- **Gate Markdown code blocks too.** Rejected by the maintainer: it rewrites
  hand-laid-out blocks and ADR bodies that are amended, not rewritten.
