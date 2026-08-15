# Repository governance design

**Issue:** #158  
**Decision:** [ADR 0022](../../adr/0022-repository-governance-metadata.md)  
**Branch:** `feat/mit-governance-158` from `main`  
**Guardrails:** `just verify`; `uv run prek run --all-files`

## Goal and scope

Establish MIT licensing and a consistent public contribution and private vulnerability-reporting
path across canonical repository files, the README, and package metadata. The permitted surface is
`LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `README.md`, `pyproject.toml`, focused metadata tests,
this specification, ADR 0022, and its implementation plan.

Publication credentials, PyPI upload or release workflows, version provenance, and release-artifact
construction are excluded. The host is arm64; declared targets are amd64 and arm64, and the host is
included. All content and validation are architecture-independent.

## Governance contract

`LICENSE` contains the unmodified MIT License with `Copyright (c) 2026 hmc-mcp contributors`, which
states collective project attribution without asserting exclusive ownership by one contributor.
`CONTRIBUTING.md` gives the shortest complete local path: fork or branch, `uv sync --locked`, make a
focused change with tests, run `just verify` and `uv run prek run --all-files`, then open a PR. It
directs suspected vulnerabilities away from public issues and to `SECURITY.md`.

`SECURITY.md` directs reports only to
`https://github.com/randomparity/hmc-mcp/security/advisories/new`, tells reporters not to open a
public issue, requests reproduction and impact details without requesting secrets, and makes no
response-time or supported-version promise. GitHub private vulnerability reporting is enabled and
is the existing access-control boundary.

The README gains a compact `Contributing, security, and license` section linking the three canonical
files. It does not duplicate their instructions.

## Package metadata

The `[project]` table declares `license = "MIT"`, `license-files = ["LICENSE"]`, an MIT classifier,
and these direct URLs:

- `Repository = "https://github.com/randomparity/hmc-mcp"`
- `Contributing = "https://github.com/randomparity/hmc-mcp/blob/main/CONTRIBUTING.md"`
- `Security = "https://github.com/randomparity/hmc-mcp/security/policy"`

The package metadata and README links therefore identify the same policies without assuming that a
package index renders repository-relative links.

## Validation and failure behavior

`tests/test_project_metadata.py` parses `pyproject.toml` with `tomllib`, checks the exact license
expression, license-file declaration, MIT classifier, and project URLs, verifies each canonical
policy file exists, and checks the README links them. It also asserts that `SECURITY.md` names the
private advisory URL and rejects public issue reporting. Exact values make drift fail with the
offending contract visible in the assertion.

The test scans workflow and metadata surfaces to ensure this change adds neither a PyPI publication
workflow nor publication credential names. Existing dependency and CI tests remain unchanged.

## Threat model

### Boundary inventory

- **Added:** an unaffiliated reporter follows public documentation into GitHub's private advisory
  form. The reporter controls the report body and attachments; GitHub controls authentication,
  storage, authorization, and delivery to repository maintainers.
- **Widened:** none. The repository setting already owns the private advisory boundary; this change
  only makes its entry point discoverable.

### Actors and trust

Untrusted actors are public repository visitors and GitHub users submitting reports. Repository
maintainers are trusted to review private advisories. GitHub is trusted to authenticate users and
restrict advisory contents according to repository roles. The project does not receive or process
report contents in its code or CI.

### Controls

The documentation uses the repository-specific HTTPS advisory URL and explicitly rejects public
issues. GitHub supplies authorization and private storage. The policy asks for reproduction,
affected versions, and impact, but never asks for passwords, tokens, or production data. A missing
or disabled GitHub feature fails visibly at the linked page; no fallback silently makes a report
public.

### Out of scope

GitHub account recovery, GitHub service availability, maintainer incident-response procedure, and
security response SLAs are platform or future policy concerns. This change does not accept reports
over email, automate advisory processing, or protect information a reporter independently posts in
public.

## Acceptance criteria

1. The canonical MIT license, contribution guide, and private security policy exist and agree.
2. README navigation and PEP 621 metadata identify the canonical policies.
3. Focused tests fail on missing or inconsistent governance metadata and pass on the final files.
4. `just verify` and `uv run prek run --all-files` pass with zero warnings.
5. No publication credential or PyPI release workflow is introduced.

