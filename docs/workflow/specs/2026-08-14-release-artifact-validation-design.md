# Release artifact construction and validation design

## Goal

Extend the canonical verification graph so a clean checkout produces one wheel and one source
distribution, independently validates their metadata and contents, and retains only the wheel in CI
for downstream fresh-environment tests. No step publishes a package or uses publication credentials.

This design implements issue #162 and epic #156 requirement 5. It depends on the Git-derived version
contract merged for issue #159 and follows
[ADR 0024](../../adr/0024-separate-artifact-build-and-validation.md).

## Command contract

`just build` removes the repository's `dist/` directory and calls `uv build --wheel --sdist
--out-dir dist .`. A dirty or provenance-incomplete checkout fails through the existing version
backend with its actionable public-safe error. Successful construction leaves exactly the files
that the backend produced.

`just verify-artifacts` passes the existing `dist/` directory and repository root to the internal
validator and never invokes `uv build`. It fails when the directory is absent, the artifact set is
not exactly one wheel and one `.tar.gz` source distribution, an archive is malformed, or an
invariant below is false.
`just verify` runs the existing source checks, then `build`, then `verify-artifacts`.

## Artifact invariants

The validator derives no version from Git. It reads each archive and proves:

- wheel filename, `.dist-info` directory, and `METADATA` identify normalized project `hmc-mcp`;
- sdist filename, top-level directory, and `PKG-INFO` identify the same project;
- every validated archive member name uses `/` separators and Unicode NFC; has no backslash, empty,
  `.` or `..` component; and has no leading slash, drive-like colon in its first component, or
  duplicate after this validation; all wheel package and `.dist-info` content members and all sdist
  package and required-input members are regular files;
- the wheel has exactly one regular `.dist-info/RECORD` with one unambiguous row per archive file,
  no absent-member rows, and matching SHA-256 digest and size for every file except `RECORD` itself,
  whose digest and size fields are empty as permitted by the wheel format;
- the wheel has exactly one regular `.dist-info/WHEEL`; its singleton `Wheel-Version` is `1.0`,
  `Root-Is-Purelib` is `true`, and its non-empty `Tag` set is exactly the filename tag set
  (`py3-none-any` for this pure-Python project);
- one valid version agrees across the wheel filename, `.dist-info` directory, `METADATA`, sdist
  filename, sdist top-level directory, and `PKG-INFO`; both metadata documents match
  `pyproject.toml` for normalized project name, exact `requires-python` and license expression, and
  the normalized set of declared runtime dependency requirements;
- `METADATA` and `PKG-INFO` each contain exactly one `Metadata-Version`, `Name`, `Version`,
  `Requires-Python`, and `License-Expression`; `Metadata-Version` is exactly `2.5`, matching the
  pinned build backend, and malformed metadata or conflicting singleton fields fail closed;
- the wheel's `.dist-info/entry_points.txt` and the sdist's embedded `pyproject.toml` both declare
  exactly the checkout's `[project.scripts]` mapping, comparing section keys and target strings
  exactly after standard INI/TOML parsing;
- all regular `.py` members beneath wheel `hmc_mcp/` and sdist `src/hmc_mcp/` have the same
  normalized paths and byte-for-byte contents as all regular `.py` files beneath the source
  checkout's `src/hmc_mcp/`, without querying Git; neither archive contains another regular
  package member, so generated cache and tool files in the checkout are excluded deterministically;
  and both
  contain the explicit package-boundary sentinels `hmc_mcp/__init__.py` and
  `hmc_mcp/server.py`; the clean provenance precondition makes that filesystem set authoritative
  for a canonical build, while standalone validation reports any dirty source/artifact mismatch;
- the sdist contains `.gitignore`, `pyproject.toml`, `README.md`, `LICENSE`,
  `scripts/versioning.py`, and the package members required to rebuild a wheel, with every named
  required input byte-for-byte equal to its checkout counterpart and every path confined beneath
  one top-level directory; its complete regular-file set is exactly those five checkout inputs,
  `PKG-INFO`, and the expected
  `src/hmc_mcp/**/*.py` members; packaging configuration explicitly limits selected source files,
  while hatchling automatically adds `.gitignore` and `PKG-INFO`;
  every archive member is a regular file, so symlinks, hard links, devices, FIFOs, and directory
  entries are rejected, and link-bearing members are rejected regardless of whether their target
  appears confined;
- the wheel's complete member set is exactly the expected `hmc_mcp/**/*.py` files plus one each of
  `METADATA`, `WHEEL`, `entry_points.txt`, `licenses/LICENSE`, and `RECORD` beneath the single
  `.dist-info` directory; `.data` trees, another top-level package, and any other payload fail.

Requirement comparison ignores insignificant whitespace and canonicalizes project names while
preserving extras, markers, and exact pins; it compares sets because declaration order carries no
meaning. Every failure names the artifact and violated invariant. Validation refuses missing,
duplicate, unexpected, corrupt, path-traversing, non-regular, link-bearing, or inconsistent
artifacts rather than choosing one. Both archive member universes are closed. It reads archive
metadata and member bytes directly and never extracts or executes an archive.

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

## Testing

Command-contract tests prove recipe names, ordering, clean `dist/` replacement, validation without
source rebuilding, wheel-only CI upload, immutable action pinning, matrix-unique names, and absence
of publication permissions or credentials. Packaging tests independently mutate every promised
invariant: artifact counts and extensions; corrupt archives; project name; every version-bearing
filename, archive-root, and metadata location plus one synchronized invalid version across all six;
Python requirement; license; each archive-side console entry-point declaration; each dependency-set
side;
source/wheel/sdist package-member equality and sentinels, including synchronized omission and an
extra non-Python member or byte-divergent package file in either archive; missing, non-regular, or
byte-divergent required sdist inputs and an unexpected regular file outside the package tree;
absolute, drive-like, backslash-bearing, non-NFC, empty-component, dot-component, escaping, and
canonical-collision member paths; duplicate metadata and package-member paths; non-regular
package/metadata/required-input members;
and wheel `RECORD` missing, extra, duplicate, wrong-size, and wrong-digest rows. Every wheel mutation
outside the `RECORD`-specific cases updates the affected digest and size row so unrelated record
validation remains valid, and every negative test asserts the named intended invariant. Each
mutation starts from a known-valid clean-checkout build and changes the archive directly, so it
cannot share the validator's expected-value logic. A mutation check will also break one
implementation comparison and confirm the focused test fails before the implementation is restored.
The matrix also covers missing, duplicate, malformed, unsupported, or filename-discordant `WHEEL`
fields; unexpected top-level wheel payloads; every non-regular tar member type and escaping link
target; duplicate singleton core-metadata headers; and missing or unsupported `Metadata-Version` in
both metadata documents.

The required gates are `just setup`, focused pytest during TDD, `just verify`, and separately
`UV_NO_SYNC=1 uv run prek run --all-files`. Workflow changes also pass the repository's pinned
offline `zizmor` gate through `just verify`. The canonical global `just setup` recipe invokes
`uv sync --locked --link-mode copy`, avoiding cross-filesystem hard-link warnings without changing
the locked dependency graph.

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

The validator defensively checks archive member names, required member types, and metadata without
extracting or executing archive content. It rejects absolute or escaping paths, requires expected
content entries to be regular files, bounds the accepted artifact count, and reports invariant names
rather than archive payloads. Protection from intentionally oversized or highly compressed archives
is outside this same-checkout correctness boundary and rests on the existing CI timeout and runner
limits. Supply-chain compromise of GitHub-hosted runners, the pinned action, uv, or the locked build
backend is outside this change and remains governed by existing pinning and workflow security checks.
