"""The README '## Layout' block must name every top-level package module."""

import fnmatch
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / "src" / "hmc_mcp"
LAYOUT_HEADING = "## Layout"
ENTRY = re.compile(r"^\s+(\S+\.py)")


def _layout_block(readme: str) -> str:
    """Return the fenced code block that follows the '## Layout' heading."""
    _, _, after = readme.partition(f"\n{LAYOUT_HEADING}\n")
    assert after, f"README.md has no '{LAYOUT_HEADING}' section"
    fences = after.split("```")
    assert len(fences) >= 3, f"'{LAYOUT_HEADING}' is not followed by a code block"
    return fences[1]


def _layout_entries(readme: str) -> list[str]:
    """Return the filename patterns the layout block lists, in order."""
    return [
        match.group(1)
        for line in _layout_block(readme).splitlines()
        if (match := ENTRY.match(line))
    ]


def _module_names(package: Path) -> list[str]:
    return sorted(path.name for path in package.glob("*.py"))


def _uncovered(module_names: list[str], entries: list[str]) -> list[str]:
    """Return the module names no layout entry names or matches as a glob."""
    return [
        name
        for name in module_names
        if not any(fnmatch.fnmatch(name, entry) for entry in entries)
    ]


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def test_layout_block_lists_filenames_and_globs() -> None:
    """A parse that silently found nothing would make the coverage check vacuous."""
    entries = _layout_entries(_readme())

    assert "client_*.py" in entries
    assert "config.py" in entries
    assert len(entries) > 20


def test_every_module_has_a_layout_entry() -> None:
    entries = _layout_entries(_readme())
    modules = _module_names(PACKAGE)

    assert modules, f"no modules found under {PACKAGE}"
    uncovered = _uncovered(modules, entries)

    assert uncovered == [], (
        f"'{LAYOUT_HEADING}' names no entry covering: {', '.join(uncovered)}"
    )


def test_a_module_no_entry_names_is_reported(tmp_path: Path) -> None:
    """Adding a top-level module without a layout entry must fail the check."""
    package = tmp_path / "hmc_mcp"
    package.mkdir()
    (package / "client_lpars.py").touch()
    (package / "brand_new_module.py").touch()

    uncovered = _uncovered(_module_names(package), _layout_entries(_readme()))

    assert uncovered == ["brand_new_module.py"]
