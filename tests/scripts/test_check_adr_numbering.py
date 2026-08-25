"""Tests for the ADR numbering guardrail (scripts/check_adr_numbering.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).parents[2] / "scripts" / "check_adr_numbering.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "check_adr_numbering", MODULE_PATH
)
assert MODULE_SPEC is not None
assert MODULE_SPEC.loader is not None
check_adr_numbering = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(check_adr_numbering)


def _records(tmp_path: Path, *names: str) -> Path:
    adr_dir = tmp_path / "adr"
    adr_dir.mkdir()
    for name in names:
        (adr_dir / name).write_text("# record\n", encoding="utf-8")
    return adr_dir


def test_passes_on_the_committed_records(capsys) -> None:
    """The committed docs/adr/ numbers are unique; exit 0."""
    assert check_adr_numbering.main([]) == 0
    assert "unique" in capsys.readouterr().out


def test_duplicate_number_fails(tmp_path, capsys) -> None:
    """Two records sharing a number fail and both filenames are named."""
    adr_dir = _records(tmp_path, "0001-first.md", "0002-second.md", "0002-third.md")

    assert check_adr_numbering.main(["--adr-dir", str(adr_dir)]) == 1
    err = capsys.readouterr().err
    assert "0002" in err
    assert "0002-second.md" in err
    assert "0002-third.md" in err
    # The unique number is not reported as a problem.
    assert "0001-first.md" not in err


def test_three_records_sharing_a_number_all_appear(tmp_path, capsys) -> None:
    """A duplicate report lists every colliding record, not just the first two."""
    adr_dir = _records(tmp_path, "0007-a.md", "0007-b.md", "0007-c.md")

    assert check_adr_numbering.main(["--adr-dir", str(adr_dir)]) == 1
    err = capsys.readouterr().err
    assert "0007-a.md" in err
    assert "0007-b.md" in err
    assert "0007-c.md" in err


def test_gaps_are_legal(tmp_path, capsys) -> None:
    """Uniqueness is enforced, contiguity is not: a gap passes."""
    adr_dir = _records(tmp_path, "0001-first.md", "0004-fourth.md", "0099-last.md")

    assert check_adr_numbering.main(["--adr-dir", str(adr_dir)]) == 0
    assert "unique" in capsys.readouterr().out


def test_malformed_filename_fails(tmp_path, capsys) -> None:
    """A record whose name carries no four-digit number fails."""
    adr_dir = _records(tmp_path, "0001-first.md", "notes.md")

    assert check_adr_numbering.main(["--adr-dir", str(adr_dir)]) == 1
    err = capsys.readouterr().err
    assert "notes.md" in err
    assert "NNNN-" in err


def test_short_number_is_malformed(tmp_path, capsys) -> None:
    """Three digits is not a record number: it would hide a collision with 0054."""
    adr_dir = _records(tmp_path, "054-short.md")

    assert check_adr_numbering.main(["--adr-dir", str(adr_dir)]) == 1
    assert "054-short.md" in capsys.readouterr().err


def test_uppercase_slug_is_malformed(tmp_path, capsys) -> None:
    """Record slugs are lowercase kebab-case."""
    adr_dir = _records(tmp_path, "0001-Not-Kebab.md")

    assert check_adr_numbering.main(["--adr-dir", str(adr_dir)]) == 1
    assert "0001-Not-Kebab.md" in capsys.readouterr().err


def test_missing_slug_is_malformed(tmp_path, capsys) -> None:
    """A bare number with no slug is not a record filename."""
    adr_dir = _records(tmp_path, "0001.md")

    assert check_adr_numbering.main(["--adr-dir", str(adr_dir)]) == 1
    assert "0001.md" in capsys.readouterr().err


def test_missing_directory_fails(tmp_path, capsys) -> None:
    """A moved or renamed record directory fails rather than passing vacuously."""
    assert check_adr_numbering.main(["--adr-dir", str(tmp_path / "nope")]) == 1
    assert "not found" in capsys.readouterr().err


def test_empty_directory_fails(tmp_path, capsys) -> None:
    """An empty record directory means the glob found nothing to check."""
    adr_dir = _records(tmp_path)

    assert check_adr_numbering.main(["--adr-dir", str(adr_dir)]) == 1
    assert "no decision records" in capsys.readouterr().err


def test_non_markdown_files_are_ignored(tmp_path, capsys) -> None:
    """The guard owns the records, not every file that lands beside them."""
    adr_dir = _records(tmp_path, "0001-first.md")
    (adr_dir / "diagram.png").write_bytes(b"")

    assert check_adr_numbering.main(["--adr-dir", str(adr_dir)]) == 0
    assert "diagram.png" not in capsys.readouterr().out
