"""Release metadata contract: every declared version ships with a changelog entry."""

import re
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RELEASE_HEADING = re.compile(r"^## \[([^\]]+)\]", re.MULTILINE)


def test_declared_version_has_a_changelog_entry() -> None:
    with (_REPO_ROOT / "pyproject.toml").open("rb") as project:
        version = tomllib.load(project)["project"]["version"]
    changelog = (_REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert version in set(_RELEASE_HEADING.findall(changelog)), (
        f"pyproject.toml declares {version} but CHANGELOG.md has no '## [{version}]' "
        "entry; a release cannot ship unrecorded"
    )
