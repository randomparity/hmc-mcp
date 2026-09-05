import importlib.util
import json
from http.client import HTTPMessage
from io import BytesIO
from pathlib import Path
from typing import Self
from urllib.error import HTTPError, URLError

import pytest

MODULE_PATH = Path(__file__).parents[2] / "scripts" / "check_python_support.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "check_python_support", MODULE_PATH
)
assert MODULE_SPEC is not None
assert MODULE_SPEC.loader is not None
check_python_support = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(check_python_support)


def lifecycle_payload(**statuses: str) -> bytes:
    return json.dumps(
        {version: {"status": status} for version, status in statuses.items()}
    ).encode()


def test_supported_versions_selects_stable_non_eol_releases() -> None:
    payload = lifecycle_payload(
        **{
            "3.10": "security",
            "3.11": "security",
            "3.12": "security",
            "3.13": "security",
            "3.14": "bugfix",
            "3.15": "prerelease",
            "3.16": "feature",
            "3.9": "end-of-life",
        }
    )

    assert check_python_support.supported_versions(payload) == (
        "3.11",
        "3.12",
        "3.13",
        "3.14",
    )


def test_supported_versions_includes_a_new_stable_release() -> None:
    payload = lifecycle_payload(**{"3.14": "security", "3.15": "bugfix"})

    assert check_python_support.supported_versions(payload) == ("3.14", "3.15")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"not-json", "valid JSON"),
        (b"[]", "JSON object"),
        (json.dumps({"3.14": "bugfix"}).encode(), "object entry"),
        (lifecycle_payload(**{"3.x": "bugfix"}), "major.minor"),
        (json.dumps({"3.14": {}}).encode(), "status"),
        (lifecycle_payload(**{"3.14": "mystery"}), "unsupported status"),
    ],
)
def test_supported_versions_rejects_malformed_input(
    payload: bytes, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        check_python_support.supported_versions(payload)


def test_compare_versions_reports_missing_and_unexpected_releases() -> None:
    with pytest.raises(
        ValueError,
        match=r"missing: 3\.14; unexpected: 3\.15",
    ):
        check_python_support.compare_versions(
            ["3.11", "3.12", "3.13", "3.15"],
            ["3.11", "3.12", "3.13", "3.14"],
        )


@pytest.mark.parametrize(
    ("expected", "message"),
    [
        ([], "at least one"),
        (["3.11", "3.11"], "duplicate"),
        (["3.10"], "3.11 or newer"),
        (["3.x"], "major.minor"),
    ],
)
def test_compare_versions_validates_expected_list(
    expected: list[str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        check_python_support.compare_versions(expected, ["3.11"])


class FakeResponse:
    def __init__(self, payload: bytes, url: str = check_python_support.LIFECYCLE_URL):
        self.payload = BytesIO(payload)
        self.url = url

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.payload.read(size)

    def geturl(self) -> str:
        return self.url


def test_main_reads_bounded_response_and_accepts_matching_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = lifecycle_payload(**{"3.11": "security", "3.12": "bugfix"})
    observed: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: float) -> FakeResponse:
        observed["request"] = request
        observed["timeout"] = timeout
        return FakeResponse(payload)

    monkeypatch.setattr(check_python_support, "urlopen", fake_urlopen)
    monkeypatch.setattr(check_python_support.sys, "argv", ["check", "3.11", "3.12"])

    assert check_python_support.main() == 0
    assert observed["timeout"] == 10
    assert observed["request"].full_url == check_python_support.LIFECYCLE_URL


def test_main_rejects_oversized_response(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        check_python_support,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(
            b"x" * (check_python_support.MAX_RESPONSE_BYTES + 1)
        ),
    )
    monkeypatch.setattr(check_python_support.sys, "argv", ["check", "3.11"])

    assert check_python_support.main() == 1
    assert "256 KiB" in capsys.readouterr().err


def test_main_rejects_redirects(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    redirected = FakeResponse(b"{}", "https://example.test/releases.json")
    monkeypatch.setattr(check_python_support, "urlopen", lambda *_a, **_k: redirected)
    monkeypatch.setattr(check_python_support.sys, "argv", ["check", "3.11"])

    assert check_python_support.main() == 1
    assert "redirect" in capsys.readouterr().err


@pytest.mark.parametrize(
    "error",
    [TimeoutError("timed out"), URLError("offline")],
)
def test_main_reports_actionable_network_failures(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*_args: object, **_kwargs: object) -> FakeResponse:
        raise error

    monkeypatch.setattr(check_python_support, "urlopen", fail)
    monkeypatch.setattr(check_python_support.sys, "argv", ["check", "3.11"])

    assert check_python_support.main() == 1
    error_output = capsys.readouterr().err
    assert "fetch" in error_output
    assert "retry" in error_output


def test_main_reports_http_redirect_as_network_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    error = HTTPError(
        check_python_support.LIFECYCLE_URL,
        302,
        "Found",
        HTTPMessage(),
        None,
    )
    monkeypatch.setattr(
        check_python_support,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(check_python_support.sys, "argv", ["check", "3.11"])

    assert check_python_support.main() == 1
    error_output = capsys.readouterr().err
    assert "redirect" in error_output
    assert "retry" in error_output
