#!/usr/bin/env python3
"""Guard: every decision record in ``docs/adr/`` must carry a unique number.

Records are cited by number from code comments, test docstrings, and other
records, so a number that identifies two files makes every citation of it
ambiguous. This guard asserts uniqueness only. Gaps are legal: a reserved
number that was never written is harmless, and enforcing contiguity would
forbid ever reserving one.

A filename that does not match ``NNNN-lowercase-kebab-slug.md`` fails too.
Without that, renaming a record out of the pattern would silently remove it
from the uniqueness check rather than reporting a problem.

A record must also announce its own number: the H1 it opens with has to carry
the number its filename carries. Renumbering a record is a rename plus a
heading edit, and a rename that lands without the heading edit leaves the file
citing itself by the number it no longer owns.

Usage:
    python scripts/check_adr_numbering.py [--adr-dir <path>]

Default record directory: docs/adr (relative to the repo root, the parent of
the directory containing this script).

Exits 0 when every record number is unique and matches its heading; exits 1 and
prints the offending filenames when any check fails.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

# Resolve the repo root relative to this script so the guard can be run from
# any working directory.
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_DEFAULT_ADR_DIR = _REPO_ROOT / "docs" / "adr"

_RECORD_NAME = re.compile(r"^(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")

# The heading spellings the committed records use: "# ADR 0099: <title>",
# "# 0034: <title>", and "# ADR-0025: <title>". All three are accepted; this
# guard checks the number, not the wording around it.
_HEADING_NUMBER = re.compile(r"^#\s*(?:ADR[\s-]+)?(\d{4})\b")


def _heading_number(path: Path) -> str | None:
    """Return the number the record's H1 announces, or None when it has none.

    The H1 is the first non-blank line, which is where every committed record
    puts it. Searching further down would let a heading inside a code fence, or
    a second-level heading, stand in for the one the record opens with.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        match = _HEADING_NUMBER.match(line)
        return match[1] if match else None
    return None


def _validate(adr_dir: Path, records: list[str]) -> list[str]:
    """Return failure messages; empty when every record passes every check."""
    errors: list[str] = []
    by_number: dict[str, list[str]] = defaultdict(list)

    for name in records:
        match = _RECORD_NAME.match(name)
        if match is None:
            errors.append(
                f"{name}: not a record filename (expected NNNN-lowercase-slug.md)"
            )
            continue
        number = match[1]
        by_number[number].append(name)

        heading = _heading_number(adr_dir / name)
        if heading is None:
            errors.append(
                f"{name}: no numbered H1 heading "
                "(expected '# ADR NNNN: <title>' as the first line)"
            )
        elif heading != number:
            errors.append(
                f"{name}: heading says {heading} but the filename says {number}"
            )

    for number, names in sorted(by_number.items()):
        if len(names) > 1:
            errors.append(
                f"number {number} is used by {len(names)} records: "
                + ", ".join(names)
            )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--adr-dir",
        type=Path,
        default=_DEFAULT_ADR_DIR,
        help=f"Directory holding the decision records (default: {_DEFAULT_ADR_DIR})",
    )
    args = parser.parse_args(argv)

    adr_dir: Path = args.adr_dir
    if not adr_dir.is_dir():
        print(f"ERROR: record directory not found: {adr_dir}", file=sys.stderr)
        return 1

    records = sorted(path.name for path in adr_dir.glob("*.md"))
    if not records:
        print(f"ERROR: no decision records found in {adr_dir}", file=sys.stderr)
        return 1

    errors = _validate(adr_dir, records)
    if errors:
        print(f"ERROR: ADR numbering check failed for {adr_dir}:", file=sys.stderr)
        for err in errors:
            print(f"     {err}", file=sys.stderr)
        return 1

    print(
        f"OK: {len(records)} decision records in {adr_dir} have unique numbers "
        "matching their headings."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
