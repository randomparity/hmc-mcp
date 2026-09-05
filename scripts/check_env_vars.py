"""Guard: every HMC_* env var defined in HMCConfig must appear in the doc.

Usage:
    python scripts/check_env_vars.py [--doc <path>]

Default doc path: docs/environment-variables.md (relative to the repo root,
which is the parent of the directory containing this script).

Exits 0 when every env var is documented; exits 1 and prints the missing
names when any are absent.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from hmc_mcp.config import HMCConfig

# Resolve the repo root relative to this script so the guard can be run from
# any working directory.
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_DEFAULT_DOC = _REPO_ROOT / "docs" / "environment-variables.md"


def _env_var_names() -> list[str]:
    """Return every HMC_* env var name declared in HMCConfig.

    Assumes no field uses ``alias``, ``validation_alias``, or ``env`` kwargs
    to override the default ``<prefix><FIELD_NAME>`` env var derivation.
    Raises ``RuntimeError`` if any field carries an alias override so the
    guard fails loudly rather than checking the wrong name.
    """
    prefix = HMCConfig.model_config.get("env_prefix") or ""
    names: list[str] = []
    for field_name, field_info in HMCConfig.model_fields.items():
        # pydantic-settings honours alias, validation_alias, and the env kwarg
        # as alternative env var name sources.  Detect any of them so the guard
        # fails loudly rather than silently checking the wrong derived name.
        if (
            getattr(field_info, "alias", None)
            or getattr(field_info, "validation_alias", None)
            or getattr(field_info, "env", None)
        ):
            raise RuntimeError(
                f"HMCConfig.{field_name} has an alias/env override; "
                "update _env_var_names() to resolve the actual env var name."
            )
        names.append(prefix + field_name.upper())
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--doc",
        type=Path,
        default=_DEFAULT_DOC,
        help="Path to the environment-variables Markdown doc "
        f"(default: {_DEFAULT_DOC})",
    )
    args = parser.parse_args(argv)

    doc_path: Path = args.doc
    if not doc_path.exists():
        print(f"ERROR: doc not found: {doc_path}", file=sys.stderr)
        return 1

    doc_text = doc_path.read_text()
    env_vars = _env_var_names()
    # Restrict the search to table rows inside the ## Reference section so
    # that a var mentioned only in prose (including prose inside the section)
    # does not satisfy the check — only a documented table row counts.
    ref_match = re.search(r"^## Reference\b", doc_text, re.MULTILINE)
    if not ref_match:
        print(
            'ERROR: doc has no "## Reference" section; cannot verify documentation coverage.',
            file=sys.stderr,
        )
        return 1
    # Slice from ## Reference to the next ## heading (or end of file).
    section_start = ref_match.start()
    next_section = re.search(r"^## ", doc_text[ref_match.end() :], re.MULTILINE)
    section_end = (
        ref_match.end() + next_section.start() if next_section else len(doc_text)
    )
    ref_section = doc_text[section_start:section_end]
    # Filter to Markdown table rows (lines starting with |) to exclude
    # prose, comments, or example blocks within the section.
    table_text = "\n".join(
        ln for ln in ref_section.splitlines() if ln.strip().startswith("|")
    )
    # Use word-boundary regex matching so that "HMC_SSH_TIME" is not falsely
    # satisfied by the presence of "HMC_SSH_TIMEOUT".
    missing = sorted(
        v for v in env_vars if not re.search(rf"\b{re.escape(v)}\b", table_text)
    )

    if missing:
        print(
            "ERROR: the following HMC_* env vars are defined in HMCConfig but "
            "missing from the documentation:\n"
        )
        for v in missing:
            print(f"  {v}")
        print(
            f"\nAdd them to {doc_path} or update HMCConfig — every env var "
            "must be documented before it ships."
        )
        return 1

    print(f"OK: all {len(env_vars)} HMC_* env vars are documented.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
