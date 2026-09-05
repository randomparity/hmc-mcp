from __future__ import annotations

import pytest

from hmc_mcp.errors import HMCError
from hmc_mcp.operations.error_translation import (
    translate_pcm_error,
    translate_template_error,
    translate_virtual_network_create_error,
)


@pytest.mark.parametrize(
    ("translator", "status", "message"),
    [
        (translate_pcm_error, 403, "PCM authority"),
        (translate_pcm_error, 406, "not licensed or not enabled"),
        (translate_template_error, 406, "not licensed or not supported"),
        (translate_virtual_network_create_error, 406, "virtual network create"),
    ],
)
def test_error_translators_return_translated_errors(translator, status, message):
    original = HMCError("raw failure", status, "sensitive response body")

    translated = translator(original)

    assert translated.status_code == status
    assert translated.body == "sensitive response body"
    assert message in str(translated)


@pytest.mark.parametrize(
    "translator",
    [translate_pcm_error, translate_template_error, translate_virtual_network_create_error],
)
def test_error_translators_return_unmatched_errors_unchanged(translator):
    original = HMCError("raw failure", 500)

    assert translator(original) is original
