"""Reject repository-visible ``conftest.py`` files outside the canonical path."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CANONICAL_CONFTEST = "tests/conftest.py"


def _repository_files(repo_root: Path) -> tuple[str, ...]:
    """Return tracked and unignored untracked repository paths."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f"cannot inspect repository paths in {repo_root}: {error}") from error
    return tuple(os.fsdecode(path) for path in result.stdout.split(b"\0") if path)


def unexpected_conftests(repo_root: Path) -> tuple[str, ...]:
    """Return repository conftest paths that violate the canonical layout."""
    return tuple(
        sorted(
            path
            for path in _repository_files(repo_root)
            if PurePosixPath(path).name == "conftest.py"
            and path != _CANONICAL_CONFTEST
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT)
    args = parser.parse_args(argv)

    try:
        unexpected = unexpected_conftests(args.repo_root)
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if unexpected:
        print("ERROR: repository test layout is invalid:", file=sys.stderr)
        for path in unexpected:
            print(f"     {path}", file=sys.stderr)
        print(
            "     tests/conftest.py is the only permitted conftest.py; "
            "another file can shadow its bare imports during pytest collection",
            file=sys.stderr,
        )
        return 1

    print("OK: repository uses the canonical test layout.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
