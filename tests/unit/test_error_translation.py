from __future__ import annotations

import asyncio

import pytest

from hmc_mcp.error_translation import (
    run_with_error_translation,
    translate_pcm_error,
    translate_template_error,
    translate_virtual_network_create_error,
)
from hmc_mcp.errors import HMCError


@pytest.mark.parametrize(
    ("translator", "status", "message"),
    [
        (translate_pcm_error, 403, "PCM authority"),
        (translate_pcm_error, 406, "not licensed or not enabled"),
        (translate_template_error, 406, "not licensed or not supported"),
        (translate_virtual_network_create_error, 406, "virtual network create"),
    ],
)
def test_shared_error_translation_preserves_body_and_chains_cause(
    translator, status, message
):
    original = HMCError("raw failure", status, "sensitive response body")

    async def fail():
        raise original

    with pytest.raises(HMCError, match=message) as exc_info:
        asyncio.run(run_with_error_translation(fail, translator))

    assert exc_info.value.status_code == status
    assert exc_info.value.body == "sensitive response body"
    assert "sensitive response body" in str(exc_info.value)
    assert exc_info.value.__cause__ is original
