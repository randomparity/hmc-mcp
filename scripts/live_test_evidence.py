"""Render a citable PASS/SKIP/FAIL matrix from a live-run results document.

PR #869's matrix was hand-copied from a terminal. It carried no commit, so
nothing contradicted it when the branch moved two weeks and the arm stopped
being able to import. A matrix that cannot be falsified is not evidence.

Usage:
    uv run --no-sync python scripts/live_test_evidence.py test-results-round2.json

Output is Markdown, meant to be pasted into a pull request, issue or ADR.

**Refuses a document it cannot attribute.** No `run.tested_commit` means no
matrix and a non-zero exit, because an unattributed matrix is the failure this
script exists to prevent. A run whose tree was dirty is rendered with that
stated: the sha names a tree nobody exercised.

**Renders through a closed allowlist.** Result rows are HMC-derived and are
*not* uniformly redacted — `RunState.record` applies its redaction pass only
when `status == "FAIL"`, and the `tool` label on no path at all. So this emits
only `subtask`, `status`, `result`, `timestamp`, and a `tool` truncated at its
first `(`; never a row's `data` or `note`, and never the document's `hmc`
block, which carries the HMC hostname and account name. An allowlist fails
closed as the document grows a field.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: Everything this script will print from a result row. Adding a field here is
#: a deliberate act with a redaction question attached; the default is silence.
_ROW_ALLOWLIST = ("subtask", "tool", "status", "result", "timestamp")

_COLUMN_TITLES = {
    "subtask": "Subtask",
    "tool": "Tool",
    "status": "Status",
    "result": "Result",
    "timestamp": "Timestamp",
}

_STATUSES = ("PASS", "FAIL", "SKIP")


def _cell(key: str, value: str) -> str:
    """One table cell, with `|` escaped so a value cannot forge a column."""
    escaped = value.replace("|", "\\|")
    return f"`{escaped}`" if key == "tool" else escaped


def sanitise_tool(label: Any) -> str:
    """The tool identifier alone, with any HMC-derived parenthetical dropped.

    `vmedia.py` builds labels like `hmc_delete_optical_media (<name>)` straight
    from an HMC response, and nothing redacts a `tool` value on any path. The
    identifier before the first `(` is the part this repository writes.
    """
    return str(label).split("(")[0].strip()


def allowlisted_row(row: Any) -> dict[str, str]:
    """One result row reduced to the fields that may be published."""
    if not isinstance(row, dict):
        return dict.fromkeys(_ROW_ALLOWLIST, "?")
    rendered = {key: str(row.get(key, "")) for key in _ROW_ALLOWLIST}
    rendered["tool"] = sanitise_tool(row.get("tool", ""))
    return rendered


def _provenance(document: dict[str, Any]) -> tuple[str, str] | None:
    """The run's commit and a qualifier, or `None` when it cannot be attributed."""
    run = document.get("run")
    if not isinstance(run, dict):
        return None
    commit = run.get("tested_commit")
    if not isinstance(commit, str) or not commit.strip():
        return None
    if run.get("tree_clean") is False:
        return commit, (
            " — **tree was dirty**: `src/` or `scripts/` had uncommitted changes, "
            "so this sha does not name the code that ran"
        )
    if run.get("tree_clean") is None:
        return commit, " — clean-tree state unknown"
    return commit, ""


def render(document: dict[str, Any]) -> str | None:
    """The Markdown matrix, or `None` when the document cannot be attributed."""
    attribution = _provenance(document)
    if attribution is None:
        return None
    commit, qualifier = attribution
    run = document["run"]
    rows = [allowlisted_row(row) for row in document.get("results", [])]
    counts = {
        status: sum(1 for row in rows if row["status"] == status)
        for status in _STATUSES
    }

    selection = (
        f"Group: `{run.get('group') or '(none)'}` · "
        f"Subtasks dispatched: `{run.get('subtasks')}` · "
        f"Finished: `{run.get('finished')}`"
    )
    totals = (
        f"**TOTAL {len(rows)}** · PASS {counts['PASS']} · "
        f"FAIL {counts['FAIL']} · SKIP {counts['SKIP']}"
    )
    provenance_note = (
        "Rendered by `scripts/live_test_evidence.py` from the run's own results "
        "document. Row `data` and `note` fields and the document's `hmc` block "
        "are withheld: they carry HMC-derived text that is redacted only on "
        "FAIL rows."
    )

    lines = [
        f"Live run on `{commit}`{qualifier}",
        "",
        selection,
        "",
        totals,
        "",
        "| " + " | ".join(_COLUMN_TITLES[key] for key in _ROW_ALLOWLIST) + " |",
        "|" + "---|" * len(_ROW_ALLOWLIST),
    ]
    # Columns are generated from the allowlist rather than written out, so the
    # allowlist is the single control: a field added to it is published, and a
    # field absent from it cannot be published by a second list drifting.
    lines.extend(
        "| " + " | ".join(_cell(key, row[key]) for key in _ROW_ALLOWLIST) + " |"
        for row in rows
    )
    lines.extend(["", provenance_note])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("results", type=Path, help="a test-results-*.json document")
    args = parser.parse_args(argv)

    try:
        document = json.loads(args.results.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        print(f"ERROR: cannot read {args.results}: {error}", file=sys.stderr)
        return 1
    if not isinstance(document, dict):
        print(f"ERROR: {args.results} is not a results document", file=sys.stderr)
        return 1

    matrix = render(document)
    if matrix is None:
        print(
            f"ERROR: {args.results} carries no run.tested_commit — it cannot be "
            "attributed to a commit, and an unattributed matrix is not evidence.\n"
            "       Re-run with a runner that stamps run provenance.",
            file=sys.stderr,
        )
        return 1

    print(matrix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
