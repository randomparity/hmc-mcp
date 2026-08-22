import re
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
EXACT_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9._,-]+\])?=="
    r"(?P<version>[A-Za-z0-9][A-Za-z0-9.!+_-]*)$"
)
# ADR 0068: the library runtime dependencies declare exactly one compatible
# range -- a floor at the current locked release and one upper bound. Anything
# else (extras, markers, multiple specifiers) must go through a reviewed
# widening of this pattern, not slip past the guard.
RANGED_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r">=(?P<floor>[A-Za-z0-9][A-Za-z0-9.!+_-]*),(?P<cap><[A-Za-z0-9][A-Za-z0-9.!+_-]*)$"
)
# The exhaustive library install-path set (ADR 0068). Adding a runtime
# dependency means adding it here consciously, with its policy-checked range.
LIBRARY_DEPENDENCIES = frozenset(
    {"asyncssh", "defusedxml", "httpx", "pydantic", "pydantic-settings"}
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


def _pyproject() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)


def _library_requirements() -> list[str]:
    """The library install path: [project.dependencies] (ADR 0068)."""
    project = _pyproject()
    return list(project["project"]["dependencies"])


def _exact_requirements() -> list[str]:
    """Everything ADR 0001 still governs: app extra, dev group, build backend."""
    project = _pyproject()
    return [
        *project["project"]["optional-dependencies"]["app"],
        *project["dependency-groups"]["dev"],
        *project["build-system"]["requires"],
    ]


def _locked_versions() -> dict[str, str]:
    with (ROOT / "uv.lock").open("rb") as file:
        lock = tomllib.load(file)
    return {
        package["name"].lower().replace("_", "-"): package["version"]
        for package in lock["package"]
        if "version" in package
    }


def _release(version: str) -> tuple[int, ...]:
    """Numeric release tuple of the plain X.Y.Z shapes this lock pins."""
    match = re.match(r"\d+(?:\.\d+)*", version)
    assert match, f"unsupported version shape for range comparison: {version}"
    return tuple(int(part) for part in match[0].split("."))


@pytest.mark.parametrize("requirement", _exact_requirements())
def test_application_surface_dependencies_are_exactly_pinned(
    requirement: str,
) -> None:
    assert EXACT_REQUIREMENT.fullmatch(requirement), (
        f"application-surface dependency must stay exactly pinned: {requirement}"
    )


@pytest.mark.parametrize("requirement", _library_requirements())
def test_library_runtime_dependencies_declare_compatible_ranges(
    requirement: str,
) -> None:
    match = RANGED_REQUIREMENT.fullmatch(requirement)
    assert match, (
        f"library runtime dependency must declare one >=floor,<cap range: "
        f"{requirement}"
    )
    assert match["name"].lower().replace("_", "-") in LIBRARY_DEPENDENCIES


def test_library_dependency_set_is_exhaustive() -> None:
    names = {
        RANGED_REQUIREMENT.fullmatch(requirement)["name"]
        .lower()
        .replace("_", "-")
        for requirement in _library_requirements()
    }
    assert names == LIBRARY_DEPENDENCIES, (
        "a runtime dependency joined or left the library set; update "
        "LIBRARY_DEPENDENCIES and ADR 0068's policy notes together"
    )


def test_exact_application_pins_match_the_lockfile() -> None:
    locked = _locked_versions()
    for requirement in _exact_requirements():
        match = EXACT_REQUIREMENT.fullmatch(requirement)
        assert match, f"cannot verify a non-exact requirement: {requirement}"
        name = match["name"].lower().replace("_", "-")
        assert locked.get(name) == match["version"], (
            f"{name} is pinned to {match['version']} but lock has {locked.get(name)}"
        )


@pytest.mark.parametrize("requirement", _library_requirements())
def test_locked_versions_satisfy_the_declared_ranges(requirement: str) -> None:
    """The committed resolution stays inside every relaxed range (ADR 0068)."""
    match = RANGED_REQUIREMENT.fullmatch(requirement)
    assert match, f"cannot verify a non-ranged requirement: {requirement}"
    name = match["name"].lower().replace("_", "-")
    locked = _locked_versions().get(name)
    assert locked is not None, f"{name} is declared but missing from uv.lock"
    floor = _release(match["floor"])
    cap = _release(match["cap"].removeprefix("<"))
    assert floor <= _release(locked) < cap, (
        f"lock has {name} {locked}, outside the declared range {requirement}"
    )


def test_dependabot_updates_uv_dependencies_as_one_cooled_group() -> None:
    config = ROOT / ".github" / "dependabot.yml"
    assert config.read_text() == DEPENDABOT_CONFIG
