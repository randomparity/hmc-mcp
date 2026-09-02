#!/usr/bin/env python3
"""Compare reviewed Python versions with Python's lifecycle dataset."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

LIFECYCLE_URL = "https://peps.python.org/api/release-cycle.json"
MINIMUM_VERSION = (3, 11)
MAX_RESPONSE_BYTES = 256 * 1024
SOCKET_TIMEOUT_SECONDS = 10
SUPPORTED_STATUSES = frozenset({"bugfix", "security"})
KNOWN_STATUSES = SUPPORTED_STATUSES | {"feature", "prerelease", "end-of-life"}
VERSION_PATTERN = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)$")


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


urlopen = build_opener(_RejectRedirects()).open


def _parse_version(value: str) -> tuple[int, int]:
    match = VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid Python version {value!r}; expected major.minor")
    return int(match["major"]), int(match["minor"])


def supported_versions(payload: bytes) -> tuple[str, ...]:
    """Return stable, supported CPython versions at or above the policy floor."""
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("lifecycle response must be valid JSON") from error
    if not isinstance(document, dict):
        raise ValueError("lifecycle response must have a JSON object root")

    selected: list[tuple[tuple[int, int], str]] = []
    for version, entry in document.items():
        if not isinstance(version, str):
            raise ValueError(
                "lifecycle version keys must be strings in major.minor form"
            )
        parsed_version = _parse_version(version)
        if not isinstance(entry, dict):
            raise ValueError(f"lifecycle version {version} must have an object entry")
        status = entry.get("status")
        if not isinstance(status, str):
            raise ValueError(f"lifecycle version {version} must have a string status")
        if status not in KNOWN_STATUSES:
            raise ValueError(
                f"lifecycle version {version} has unsupported status {status!r}"
            )
        if parsed_version >= MINIMUM_VERSION and status in SUPPORTED_STATUSES:
            selected.append((parsed_version, version))

    return tuple(version for _, version in sorted(selected))


def compare_versions(expected: Sequence[str], actual: Sequence[str]) -> None:
    """Raise when the reviewed and authoritative version sets differ."""
    if not expected:
        raise ValueError("expected version list must contain at least one version")
    parsed_expected = [_parse_version(version) for version in expected]
    if len(set(expected)) != len(expected):
        raise ValueError("expected version list contains a duplicate")
    below_floor = [
        version
        for version, parsed in zip(expected, parsed_expected, strict=True)
        if parsed < MINIMUM_VERSION
    ]
    if below_floor:
        raise ValueError("expected versions must be Python 3.11 or newer")

    expected_versions = set(expected)
    actual_versions = set(actual)
    missing = sorted(actual_versions - expected_versions, key=_parse_version)
    unexpected = sorted(expected_versions - actual_versions, key=_parse_version)
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected: {', '.join(unexpected)}")
        raise ValueError("Python support policy drift: " + "; ".join(details))


def _fetch_payload() -> bytes:
    request = Request(LIFECYCLE_URL, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=SOCKET_TIMEOUT_SECONDS) as response:
            if response.geturl() != LIFECYCLE_URL:
                raise ValueError("lifecycle fetch rejected an HTTP redirect")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        detail = "redirect" if 300 <= error.code < 400 else f"HTTP {error.code}"
        raise RuntimeError(
            f"failed to fetch Python lifecycle data ({detail}); inspect the authority and retry"
        ) from error
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError(
            "failed to fetch Python lifecycle data; check network availability and retry"
        ) from error
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("lifecycle response exceeds the 256 KiB limit")
    return payload


def main() -> int:
    """Run the lifecycle comparison CLI."""
    try:
        compare_versions(sys.argv[1:], supported_versions(_fetch_payload()))
    except (RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print("Python support policy matches the authoritative lifecycle data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
