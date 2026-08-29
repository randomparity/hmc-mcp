"""Tests for client_parse's error-tagging wrappers.

The two helpers under test exist specifically so a ParseError surfaces as an
HMCError naming the HMC call that returned the malformed XML (the same
behavior covered end-to-end in tests/unit/test_client.py for the feed and PCM
preferences paths); these unit tests pin the tagging contract directly.
"""

import pytest
from defusedxml import ElementTree as DET

from hmc_mcp.client.client_parse import _find_text, _metric_links
from hmc_mcp.errors import HMCError


def test_find_text_parse_error_tags_context():
    with pytest.raises(HMCError) as exc_info:
        _find_text("<feed><entry>", "test context")
    assert "Failed to parse test context response" in str(exc_info.value)
    assert "no element found" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, DET.ParseError)


def test_metric_links_parse_error_tags_context():
    with pytest.raises(HMCError) as exc_info:
        _metric_links("<ManagementConsolePcmPreference><unclosed>", "test context")
    assert "Failed to parse test context response" in str(exc_info.value)
    assert "no element found" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, DET.ParseError)
