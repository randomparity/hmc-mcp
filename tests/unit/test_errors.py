"""Error rendering preserves malformed bodies without hiding code defects."""

from __future__ import annotations

import pytest

from hmc_mcp.errors import HMCError


def test_hmc_error_extracts_message_from_xml_body() -> None:
    error = HMCError("request failed", 500, "<Error><Message>bad input</Message></Error>")

    assert str(error) == "request failed (HTTP 500): bad input"


def test_hmc_error_falls_back_to_malformed_body() -> None:
    error = HMCError("request failed", 500, "<Error><Message>truncated")

    assert str(error) == "request failed (HTTP 500): <Error><Message>truncated"


def test_hmc_error_falls_back_to_entity_body_without_masking_http_failure() -> None:
    body = "<!DOCTYPE Error [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]><Error>&xxe;</Error>"

    error = HMCError("request failed", 500, body)

    assert error.status_code == 500
    assert error.body == body
    assert str(error) == f"request failed (HTTP 500): {body[:500]}"


def test_hmc_error_does_not_mask_unexpected_formatter_failure(monkeypatch) -> None:
    def fail_unexpectedly(*_args: object) -> None:
        raise RuntimeError("formatter defect")

    monkeypatch.setattr("hmc_mcp.errors.find_text", fail_unexpectedly)

    with pytest.raises(RuntimeError, match="formatter defect"):
        HMCError("request failed", 500, "<Error />")
