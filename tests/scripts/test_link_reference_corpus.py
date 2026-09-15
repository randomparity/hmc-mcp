"""Tests for scripts/link_reference_corpus.py.

Covers the pure worktree-detection and degrade-to-no-op logic in
``sync_reference_corpus`` (issue #801), plus ``main()``'s never-fail
announcement of a git-resolution failure.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[2] / "scripts" / "link_reference_corpus.py"
MODULE_SPEC = importlib.util.spec_from_file_location("link_reference_corpus", MODULE_PATH)
assert MODULE_SPEC is not None
assert MODULE_SPEC.loader is not None
link_reference_corpus = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(link_reference_corpus)

REFERENCE_PATHS = link_reference_corpus.REFERENCE_PATHS
main = link_reference_corpus.main
sync_reference_corpus = link_reference_corpus.sync_reference_corpus


def _touch_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("content\n")


def _touch_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "placeholder.md").write_text("content\n")


def test_main_checkout_is_a_no_op(tmp_path: Path) -> None:
    """When worktree_root == main_root, nothing is created and nothing is reported."""
    root = tmp_path / "main"
    root.mkdir()

    messages = sync_reference_corpus(root, root)

    assert messages == []
    for rel in REFERENCE_PATHS:
        assert not (root / rel).exists()


def test_links_present_targets_from_main_checkout(tmp_path: Path) -> None:
    """Each target present in the main checkout is relative-symlinked into the worktree."""
    main_root = tmp_path / "main"
    worktree_root = tmp_path / "worktrees" / "feat"
    worktree_root.mkdir(parents=True)
    _touch_dir(main_root / "docs" / "refs")
    _touch_file(main_root / "AGENTS.local.md")

    messages = sync_reference_corpus(worktree_root, main_root)

    assert len(messages) == 2
    linked_dir = worktree_root / "docs" / "refs"
    linked_file = worktree_root / "AGENTS.local.md"
    assert linked_dir.is_symlink()
    assert linked_file.is_symlink()
    assert linked_dir.resolve() == (main_root / "docs" / "refs").resolve()
    assert linked_file.resolve() == (main_root / "AGENTS.local.md").resolve()
    assert (linked_dir / "placeholder.md").read_text() == "content\n"
    assert linked_file.read_text() == "content\n"
    assert not Path(linked_dir.readlink()).is_absolute()
    assert not Path(linked_file.readlink()).is_absolute()


def test_announces_missing_targets_without_failing(tmp_path: Path) -> None:
    """When the main checkout lacks a target, nothing is created and it is announced."""
    main_root = tmp_path / "main"
    main_root.mkdir()
    worktree_root = tmp_path / "worktrees" / "feat"
    worktree_root.mkdir(parents=True)

    messages = sync_reference_corpus(worktree_root, main_root)

    assert len(messages) == 2
    assert all("not available" in message for message in messages)
    for rel in REFERENCE_PATHS:
        assert not (worktree_root / rel).exists()


def test_skips_existing_symlink(tmp_path: Path) -> None:
    """A path already symlinked is left untouched (idempotent, never clobbered)."""
    main_root = tmp_path / "main"
    worktree_root = tmp_path / "worktrees" / "feat"
    worktree_root.mkdir(parents=True)
    _touch_file(main_root / "AGENTS.local.md")
    existing_link = worktree_root / "AGENTS.local.md"
    existing_link.symlink_to(main_root / "AGENTS.local.md")
    original_target = existing_link.readlink()

    messages = sync_reference_corpus(worktree_root, main_root)

    assert not any("AGENTS.local.md" in message for message in messages)
    assert existing_link.readlink() == original_target


def test_skips_existing_real_path(tmp_path: Path) -> None:
    """A real file or directory already at the target is never clobbered."""
    main_root = tmp_path / "main"
    worktree_root = tmp_path / "worktrees" / "feat"
    worktree_root.mkdir(parents=True)
    _touch_dir(main_root / "docs" / "refs")
    real_dir = worktree_root / "docs" / "refs"
    _touch_dir(real_dir)
    (real_dir / "placeholder.md").write_text("worktree-local\n")

    messages = sync_reference_corpus(worktree_root, main_root)

    assert not any("docs/refs" in message for message in messages)
    assert not real_dir.is_symlink()
    assert (real_dir / "placeholder.md").read_text() == "worktree-local\n"


def test_git_resolution_failure_is_announced(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() announces a git-call failure and still exits 0, never swallowing it."""

    def _raise(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(128, ["git", "rev-parse", "--show-toplevel"])

    monkeypatch.setattr(subprocess, "run", _raise)

    exit_code = main()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "could not resolve" in captured.out
