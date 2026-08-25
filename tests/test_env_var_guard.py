"""Tests for the environment-variable documentation guard.

The guard script (scripts/check_env_vars.py) must:
  - Exit 0 when every HMC_* env var in HMCConfig is documented.
  - Exit 1 and name missing vars when any var is absent from the doc.
  - Be callable as ``uv run python scripts/check_env_vars.py``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from types import ModuleType
from pathlib import Path

import pytest

from hmc_mcp.config import HMCConfig


ROOT = Path(__file__).parents[1]
GUARD = ROOT / "scripts" / "check_env_vars.py"
DOC = ROOT / "docs" / "environment-variables.md"

# Derived from the live HMCConfig — stays in sync automatically.
_PREFIX = HMCConfig.model_config.get("env_prefix", "")
EXPECTED_ENV_VARS = {_PREFIX + f.upper() for f in HMCConfig.model_fields}


@pytest.fixture(scope="module")
def guard_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_env_vars", GUARD)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: The one Reference row that is not an HMCConfig field. HMC_PROFILE selects a
#: profile inside load_profile(); it never reaches the settings model. The
#: "Library Consumers" section of the doc states this, so it is pinned here.
NON_FIELD_ENV_VARS = {"HMC_PROFILE"}


def _reference_table_text() -> str:
    """Return the Markdown table rows inside the doc's ## Reference section.

    Same slicing the production guard uses, so this module and the guard agree
    on what "documented" means: a table row, not prose.
    """
    import re as _re

    doc_text = DOC.read_text()
    ref_match = _re.search(r"^## Reference\b", doc_text, _re.MULTILINE)
    assert ref_match, "docs/environment-variables.md must have a ## Reference section"
    next_section = _re.search(r"^## ", doc_text[ref_match.end() :], _re.MULTILINE)
    section_end = (
        ref_match.end() + next_section.start() if next_section else len(doc_text)
    )
    ref_section = doc_text[ref_match.start() : section_end]
    return "\n".join(
        ln for ln in ref_section.splitlines() if ln.strip().startswith("|")
    )


def test_guard_script_exists() -> None:
    assert GUARD.exists(), f"Guard script not found: {GUARD}"


def test_env_var_doc_exists() -> None:
    assert DOC.exists(), f"Environment variable doc not found: {DOC}"


def test_env_var_doc_lists_all_expected_vars() -> None:
    """Every HMC_* var must appear in a table row inside the ## Reference section.

    Uses the same lookup semantics as the production guard so this test and the
    guard stay logically consistent: a var in prose does not satisfy either.
    """
    import re as _re

    table_text = _reference_table_text()
    missing = [
        var
        for var in EXPECTED_ENV_VARS
        if not _re.search(rf"\b{_re.escape(var)}\b", table_text)
    ]
    assert not missing, f"Doc Reference table is missing env vars: {missing}"


def test_env_var_doc_lists_no_var_that_is_not_a_field() -> None:
    """The opposite direction: a row that no longer names a field must fail.

    The production guard only checks that every field has a row, so a var
    removed from HMCConfig leaves a documented row that lies about what the
    package reads. docs/environment-variables.md's "Library Consumers" section
    points at this table as the *exhaustive* field list, and a partial or
    over-broad list is the same trap issue #368 is about, so the claim is
    enforced here in both directions.
    """
    import re as _re

    documented = set(_re.findall(r"\bHMC_[A-Z0-9_]+\b", _reference_table_text()))

    assert documented == EXPECTED_ENV_VARS | NON_FIELD_ENV_VARS


def _make_doc(vars_to_include: set[str]) -> str:
    """Build a minimal env-var doc with a ## Reference section."""
    rows = "\n".join(f"| `{v}` | string | - | desc |" for v in vars_to_include)
    return (
        f"# Environment Variables\n\n## Reference\n\n{rows}\n\n## Notes\n\nsome prose\n"
    )


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


def test_guard_uses_default_doc_path_when_no_arg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard with no --doc argument reads docs/environment-variables.md."""
    monkeypatch.chdir(ROOT)
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, f"Guard failed:\n{result.stdout}\n{result.stderr}"


def test_main_reports_missing_doc_to_stderr(
    guard_module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing_doc = tmp_path / "absent.md"

    assert guard_module.main(["--doc", str(missing_doc)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == f"ERROR: doc not found: {missing_doc}\n"


def test_main_reports_missing_reference_to_stderr(
    guard_module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = tmp_path / "environment-variables.md"
    doc.write_text("# Environment Variables\n\n| `HMC_HOST` |\n")

    assert guard_module.main(["--doc", str(doc)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == (
        'ERROR: doc has no "## Reference" section; '
        "cannot verify documentation coverage.\n"
    )


def test_main_lists_missing_variables_in_stable_order(
    guard_module: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = tmp_path / "environment-variables.md"
    doc.write_text(_make_doc({"HMC_PRESENT"}))
    monkeypatch.setattr(
        guard_module,
        "_env_var_names",
        lambda: ["HMC_ZEBRA", "HMC_PRESENT", "HMC_ALPHA"],
    )

    assert guard_module.main(["--doc", str(doc)]) == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.index("  HMC_ALPHA\n") < output.out.index("  HMC_ZEBRA\n")
    assert f"Add them to {doc}" in output.out


def test_main_reports_success_count(
    guard_module: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = tmp_path / "environment-variables.md"
    doc.write_text(_make_doc({"HMC_HOST", "HMC_USER"}))
    monkeypatch.setattr(
        guard_module, "_env_var_names", lambda: ["HMC_USER", "HMC_HOST"]
    )

    assert guard_module.main(["--doc", str(doc)]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out == "OK: all 2 HMC_* env vars are documented.\n"
