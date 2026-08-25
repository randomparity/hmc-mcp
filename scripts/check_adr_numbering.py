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

Usage:
    python scripts/check_adr_numbering.py [--adr-dir <path>]

Default record directory: docs/adr (relative to the repo root, the parent of
the directory containing this script).

Exits 0 when every record number is unique; exits 1 and prints the offending
filenames when any check fails.
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


def _validate(adr_dir: Path) -> list[str]:
    """Return failure messages; empty when every record number is unique."""
    errors: list[str] = []
    by_number: dict[str, list[str]] = defaultdict(list)

    records = sorted(path.name for path in adr_dir.glob("*.md"))
    if not records:
        return [f"no decision records found in {adr_dir}"]

    for name in records:
        match = _RECORD_NAME.match(name)
        if match is None:
            errors.append(
                f"{name}: not a record filename (expected NNNN-lowercase-slug.md)"
            )
            continue
        by_number[match[1]].append(name)

    for number, names in sorted(by_number.items()):
        if len(names) > 1:
            errors.append(f"number {number} is used by {len(names)} records: " + ", ".join(names))

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

    errors = _validate(adr_dir)
    if errors:
        print(f"ERROR: ADR numbering check failed for {adr_dir}:", file=sys.stderr)
        for err in errors:
            print(f"     {err}", file=sys.stderr)
        return 1

    count = len(list(adr_dir.glob("*.md")))
    print(f"OK: {count} decision records in {adr_dir} have unique numbers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
