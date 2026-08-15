"""HMC Logon/Logoff latency measurement — issue #155.

Measures the per-call cost of HMC session creation (Logon) and destruction
(Logoff) using the production HMC client code against the HMC configured in
~/.config/hmc-mcp/config.toml.

Usage:
    uv run python scripts/measure_logon_latency.py

Output: summary printed to stdout; raw samples written to
        measure_logon_latency_results.json.

Do NOT post credentials, session tokens, hostnames, IP addresses, or other
environment-identifying secrets from these results.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import httpx

from hmc_mcp.config import load_profile
from hmc_mcp.client import HMCClient, LOGON_REQUEST_TEMPLATE, MEDIA_WEB
from hmc_mcp.xmlutil import WEB_NS
from hmc_mcp.client_parse import _find_text
from hmc_mcp.errors import HMCError

WARMUP_CYCLES = 3
MEASUREMENT_CYCLES = 20
RESULTS_FILE = Path("measure_logon_latency_results.json")


async def _one_cycle(config) -> tuple[float, float]:
    """Execute one Logon + Logoff cycle.

    Returns (logon_seconds, logoff_seconds) measured with time.monotonic().
    The httpx client is freshly constructed per cycle to mirror the
    per-call-client pattern currently used in the server.
    """
    async with httpx.AsyncClient(
        base_url=config.base_url,
        verify=config.verify_ssl,
        timeout=config.timeout,
    ) as http:
        body = LOGON_REQUEST_TEMPLATE.format(
            web_ns=WEB_NS, user=config.user, password=config.password
        )
        headers_logon = {
            "Content-Type": f"{MEDIA_WEB}; type=LogonRequest",
            "Accept": f"{MEDIA_WEB}; type=LogonResponse",
        }

        # --- Logon ---
        t0 = time.monotonic()
        resp = await http.put(
            "/rest/api/web/Logon",
            content=body,
            headers=headers_logon,
        )
        logon_s = time.monotonic() - t0

        if resp.status_code != 200:
            raise HMCError("HMC logon failed", resp.status_code, resp.text)
        token = _find_text(resp.text, "/rest/api/web/Logon", "X-API-Session")
        if not token:
            raise HMCError("Logon response missing X-API-Session token")

        # --- Logoff ---
        t1 = time.monotonic()
        logoff_resp = await http.delete(
            "/rest/api/web/Logon",
            headers={"Accept": MEDIA_WEB, "X-API-Session": token},
        )
        logoff_s = time.monotonic() - t1

        # 200 or 204 both indicate clean logoff on HMC
        if logoff_resp.status_code not in (200, 204):
            raise HMCError(
                "HMC logoff failed", logoff_resp.status_code, logoff_resp.text
            )

    return logon_s, logoff_s


async def run_measurement() -> None:
    # Suppress TLS warning noise during bulk timing
    warnings.filterwarnings("ignore")

    config = load_profile()
    print(f"Profile loaded: host={config.host}  user={config.user}")
    print()

    # Confirm connectivity with a single cycle before bulk run
    print("Connectivity check (1 cycle)…")
    try:
        ls, lo = await _one_cycle(config)
        print(f"  OK — Logon {ls*1000:.1f} ms  Logoff {lo*1000:.1f} ms")
    except Exception as exc:
        print(f"  FAILED: {exc}")
        print("Aborting — cannot reach HMC.")
        return

    # Warm-up
    print(f"\nWarm-up ({WARMUP_CYCLES} cycles, discarded)…")
    warmup_failures = 0
    for i in range(WARMUP_CYCLES):
        try:
            ls, lo = await _one_cycle(config)
            print(f"  warmup {i+1}: Logon {ls*1000:.1f} ms  Logoff {lo*1000:.1f} ms")
        except Exception as exc:
            warmup_failures += 1
            print(f"  warmup {i+1}: FAILED — {exc}")

    # Measurement run
    print(f"\nMeasurement run ({MEASUREMENT_CYCLES} cycles)…")
    logon_samples: list[float] = []
    logoff_samples: list[float] = []
    failures = 0
    raw: list[dict] = []

    for i in range(MEASUREMENT_CYCLES):
        try:
            ls, lo = await _one_cycle(config)
            logon_samples.append(ls)
            logoff_samples.append(lo)
            raw.append({"cycle": i + 1, "logon_ms": ls * 1000, "logoff_ms": lo * 1000, "status": "ok"})
            print(
                f"  cycle {i+1:2d}: Logon {ls*1000:6.1f} ms  Logoff {lo*1000:6.1f} ms"
            )
        except Exception as exc:
            failures += 1
            raw.append({"cycle": i + 1, "status": "fail", "error": str(exc)})
            print(f"  cycle {i+1:2d}: FAILED — {exc}")

    n = len(logon_samples)

    def p95(samples: list[float]) -> float:
        if not samples:
            return float("nan")
        sorted_s = sorted(samples)
        idx = int(0.95 * len(sorted_s))
        if idx >= len(sorted_s):
            idx = len(sorted_s) - 1
        return sorted_s[idx]

    logon_median = statistics.median(logon_samples) * 1000 if logon_samples else float("nan")
    logon_p95 = p95(logon_samples) * 1000
    logoff_median = statistics.median(logoff_samples) * 1000 if logoff_samples else float("nan")
    logoff_p95 = p95(logoff_samples) * 1000

    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"  Successful cycles:      {n}/{MEASUREMENT_CYCLES}")
    print(f"  Failed cycles:          {failures}")
    print(f"  Logon   median:         {logon_median:.1f} ms")
    print(f"  Logon   p95:            {logon_p95:.1f} ms")
    print(f"  Logoff  median:         {logoff_median:.1f} ms")
    print(f"  Logoff  p95:            {logoff_p95:.1f} ms")
    print()
    print("All created sessions were immediately closed in the same cycle.")
    print("No session identifiers are retained.")

    # Write results (no credentials or tokens)
    results = {
        "measurement_utc": datetime.now(timezone.utc).isoformat(),
        "warmup_cycles": WARMUP_CYCLES,
        "warmup_failures": warmup_failures,
        "measurement_cycles": MEASUREMENT_CYCLES,
        "successful_cycles": n,
        "failed_cycles": failures,
        "logon_median_ms": round(logon_median, 2),
        "logon_p95_ms": round(logon_p95, 2),
        "logoff_median_ms": round(logoff_median, 2),
        "logoff_p95_ms": round(logoff_p95, 2),
        "samples": raw,
    }
    RESULTS_FILE.write_text(json.dumps(results, indent=2))
    print(f"\nRaw samples written to {RESULTS_FILE}")


if __name__ == "__main__":
    asyncio.run(run_measurement())
