"""Tests for the environment-variable documentation guard.

The guard script (scripts/check_env_vars.py) must:
  - Exit 0 when every HMC_* env var in HMCConfig is documented.
  - Exit 1 and name missing vars when any var is absent from the doc.
  - Be callable as ``uv run python scripts/check_env_vars.py``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from hmc_mcp.config import HMCConfig  # noqa: E402


ROOT = Path(__file__).parents[1]
GUARD = ROOT / "scripts" / "check_env_vars.py"
DOC = ROOT / "docs" / "environment-variables.md"

# Derived from the live HMCConfig — stays in sync automatically.
_PREFIX = HMCConfig.model_config.get("env_prefix", "")
EXPECTED_ENV_VARS = {_PREFIX + f.upper() for f in HMCConfig.model_fields}


def test_guard_script_exists() -> None:
    assert GUARD.exists(), f"Guard script not found: {GUARD}"


def test_env_var_doc_exists() -> None:
    assert DOC.exists(), f"Environment variable doc not found: {DOC}"


def test_env_var_doc_lists_all_expected_vars() -> None:
    """Every HMC_* var in EXPECTED_ENV_VARS must appear in the doc."""
    doc_text = DOC.read_text()
    missing = [var for var in EXPECTED_ENV_VARS if var not in doc_text]
    assert not missing, f"Doc is missing env vars: {missing}"


def _make_doc(vars_to_include: set[str]) -> str:
    """Build a minimal env-var doc with a ## Reference section."""
    rows = "\n".join(f"| `{v}` | string | - | desc |" for v in vars_to_include)
    return f"# Environment Variables\n\n## Reference\n\n{rows}\n\n## Notes\n\nsome prose\n"


def test_guard_passes_on_complete_doc(tmp_path: Path) -> None:
    """Guard exits 0 when the Reference table contains all env var names."""
    doc = tmp_path / "environment-variables.md"
    doc.write_text(_make_doc(EXPECTED_ENV_VARS))

    result = subprocess.run(
        [sys.executable, str(GUARD), "--doc", str(doc)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_guard_fails_on_incomplete_doc(tmp_path: Path) -> None:
    """Guard exits 1 and names the missing var when any var is absent."""
    doc = tmp_path / "environment-variables.md"
    # Write a doc that includes every var *except* HMC_TIMEOUT.
    vars_minus_timeout = EXPECTED_ENV_VARS - {"HMC_TIMEOUT"}
    doc.write_text(_make_doc(vars_minus_timeout))

    result = subprocess.run(
        [sys.executable, str(GUARD), "--doc", str(doc)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "HMC_TIMEOUT" in result.stdout + result.stderr


def test_guard_ignores_vars_only_in_prose(tmp_path: Path) -> None:
    """Guard fails if a var appears only in prose outside the Reference table."""
    doc = tmp_path / "environment-variables.md"
    # All vars in the Reference table except HMC_TIMEOUT, which appears only
    # in the Notes prose section (outside ## Reference).
    vars_minus_timeout = EXPECTED_ENV_VARS - {"HMC_TIMEOUT"}
    body = _make_doc(vars_minus_timeout)
    body += "\nSee also HMC_TIMEOUT for more details.\n"  # prose mention only
    doc.write_text(body)

    result = subprocess.run(
        [sys.executable, str(GUARD), "--doc", str(doc)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "HMC_TIMEOUT" in result.stdout + result.stderr


def test_guard_ignores_vars_in_prose_inside_reference_section(tmp_path: Path) -> None:
    """Guard fails if a var appears only in a prose line inside ## Reference."""
    doc = tmp_path / "environment-variables.md"
    # All vars in the Reference table except HMC_TIMEOUT, which appears as a
    # prose comment line (not a | row) inside the ## Reference section.
    vars_minus_timeout = EXPECTED_ENV_VARS - {"HMC_TIMEOUT"}
    rows = "\n".join(f"| `{v}` | string | - | desc |" for v in vars_minus_timeout)
    body = (
        "# Environment Variables\n\n"
        "## Reference\n\n"
        "Note: HMC_TIMEOUT was added in v0.2.\n\n"  # prose line inside the section
        f"{rows}\n\n"
        "## Notes\n\nsome prose\n"
    )
    doc.write_text(body)

    result = subprocess.run(
        [sys.executable, str(GUARD), "--doc", str(doc)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "HMC_TIMEOUT" in result.stdout + result.stderr


def test_guard_fails_when_reference_section_missing(tmp_path: Path) -> None:
    """Guard exits 1 when the doc has no ## Reference section."""
    doc = tmp_path / "environment-variables.md"
    # Write a doc with all vars but no ## Reference heading.
    rows = "\n".join(f"| `{v}` | string | - | desc |" for v in EXPECTED_ENV_VARS)
    doc.write_text(f"# Environment Variables\n\n{rows}\n")

    result = subprocess.run(
        [sys.executable, str(GUARD), "--doc", str(doc)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Reference" in result.stdout + result.stderr


def test_guard_uses_default_doc_path_when_no_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard with no --doc argument reads docs/environment-variables.md."""
    monkeypatch.chdir(ROOT)
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, f"Guard failed:\n{result.stdout}\n{result.stderr}"
