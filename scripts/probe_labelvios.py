"""Read-only probe for lslabelvios evidence — issue #559.

Stage 1: connects to each HMC profile and captures:
  - lshmc -V                   (version banner)
  - lssyscfg -r sys -F name,type_model,serial_num  (managed-system inventory)

Stage 2: for each managed system that is operational, captures:
  - lslabelvios -r fcport -F --header  -m <sys>
  - lslabelvios -r group --filter resources=vfc -F --header  -m <sys>
  - lslabelvios -r fcport  -m <sys>                  (no -F, default output)
  - lslabelvios -r group --filter resources=vfc  -m <sys>

All commands are read-only.  No mutation is performed.

NOTE: The port in config.toml is the HTTPS REST port.  HMC SSH always
listens on port 22.  This script connects on port 22 for all profiles.

Usage:
    uv run --no-sync python scripts/probe_labelvios.py
"""

from __future__ import annotations

import asyncio
import pathlib
import tomllib
from dataclasses import dataclass

import asyncssh

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

_CONFIG_PATH = pathlib.Path.home() / ".config/hmc-mcp/config.toml"


@dataclass
class Profile:
    name: str
    host: str
    user: str
    password: str


def load_profiles() -> list[Profile]:
    with open(_CONFIG_PATH, "rb") as fh:
        data = tomllib.load(fh)
    profiles = []
    for name, p in data.get("profiles", {}).items():
        profiles.append(
            Profile(
                name=name,
                host=p["host"],
                user=p["user"],
                password=p["password"],
            )
        )
    return profiles


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------


async def run(conn: asyncssh.SSHClientConnection, cmd: str) -> tuple[int, str, str]:
    """Run *cmd*; return (exit_status, stdout, stderr).  Never raises."""
    try:
        result = await conn.run(cmd, check=False)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return result.exit_status or 0, stdout, stderr
    except Exception as exc:  # noqa: BLE001 - the probe reports any failure as a result field
        return -1, "", str(exc)


def connect_kwargs(profile: Profile) -> dict:
    return {
        "host": profile.host,
        "port": 22,
        "username": profile.user,
        "password": profile.password,
        "known_hosts": None,
        "preferred_auth": "password",
        "client_keys": [],
    }


# ---------------------------------------------------------------------------
# Per-profile probe
# ---------------------------------------------------------------------------


async def probe_profile(profile: Profile) -> dict:
    """Stage-1 + Stage-2 probe for one HMC profile."""
    result: dict = {
        "profile": profile.name,
        "host": profile.host,
        "queries": {},
        "systems": [],
    }
    try:
        async with asyncssh.connect(**connect_kwargs(profile)) as conn:
            # --- Stage 1: HMC version and managed-system list ---
            rc, stdout, stderr = await run(conn, "lshmc -V")
            result["queries"]["lshmc -V"] = {
                "cmd": "lshmc -V",
                "exit_status": rc,
                "stdout": stdout.strip(),
                "stderr": stderr.strip(),
            }

            rc, stdout, stderr = await run(
                conn, "lssyscfg -r sys -F name,type_model,serial_num,state"
            )
            result["queries"]["lssyscfg -r sys"] = {
                "cmd": "lssyscfg -r sys -F name,type_model,serial_num,state",
                "exit_status": rc,
                "stdout": stdout.strip(),
                "stderr": stderr.strip(),
            }

            # Parse system names for stage 2
            systems: list[str] = []
            if rc == 0:
                for line in stdout.strip().splitlines():
                    parts = line.split(",")
                    if parts and parts[0].strip():
                        sys_name = parts[0].strip()
                        state = parts[3].strip() if len(parts) > 3 else ""
                        systems.append(sys_name)
                        result["systems"].append({"name": sys_name, "state": state})

            # --- Stage 2: lslabelvios per managed system ---
            for sys_name in systems:
                import shlex
                m = shlex.quote(sys_name)
                sys_queries: dict = {}

                for label, cmd in [
                    (
                        "lslabelvios -r fcport -F --header",
                        f"lslabelvios -r fcport -F --header -m {m}",
                    ),
                    (
                        "lslabelvios -r group --filter resources=vfc -F --header",
                        f"lslabelvios -r group --filter resources=vfc -F --header -m {m}",
                    ),
                    (
                        "lslabelvios -r fcport (default)",
                        f"lslabelvios -r fcport -m {m}",
                    ),
                    (
                        "lslabelvios -r group --filter resources=vfc (default)",
                        f"lslabelvios -r group --filter resources=vfc -m {m}",
                    ),
                ]:
                    rc2, out2, err2 = await run(conn, cmd)
                    sys_queries[label] = {
                        "cmd": cmd,
                        "exit_status": rc2,
                        "stdout": out2.strip(),
                        "stderr": err2.strip(),
                    }

                result["queries"][f"[{sys_name}]"] = sys_queries

    except Exception as exc:  # noqa: BLE001 - the probe reports any failure as a result field
        result["error"] = str(exc)
    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

DIVIDER = "=" * 72


def report(results: list[dict]) -> None:
    for r in results:
        print(f"\n{DIVIDER}")
        print(f"PROFILE : {r['profile']}  ({r['host']})")
        print(DIVIDER)

        if "error" in r:
            print(f"  CONNECTION ERROR: {r['error']}")
            continue

        for label, q in r["queries"].items():
            if label.startswith("["):
                # Per-system block
                sys_name = label
                print(f"\n  {'─'*60}")
                print(f"  SYSTEM: {sys_name}")
                print(f"  {'─'*60}")
                for qlabel, qq in q.items():
                    _print_query(f"  {qlabel}", qq)
            else:
                _print_query(f"  {label}", q)


def _print_query(label: str, q: dict) -> None:
    print(f"\n  --- {label} ---")
    print(f"  cmd         : {q['cmd']}")
    print(f"  exit_status : {q['exit_status']}")
    if q["stdout"]:
        for line in q["stdout"].splitlines():
            print(f"  stdout      : {line}")
    else:
        print("  stdout      : (empty)")
    if q["stderr"]:
        for line in q["stderr"].splitlines():
            print(f"  stderr      : {line}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    profiles = load_profiles()
    print(f"Probing {len(profiles)} HMC profile(s) …\n")

    tasks = [probe_profile(p) for p in profiles]
    results = await asyncio.gather(*tasks)

    report(list(results))

    # Summary
    print(f"\n\n{'SUMMARY':^72}")
    print(f"{'Profile':<20} {'Host':<36} {'Systems':<8} {'Status'}")
    print("-" * 72)
    for r in results:
        if "error" in r:
            status = "ERROR: " + r["error"]
            nsys = "-"
        else:
            status = "ok"
            nsys = str(len(r.get("systems", [])))
        print(f"{r['profile']:<20} {r['host']:<36} {nsys:<8} {status}")


if __name__ == "__main__":
    asyncio.run(main())
