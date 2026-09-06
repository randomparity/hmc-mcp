"""Structured-concurrency contracts for composite operations."""

from __future__ import annotations

import pytest

from hmc_mcp.operations.inventory.composite import (
    _fetch_lpar_data,
    _fetch_system_summary_data,
)


class _FailingCompositeClient:
    async def get_logical_partition(self, _uuid: str):
        raise RuntimeError("primary request failed")

    async def list_child(self, *_args):
        raise AssertionError("child endpoint must not be called")


@pytest.mark.asyncio
async def test_parent_failure_prevents_child_fetch():
    with pytest.raises(RuntimeError, match="primary request failed"):
        await _fetch_lpar_data(_FailingCompositeClient(), "lpar-uuid")


class _MissingParentClient:
    async def get_logical_partition(self, _uuid: str):
        return None

    async def list_child(self, *_args):
        raise RuntimeError("child endpoint must not be called")

    async def get_managed_system(self, _uuid: str):
        return None

    async def list_logical_partitions(self, _uuid: str):
        raise RuntimeError("child endpoint must not be called")

    async def list_vios(self, _uuid: str):
        raise RuntimeError("child endpoint must not be called")


@pytest.mark.asyncio
async def test_missing_lpar_is_reported_before_child_fetch():
    with pytest.raises(ValueError, match="LPAR 'lpar-uuid' not found"):
        await _fetch_lpar_data(_MissingParentClient(), "lpar-uuid")


@pytest.mark.asyncio
async def test_missing_system_is_reported_before_child_fetch():
    with pytest.raises(ValueError, match="Managed system 'system-uuid' not found"):
        await _fetch_system_summary_data(_MissingParentClient(), "system-uuid")
