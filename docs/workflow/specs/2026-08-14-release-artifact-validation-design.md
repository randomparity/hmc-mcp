# Release artifact construction and validation design

## Goal

Extend the canonical verification graph so a clean checkout produces one wheel and one source
distribution, independently validates their metadata and contents, and retains only the wheel in CI
for downstream fresh-environment tests. No step publishes a package or uses publication credentials.

This design implements issue #162 and epic #156 requirement 5. It depends on the Git-derived version
contract merged for issue #159 and follows [ADR 0024](../../../adr/0024-separate-artifact-build-and-validation.md).

## Command contract

`just build` removes the repository's `dist/` directory and calls `uv build --wheel --sdist
--out-dir dist .`. A dirty or provenance-incomplete checkout fails through the existing version
backend with its actionable public-safe error. Successful construction leaves exactly the files
that the backend produced.

`just verify-artifacts` consumes the existing `dist/` directory and never invokes `uv build` for the
source checkout. It fails when the directory is absent, the artifact set is not exactly one wheel
and one `.tar.gz` source distribution, an archive is malformed, or an invariant below is false.
`just verify` runs the existing source checks, then `build`, then `verify-artifacts`.

## Artifact invariants

The validator derives no version from Git. It reads each archive and proves:

- wheel filename, `.dist-info` directory, and `METADATA` identify normalized project `hmc-mcp`;
- sdist filename, top-level directory, and `PKG-INFO` identify the same project;
- both metadata documents have one equal, valid version and match `pyproject.toml` for normalized
  project name, exact `requires-python` and license expression, the `hmc-mcp` console entry point,
  and the normalized set of declared runtime dependency requirements;
- wheel `hmc_mcp/**/*.py` members and sdist `src/hmc_mcp/**/*.py` members both equal the source
  checkout's `src/hmc_mcp/**/*.py` set after prefix normalization, without querying Git, and both
  contain the explicit package-boundary sentinels `hmc_mcp/__init__.py` and
  `hmc_mcp/server.py`; the clean provenance precondition makes that filesystem set authoritative
  for a canonical build, while standalone validation reports any dirty source/artifact mismatch;
- the sdist contains `pyproject.toml`, `README.md`, `LICENSE`, `scripts/versioning.py`, and the
  package members required to rebuild a wheel, with every path confined beneath one top-level
  directory; both archives contain only regular files and directories, rejecting links and
  special-file members before any rebuild; and
- rebuilding a wheel from the sdist in a temporary directory without repository Git metadata
  preserves the validated version, normalized core metadata, console entry point, dependency set,
  and package-member set of the original wheel.

Requirement comparison ignores insignificant whitespace and canonicalizes project names while
preserving extras, markers, and exact pins; it compares sets because declaration order carries no
meaning. Every failure names the artifact and violated invariant. Validation refuses missing,
duplicate, unexpected, corrupt, path-traversing, link-bearing, special-file, or inconsistent
artifacts rather than choosing one.

## CI data flow

Every existing matrix leg continues to run `just verify`; therefore artifact construction and
validation occur from the full-history, clean Actions checkout. After that gate succeeds, the job
uploads `dist/*.whl` with the immutable `actions/upload-artifact` v7.0.1 commit. The artifact name
includes architecture and Python version so concurrent matrix legs cannot collide. The action uses
`if-no-files-found: error` and a short retention period. It does not require write permissions.

The sdist is validated but not uploaded. Issue #163 owns downloading a wheel into fresh native
matrix environments and exercising installed CLI/MCP paths.

## Failure behavior

Construction preserves the version backend's redacted actionable failures for dirty or incomplete
provenance. Validation errors are concise and public-safe: they include a local artifact name and
the failed invariant, never environment variables, credentials, raw Git stderr, or archive content.
Temporary extraction and rebuild state is automatically removed on success and failure.

## Testing

Command-contract tests prove recipe names, ordering, clean `dist/` replacement, validation without
source rebuilding, wheel-only CI upload, immutable action pinning, matrix-unique names, and absence
of publication permissions or credentials. Packaging tests independently mutate every promised
invariant: artifact counts and extensions; corrupt archives; project name; version; Python
requirement; license; console entry point; each dependency-set side; source/wheel/sdist
package-member equality and sentinels, including synchronized omission from both archives; required
sdist inputs; absolute and escaping member paths; symlink, hard-link, device, and FIFO members;
rebuild failure; and rebuilt-wheel version, metadata, entry-point, dependency, and package-member
mismatches caused by independently changing embedded sdist build inputs. Each mutation starts from a
known-valid clean-checkout build and changes the archive directly, so it cannot share the
validator's expected-value logic. A mutation check will also break one implementation comparison
and confirm the focused test fails before the implementation is restored.

The required gates are `just setup`, focused pytest during TDD, `just verify`, and separately
`UV_NO_SYNC=1 uv run prek run --all-files`. Workflow changes also pass the repository's pinned
offline `zizmor` gate through `just verify`.

## Scope boundaries

This change does not publish to PyPI, configure publication credentials, widen workflow permissions,
choose native runner providers, or implement the downstream Python-by-architecture installation
matrix. It adds no runtime dependency and no new package API.

## Threat model

The added trust boundary is the CI artifact service receiving a wheel produced by a repository job.
The relevant untrusted actors are pull-request authors controlling repository contents. Existing
read-only workflow permissions, credential-free checkout, and the job timeout constrain the CI job;
the uploader receives only an explicit wheel glob and fails closed when it is empty. The validator
is a correctness guard over outputs created by that same checkout, not a sandbox for arbitrary
third-party archives; a pull-request author can modify both producer and validator in one diff.

The validator defensively checks archive member names, member types, link targets, and metadata. It
performs no extraction into the source checkout, accepts only regular files and directories, rejects
absolute or escaping paths plus all symbolic links, hard links, devices, and FIFOs, bounds the
accepted artifact count, and reports invariant names rather than archive payloads. Only after those
checks does the sdist rebuild occur in an isolated temporary directory. Protection from intentionally
oversized or highly compressed archives is outside this same-checkout correctness boundary and rests
on the existing CI timeout and runner limits. Supply-chain compromise of GitHub-hosted runners, the
pinned action, uv, or the locked build backend is outside this change and remains governed by
existing pinning and workflow security checks.
