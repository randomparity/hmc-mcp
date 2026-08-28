"""MCP fleet-health handler boundary tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from hmc_mcp.operations.health import FleetHealthResult
from hmc_mcp.server_tools.health import hmc_fleet_health


class _Context:
    def __init__(self, client: object) -> None:
        self.client = client

    async def __aenter__(self) -> object:
        return self.client

    async def __aexit__(self, *_args: object) -> None:
        return None


def test_fleet_health_handler_delegates_profile_and_preserves_shape() -> None:
    client = object()
    result = FleetHealthResult(
        systems=({"name": "sys1", "state": "Error"},),
        vios=(),
        lpars=(),
        failed_jobs=(),
        warnings=("jobs unavailable",),
    )
    operation = AsyncMock(return_value=result)
    with (
        patch(
            "hmc_mcp._app.client_from_env", return_value=_Context(client)
        ) as factory,
        patch("hmc_mcp.server_tools.health.fleet_health", operation),
    ):
        actual = hmc_fleet_health(profile="prod")

    factory.assert_called_once_with("prod")
    operation.assert_awaited_once_with(client)
    assert actual == {
        "systems": ({"name": "sys1", "state": "Error"},),
        "vios": (),
        "lpars": (),
        "failed_jobs": (),
        "warnings": ("jobs unavailable",),
    }
