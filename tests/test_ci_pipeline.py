import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
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
    "tests/unit/test_ssh.py": 1,
    "tests/unit/test_client.py": 1,
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


def _job_body(workflow: str, name: str) -> str:
    """Slice one job at its structural boundary — the next top-level job header.

    Anchoring on a named neighbour instead lets the body swallow every job
    between the two, so an assertion meant for one job passes while sitting in
    another, and inserting a job silently widens the window.
    """
    match = re.search(
        rf"^  {re.escape(name)}:\n(?P<body>.*?)(?=^  \S+:$|\Z)",
        workflow,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"job not found in workflow: {name}"
    return match["body"]


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
        "tool-docs",
        "tool-docs-check",
        "adr-numbering",
        "doc-freshness",
        "static",
        "test",
        "test-verbose",
        "smoke",
        "smoke-verbose",
    ):
        assert f"\n{recipe}:" in justfile
    assert (
        "\nstatic: lint typecheck secrets workflow-security env-vars nicknames "
        "tool-docs-check adr-numbering doc-freshness\n" in justfile
    )
    assert (
        "\nadr-numbering:\n"
        "    uv run --no-sync python scripts/check_adr_numbering.py\n"
        in justfile
    )
    assert (
        "\ndoc-freshness:\n"
        "    uv run --no-sync python scripts/check_generated_docs.py\n"
        in justfile
    )
    assert (
        "\ntool-docs:\n"
        "    uv run --no-sync python scripts/gen_tool_reference.py\n"
        in justfile
    )
    assert (
        "\ntool-docs-check:\n"
        "    uv run --no-sync python scripts/gen_tool_reference.py --check\n"
        in justfile
    )
    assert "\nbuild:\n    uv build --clear --wheel --sdist --out-dir dist .\n" in justfile
    assert (
        "\nverify-artifacts:\n"
        "    uv run --no-sync python tests/validate_release_artifacts.py dist .\n"
        in justfile
    )
    assert "\nverify: static test smoke build verify-artifacts\n" in justfile
    assert (
        "\ntest:\n"
        "    uv run --no-sync python scripts/run_tests.py\n"
        in justfile
    )
    assert (
        "\ntest-verbose:\n"
        "    uv run --no-sync pytest -q --cov-report=term-missing\n"
        in justfile
    )
    assert "\nsmoke:\n    uv run --no-sync python scripts/smoke_mcp.py\n" in justfile
    assert (
        "\nsmoke-verbose:\n"
        "    uv run --no-sync python scripts/smoke_mcp.py --verbose\n"
        in justfile
    )
    assert "--baseline .secrets.baseline --no-verify --" in justfile
    assert "uv run --no-sync hmc-mcp --help >/dev/null" in justfile
    assert "uv run --no-sync python scripts/smoke_cli_groups.py" in justfile
    # The group helps are derived, so a per-group line is the hand-maintained
    # mirror growing back. `hmc-mcp --help` above is not one: the pattern needs a
    # group token before the flag, which `--help` itself cannot supply.
    assert re.search(r"hmc-mcp [a-z][a-z-]* --help", justfile) is None, (
        "name a group's help here and it is a hand-maintained list again; "
        "scripts/smoke_cli_groups.py derives them from the Typer app"
    )


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
    """One hook per `static` member, derived from both files rather than restated.

    The recipe list and the hook list both live in the tree, so a test that writes
    either one down again is a hand-maintained mirror and a literal hook count is
    a number kept in step by memory -- exactly what ADR 0098 §1c calls a defect.
    Deriving both sides and comparing them is the property anyone wanted: every
    gate `just verify` reaches is also a commit-time hook, and no hook runs
    something `just verify` does not.
    """
    justfile = (ROOT / "justfile").read_text()
    config = (ROOT / ".pre-commit-config.yaml").read_text()

    static = re.search(r"^static:(?P<dependencies>[^\n]*)$", justfile, re.MULTILINE)
    assert static
    members = static["dependencies"].split()
    assert members

    hooks = re.findall(r"^\s*- id: (\S+)$", config, re.MULTILINE)
    entries = re.findall(r"^\s*entry: just (\S+)$", config, re.MULTILINE)

    assert config.count("repo: local") == 1
    # Every hook delegates to a recipe, and the delegated set is `static` exactly.
    assert len(entries) == len(hooks)
    assert sorted(entries) == sorted(members)
    assert config.count("pass_filenames: false") == len(hooks)
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
    assert workflow.count("runs-on: ubuntu-24.04") == 4
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
        # Named as its own step as well as reached through `static` -> `verify`,
        # so generated-docs drift is its own failed check (ADR 0097, ADR 0098).
        "just tool-docs-check",
        "just doc-freshness",
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


def test_active_ci_checkouts_with_project_uv_do_not_fetch_full_history() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    active_workflow, _ = _inactive_ppc64le_job(workflow)
    checkout_settings = re.findall(
        r"uses: actions/checkout@[^\n]+\n"
        r"        with:\n"
        r"(?P<settings>(?:          [^\n]+\n)+)",
        active_workflow,
    )

    assert len(checkout_settings) == 5
    assert active_workflow.count("uv run") >= 2
    # Full history is no longer fetched: the version is declared statically and no
    # workflow step reads Git history (ADR 0033).
    assert not any("fetch-depth" in settings for settings in checkout_settings)
    for settings in checkout_settings:
        assert "persist-credentials: false\n" in settings


def test_dirty_project_commands_do_not_rebuild_editable_metadata(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _copy_tracked_project(project)
    # No commit: the build reads no Git state (ADR 0033). The repository and the staged
    # index are still needed, because `prek run --all-files` resolves its file list
    # through `git ls-files`. No hooks-path override here -- `prek run` executes the
    # hooks declared in .pre-commit-config.yaml and never consults core.hooksPath.
    subprocess.run(
        ["git", "init", "-q", "--initial-branch=main"], cwd=project, check=True
    )
    subprocess.run(["git", "add", "."], cwd=project, check=True)
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
    body = _job_body(active_workflow, "wheel-smoke")

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
    body = _job_body(active_workflow, "library-wheel-smoke")

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
    # PEP 561: the marker is asserted against the installed distribution, so a
    # build-configuration regression that omits it fails this job.
    assert 'assert (package_path.parent / "py.typed").is_file(), package_path' in body
    # Presence is not usability: a second step makes a PEP 561-enforcing checker
    # read the installed distribution, and fails on the diagnostic that means
    # the marker was not read. Both directions are asserted so the gate cannot
    # pass vacuously against an untyped package.
    assert "--python-executable .library-wheel-venv/bin/python" in body
    assert "grep -q 'import-untyped' probe.txt" in body
    assert "grep -q 'attr-defined' probe.txt" in body
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


def test_github_ci_exercises_each_declared_range_floor() -> None:
    """ADR 0068: a declared range is a claim only if its low end is exercised."""
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    active_workflow, _ = _inactive_ppc64le_job(workflow)
    body = _job_body(active_workflow, "library-range-floors")

    assert "    needs: ci\n" in body
    assert "    name: amd64 / Python 3.13 / library range floors\n" in body
    # The floors are derived from the declaration, never transcribed: a new or
    # widened runtime range is floor-tested without touching this job.
    assert 'with open("pyproject.toml", "rb") as file:' in body
    assert "--requirements .range-floors.txt" in body
    assert '            "${wheels[0]}"' in body
    assert "uv venv" in body
    # The exercised surface is the bare installed API, not the app extra.
    assert "from hmc_mcp.api import capacity_report" in body
    assert "[app]" not in body
    assert "scripts/smoke_mcp.py" not in body
    # Held here since narrowing the library-wheel-smoke match stopped its body
    # from spilling into this job and covering these incidentally.
    assert "uv export" not in body
    assert "--no-deps" not in body
    assert "pip install -e" not in body
    assert "import hmc_mcp.api" not in body


def test_the_floor_derivation_covers_every_declared_range(tmp_path: Path) -> None:
    """Run the workflow's derivation snippet against pyproject.toml itself."""
    from test_supply_chain import LIBRARY_DEPENDENCIES, RANGED_REQUIREMENT

    with (ROOT / "pyproject.toml").open("rb") as file:
        dependencies = tomllib.load(file)["project"]["dependencies"]

    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    step = re.search(
        r"Derive every declared range's floor.*?<<'PY'\n(?P<script>.*?)\n {10}PY",
        workflow,
        re.DOTALL,
    )
    assert step, "floor-derivation heredoc not found in ci.yml"
    script = textwrap.dedent(step["script"])

    shutil.copy2(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    (tmp_path / "_derive_floors.py").write_text(script)
    result = subprocess.run(
        [sys.executable, "_derive_floors.py"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    expected = {
        f"{match['name']}=={match['floor']}"
        for spec in dependencies
        if (match := RANGED_REQUIREMENT.fullmatch(spec))
    }
    derived = set((tmp_path / ".range-floors.txt").read_text().splitlines())
    assert derived == expected
    assert {line.split("==")[0] for line in derived} == set(LIBRARY_DEPENDENCIES)


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


README_STACK_HEADING = "## Stack"
README_STACK_NEXT = "## Contributing, security, and license"
PYTHON_FLOOR_CLAIMS = ("Python ≥3.11", "stable, non-EOL CPython release")


def _readme_stack(readme: str) -> str:
    """Return the body of README's '## Stack' section.

    Heading *order* is asserted, not mere presence: without it a renamed or reordered
    heading makes the second split return the rest of the file and the slice stops
    being a section.
    """
    for heading in (README_STACK_HEADING, README_STACK_NEXT):
        assert heading in readme, f"README has no '{heading}' heading"
    assert readme.index(README_STACK_HEADING) < readme.index(README_STACK_NEXT), (
        f"README must keep '{README_STACK_HEADING}' before '{README_STACK_NEXT}'"
    )
    return readme.split(README_STACK_HEADING, 1)[1].split(README_STACK_NEXT, 1)[0]


def test_python_policy_metadata_is_aligned() -> None:
    with (ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)
    with (ROOT / "uv.lock").open("rb") as file:
        lockfile = tomllib.load(file)

    assert project["project"]["requires-python"] == ">=3.11"
    assert (ROOT / ".python-version").read_text().strip() == "3.11"
    assert lockfile["requires-python"] == ">=3.11"
    stack = _readme_stack((ROOT / "README.md").read_text())
    for claim in PYTHON_FLOOR_CLAIMS:
        assert claim in stack, f"'{README_STACK_HEADING}' must state {claim!r}"


def test_python_floor_relocated_out_of_the_stack_section_is_caught() -> None:
    """The negative variant: the floor stays in the README but leaves '## Stack'."""
    readme = (ROOT / "README.md").read_text()
    stack = _readme_stack(readme)
    relocated = readme.replace(stack, "\n\n", 1).replace(
        "## Install\n", f"## Install\n{stack}", 1
    )

    assert all(claim in relocated for claim in PYTHON_FLOOR_CLAIMS)
    moved = _readme_stack(relocated)
    assert [claim for claim in PYTHON_FLOOR_CLAIMS if claim in moved] == []


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


# --------------------------------------------------------------------------- #
# coverage gate (#240)
#
# The gate declared 90% but compared the total rounded to coverage.py's default
# precision of 0, so 89.78% rounded to 90 and passed while pytest-cov printed
# FAIL from the unrounded total. These tests lock the fixed gate: two drive a
# generated project through a real pytest run, three guard the configuration
# that makes the comparison exact.
# --------------------------------------------------------------------------- #

GATE_STATEMENTS = 1000
# coverage.py excludes this comment from the denominator with no configuration
# at all, so it shrinks the measured total without covering anything. Written
# as a literal rather than read from coverage.config: that module is private
# (CoverageConfig is not exported from the package), coverage is not a declared
# dependency here -- it arrives transitively through pytest-cov -- and an import
# at module scope turns a resolution change into a collection error that takes
# every test in this file with it.
NO_COVER_PRAGMA = re.compile(r"#\s*pragma:?\s*no\s*cover", re.IGNORECASE)
# Lines allowed to carry one anyway. Empty on purpose: the point is that
# widening it is a reviewed diff, not that it can never happen.
REVIEWED_NO_COVER: frozenset[str] = frozenset()
# Anything that runs the package-wide suite, and therefore the gate.
GATE_INVOCATIONS = ("pytest", "just test", "just verify")


def _starts_a_block(line: str) -> bool:
    """A workflow step (a `- ` list item) or a justfile recipe header (column 0)."""
    if line.strip().startswith("- "):
        return True
    return bool(line) and not line[0].isspace() and not line.startswith("#")


def _gate_blocks(text: str) -> list[tuple[int, list[str]]]:
    """Slice a justfile or workflow into the blocks that run the package-wide suite.

    A block runs from one header to the next: a workflow step and the keys under
    it, or a justfile recipe and its indented body. Returning the block rather
    than the whole file is what keeps the relocation rule off edits that have
    nothing to do with the test suite.
    """
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if _starts_a_block(line)]
    blocks = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = lines[start:end]
        if any(token in line for line in block for token in GATE_INVOCATIONS):
            blocks.append((start + 1, block))
    return blocks


def _project_toml() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)


def _coverage_gate() -> tuple[float, dict]:
    """Return the configured floor and the whole [tool.coverage.report] table.

    float, not int: int(90.9) truncates to 90, which would let the assertions
    below pass while the real floor -- and the diagnostic the child emits --
    had moved.
    """
    report = _project_toml()["tool"]["coverage"]["report"]
    return float(report["fail_under"]), report


def _write_gate_project(root: Path, report: dict, covered: int) -> None:
    """Build a package of exactly GATE_STATEMENTS statements, `covered` executed."""
    total = GATE_STATEMENTS
    assert 0 < covered < total
    package = root / "gatepkg"
    package.mkdir()
    package.joinpath("covered.py").write_text(
        "".join(f"v{i} = {i}\n" for i in range(1, covered - 1))
    )
    package.joinpath("uncovered.py").write_text(
        "def never_called():\n"
        + "".join(f"    w{i} = {i}\n" for i in range(1, total - covered + 1))
    )
    package.joinpath("__init__.py").write_text("from . import covered, uncovered\n")
    tests = root / "tests"
    tests.mkdir()
    tests.joinpath("test_gate.py").write_text(
        "def test_touch():\n    import gatepkg.covered\n    assert gatepkg.covered.v1 == 1\n"
    )
    # json.dumps, not repr: repr(True) is "True", which is not valid TOML.
    report_toml = "\n".join(
        f"{key} = {json.dumps(value)}" for key, value in sorted(report.items())
    )
    root.joinpath("pyproject.toml").write_text(
        "[project]\n"
        'name = "gatepkg"\n'
        'version = "0.0.0"\n'
        "\n"
        "[tool.pytest.ini_options]\n"
        'pythonpath = ["."]\n'
        'addopts = "--cov=gatepkg --cov-report="\n'
        "\n"
        "[tool.coverage.report]\n" + report_toml + "\n"
    )


def _run_gate_project(root: Path) -> "subprocess.CompletedProcess[str]":
    # An exported PYTEST_ADDOPTS=--no-cov (or a COVERAGE_RCFILE left over from
    # debugging) would otherwise decide the child's coverage instead of the
    # generated project, reddening this test for an unrelated reason.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTEST_ADDOPTS", "COVERAGE_RCFILE", "COVERAGE_FILE"}
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=root,
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=180,
    )


def test_coverage_gate_declares_one_exact_floor() -> None:
    floor, report = _coverage_gate()
    project = _project_toml()
    addopts = project["tool"]["pytest"]["ini_options"]["addopts"]

    assert floor == 90
    assert report["precision"] >= 2
    # Without a measured source nothing consults fail_under at all. Token, not
    # substring: "--cov=hmc_mcp/config.py" contains "--cov=hmc_mcp" and would
    # narrow the measured source to one file, giving a total near 100%.
    assert "--cov=hmc_mcp" in addopts.split()
    assert "--cov-report=" in addopts.split()
    assert "term-missing" not in addopts
    # Each of these silently disarms the gate: a command-line floor or precision
    # overrides the configured one, --no-cov switches measurement off, and
    # --cov-config sends coverage.py to a different file entirely.
    for flag in ("--cov-fail-under", "--no-cov", "--cov-precision", "--cov-config"):
        assert flag not in addopts, flag
    # A denominator key disarms the gate without touching the floor: it drops
    # statements from the total rather than covering them. Measured on the probe
    # package at 899/1000 covered, floor 90, each key added on its own:
    # omit -> 898/898 100.00% exit 0, exclude_also -> 898/898 100.00% exit 0,
    # include -> 897/897 100.00% exit 0. The same names live in both sections,
    # so both are rejected by the same rule -- coverage.py accepts omit and
    # include under [tool.coverage.run] and omit, include, exclude_lines and
    # exclude_also under [tool.coverage.report].
    for section in ("run", "report"):
        for key in project["tool"]["coverage"].get(section, {}):
            assert key not in {"omit", "include"} and not key.startswith("exclude"), (
                f"[tool.coverage.{section}] {key} shrinks the measured denominator, "
                f"so the total can reach 100% with the floor untouched. Widening the "
                f"freeze below does not admit it either. The source route to the same "
                f"effect is closed separately, by the no-cover scan."
            )
    # The freeze is broader than the rejection above, and deliberately so.
    # coverage.py accepts seventeen keys in [tool.coverage.report], and
    # enumerating every harmful one means betting the enumeration stays complete
    # across versions -- partial_also and partial_branches already bite the
    # moment anyone adds --cov-branch. A display-only key is not a threat, but
    # it is also not free: it is one line here to add it deliberately.
    assert set(report) == {"fail_under", "precision"}, (
        f"[tool.coverage.report] is frozen to the coverage gate's two keys; got "
        f"{sorted(report)}. A key the rejection above allows may be added -- add "
        f"it here too, in the same commit, so the gate's configuration stays "
        f"reviewed. Widening this set does not admit a denominator key, because "
        f"the rejection above runs first."
    )


def test_coverage_gate_denominator_is_not_shrunk_in_source() -> None:
    """A no-cover pragma shrinks the denominator without touching pyproject.toml.

    The configuration rules above close the config-file route. This closes the
    source route, which is cheaper and needs no reviewed key: on the probe
    package at 899/1000 covered, a single `# pragma: no cover` on the uncovered
    function reports `TOTAL 898 0 100.00%` and exits 0 -- the same disarm
    `omit` produces, reached with pyproject.toml byte-identical.

    `exclude_lines = []` closes that route too (verified: back to
    `TOTAL 1000 101 89.90%`, exit 1) and is deliberately not the remedy. It is
    rejected by the rule above, and it would also unexclude `if TYPE_CHECKING:`
    and `...` stub bodies, which are excluded because they genuinely cannot run
    under test. Scanning the source closes the open-ended channel and leaves the
    two bounded ones alone.
    """
    offenders = [
        location
        for path in sorted((ROOT / "src" / "hmc_mcp").rglob("*.py"))
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if NO_COVER_PRAGMA.search(line)
        and (location := f"{path.relative_to(ROOT)}:{number}") not in REVIEWED_NO_COVER
    ]
    assert not offenders, (
        f"{', '.join(offenders)}: `# pragma: no cover` drops these statements "
        f"from the coverage total instead of covering them, so the reported "
        f"percentage stops describing the package. If a line genuinely cannot be "
        f"executed under test, add its `path:line` to REVIEWED_NO_COVER."
    )


def test_coverage_gate_is_not_defeated_at_the_invocation_sites() -> None:
    """The floor can be overridden from any pytest invocation, not just addopts.

    Scans every workflow and the whole justfile rather than one recipe, so a new
    recipe or a new workflow that runs pytest is covered too.

    --no-cov is held to a weaker rule than the other three, because it is the one
    flag with a legitimate use here: the addopts comment directs
    `pytest --no-cov <paths>` for a focused subset. Forbidding it outright would
    redden this test the first time someone adds a `just test-fast` recipe -- a
    false alarm whose natural remedy is deleting the assertion. So it is rejected
    only where no test path accompanies it.

    That heuristic is deliberately not the whole guard, because it is weakest
    exactly where it matters most: every test here lives under tests/, so
    `pytest -q --no-cov tests` in the `test` recipe would satisfy it while
    disabling the gate on a full-suite run. The recipe that runs the gate is
    therefore pinned exactly rather than pattern-matched.
    """
    sources = {"justfile": (ROOT / "justfile").read_text()}
    workflows = ROOT / ".github" / "workflows"
    for pattern in ("*.yml", "*.yaml"):
        for path in sorted(workflows.glob(pattern)):
            sources[path.name] = path.read_text()

    assert len(sources) >= 2
    # Same idiom as test_justfile_exposes_one_composed_verification_graph, which
    # pins `build` and `verify-artifacts` this way.
    assert (
        "\ntest:\n    uv run --no-sync python scripts/run_tests.py\n"
        in sources["justfile"]
    ), (
        "the `test` recipe body is pinned because it runs the package-wide coverage "
        "gate; any edit to it -- including one unrelated to coverage -- has to be "
        "made here too, so the gate cannot be disabled by a recipe change alone"
    )
    for name, text in sources.items():
        # COVERAGE_RCFILE is --cov-config delivered as an environment variable: a
        # committed `env:` on a workflow step or an `export` in a recipe redirects
        # coverage.py away from pyproject.toml, and fail_under and precision both
        # fall back to 0. A contributor's own local export is outside this scan and
        # is accepted; a committed one is exactly what it is here to catch.
        for flag in (
            "--cov-fail-under",
            "--cov-precision",
            "--cov-config",
            "COVERAGE_RCFILE",
        ):
            assert flag not in text, f"{name}: {flag}"
        # The one disarm vector that carries no forbidden token at all. coverage.py
        # opens its configuration relative to the working directory and does not walk
        # upward as pytest does for addopts, so a step that runs pytest from a
        # subdirectory still measures the package -- addopts survives -- but reads no
        # fail_under, falls back to 0, and prints no banner. Verified on this
        # repository: the same subset run exits 1 with the gate diagnostic from the
        # root and 0 from tests/.
        #
        # Scoped to the blocks that run the suite, not the whole file, and
        # deliberately so. `working-directory` is an ordinary Actions key -- a docs
        # build, a container build, a step scoped to a subdirectory -- and rejecting
        # it everywhere would redden a test named for the coverage gate on edits that
        # never touch the suite. That is a false alarm whose cheapest remedy is
        # deleting the assertion, which is how a guard dies. Inside a block that runs
        # the gate no exception is owed, because relocating it is never legitimate,
        # so both spellings are rejected outright. The residual is a job-level
        # `defaults.run.working-directory`, which sits outside any step block; it is
        # recorded in ADR 0034 rather than guarded here.
        for line_number, block in _gate_blocks(text):
            for token in ("working-directory", "cd "):
                offender = next((line for line in block if token in line), None)
                assert offender is None, (
                    f"{name}:{line_number}: `{token.strip()}` relocates a block that "
                    f"runs the package-wide suite ({offender.strip()}), and coverage.py "
                    f"reads its configuration relative to the working directory. The "
                    f"gate would measure the package, enforce no floor, and print no "
                    f"banner. Run the suite from the repository root."
                )
        for number, line in enumerate(text.splitlines(), start=1):
            if "--no-cov" in line:
                assert "tests" in line, f"{name}:{number}: --no-cov on a package-wide run"


def test_pyproject_is_the_coverage_configuration_source() -> None:
    """Guard which files the gate is configured from -- pytest's and coverage.py's both.

    Two independent search orders have to land on pyproject.toml, and a file
    that merely exists can win either one.

    coverage.py tries .coveragerc, .coveragerc.toml, setup.cfg, tox.ini,
    pyproject.toml and stops at the first that reads; for the two .coveragerc
    forms merely existing is enough. An empty .coveragerc at the root disarms
    the gate with pyproject.toml byte-identical, and prints no FAIL banner
    either, because fail_under falls back to 0.

    pytest tries pytest.toml, .pytest.toml, pytest.ini, .pytest.ini,
    pyproject.toml, tox.ini, setup.cfg (_pytest/config/findpaths.py), and the
    first four win outright even when empty. That is the worse vector of the
    two: it takes --cov=hmc_mcp out of addopts, so nothing is measured at all,
    fail_under is never consulted, and the run is indistinguishable from a
    project with no coverage configured. Verified against this repository --
    each of the four takes a subset run from exit 1 with a coverage table to
    exit 0 with none. tox.ini and setup.cfg are not vectors on the pytest side,
    because pyproject.toml precedes them in that order.

    Adding one of these files is an ordinary thing to do -- a marker, a
    filterwarnings entry -- which is exactly why the gate cannot rely on nobody
    doing it.
    """
    for name in (
        ".coveragerc",
        ".coveragerc.toml",
        "pytest.toml",
        ".pytest.toml",
        "pytest.ini",
        ".pytest.ini",
    ):
        assert not (ROOT / name).exists(), (
            f"{name} at the repository root overrides pyproject.toml and disarms the "
            f"coverage gate; keep the gate's configuration in pyproject.toml"
        )
    for name in ("setup.cfg", "tox.ini"):
        candidate = ROOT / name
        if candidate.exists():
            assert "[coverage:" not in candidate.read_text(), name


def test_coverage_gate_fails_a_total_that_rounds_up_to_the_floor(tmp_path: Path) -> None:
    """A total just under the floor must fail, not round up into passing.

    Built from the configured floor rather than fixed constants: with 1000
    statements and 10 * floor - 1 covered, the true total is floor - 0.1 percent,
    which rounds to the floor at coverage.py's default precision of 0.
    """
    floor, report = _coverage_gate()
    _write_gate_project(tmp_path, report, covered=round(10 * floor) - 1)

    result = _run_gate_project(tmp_path)

    # EXIT_TESTSFAILED exactly. A non-zero check would also pass on a syntax
    # error (1 or 2), on no tests collected (5), or on an unimportable pytest --
    # every way this harness can break -- so it would prove nothing about the gate.
    assert result.returncode == 1, result.stdout + result.stderr
    # Pins the measured total, the floor, and the precision in one string.
    # Formatted from the configured precision, not hardcoded: at precision = 3
    # pytest-cov emits "total of 89.900 is less than fail-under=90.000", and a
    # hardcoded string would redden this test for a change that strengthened the gate.
    digits = report["precision"]
    assert (
        f"Coverage failure: total of {floor - 0.1:.{digits}f} "
        f"is less than fail-under={floor:.{digits}f}" in result.stdout
    ), result.stdout


def test_coverage_gate_passes_a_total_exactly_on_the_floor(tmp_path: Path) -> None:
    """Control: without it, a permanently broken harness reads as a working gate."""
    floor, report = _coverage_gate()
    _write_gate_project(tmp_path, report, covered=round(10 * floor))

    result = _run_gate_project(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Coverage failure" not in result.stdout
    # Acceptance criterion 3, and a different code path from the line above.
    # pytest-cov decides the exit status on the rounded total and the banner on
    # the unrounded one, so a run can exit 0 while still printing FAIL -- that
    # disagreement is the reported defect, and at 89.996% it still reproduces.
    # Asserting only the absence of the failure-path diagnostic would pass there.
    assert "FAIL" not in result.stdout, result.stdout
