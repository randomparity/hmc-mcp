"""ADR 0092 §3's classification tables are only authoritative if their citations hold.

Every row of §3 names an operation and cites the `file.py:line` where it is defined.
§3 states the obligation in prose — "a PR that moves a definition cited in §3, §4 or
§5 re-verifies that `file:line` in the same change" — and nothing enforced it, so rows
drifted whenever a function was added above a cited line. This module is that
enforcement for §3: it parses every row, resolves the cited line with `ast`, and
asserts the definition starting there carries exactly the name the row claims.

Identity is checked against the parsed definition's name, not against the text of the
line, so a row naming `capture_lpar_console` cannot be satisfied by a line defining
`hmc_capture_lpar_console`.
"""

import ast
import re
from functools import cache
from pathlib import Path
from typing import NamedTuple

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADR_PATH = (
    _REPO_ROOT / "docs" / "adr" / "0092-uniform-lpar-ownership-authorization-rule.md"
)
_SOURCE_ROOT = _REPO_ROOT / "src" / "hmc_mcp"

_SECTION_HEADING = "### 3. Classification"
_NEXT_SECTION_HEADING = "### 4."

# `hmc_backup_lpar_profiles` (`server_tools/profiles.py:35`, `:86`) — a bare `:86` continues
# the file named by the citation before it in the same row.
_CITATION = re.compile(r"`(?:([\w./]+\.py))?:(\d+)`")
_BACKTICKED = re.compile(r"`([^`]+)`")

# §3 carried 41 checkable rows when this guard landed. The floor catches a parser that
# silently stops matching; §6 only ever adds rows, so it never needs lowering.
_MINIMUM_ROWS = 40

# The one §3 row whose subject is not a Python definition: it names a CLI command, and
# cites the line inside the command body that writes without an ownership check.
_UNCHECKED_ROWS = frozenset({"`hmc lpar modify` (CLI)"})


class Citation(NamedTuple):
    """One §3 row's claim: `symbol` is defined at `path`:`line`."""

    symbol: str
    path: Path
    line: int

    def __str__(self) -> str:
        return f"{self.symbol}@{self.path.name}:{self.line}"


def _section_3_lines() -> list[str]:
    lines = _ADR_PATH.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(_SECTION_HEADING)]
    assert len(starts) == 1, f"{_ADR_PATH.name} has {len(starts)} '§3' headings"
    ends = [
        i
        for i, line in enumerate(lines)
        if i > starts[0] and line.startswith(_NEXT_SECTION_HEADING)
    ]
    assert ends, f"{_ADR_PATH.name} has no §4 heading to bound §3"
    return lines[starts[0] : ends[0]]


def _row_citations(cells: list[str]) -> list[tuple[str, int]]:
    """Return (file, line) for a row, resolving bare `:N` against the preceding file.

    §3.1–§3.3 put the location in the second cell; §3.4's registers put it inline in
    the first. The remaining cells hold guard-call sites and prose, which cite lines
    that are not definitions, so only the cell that carries the row's own location is
    read.
    """
    source = next((cell for cell in cells[:2] if _CITATION.search(cell)), "")
    resolved: list[tuple[str, int]] = []
    for name, line in _CITATION.findall(source):
        if not name:
            assert resolved, f"bare line citation with no file to continue: {source!r}"
            name = resolved[-1][0]
        resolved.append((name, int(line)))
    return resolved


def _parse_citations() -> list[Citation]:
    citations: list[Citation] = []
    for line in _section_3_lines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        symbols = [
            token for token in _BACKTICKED.findall(cells[0]) if token.isidentifier()
        ]
        if not symbols:
            # Header and separator rows cite nothing, so they drop out here. A row
            # that cites a location must name a definition, or it would leave the
            # check silently — the drift this module exists to catch.
            assert not _CITATION.search(line) or cells[0] in _UNCHECKED_ROWS, (
                f"§3 row cites a location but names no Python definition: {line}"
            )
            continue
        located = _row_citations(cells)
        assert len(symbols) == len(located), (
            f"§3 row names {len(symbols)} symbols but cites {len(located)} locations: "
            f"{line}"
        )
        citations.extend(
            Citation(symbol, _SOURCE_ROOT / file, line_number)
            for symbol, (file, line_number) in zip(symbols, located)
        )
    return citations


@cache
def _definitions(path: Path) -> dict[int, str]:
    """Map each definition's starting line to its name (decorators excluded)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.lineno: node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }


try:
    _CITATIONS = _parse_citations()
    _PARSE_FAILURE: str | None = None
except AssertionError as exc:
    # Parsing runs at import because `parametrize` needs the rows at collection
    # time. Carrying the failure into a test keeps a malformed §3 from aborting
    # collection for the whole suite.
    _CITATIONS = []
    _PARSE_FAILURE = str(exc)


def test_section_3_rows_are_all_parsed() -> None:
    assert _PARSE_FAILURE is None, _PARSE_FAILURE
    assert len(_CITATIONS) >= _MINIMUM_ROWS, (
        f"parsed only {len(_CITATIONS)} §3 citations; the table format changed and "
        "this guard is no longer reading it"
    )


@pytest.mark.parametrize("citation", _CITATIONS, ids=str)
def test_section_3_citation_names_the_definition_at_that_line(
    citation: Citation,
) -> None:
    assert citation.path.is_file(), (
        f"ADR 0092 §3 cites {citation.path.name}, which does not exist"
    )
    defined = _definitions(citation.path).get(citation.line)
    assert defined == citation.symbol, (
        f"ADR 0092 §3 cites `{citation.symbol}` at {citation.path.name}:"
        f"{citation.line}, but that line "
        + (f"defines `{defined}`" if defined else "starts no definition")
        + " — re-verify the row against the source (§3's citation obligation)"
    )
