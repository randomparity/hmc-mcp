"""Versioningit methods for versions derived from validated Git history."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from versioningit import VCSDescription

RELEASE_TAG = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
RELEASE_LINES = ("patch", "minor", "major")
GIT_TIMEOUT_SECONDS = 10


def _run_git(project_dir: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(project_dir), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Git provenance check failed: {error}") from error


def _raise_git_error(result: subprocess.CompletedProcess[str]) -> None:
    detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
    raise RuntimeError(f"Git provenance check failed: {detail}")


def _git(project_dir: Path, *arguments: str) -> str:
    result = _run_git(project_dir, *arguments)
    if result.returncode != 0:
        _raise_git_error(result)
    return result.stdout.strip()


def _release_tags(project_dir: Path) -> list[tuple[tuple[int, int, int], str]]:
    tags = []
    for tag in _git(project_dir, "tag", "--merged", "HEAD").splitlines():
        if RELEASE_TAG.fullmatch(tag) is not None:
            major, minor, patch = tag.split(".")
            version = (int(major), int(minor), int(patch))
            tags.append((version, tag))
    return tags


def _branch(project_dir: Path) -> str | None:
    result = _run_git(project_dir, "symbolic-ref", "--quiet", "--short", "HEAD")
    if result.returncode == 1:
        return None
    if result.returncode != 0:
        _raise_git_error(result)
    return result.stdout.strip()


def _validate_repository(project_dir: Path) -> None:
    if _git(project_dir, "rev-parse", "--is-inside-work-tree") != "true":
        raise RuntimeError("Git provenance check failed: not a Git worktree")
    if _git(project_dir, "rev-parse", "--is-shallow-repository") == "true":
        raise RuntimeError(
            "Git repository is shallow; fetch full history before building the package"
        )
    if _git(project_dir, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError(
            "Git repository is dirty; commit or clean staged, unstaged, and untracked files"
        )


def describe_git(
    *, project_dir: str | Path, params: dict[str, object]
) -> VCSDescription:
    """Describe a clean, complete repository for Versioningit."""
    del params
    repository = Path(project_dir)
    _validate_repository(repository)
    head = _git(repository, "rev-parse", "HEAD")
    revision = _git(repository, "rev-parse", "--short=7", "HEAD")
    tags = _release_tags(repository)

    if tags:
        _, tag = max(tags)
        tag_commit = _git(repository, "rev-parse", f"{tag}^{{commit}}")
        distance = int(_git(repository, "rev-list", "--count", f"{tag}..HEAD"))
        state = "exact" if head == tag_commit else "distance"
    else:
        tag = "0.0.0"
        distance = int(_git(repository, "rev-list", "--count", "HEAD"))
        state = "distance"

    return VCSDescription(
        tag=tag,
        state=state,
        branch=_branch(repository),
        fields={
            "distance": distance,
            "rev": revision,
            "revision": head,
            "vcs": "g",
            "vcs_name": "git",
        },
    )


def next_release(*, version: str, branch: str | None, params: dict[str, object]) -> str:
    """Advance the configured semantic release component."""
    del branch
    release_line = params.get("release-line")
    if release_line not in RELEASE_LINES:
        raise ValueError("release-line must be one of: patch, minor, major")
    if RELEASE_TAG.fullmatch(version) is None:
        raise ValueError(f"base version must be canonical X.Y.Z, got {version!r}")

    major, minor, patch = (int(component) for component in version.split("."))
    if release_line == "patch":
        patch += 1
    elif release_line == "minor":
        minor, patch = minor + 1, 0
    else:
        major, minor, patch = major + 1, 0, 0
    return f"{major}.{minor}.{patch}"
