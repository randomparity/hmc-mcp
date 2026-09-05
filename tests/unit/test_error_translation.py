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
def test_error_translators_preserve_body_and_chain_cause(translator, status, message):
    original = HMCError("raw failure", status, "sensitive response body")

    with pytest.raises(HMCError, match=message) as exc_info:
        translator(original)

    assert exc_info.value.status_code == status
    assert exc_info.value.body == "sensitive response body"
    assert "sensitive response body" in str(exc_info.value)
    assert exc_info.value.__cause__ is original
