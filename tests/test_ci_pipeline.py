import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[1]
TOOL_PINS = {
    "detect-secrets==1.5.0",
    "prek==0.4.10",
    "ruff==0.15.22",
    "ty==0.0.62",
}
TY_INCLUDE = [
    "src/hmc_mcp/config.py",
    "src/hmc_mcp/documents.py",
    "src/hmc_mcp/errors.py",
]
BASELINED_FIXTURES = {
    "justfile": 1,
    "tests/app/test_cli.py": 2,
    "tests/app/test_cli_e2e.py": 1,
    "tests/conftest.py": 1,
    "tests/security/test_users.py": 2,
    "tests/unit/test_ssh.py": 1,
}


def test_quality_tools_are_pinned_with_a_strict_type_boundary() -> None:
    with (ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)

    assert TOOL_PINS <= set(project["dependency-groups"]["dev"])
    assert project["tool"]["ty"]["src"]["include"] == TY_INCLUDE
    assert "rules" not in project["tool"]["ty"]


def test_justfile_exposes_one_composed_verification_graph() -> None:
    justfile = (ROOT / "justfile").read_text()

    for recipe in ("setup", "lint", "typecheck", "secrets", "static"):
        assert f"\n{recipe}:" in justfile
    assert "\nverify: static test smoke\n" in justfile
    assert "uv run hmc-mcp metrics --help" in justfile


def test_prek_hooks_delegate_to_focused_just_recipes() -> None:
    config = (ROOT / ".pre-commit-config.yaml").read_text()

    assert config.count("repo: local") == 1
    for recipe in ("lint", "typecheck", "secrets"):
        assert f"entry: just {recipe}" in config
    assert config.count("pass_filenames: false") == 3
    assert "entry: uv run" not in config


def test_secret_baseline_is_an_exact_reviewed_fixture_allowlist() -> None:
    with (ROOT / ".secrets.baseline").open("rb") as file:
        baseline = json.load(file)

    results = baseline["results"]
    assert {path: len(findings) for path, findings in results.items()} == BASELINED_FIXTURES
    excluded_paths = baseline.get("exclude", {})
    assert not any(
        path == "tests" or path.startswith("tests/") for path in excluded_paths
    )
