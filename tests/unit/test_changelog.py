"""Release metadata contract: every declared version ships with a changelog entry.

ADR 0029 makes facade-manifest movement observable only through the changelog, so a
release without an entry is a contract violation. See CHANGELOG.md for the convention.
"""

import re
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_RELEASE_HEADING = re.compile(r"^## \[([^\]]+)\]", re.MULTILINE)


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


def test_declared_version_has_a_changelog_entry() -> None:
    version = _declared_version()
    versions = {name for name, _ in _release_sections()}
    assert version in versions, (
        f"pyproject.toml declares {version} but CHANGELOG.md has no '## [{version}]' "
        f"entry; a release cannot ship unrecorded"
    )


def test_every_release_entry_has_a_facade_manifest_section() -> None:
    for name, body in _release_sections():
        assert re.search(r"^### Facade manifest$", body, re.MULTILINE), (
            f"CHANGELOG.md entry '{name}' is missing its mandatory "
            f"'### Facade manifest' section"
        )
