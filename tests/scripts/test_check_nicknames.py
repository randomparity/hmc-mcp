"""Tests for the nicknames guardrail (scripts/check_nicknames.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).parents[2] / "scripts" / "check_nicknames.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
       "check_nicknames", MODULE_PATH
)
assert MODULE_SPEC is not None
assert MODULE_SPEC.loader is not None
check_nicknames = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(check_nicknames)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_passes_on_valid_fixture() -> None:
    """The committed fixture is well-formed; exit 0."""
    assert check_nicknames.main([]) == 0


def test_dangling_target_fails(tmp_path, capsys) -> None:
    """A nickname whose target is not a profile key fails."""
    path = _write(
         tmp_path,
          'default_profile = "prod"\n\n[profiles.prod]\nhost = "h"\nuser = "u"\n\n[nicknames]\nbig-iron = "does-not-exist"\n',
       )
    assert check_nicknames.main(["--config", str(path)]) == 1
    assert "does-not-exist" in capsys.readouterr().err


def test_collision_with_profile_fails(tmp_path, capsys) -> None:
    """A nickname key that shadows a profile key fails."""
    path = _write(
         tmp_path,
          'default_profile = "prod"\n\n[profiles.prod]\nhost = "h"\nuser = "u"\n\n[nicknames]\nprod = "prod"\n',
       )
    assert check_nicknames.main(["--config", str(path)]) == 1
    assert "collides" in capsys.readouterr().err


def test_chained_target_fails(tmp_path, capsys) -> None:
    """A target that is itself a nickname key (a chain) fails."""
    path = _write(
         tmp_path,
          'default_profile = "prod"\n\n[profiles.prod]\nhost = "h"\nuser = "u"\n\n[nicknames]\na = "b"\nb = "prod"\n',
       )
    assert check_nicknames.main(["--config", str(path)]) == 1
    assert "chained" in capsys.readouterr().err


def test_malformed_non_string_target_fails(tmp_path, capsys) -> None:
    """A nickname with a non-string target fails."""
    path = _write(
         tmp_path,
          'default_profile = "prod"\n\n[profiles.prod]\nhost = "h"\nuser = "u"\n\n[nicknames]\nbig-iron = 42\n',
       )
    assert check_nicknames.main(["--config", str(path)]) == 1
    err = capsys.readouterr().err
    assert "profile-key string" in err


def test_non_table_nicknames_fails(tmp_path, capsys) -> None:
    """A nicknames value that is not a table fails."""
    path = _write(
         tmp_path,
          'default_profile = "prod"\nnicknames = "not-a-table"\n\n[profiles.prod]\nhost = "h"\nuser = "u"\n',
       )
    assert check_nicknames.main(["--config", str(path)]) == 1
    assert "must be a table" in capsys.readouterr().err


def test_missing_config_reports_error(tmp_path, capsys) -> None:
    """A missing config path exits 1 with a clear message."""
    assert check_nicknames.main(["--config", str(tmp_path / "nope.toml")]) == 1
    assert "not found" in capsys.readouterr().err


def test_valid_nicknames_pass(tmp_path, capsys) -> None:
    """A well-formed nicknames table passes with an OK message."""
    path = _write(
         tmp_path,
          'default_profile = "prod"\n\n[profiles.prod]\nhost = "h"\nuser = "u"\n\n[profiles.stg]\nhost = "s"\nuser = "u"\n\n[nicknames]\nbig-iron = "prod"\nstaging = "stg"\n',
       )
    assert check_nicknames.main(["--config", str(path)]) == 0
    assert "well-formed" in capsys.readouterr().out
