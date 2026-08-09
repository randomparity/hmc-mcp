#!/usr/bin/env python3
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
import sys
from pathlib import Path

# Resolve the repo root relative to this script so the guard can be run from
# any working directory.
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_DEFAULT_DOC = _REPO_ROOT / "docs" / "environment-variables.md"

# Add src/ to the path so HMCConfig can be imported without installation.
sys.path.insert(0, str(_REPO_ROOT / "src"))

from hmc_mcp.config import HMCConfig  # noqa: E402  (after sys.path tweak)


def _env_var_names() -> list[str]:
    """Return every HMC_* env var name declared in HMCConfig."""
    prefix = HMCConfig.model_config.get("env_prefix", "")
    return [prefix + field_name.upper() for field_name in HMCConfig.model_fields]


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
    missing = [v for v in env_vars if v not in doc_text]

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
    sys.exit(main())
