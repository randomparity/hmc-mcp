import re
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
EXACT_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9._,-]+\])?=="
    r"(?P<version>[A-Za-z0-9][A-Za-z0-9.!+_-]*)$"
)
DEPENDABOT_CONFIG = """\
version: 2
updates:
  - package-ecosystem: "uv"
    directory: "/"
    schedule:
      interval: "weekly"
    cooldown:
      default-days: 7
    groups:
      all-dependencies:
        applies-to: "version-updates"
        patterns:
          - "*"
"""


def _project_requirements() -> list[str]:
    with (ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)

    return [
        *project["project"]["dependencies"],
        *project["dependency-groups"]["dev"],
        *project["build-system"]["requires"],
    ]


def _locked_requirements() -> list[str]:
    with (ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)

    return [
        *project["project"]["dependencies"],
        *project["dependency-groups"]["dev"],
        *project["build-system"]["requires"],
    ]


@pytest.mark.parametrize("requirement", _project_requirements())
def test_direct_dependencies_are_exactly_pinned(requirement: str) -> None:
    assert EXACT_REQUIREMENT.fullmatch(requirement), (
        f"direct dependency must use one exact registry pin: {requirement}"
    )


def test_direct_dependency_pins_match_the_lockfile() -> None:
    with (ROOT / "uv.lock").open("rb") as file:
        lock = tomllib.load(file)
    locked = {
        package["name"].lower().replace("_", "-"): package["version"]
        for package in lock["package"]
        if "version" in package
    }

    for requirement in _locked_requirements():
        match = EXACT_REQUIREMENT.fullmatch(requirement)
        assert match, f"cannot verify a non-exact requirement: {requirement}"
        name = match["name"].lower().replace("_", "-")
        assert locked.get(name) == match["version"], (
            f"{name} is pinned to {match['version']} but lock has {locked.get(name)}"
        )


def test_dependabot_updates_uv_dependencies_as_one_cooled_group() -> None:
    config = ROOT / ".github" / "dependabot.yml"
    assert config.read_text() == DEPENDABOT_CONFIG
