import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[1]
TOOL_PINS = {
    "detect-secrets==1.5.0",
    "prek==0.4.10",
    "ruff==0.15.22",
    "ty==0.0.62",
    "zizmor==1.29.0",
}
TY_INCLUDE = ["src/hmc_mcp"]
BASELINED_FINDINGS = {
    "justfile": 1,
    "tests/app/test_cli.py": 2,
    "tests/app/test_cli_e2e.py": 1,
    "tests/conftest.py": 1,
    "tests/security/test_users.py": 2,
    "tests/unit/test_ssh.py": 1,
}
ACTION_PINS = {
    "actions/checkout": (
        "3d3c42e5aac5ba805825da76410c181273ba90b1",  # pragma: allowlist secret
        "v7.0.1",
    ),
    "astral-sh/setup-uv": (
        "c771a70e6277c0a99b617c7a806ffedaca235ff9",  # pragma: allowlist secret
        "v9.0.0",
    ),
    "extractions/setup-just": (
        "53165ef7e734c5c07cb06b3c8e7b647c5aa16db3",  # pragma: allowlist secret
        "v4",
    ),
}


def test_quality_tools_are_pinned_with_a_strict_type_boundary() -> None:
    with (ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)

    assert TOOL_PINS <= set(project["dependency-groups"]["dev"])
    assert project["tool"]["ty"]["src"]["include"] == TY_INCLUDE
    assert "rules" not in project["tool"]["ty"]


def test_justfile_exposes_one_composed_verification_graph() -> None:
    justfile = (ROOT / "justfile").read_text()

    for recipe in (
        "setup",
        "lint",
        "typecheck",
        "secrets",
        "workflow-security",
        "env-vars",
        "static",
    ):
        assert f"\n{recipe}:" in justfile
    assert "\nstatic: lint typecheck secrets workflow-security env-vars\n" in justfile
    assert "\nverify: static test smoke\n" in justfile
    assert "--baseline .secrets.baseline --no-verify --" in justfile
    assert "uv run hmc-mcp metrics --help" in justfile


def test_prek_hooks_delegate_to_focused_just_recipes() -> None:
    config = (ROOT / ".pre-commit-config.yaml").read_text()

    assert config.count("repo: local") == 1
    for recipe in ("lint", "typecheck", "secrets", "workflow-security", "env-vars"):
        assert f"entry: just {recipe}" in config
    assert config.count("pass_filenames: false") == 5
    assert "entry: uv run" not in config


def test_secret_baseline_is_an_exact_reviewed_allowlist() -> None:
    with (ROOT / ".secrets.baseline").open("rb") as file:
        baseline = json.load(file)

    results = baseline["results"]
    assert {path: len(findings) for path, findings in results.items()} == BASELINED_FINDINGS
    excluded_paths = baseline.get("exclude", {})
    assert not any(
        path == "tests" or path.startswith("tests/") for path in excluded_paths
    )


def test_secret_scanner_treats_option_shaped_names_as_files(tmp_path: Path) -> None:
    (tmp_path / "--exclude-files").write_text("")
    (tmp_path / "credential.txt").write_text(
        'password = "option-probe-48"\n'  # pragma: allowlist secret
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "detect_secrets.pre_commit_hook",
            "--no-verify",
            "--",
            "--exclude-files",
            "credential.txt",
        ],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    assert "credential.txt:1" in result.stdout


def test_github_ci_uses_the_local_gates_with_least_privilege() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "pull_request:" in workflow
    assert re.search(r"push:\n\s+branches:\s+\[main\]", workflow)
    permissions = re.search(r"^permissions:\n(?P<body>(?:  .+\n)+)\n", workflow, re.MULTILINE)
    assert permissions
    assert permissions["body"] == "  contents: read\n"
    assert workflow.count("permissions:") == 1
    assert "cancel-in-progress: true" in workflow
    assert "runs-on: ubuntu-24.04" in workflow
    assert "timeout-minutes: 20" in workflow
    expected_actions = {f"{action}@{sha}" for action, (sha, _) in ACTION_PINS.items()}
    assert set(re.findall(r"uses:\s+([^\s#]+)", workflow)) == expected_actions
    for action, (sha, version) in ACTION_PINS.items():
        assert f"uses: {action}@{sha}  # {version}" in workflow
    assert "persist-credentials: false" in workflow
    assert 'version: "0.12.3"' in workflow
    assert 'just-version: "1.58.0"' in workflow
    for command in ("just setup", "just verify", "uv run prek run --all-files"):
        assert f"run: {command}" in workflow
