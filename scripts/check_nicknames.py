"""Guard: a committed config fixture must have a well-formed ``nicknames`` table.

Every nickname target must be an existing profile key, no nickname key may
collide with a profile key, and no target may itself be a nickname key (which
would imply a chain, forbidden: resolution is one level deep). A malformed
table (not string -> string) fails too. This mirrors the runtime contract in
``hmc_mcp.config`` so the guardrail and the loader cannot drift.

Usage:
    python scripts/check_nicknames.py [--config <path>]

Default config path: tests/fixtures/config.example.toml (relative to the repo
root, the parent of the directory containing this script).

Exits 0 when the fixture is valid; exits 1 and prints the offending nicknames
when any check fails.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from hmc_mcp.config import ConfigError, _coerce_nicknames

# Resolve the repo root relative to this script so the guard can be run from
# any working directory.
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_DEFAULT_CONFIG = _REPO_ROOT / "tests" / "fixtures" / "config.example.toml"


def _validate(config_path: Path) -> list[str]:
    """Return failure messages; empty when the fixture is valid."""
    errors: list[str] = []

    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"cannot read config {config_path}: {exc}"]

    try:
        doc = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        return [f"config {config_path}: TOML parse error: {exc}"]

    profiles = doc.get("profiles", {})
    if not isinstance(profiles, dict):
        return ["'profiles' must be a table"]

    # Shared malformed-table validation: a non-mapping or non-string target is
    # a config error, exactly as the runtime loader enforces.
    try:
        nicknames = _coerce_nicknames(doc.get("nicknames"), config_path)
    except ConfigError as exc:
        return [f"{config_path}: {exc}"]

    profile_keys = set(profiles)
    nickname_keys = set(nicknames)

    # Collision: a nickname key must not shadow a real profile key.
    for key in sorted(nickname_keys & profile_keys):
        errors.append(f"nickname {key!r} collides with an existing profile key")

    # Dangling target: a nickname must resolve to a real profile key.
    for key in sorted(nicknames):
        target = nicknames[key]
        if target not in profile_keys:
            errors.append(f"nickname {key!r} targets missing profile {target!r}")

    # Chain: a target must not itself be a nickname key (resolution is one
    # level deep, so a nested target can never resolve).
    for key in sorted(nicknames):
        target = nicknames[key]
        if target in nickname_keys:
            errors.append(
                f"nickname {key!r} targets {target!r}, which is itself a "
                f"nickname (chained nicknames are not allowed)"
            )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        help="Path to the config TOML to validate "
        f"(default: {_DEFAULT_CONFIG})",
    )
    args = parser.parse_args(argv)

    config_path: Path = args.config
    if not config_path.exists():
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        return 1

    errors = _validate(config_path)
    if errors:
        print(f"ERROR: nickname check failed for {config_path}:", file=sys.stderr)
        for err in errors:
            print(f"     {err}", file=sys.stderr)
        return 1

    print(f"OK: nicknames table in {config_path} is well-formed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
