"""Release metadata contract: every declared version ships with a changelog entry.

ADR 0029 makes facade-manifest movement observable only through the changelog, so a
release without an entry is a contract violation. See CHANGELOG.md for the convention.

The manifest guard asserts content, not heading presence. Its boundary is the oldest
release entry, whose ``### Facade manifest`` section enumerates ``hmc_mcp.api.__all__``
in full: every export that section does not already name is a post-baseline addition and
must be named by ``[Unreleased]``. That is why this file needs no git tag — the repository
carries none, so no entry, ``[Unreleased]`` least of all, has a tag boundary to diff
against, and the enumerated baseline stands in for one.

Two things stay deliberately outside the mechanism:

* **Removals and renames.** A removed export is by definition absent from ``__all__``,
  and without tags there is no per-release snapshot to diff it against, so a "Removed:"
  or "Renamed:" line has nothing to corroborate it. Only additions are derivable.
* **Per-entry attribution among released entries.** Names are matched against the union
  of the released manifests, so once a second release is cut this file can say that some
  released entry names an export but not which one. ``[Unreleased]`` keeps exact
  attribution, because everything the released union misses belongs to it.

Matching is by code-formatted name, and a manifest section's ordinary prose is searched
along with its ``Added:`` lines. The check is therefore lenient: incidental backticks can
cover an export the section never meant to declare. It under-reports rather than crying
wolf, and it still fires on the defect it exists for -- an export added with no manifest
entry at all.
"""

import re
import tomllib
from collections.abc import Iterable
from pathlib import Path

from hmc_mcp import api

_REPO_ROOT = Path(__file__).resolve().parents[2]

_RELEASE_HEADING = re.compile(r"^## \[([^\]]+)\]", re.MULTILINE)

_UNRELEASED = "Unreleased"

_MANIFEST_SECTION = re.compile(
    r"^### Facade manifest$(?P<body>.*?)(?=^### |\Z)", re.MULTILINE | re.DOTALL
)

_CODE_FORMATTED_NAME = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")


def _declared_version() -> str:
    with (_REPO_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def _changelog_text() -> str:
    return (_REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def _release_sections() -> list[tuple[str, str]]:
    """Return (version, body) for each ``## [<version>]`` entry, including Unreleased."""
    text = _changelog_text()
    matches = list(_RELEASE_HEADING.finditer(text))
    if not matches:
        raise AssertionError("CHANGELOG.md contains no release entries")
    return [
        (match.group(1), text[match.end() : next_.start() if next_ else len(text)])
        for match, next_ in zip(matches, [*matches[1:], None])
    ]


def _facade_manifest(body: str) -> str | None:
    """Return the ``### Facade manifest`` section of one release entry, if it has one."""
    match = _MANIFEST_SECTION.search(body)
    return match.group("body") if match else None


def _facade_manifests() -> dict[str, str]:
    """Map each release entry to its facade-manifest section, skipping entries without one.

    ``test_every_release_entry_has_a_facade_manifest_section`` owns the missing-section
    failure, so the callers here do not have to restate it.
    """
    found = ((name, _facade_manifest(body)) for name, body in _release_sections())
    return {name: section for name, section in found if section is not None}


def _names_in(manifest: str) -> set[str]:
    return set(_CODE_FORMATTED_NAME.findall(manifest))


def _exports_missing_from_the_unreleased_manifest(
    exports: Iterable[str],
    released_manifests: Iterable[str],
    unreleased_manifest: str,
) -> set[str]:
    """Return the exports no manifest section names.

    An export the released manifests already name was published before this cycle. Every
    other export is part of the running delta and ``[Unreleased]`` has to name it.
    """
    already_released: set[str] = set()
    for manifest in released_manifests:
        already_released |= _names_in(manifest)
    delta = set(exports) - already_released
    return delta - _names_in(unreleased_manifest)


def test_declared_version_has_a_changelog_entry() -> None:
    version = _declared_version()
    versions = {name for name, _ in _release_sections()}
    assert version in versions, (
        f"pyproject.toml declares {version} but CHANGELOG.md has no '## [{version}]' "
        f"entry; a release cannot ship unrecorded"
    )


def test_every_release_entry_has_a_facade_manifest_section() -> None:
    for name, body in _release_sections():
        assert _facade_manifest(body) is not None, (
            f"CHANGELOG.md entry '{name}' is missing its mandatory "
            f"'### Facade manifest' section"
        )


def test_every_facade_manifest_section_says_something() -> None:
    """A heading over an empty section satisfies presence and states nothing."""
    for name, manifest in _facade_manifests().items():
        assert "`" in manifest, (
            f"CHANGELOG.md entry '{name}' has an empty '### Facade manifest' section; "
            f"it must name the exports that moved, or state that "
            f"`hmc_mcp.api.__all__` did not change"
        )


def test_unreleased_facade_manifest_names_every_export_added_since_the_baseline() -> None:
    manifests = _facade_manifests()
    assert _UNRELEASED in manifests, (
        f"CHANGELOG.md has no '[{_UNRELEASED}]' entry with a '### Facade manifest' "
        f"section, so no entry can account for exports added since the last release"
    )
    released = [
        manifest for name, manifest in manifests.items() if name != _UNRELEASED
    ]
    assert released, (
        "CHANGELOG.md has no released entry with a facade manifest, so there is no "
        "baseline to derive the export delta against"
    )

    missing = _exports_missing_from_the_unreleased_manifest(
        api.__all__, released, manifests[_UNRELEASED]
    )
    assert not missing, (
        f"{len(missing)} export(s) in hmc_mcp.api.__all__ are named by no "
        f"'### Facade manifest' section: {', '.join(sorted(missing))}. ADR 0029 calls "
        f"__all__ an exhaustive manifest and CHANGELOG.md requires the section to name "
        f"every added export, so each of these must be named in the "
        f"'[{_UNRELEASED}]' manifest"
    )


def test_an_export_dropped_from_the_unreleased_manifest_is_caught() -> None:
    """The negative variant: the real manifest, minus one export it does declare."""
    manifests = _facade_manifests()
    released = [
        manifest for name, manifest in manifests.items() if name != _UNRELEASED
    ]
    baseline = set().union(*(_names_in(manifest) for manifest in released))
    dropped = sorted(set(api.__all__) - baseline)[0]

    stale = manifests[_UNRELEASED].replace(f"`{dropped}`", dropped)

    assert _exports_missing_from_the_unreleased_manifest(
        api.__all__, released, stale
    ) == {dropped}, (
        f"removing `{dropped}` from the '[{_UNRELEASED}]' manifest must be detected; "
        f"the guard is only checking that a heading exists"
    )


def test_a_complete_unreleased_manifest_is_not_flagged() -> None:
    """The look-alike: the same section, unmutated, must stay silent."""
    manifests = _facade_manifests()
    released = [
        manifest for name, manifest in manifests.items() if name != _UNRELEASED
    ]

    assert (
        _exports_missing_from_the_unreleased_manifest(
            api.__all__, released, manifests[_UNRELEASED]
        )
        == set()
    )


def test_an_export_named_only_in_uncoded_prose_does_not_count() -> None:
    """``Added: thing`` is prose; the manifest has to name the export as code."""
    assert _exports_missing_from_the_unreleased_manifest(
        ["published", "added", "prose_only"],
        ["- Initial manifest: `published`."],
        "- Added: `added`, and prose_only which is mentioned but not code-formatted.",
    ) == {"prose_only"}
