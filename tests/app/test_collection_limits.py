"""Contract tests for client-side collection payload limits."""

from unittest.mock import AsyncMock

import pytest

from hmc_mcp._app import _run_limited_collection


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        (None, [{"id": 1}, {"id": 2}, {"id": 3}]),
        (2, [{"id": 1}, {"id": 2}]),
        (0, []),
    ],
)
def test_run_limited_collection_caps_after_operation(limit, expected):
    entries = [{"id": 1}, {"id": 2}, {"id": 3}]
    operation = AsyncMock(return_value=entries)

    result = _run_limited_collection(operation, limit)

    assert result == expected
    operation.assert_awaited_once_with()


def test_run_limited_collection_rejects_negative_limit_before_operation():
    operation = AsyncMock(return_value=[])

    with pytest.raises(ValueError, match="^limit must be greater than or equal to 0$"):
        _run_limited_collection(operation, -1)

    operation.assert_not_called()
