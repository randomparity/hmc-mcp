"""Error rendering preserves malformed bodies without hiding code defects."""

from __future__ import annotations

import pytest
from conftest import live_fixture

from hmcpctl.errors import MAX_ERROR_BODY_BYTES, HMCError


def test_hmc_error_extracts_message_past_4096_byte_truncation_cutoff() -> None:
    padding = "x" * 4200
    body = f"<Error><Padding>{padding}</Padding><Message>schema violation</Message></Error>"
    assert len(body.encode("utf-8")) > MAX_ERROR_BODY_BYTES

    error = HMCError("request failed", 500, body)

    assert str(error) == "request failed (HTTP 500): schema violation"
    assert len(error.body.encode("utf-8")) == MAX_ERROR_BODY_BYTES


def test_hmc_error_extracts_message_from_xml_body() -> None:
    error = HMCError(
        "request failed", 500, "<Error><Message>bad input</Message></Error>"
    )

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

    monkeypatch.setattr("hmcpctl.errors.find_text", fail_unexpectedly)

    with pytest.raises(RuntimeError, match="formatter defect"):
        HMCError("request failed", 500, "<Error />")


def test_hmc_error_renders_the_captured_http_error_response_message() -> None:
    """V10R3 error bodies are an `HttpErrorResponse` whose text is `<Message>`."""
    capture = live_fixture("rest-lpar-not-found")

    error = HMCError("request failed", capture["status"], capture["body"])

    assert str(error) == (
        "request failed (HTTP 404): REST029B The URL presented to the Management "
        "Console REST Web Services is not valid. The supplied URI does not identify "
        "a known resource."
    )


def test_hmc_error_does_not_read_unobserved_error_element_names() -> None:
    """No capture or reference has an `<error>`/`<msg>` body; it renders raw (#1202)."""
    for body in ("<error>guessed</error>", "<Fault><msg>guessed</msg></Fault>"):
        assert str(HMCError("request failed", 500, body)) == (
            f"request failed (HTTP 500): {body}"
        )
