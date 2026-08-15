from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
versioning = importlib.import_module("versioning")
describe_git = versioning.describe_git
next_release = versioning.next_release


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit(repository: Path, message: str) -> None:
    tracked = repository / "tracked.txt"
    tracked.write_text(f"{message}\n", encoding="utf-8")
    git(repository, "add", "tracked.txt")
    git(repository, "commit", "-m", message)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "--initial-branch=main")
    git(tmp_path, "config", "user.email", "tests@example.invalid")
    git(tmp_path, "config", "user.name", "Version Tests")
    commit(tmp_path, "first")
    return tmp_path


def test_exact_release_tag_is_clean(repository: Path) -> None:
    git(repository, "tag", "1.2.3")

    description = describe_git(project_dir=repository, params={})

    assert description.tag == "1.2.3"
    assert description.state == "exact"
    assert description.fields["distance"] == 0


def test_annotated_release_tag_is_equivalent(repository: Path) -> None:
    git(repository, "tag", "-a", "1.2.3", "-m", "release")

    description = describe_git(project_dir=repository, params={})

    assert description.tag == "1.2.3"
    assert description.state == "exact"


def test_highest_reachable_version_is_base_not_nearest_tag(repository: Path) -> None:
    git(repository, "tag", "2.0.0")
    commit(repository, "second")
    git(repository, "tag", "1.9.0")
    commit(repository, "third")

    description = describe_git(project_dir=repository, params={})

    assert description.tag == "2.0.0"
    assert description.state == "distance"
    assert description.fields["distance"] == 2


def test_lower_version_tag_on_head_does_not_become_release(repository: Path) -> None:
    git(repository, "tag", "2.0.0")
    commit(repository, "second")
    git(repository, "tag", "1.9.0")

    description = describe_git(project_dir=repository, params={})

    assert description.tag == "2.0.0"
    assert description.state == "distance"


def test_development_description_uses_unique_revision(repository: Path) -> None:
    git(repository, "tag", "1.2.3")
    commit(repository, "second")

    description = describe_git(project_dir=repository, params={})

    assert description.fields["distance"] == 1
    assert description.fields["rev"] == git(
        repository, "rev-parse", "--short=7", "HEAD"
    )
    assert len(description.fields["rev"]) >= 7
    assert git(repository, "rev-parse", description.fields["rev"]) == git(
        repository, "rev-parse", "HEAD"
    )


def test_no_tag_history_uses_semantic_origin(repository: Path) -> None:
    commit(repository, "second")

    description = describe_git(project_dir=repository, params={})

    assert description.tag == "0.0.0"
    assert description.state == "distance"
    assert description.fields["distance"] == 2


@pytest.mark.parametrize("tag", ["v1.2.3", "1.2", "1.2.3rc1", "01.2.3", "1.02.3"])
def test_noncanonical_tags_are_ignored(repository: Path, tag: str) -> None:
    git(repository, "tag", tag)

    description = describe_git(project_dir=repository, params={})

    assert description.tag == "0.0.0"


@pytest.mark.parametrize("dirty_kind", ["staged", "unstaged", "untracked"])
def test_dirty_repository_fails_actionably(repository: Path, dirty_kind: str) -> None:
    if dirty_kind == "staged":
        (repository / "staged.txt").write_text("staged\n", encoding="utf-8")
        git(repository, "add", "staged.txt")
    elif dirty_kind == "unstaged":
        (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
    else:
        (repository / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"dirty.*commit or clean"):
        describe_git(project_dir=repository, params={})


def test_ignored_files_do_not_make_repository_dirty(repository: Path) -> None:
    (repository / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    git(repository, "add", ".gitignore")
    git(repository, "commit", "-m", "ignore fixture")
    (repository / "ignored.txt").write_text("ignored\n", encoding="utf-8")

    description = describe_git(project_dir=repository, params={})

    assert description.state == "distance"


def test_shallow_repository_fails_actionably(repository: Path, tmp_path: Path) -> None:
    commit(repository, "second")
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "--quiet", "--depth=1", repository.as_uri(), str(shallow)],
        check=True,
    )
    assert git(shallow, "rev-parse", "--is-shallow-repository") == "true"

    with pytest.raises(RuntimeError, match=r"shallow.*fetch full history"):
        describe_git(project_dir=shallow, params={})


@pytest.mark.parametrize(
    ("release_line", "expected"),
    [("patch", "1.2.4"), ("minor", "1.3.0"), ("major", "2.0.0")],
)
def test_next_release_bumps_selected_component(
    release_line: str, expected: str
) -> None:
    assert (
        next_release(
            version="1.2.3",
            branch="main",
            params={"release-line": release_line},
        )
        == expected
    )


@pytest.mark.parametrize(
    ("release_line", "expected"),
    [("patch", "0.0.1"), ("minor", "0.1.0"), ("major", "1.0.0")],
)
def test_next_release_bumps_tagless_origin(release_line: str, expected: str) -> None:
    assert (
        next_release(
            version="0.0.0",
            branch=None,
            params={"release-line": release_line},
        )
        == expected
    )


@pytest.mark.parametrize("params", [{}, {"release-line": "feature"}])
def test_next_release_rejects_missing_or_invalid_selector(
    params: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match=r"release-line.*patch.*minor.*major"):
        next_release(version="1.2.3", branch=None, params=params)
