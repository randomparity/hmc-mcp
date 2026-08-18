import json
import os
import re
import shutil
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
    "actions/upload-artifact": (
        "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",  # pragma: allowlist secret
        "v7.0.1",
    ),
    "actions/download-artifact": (
        "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",  # pragma: allowlist secret
        "v8.0.1",
    ),
    "docker/setup-qemu-action": (
        "96fe6ef7f33517b61c61be40b68a1882f3264fb8",  # pragma: allowlist secret
        "v4.2.0",
    ),
}
SUPPORTED_PYTHONS = ["3.11", "3.12", "3.13", "3.14"]
NATIVE_MATRIX = [
    ("amd64", "ubuntu-24.04", version) for version in SUPPORTED_PYTHONS
] + [
    ("arm64", "ubuntu-24.04-arm", version) for version in SUPPORTED_PYTHONS
]
QEMU_IMAGE = (
    "docker.io/tonistiigi/binfmt@"
    "sha256:400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0"
)
PPC64LE_BASE = (
    "ubuntu:24.04@"
    "sha256:561618e2c15bf2397621dd04f96926663a3b5616c189cf7e38db7e82f5c538ea"
)
UV_PPC64LE_SHA256 = (
    "bff188fcf2d867c5595f8db6061a39"  # pragma: allowlist secret
    "e54752ab213eaefc14287f37e85afe9ead"  # pragma: allowlist secret
)


def _copy_tracked_project(destination: Path) -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\0")
    for relative in filter(None, tracked):
        source = ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _inactive_ppc64le_job(workflow: str) -> tuple[str, str]:
    template = re.search(
        r"^  # ppc64le-release-artifact-template: begin\n"
        r"(?P<body>(?:^  #.*\n)+)"
        r"^  # ppc64le-release-artifact-template: end\n",
        workflow,
        re.MULTILINE,
    )
    assert template
    body = "".join(
        f"{line.removeprefix('  # ')}\n" for line in template["body"].splitlines()
    )
    active_workflow = workflow[: template.start()] + workflow[template.end() :]
    return active_workflow, body
SCORECARD_ACTION_PINS = {
    "actions/checkout": (
        "3d3c42e5aac5ba805825da76410c181273ba90b1",  # pragma: allowlist secret
        "v7.0.1",
    ),
    "actions/upload-artifact": (
        "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",  # pragma: allowlist secret
        "v7.0.1",
    ),
    "github/codeql-action/upload-sarif": (
        "f205ea1c3313d32999d8d6a48b4f6530d4437b38",  # pragma: allowlist secret
        "v4.37.4",
    ),
    "ossf/scorecard-action": (
        "2d1146689b8cda280b9bc96326124645441f03bc",  # pragma: allowlist secret
        "v2.4.4",
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
        "nicknames",
        "static",
    ):
        assert f"\n{recipe}:" in justfile
    assert "\nstatic: lint typecheck secrets workflow-security env-vars nicknames\n" in justfile
    assert "\nbuild:\n    uv build --clear --wheel --sdist --out-dir dist .\n" in justfile
    assert (
        "\nverify-artifacts:\n"
        "    uv run --no-sync python tests/validate_release_artifacts.py dist .\n"
        in justfile
    )
    assert "\nverify: static test smoke build verify-artifacts\n" in justfile
    assert "--baseline .secrets.baseline --no-verify --" in justfile
    assert "uv run --no-sync hmc-mcp metrics --help" in justfile


def test_just_recipes_sync_only_in_setup_and_otherwise_run_without_sync() -> None:
    justfile = (ROOT / "justfile").read_text()

    assert justfile.count("uv sync --locked --extra app --link-mode copy") == 1
    assert (
        "setup:\n"
        "    uv sync --locked --extra app --link-mode copy\n"
        "    uv run --no-sync prek install\n"
        in justfile
    )
    run_lines = [line.strip() for line in justfile.splitlines() if "uv run" in line]
    assert run_lines
    assert all("uv run --no-sync" in line for line in run_lines)


def test_prek_hooks_delegate_to_focused_just_recipes() -> None:
    config = (ROOT / ".pre-commit-config.yaml").read_text()

    assert config.count("repo: local") == 1
    for recipe in (
        "lint",
        "typecheck",
        "secrets",
        "workflow-security",
        "env-vars",
        "nicknames",
    ):
        assert f"entry: just {recipe}" in config
    assert config.count("pass_filenames: false") == 6
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
    assert re.search(r"schedule:\n\s+- cron: '[^']+'", workflow)
    assert "workflow_dispatch:" not in workflow
    permissions = re.search(
        r"^permissions:\n(?P<body>(?:  .+\n)+)\n", workflow, re.MULTILINE
    )
    assert permissions
    assert permissions["body"] == "  contents: read\n"
    assert workflow.count("permissions:") == 1
    assert "cancel-in-progress: true" in workflow
    assert workflow.count("runs-on: ubuntu-24.04") == 3
    assert "runs-on: ${{ matrix.runner }}" in workflow
    assert "timeout-minutes: 20" in workflow
    assert "timeout-minutes: 5" in workflow
    expected_actions = {f"{action}@{sha}" for action, (sha, _) in ACTION_PINS.items()}
    assert set(re.findall(r"uses:\s+([^\s#]+)", workflow)) == expected_actions
    for action, (sha, version) in ACTION_PINS.items():
        assert f"uses: {action}@{sha}  # {version}" in workflow
    assert "persist-credentials: false" in workflow
    assert 'version: "0.12.3"' in workflow
    assert 'just-version: "1.58.0"' in workflow
    for command in (
        "just setup",
        "just verify",
        "UV_NO_SYNC=1 uv run prek run --all-files",
    ):
        assert f"run: {command}" in workflow
    verification = workflow.index("run: just verify")
    upload = workflow.index("uses: actions/upload-artifact@")
    assert verification < upload
    assert "name: release-wheel-${{ matrix.architecture }}-py${{ matrix.python-version }}" in workflow
    assert "path: dist/*.whl" in workflow
    assert "if-no-files-found: error" in workflow
    assert "retention-days: 7" in workflow
    assert "dist/*.tar.gz" not in workflow
    assert "id-token: write" not in workflow
    assert "PYPI" not in workflow.upper()


def test_active_ci_checkouts_with_project_uv_use_full_history() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    active_workflow, _ = _inactive_ppc64le_job(workflow)
    checkout_settings = re.findall(
        r"uses: actions/checkout@[^\n]+\n"
        r"        with:\n"
        r"(?P<settings>(?:          [^\n]+\n)+)",
        active_workflow,
    )

    assert len(checkout_settings) == 4
    assert active_workflow.count("uv run") >= 2
    assert sum("fetch-depth: 0\n" in settings for settings in checkout_settings) == 3
    for settings in checkout_settings:
        assert "persist-credentials: false\n" in settings


def test_dirty_project_commands_do_not_rebuild_editable_metadata(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _copy_tracked_project(project)
    subprocess.run(
        ["git", "init", "-q", "--initial-branch=main"], cwd=project, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "tests@example.invalid"],
        cwd=project,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Command Tests"], cwd=project, check=True
    )
    # Disable any global pre-commit hooks (e.g. corporate secret-scanners)
    # for this ephemeral test fixture repo.
    subprocess.run(
        ["git", "config", "core.hooksPath", "/dev/null"], cwd=project, check=True
    )
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=project, check=True)
    environment = {**os.environ, "UV_LINK_MODE": "copy", "UV_NO_PROGRESS": "1"}
    subprocess.run(
        ["uv", "sync", "--locked", "--extra", "app"],
        cwd=project,
        check=True,
        capture_output=True,
        env=environment,
        text=True,
        timeout=180,
    )
    with (project / "pyproject.toml").open("a", encoding="utf-8") as file:
        file.write("\n# dirty command regression\n")

    lint = subprocess.run(
        ["just", "lint"],
        cwd=project,
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=180,
    )
    hooks = subprocess.run(
        ["uv", "run", "prek", "run", "--all-files"],
        cwd=project,
        check=False,
        capture_output=True,
        env={**environment, "UV_NO_SYNC": "1"},
        text=True,
        timeout=180,
    )

    assert lint.returncode == 0, lint.stderr
    assert "All checks passed" in lint.stdout
    assert "Building hmc-mcp" not in lint.stderr
    assert hooks.returncode == 0, hooks.stdout + hooks.stderr
    assert "Ruff lint" in hooks.stdout
    assert "Building hmc-mcp" not in hooks.stderr


def test_github_ci_uses_a_bounded_native_architecture_matrix() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    active_workflow, _ = _inactive_ppc64le_job(workflow)

    matrix = re.search(
        r"^      matrix:\n(?P<body>.*?)(?=^    steps:)",
        active_workflow,
        re.MULTILINE | re.DOTALL,
    )
    assert matrix
    expected_matrix = "        include:\n" + "".join(
        f"          - architecture: {architecture}\n"
        f"            runner: {runner}\n"
        f'            python-version: "{version}"\n'
        for architecture, runner, version in NATIVE_MATRIX
    )
    assert matrix["body"] == expected_matrix
    assert (
        "name: ${{ matrix.architecture }} / Python "
        "${{ matrix.python-version }} / verify" in workflow
    )
    assert "      fail-fast: false\n      matrix:\n" in active_workflow
    assert "python-version: ${{ matrix.python-version }}" in workflow
    assert active_workflow.count("run: just verify") == 1
    assert not re.search(r"^  ppc64le:", active_workflow, re.MULTILINE)
    assert "docker/setup-qemu-action" not in active_workflow
    assert "architecture: [amd64, arm64]" not in workflow


def test_github_ci_smokes_each_retained_wheel_in_a_fresh_environment() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    active_workflow, _ = _inactive_ppc64le_job(workflow)
    consumer = re.search(
        r"^  wheel-smoke:\n(?P<body>.*?)(?=^  python-support-drift:)",
        active_workflow,
        re.MULTILINE | re.DOTALL,
    )

    assert consumer
    body = consumer["body"]
    expected_matrix = "      matrix:\n        include:\n" + "".join(
        f"          - architecture: {architecture}\n"
        f"            runner: {runner}\n"
        f'            python-version: "{version}"\n'
        for architecture, runner, version in NATIVE_MATRIX
    )
    assert "    needs: ci\n" in body
    assert (
        "    name: ${{ matrix.architecture }} / Python "
        "${{ matrix.python-version }} / wheel smoke\n" in body
    )
    assert "      fail-fast: false\n" in body
    assert expected_matrix in body
    assert "    runs-on: ${{ matrix.runner }}\n" in body
    assert "    timeout-minutes: 10\n" in body
    assert (
        "uses: actions/download-artifact@"
        "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c  # v8.0.1" in body
    )
    assert (
        "name: release-wheel-${{ matrix.architecture }}-py"
        "${{ matrix.python-version }}" in body
    )
    assert "path: dist" in body
    assert "merge-multiple:" not in body
    assert "wheels=(dist/*.whl)" in body
    assert "${#wheels[@]} != 1" in body
    assert "expected exactly one wheel" in body
    assert "uv venv --python \"${MATRIX_PYTHON}\" .wheel-venv" in body
    assert "uv export --frozen --no-dev --no-emit-project --no-header" in body
    assert (
        "uv export --frozen --extra app --no-dev --no-emit-project --no-header"
        in body
    )
    assert "uv pip install --python .wheel-venv/bin/python" in body
    assert "--requirements .wheel-requirements.txt" in body
    assert "--requirements .wheel-app-requirements.txt" in body
    assert "uv pip install --no-deps --python .wheel-venv/bin/python" in body
    assert '"${wheels[0]}[app]"' in body
    assert "import hmc_mcp" in body
    assert "is_relative_to(environment)" in body
    for group in ("", "lpars", "storage", "network", "templates", "metrics"):
        command = ".wheel-venv/bin/hmc-mcp"
        if group:
            command += f" {group}"
        assert f"{command} --help" in body
    assert ".wheel-venv/bin/python scripts/smoke_mcp.py" in body
    assert "just setup" not in body
    assert "uv sync" not in body
    assert "pip install -e" not in body
    assert not re.search(r"^  ppc64le:", active_workflow, re.MULTILINE)


def test_github_ci_exercises_the_installed_public_api_without_app_dependencies() -> (
    None
):
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    active_workflow, _ = _inactive_ppc64le_job(workflow)
    consumer = re.search(
        r"^  library-wheel-smoke:\n(?P<body>.*?)(?=^  wheel-smoke:)",
        active_workflow,
        re.MULTILINE | re.DOTALL,
    )

    assert consumer
    body = consumer["body"]
    assert "    needs: ci\n" in body
    assert "    name: amd64 / Python 3.13 / library wheel smoke\n" in body
    assert "    runs-on: ubuntu-24.04\n" in body
    assert '          python-version: "3.13"\n' in body
    assert "name: release-wheel-amd64-py3.13" in body
    assert "uv pip install --python .library-wheel-venv/bin/python" in body
    assert '            "${wheels[0]}"' in body
    assert "from hmc_mcp.api import capacity_report" in body
    assert "import hmc_mcp.api" not in body
    for package in ("fastmcp", "mcp", "rich", "typer"):
        assert f'assert find_spec("{package}") is None' in body
    assert "class FakeHMC:" in body
    assert "async def list_managed_systems(" in body
    assert "async def list_logical_partitions(" in body
    assert "asyncio.run(capacity_report(FakeHMC()))" in body
    assert '"system_name": "p10"' in body
    assert "assert report ==" in body
    assert "[app]" not in body
    assert "uv export" not in body
    assert "--no-deps" not in body
    assert "scripts/smoke_mcp.py" not in body
    assert "pip install -e" not in body


def test_github_ci_retains_an_inactive_bounded_ppc64le_job() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    dockerfile = (ROOT / ".github" / "containers" / "ppc64le.Dockerfile").read_text()
    active_workflow, retained_job = _inactive_ppc64le_job(workflow)

    job = re.search(
        r"^ppc64le:\n(?P<body>.*)\Z",
        retained_job,
        re.MULTILINE | re.DOTALL,
    )
    assert job
    body = job["body"]
    assert "ppc64le" not in active_workflow
    assert "runs-on: ubuntu-24.04" in body
    assert "timeout-minutes: 30" in body
    assert "platforms: ppc64le" in body
    assert f"image: {QEMU_IMAGE}" in body
    assert "cache-image: false" in body
    assert "persist-credentials: false" in body
    assert "secrets." not in body
    assert "github.token" not in body
    assert "GITHUB_TOKEN" not in body
    assert "SSH_AUTH_SOCK" not in body
    assert "AWS_" not in body
    assert "GOOGLE_" not in body
    assert "AZURE_" not in body
    assert body.count("--platform linux/ppc64le") == 2
    assert "--mount type=bind,source=\"$GITHUB_WORKSPACE\",target=/workspace" in body
    assert "-e " not in body
    assert "/var/run/docker.sock" not in body

    assert dockerfile.startswith(f"FROM {PPC64LE_BASE}\n")
    assert "uv-powerpc64le-unknown-linux-gnu.tar.gz" in dockerfile
    assert UV_PPC64LE_SHA256 in dockerfile
    assert "ubuntu_snapshot=20260813T000000Z" in dockerfile
    assert "old_uri=http://ports.ubuntu.com/ubuntu-ports/" in dockerfile
    assert "https://snapshot.ubuntu.com/ubuntu/${ubuntu_snapshot}/" in dockerfile
    bootstrap_add = (
        "ADD --checksum=sha256:6bac2a01979e210d9eac1d4d56747ec7"
        "09ea60654744d66705dc3c36e7629e50 \\\n"
        "    https://snapshot.ubuntu.com/ubuntu/20260813T000000Z/pool/main/c/"
        "ca-certificates/ca-certificates_20260601~24.04.1_all.deb \\\n"
        "    /tmp/ca-certificates.deb\n"
    )
    bootstrap_chain = (
        "    && dpkg --unpack /tmp/ca-certificates.deb \\\n"
        "    && cat /usr/share/ca-certificates/mozilla/*.crt "
        "> /etc/ssl/certs/ca-certificates.crt \\\n"
        "    && apt-get update \\\n"
        "    && DEBIAN_FRONTEND=noninteractive \\\n"
        "        apt-get install --yes --no-remove --no-install-recommends "
        "--fix-broken \\\n"
    )
    toolchain_install = (
        "    && DEBIAN_FRONTEND=noninteractive \\\n"
        "        apt-get install --yes --no-remove --no-install-recommends \\\n"
        "        ca-certificates \\\n"
    )
    add_pattern = re.compile(f"^{re.escape(bootstrap_add)}", re.MULTILINE)
    assert add_pattern.search(dockerfile)
    assert bootstrap_chain in dockerfile
    assert toolchain_install in dockerfile
    assert not add_pattern.search(dockerfile.replace("--checksum=sha256:", "# checksum="))
    assert bootstrap_chain not in dockerfile.replace(
        "/etc/ssl/certs/ca-certificates.crt", "/tmp/ca-certificates.crt"
    )
    assert "ubuntu_sources=/etc/apt/sources.list.d/ubuntu.sources" in dockerfile
    assert dockerfile.count("grep -Fxc") == 2
    assert dockerfile.count('"${ubuntu_sources}" || true)" = 2') == 2
    assert '! grep -Fq "${old_uri}" "${ubuntu_sources}"' in dockerfile
    assert "python3" in dockerfile
    assert "rust_version=1.97.1" in dockerfile
    assert "rustup_sha256=" in dockerfile
    assert "ARG " not in dockerfile
    expected_cmd = (
        'CMD ["bash", "-euo", "pipefail", "-c", \\\n'
        r'    "architecture=$(uname -m) && echo \"runtime architecture: ${architecture}\" '
        r'&& test \"${architecture}\" = \"ppc64le\" '
        "&& git config --global --add safe.directory /workspace "
        "&& uv sync --locked --no-install-package prek "
        '&& UV_NO_SYNC=1 just verify"]\n'
    )
    assert dockerfile.endswith(expected_cmd)
    assert "just setup" not in dockerfile
    assert dockerfile.count("just verify") == 1
    assert "COPY" not in dockerfile
    assert "rm -rf" not in dockerfile


def test_scheduled_job_checks_the_same_explicit_versions() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    drift_job = re.search(
        r"^  python-support-drift:\n(?P<body>.*)", workflow, re.MULTILINE | re.DOTALL
    )
    assert drift_job
    body = drift_job["body"]
    assert "if: github.event_name == 'schedule'" in body
    assert "timeout-minutes: 5" in body
    command = re.search(
        r"run: uv run --no-project python scripts/check_python_support.py "
        r"(?P<args>[^\n]+)",
        body,
    )
    assert command
    assert command["args"].split() == SUPPORTED_PYTHONS
    assert "just verify" not in body


def test_python_policy_metadata_is_aligned() -> None:
    with (ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)
    with (ROOT / "uv.lock").open("rb") as file:
        lockfile = tomllib.load(file)

    assert project["project"]["requires-python"] == ">=3.11"
    assert (ROOT / ".python-version").read_text().strip() == "3.11"
    assert lockfile["requires-python"] == ">=3.11"
    readme = (ROOT / "README.md").read_text()
    assert "Python ≥3.11" in readme
    assert "stable, non-EOL CPython release" in readme


def test_scorecard_workflow_is_bounded_and_uses_least_privilege() -> None:
    workflow = (ROOT / ".github" / "workflows" / "scorecard.yml").read_text()

    assert re.search(r"push:\n\s+branches:\s+\[main\]", workflow)
    assert re.search(r"schedule:\n\s+- cron: '[^']+'", workflow)
    assert "pull_request:" not in workflow
    assert "permissions: read-all" in workflow
    assert workflow.count("permissions:") == 2
    job_permissions = re.search(
        r"^    permissions:\n(?P<body>(?:      .+\n)+)\n", workflow, re.MULTILINE
    )
    assert job_permissions
    assert job_permissions["body"] == (
        "      contents: read\n"
        "      security-events: write\n"
        "      id-token: write\n"
    )
    assert "runs-on: ubuntu-24.04" in workflow
    assert "timeout-minutes: 10" in workflow


def test_scorecard_workflow_pins_actions_and_retains_results() -> None:
    workflow = (ROOT / ".github" / "workflows" / "scorecard.yml").read_text()

    expected_actions = {
        f"{action}@{sha}" for action, (sha, _) in SCORECARD_ACTION_PINS.items()
    }
    assert set(re.findall(r"uses:\s+([^\s#]+)", workflow)) == expected_actions
    for action, (sha, version) in SCORECARD_ACTION_PINS.items():
        assert f"uses: {action}@{sha}  # {version}" in workflow
    assert "persist-credentials: false" in workflow
    assert "results_file: results.sarif" in workflow
    assert "results_format: sarif" in workflow
    assert "publish_results: true" in workflow
    assert "name: scorecard-results" in workflow
    assert "path: results.sarif" in workflow
    assert "retention-days: 5" in workflow
    assert "sarif_file: results.sarif" in workflow
