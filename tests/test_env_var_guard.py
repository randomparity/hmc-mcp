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


ROOT = Path(__file__).parents[1]
GUARD = ROOT / "scripts" / "check_env_vars.py"
DOC = ROOT / "docs" / "environment-variables.md"

# The complete authoritative set of HMC_* env vars in HMCConfig.
# This list must stay in sync with src/hmc_mcp/config.py.
EXPECTED_ENV_VARS = {
    "HMC_HOST",
    "HMC_PORT",
    "HMC_USER",
    "HMC_PASSWORD",
    "HMC_SSH_KEY_FILE",
    "HMC_VERIFY_SSL",
    "HMC_TIMEOUT",
    "HMC_SSH_TIMEOUT",
    "HMC_AUDIT_MEMENTO",
    "HMC_SCHEMA_VERSION",
}


def test_guard_script_exists() -> None:
    assert GUARD.exists(), f"Guard script not found: {GUARD}"


def test_env_var_doc_exists() -> None:
    assert DOC.exists(), f"Environment variable doc not found: {DOC}"


def test_env_var_doc_lists_all_expected_vars() -> None:
    """Every HMC_* var in EXPECTED_ENV_VARS must appear in the doc."""
    doc_text = DOC.read_text()
    missing = [var for var in EXPECTED_ENV_VARS if var not in doc_text]
    assert not missing, f"Doc is missing env vars: {missing}"


def test_guard_passes_on_complete_doc(tmp_path: Path) -> None:
    """Guard exits 0 when the doc contains all env var names."""
    doc = tmp_path / "environment-variables.md"
    # Write a doc that mentions every expected var.
    doc.write_text("\n".join(f"| `{v}` | x |" for v in EXPECTED_ENV_VARS))

    result = subprocess.run(
        [sys.executable, str(GUARD), "--doc", str(doc)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_guard_fails_on_incomplete_doc(tmp_path: Path) -> None:
    """Guard exits 1 and names the missing var when any var is absent."""
    doc = tmp_path / "environment-variables.md"
    # Write a doc that mentions every var *except* HMC_TIMEOUT.
    vars_minus_timeout = EXPECTED_ENV_VARS - {"HMC_TIMEOUT"}
    doc.write_text("\n".join(f"| `{v}` | x |" for v in vars_minus_timeout))

    result = subprocess.run(
        [sys.executable, str(GUARD), "--doc", str(doc)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "HMC_TIMEOUT" in result.stdout + result.stderr


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
