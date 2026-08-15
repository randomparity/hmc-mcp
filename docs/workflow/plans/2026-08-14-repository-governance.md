# Repository governance implementation plan

**Goal:** Establish tested MIT, contribution, and private vulnerability-reporting governance.  
**Architecture:** Canonical policy files own their subjects. README and PEP 621 metadata link to
those files, while one focused test enforces agreement without a new dependency.  
**Tech stack:** Markdown, TOML/PEP 621, Python 3.11+ `tomllib`, pytest.  
**Branch:** `feat/mit-governance-158`; **base:** `main`.

## Global constraints

- `LICENSE` uses the MIT License and `Copyright (c) 2026 hmc-mcp contributors`.
- The only vulnerability channel is GitHub private vulnerability reporting at
  `https://github.com/randomparity/hmc-mcp/security/advisories/new`; public issues are rejected.
- Metadata uses `license = "MIT"`, `license-files = ["LICENSE"]`, the MIT classifier, and direct
  Repository, Contributing, and Security URLs from the specification.
- Do not add dependencies, publication credentials, PyPI publication, release workflows, response
  SLAs, or supported-version promises.
- Host arm64 is included in declared amd64 and arm64 targets; the change is architecture-independent.
- Required guardrails are `just verify` and `uv run prek run --all-files`.

## File map

- Create `LICENSE`: canonical MIT text.
- Create `CONTRIBUTING.md`: focused contribution path and security redirect.
- Create `SECURITY.md`: private reporting policy.
- Modify `README.md`: governance navigation only.
- Modify `pyproject.toml`: PEP 621 license, classifier, and project URLs.
- Create `tests/test_project_metadata.py`: governance and metadata contract tests.

## Task 1: Prove and establish the governance contract

**Interfaces:** Consumes the exact paths, URLs, and license values in Global constraints. Produces
canonical files and metadata that README navigation and future package consumers rely on.

1. Create `tests/test_project_metadata.py` with tests that parse `pyproject.toml`, assert the exact
   license fields, MIT classifier, and three project URLs; assert `LICENSE`, `CONTRIBUTING.md`, and
   `SECURITY.md` exist; assert the README links all three; and assert `SECURITY.md` contains the
   exact advisory URL and says not to use public issues.
2. Run `uv run pytest --no-cov tests/test_project_metadata.py -q`. Expect failures for the absent
   policy files and metadata, proving the tests bite.
3. Add the exact MIT text to `LICENSE`; write the focused contribution and security policies; add
   README navigation; add the specified PEP 621 metadata and URLs.
4. Run `uv run pytest --no-cov tests/test_project_metadata.py -q`. Expect all tests to pass.
5. Run `git diff --check`, inspect the diff for duplicated or contradictory policy, and commit with
   `feat: establish repository governance`.

**Acceptance:** Every governance assertion passes; only GitHub private advisories accept security
reports; metadata and README identify the same files; no publication behavior appears.

**Rollback:** Revert the task commit. Disabling private vulnerability reporting is an owner action
outside this code change.

## Task 2: Verify the repository-wide contract

**Interfaces:** Consumes Task 1's committed contract. Produces the evidence required for review and
shipping; no later task consumes a new code interface.

1. Run `just verify`. Expect static checks, the full pytest suite, MCP smoke, and every CLI group to
   pass with zero warnings.
2. Run `uv run prek run --all-files`. Expect every hook to pass without modifying files. If a hook
   formats a file, inspect the change, rerun the focused test and both guardrails, then commit the
   mechanical correction separately.
3. Review `git diff main...HEAD --name-only` and confirm it contains only the file map plus ADR 0022,
   this specification, and this plan. Confirm no workflow file changed and inspect the
   `pyproject.toml` diff to verify it contains only the declared governance metadata.

**Acceptance:** Both exact guardrail commands exit zero and the branch remains clean.

**Rollback:** No generated or temporary artifacts remain. Revert only an evidence-driven corrective
commit if one was necessary.
