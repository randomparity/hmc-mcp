"""Structured-concurrency contracts for composite operations."""

from __future__ import annotations

import asyncio

import pytest

from hmc_mcp.operations_composite import _fetch_lpar_data


class _FailingCompositeClient:
    def __init__(self) -> None:
        self.sibling_finished = asyncio.Event()

    async def get_logical_partition(self, _uuid: str):
        await asyncio.sleep(0)
        raise RuntimeError("primary request failed")

    async def list_child(self, *_args):
        try:
            await asyncio.Event().wait()
        finally:
            self.sibling_finished.set()


@pytest.mark.asyncio
async def test_composite_failure_cancels_and_awaits_sibling():
    client = _FailingCompositeClient()

    with pytest.raises(ExceptionGroup) as exc_info:
        await _fetch_lpar_data(client, "lpar-uuid")

    assert any(
        "primary request failed" in str(error)
        for error in exc_info.value.exceptions
    )
    assert client.sibling_finished.is_set()
