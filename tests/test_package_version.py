from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
COMMAND_TIMEOUT_SECONDS = 180
# Declared statically in pyproject.toml; see ADR 0033.
PACKAGE_VERSION = "0.1.0"


def run(
    *command: str,
    cwd: Path | None = None,
    check: bool = True,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
        capture_output=True,
        env={
            **os.environ,
            "UV_LINK_MODE": "copy",
            "UV_NO_PROGRESS": "1",
            **(extra_env or {}),
        },
        text=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )


def copy_tracked_project(destination: Path) -> None:
    tracked = run("git", "ls-files", "-z", cwd=PROJECT_ROOT).stdout.split("\0")
    for relative in filter(None, tracked):
        source = PROJECT_ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def wheel_metadata(wheel: Path) -> tuple[str, set[str]]:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_name = next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        metadata = BytesParser(policy=default).parsebytes(archive.read(metadata_name))
    return str(metadata["Version"]), names


@dataclass(frozen=True)
class BuiltProject:
    project: Path
    wheel: Path
    sdist: Path
    version: str


@pytest.fixture(scope="module")
def built_project(tmp_path_factory: pytest.TempPathFactory) -> BuiltProject:
    workspace = tmp_path_factory.mktemp("package-version")
    project = workspace / "project"
    project.mkdir()
    copy_tracked_project(project)
    artifacts = workspace / "artifacts"
    run(
        "uv",
        "build",
        "--wheel",
        "--sdist",
        "--out-dir",
        str(artifacts),
        str(project),
    )
    return BuiltProject(
        project=project,
        wheel=next(artifacts.glob("*.whl")),
        sdist=next(artifacts.glob("*.tar.gz")),
        version=PACKAGE_VERSION,
    )


def test_wheel_metadata_and_package_contents(built_project: BuiltProject) -> None:
    version, names = wheel_metadata(built_project.wheel)
    source_modules = {
        path.relative_to(built_project.project / "src").as_posix()
        for path in (built_project.project / "src" / "hmc_mcp").rglob("*.py")
    }

    assert version == built_project.version
    assert source_modules <= names
    assert "hmc_mcp/__init__.py" in names
    assert "hmc_mcp/server.py" in names


def test_installed_wheel_runtime_version_matches_metadata(
    built_project: BuiltProject, tmp_path: Path
) -> None:
    target = tmp_path / "site-packages"
    run(
        "uv",
        "pip",
        "install",
        "--target",
        str(target),
        "--no-deps",
        str(built_project.wheel),
    )
    result = run(
        sys.executable,
        "-c",
        "import hmc_mcp; print(hmc_mcp.__version__)",
        cwd=tmp_path,
        extra_env={"PYTHONPATH": str(target)},
    )

    assert result.stdout.strip() == built_project.version


def test_sdist_embeds_version_and_rebuilds(
    built_project: BuiltProject, tmp_path: Path
) -> None:
    with tarfile.open(built_project.sdist) as archive:
        names = archive.getnames()
    assert any(name.endswith("/PKG-INFO") for name in names)

    rebuilt = tmp_path / "rebuilt"
    run(
        "uv",
        "build",
        "--wheel",
        "--out-dir",
        str(rebuilt),
        str(built_project.sdist),
    )
    version, _ = wheel_metadata(next(rebuilt.glob("*.whl")))

    assert version == built_project.version


def test_editable_install_uses_computed_metadata(
    built_project: BuiltProject, tmp_path: Path
) -> None:
    environment = tmp_path / "editable-venv"
    run("uv", "venv", "--python", sys.executable, str(environment))
    interpreter = environment / "bin" / "python"
    run(
        "uv",
        "pip",
        "install",
        "--python",
        str(interpreter),
        "--no-deps",
        "--editable",
        str(built_project.project),
    )
    result = run(
        str(interpreter),
        "-c",
        "import hmc_mcp; print(hmc_mcp.__version__)",
        cwd=tmp_path,
    )

    assert result.stdout.strip() == built_project.version
