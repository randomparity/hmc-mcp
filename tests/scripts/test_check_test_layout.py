"""Contract tests for the repository test-layout guard."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

MODULE_PATH = Path(__file__).parents[2] / "scripts" / "check_test_layout.py"
MODULE_SPEC = importlib.util.spec_from_file_location("check_test_layout", MODULE_PATH)
assert MODULE_SPEC is not None
assert MODULE_SPEC.loader is not None
check_test_layout = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(check_test_layout)


def _repository(tmp_path: Path, *conftests: str) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for relative_path in conftests:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# test fixture\n", encoding="utf-8")
    return tmp_path


def test_accepts_only_canonical_conftest(tmp_path: Path, capsys) -> None:
    root = _repository(tmp_path, "tests/conftest.py")

    assert check_test_layout.main(["--repo-root", str(root)]) == 0
    assert "canonical test layout" in capsys.readouterr().out


def test_rejects_nested_conftest(tmp_path: Path, capsys) -> None:
    root = _repository(tmp_path, "tests/conftest.py", "tests/app/conftest.py")
    subprocess.run(["git", "add", "tests/app/conftest.py"], cwd=root, check=True)

    assert check_test_layout.main(["--repo-root", str(root)]) == 1
    error = capsys.readouterr().err
    assert "tests/app/conftest.py" in error
    assert "tests/conftest.py is the only permitted conftest.py" in error


def test_rejects_second_root_conftest(tmp_path: Path, capsys) -> None:
    root = _repository(tmp_path, "tests/conftest.py", "conftest.py")

    assert check_test_layout.main(["--repo-root", str(root)]) == 1
    error = capsys.readouterr().err
    assert "conftest.py" in error
    assert "tests/conftest.py is the only permitted conftest.py" in error
