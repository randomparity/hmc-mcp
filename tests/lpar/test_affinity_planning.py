"""Tests for read-only memory-affinity planning over SSH."""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import asyncssh
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from hmc_mcp.server_tools import lpar_config as server_lpar_config
from hmc_mcp.authorization.access_policy import DEFAULT_CONNECTION_TOKEN
from hmc_mcp.client import HMCClient
from hmc_mcp.config import HMCConfig
from hmc_mcp.cli_commands.legacy_policy import compile_legacy_policy
from hmc_mcp.operations.ssh_network import (
    get_system_memopt_score as get_system_memopt_score_operation,
    plan_lpar_memopt_scores as plan_lpar_memopt_scores_operation,
    plan_system_memopt_score as plan_system_memopt_score_operation,
)
from hmc_mcp.ssh.transport import HMCCLIError
from hmc_mcp.ssh.affinity import (
    MemoptLparSelector,
    get_system_memopt_score,
    plan_lpar_memopt_scores,
    plan_system_memopt_score,
    validate_memopt_scenario,
)
from hmc_mcp.server import TOOL_SECURITY, create_mcp

SYSTEM = "p10-system"
LPAR_ROWS = (
    "lpar_name=web-1,lpar_id=1,curr_lpar_score=80,predicted_lpar_score=95\n"
    "lpar_name=db-1,lpar_id=2,curr_lpar_score=90,predicted_lpar_score=97"
)
SYSTEM_CURRENT = "curr_sys_score=84,firmware_extension=kept"
SYSTEM_PREDICTED = "curr_sys_score=84,predicted_sys_score=96,firmware_extension=kept"


def _config() -> HMCConfig:
    return HMCConfig(
        host="hmc.test",
        user="hscroot",
        password="abc123",  # pragma: allowlist secret
        _env_file=None,
    )


def _client() -> HMCClient:
    return HMCClient(_config())


def _connection(stdout: str = "") -> MagicMock:
    result = MagicMock(stdout=stdout)
    connection = AsyncMock()
    connection.run = AsyncMock(return_value=result)
    connection.__aenter__ = AsyncMock(return_value=connection)
    connection.__aexit__ = AsyncMock(return_value=False)
    return connection


def _process_error(stderr: str) -> asyncssh.ProcessError:
    return asyncssh.ProcessError(
        env={},
        command="lsmemopt",
        subsystem=None,
        exit_status=1,
        exit_signal=None,
        returncode=1,
        stdout="",
        stderr=stderr,
    )


@pytest.mark.parametrize(
    ("operation", "primitive", "result"),
    [
        (get_system_memopt_score_operation, "get_system_memopt_score", {"score": "1"}),
        (
            plan_lpar_memopt_scores_operation,
            "plan_lpar_memopt_scores",
            [{"score": "2"}],
        ),
        (
            plan_system_memopt_score_operation,
            "plan_system_memopt_score",
            {"score": "3"},
        ),
    ],
)
def test_shared_affinity_operations_resolve_system_uuid_before_delegating(
    operation, primitive, result
):
    system_uuid = "11111111-1111-1111-1111-111111111111"
    selector = MemoptLparSelector(names=("web",))
    delegated = AsyncMock(return_value=result)

    with (
        patch(
            "hmc_mcp.operations.ssh_network.resolve_ssh_names",
            AsyncMock(return_value=(SYSTEM, None)),
        ) as resolve,
        patch(f"hmc_mcp.operations.ssh_network._{primitive}", delegated),
    ):
        kwargs = (
            {"prioritized": selector, "excluded": None}
            if primitive.startswith("plan_")
            else {}
        )
        actual = asyncio.run(operation(_client(), system_uuid, **kwargs))

    assert actual == result
    resolve.assert_awaited_once_with(_config(), system_uuid, None)
    expected = (_config(), SYSTEM, selector, None) if kwargs else (_config(), SYSTEM)
    delegated.assert_awaited_once_with(*expected)


@pytest.mark.parametrize(
    ("prioritized", "excluded", "diagnostic"),
    [
        (
            MemoptLparSelector(names=("web",)),
            MemoptLparSelector(ids=(1,)),
            "must use the same representation",
        ),
        (
            MemoptLparSelector(names=("web",)),
            MemoptLparSelector(names=("web",)),
            "must not overlap",
        ),
    ],
)
def test_shared_planning_rejects_invalid_scenarios_before_system_resolution(
    prioritized, excluded, diagnostic
):
    resolve = AsyncMock()

    with patch("hmc_mcp.operations.ssh_network.resolve_ssh_names", resolve):
        with pytest.raises(ValueError, match=diagnostic):
            asyncio.run(
                plan_lpar_memopt_scores_operation(
                    _client(), SYSTEM, prioritized, excluded
                )
            )

    resolve.assert_not_awaited()


@pytest.mark.parametrize(
    ("adapter", "operation", "result"),
    [
        ("hmc_get_system_memopt_score", "get_system_memopt_score", {"score": "1"}),
        ("hmc_plan_lpar_memopt_scores", "plan_lpar_memopt_scores", [{"score": "2"}]),
        ("hmc_plan_system_memopt_score", "plan_system_memopt_score", {"score": "3"}),
    ],
)
def test_affinity_mcp_adapters_delegate_to_shared_operations(
    adapter, operation, result
):
    config = _config()
    client = HMCClient(config)
    context = AsyncMock()
    context.__aenter__.return_value = client
    selector = MemoptLparSelector(ids=(7,))
    delegated = AsyncMock(return_value=result)

    with (
        patch.object(
            server_lpar_config, "client_from_env", return_value=context
        ) as client_factory,
        patch.object(server_lpar_config, operation, delegated),
    ):
        kwargs = (
            {"prioritized": selector, "excluded": None}
            if operation.startswith("plan_")
            else {}
        )
        actual = getattr(server_lpar_config, adapter)(SYSTEM, profile="lab", **kwargs)

    assert actual == result
    client_factory.assert_called_once_with("lab")
    actual_client = delegated.await_args.args[0]
    assert actual_client is client
    expected = (SYSTEM, selector, None) if kwargs else (SYSTEM,)
    assert delegated.await_args.args[1:] == expected


@pytest.mark.parametrize(
    ("prioritized", "excluded", "diagnostic"),
    [
        (
            MemoptLparSelector(names=("web",)),
            MemoptLparSelector(ids=(1,)),
            "must use the same representation",
        ),
        (
            MemoptLparSelector(ids=(7,)),
            MemoptLparSelector(ids=(7,)),
            "must not overlap",
        ),
    ],
)
def test_affinity_mcp_rejects_invalid_scenarios_before_system_resolution(
    prioritized, excluded, diagnostic
):
    resolve = AsyncMock()

    with (
        patch.object(server_lpar_config, "client_from_env") as client_factory,
        patch("hmc_mcp.operations.ssh_network.resolve_ssh_names", resolve),
    ):
        with pytest.raises(ValueError, match=diagnostic):
            server_lpar_config.hmc_plan_system_memopt_score(
                SYSTEM, prioritized, excluded
            )

    resolve.assert_not_awaited()
    client_factory.assert_not_called()


def test_selector_accepts_one_representation_and_is_frozen():
    names = MemoptLparSelector(names=("web one", "db'one"))
    ids = MemoptLparSelector(ids=(1, 42))

    assert names.names == ("web one", "db'one")
    assert ids.ids == (1, 42)
    with pytest.raises(FrozenInstanceError):
        names.names = ("changed",)  # type: ignore[misc]


def _quote_heavy_dual_selector_package(extra_byte: bool = False):
    prioritized = MemoptLparSelector(names=("'" * 400 + "a" * 42,))
    excluded = MemoptLparSelector(names=("'" * 400 + "b" * (42 + extra_byte),))
    package = (
        f" -p {shlex.quote(prioritized.names[0])} -x {shlex.quote(excluded.names[0])}"
    )
    return prioritized, excluded, package


def test_selector_package_accepts_exact_quoted_aggregate_safety_ceiling():
    prioritized, excluded, package = _quote_heavy_dual_selector_package()

    validate_memopt_scenario(prioritized, excluded)

    assert len(package.encode("utf-8")) == 4096


def test_oversized_selector_is_rejected_before_resolution_or_transport():
    resolve = AsyncMock()
    prioritized, excluded, package = _quote_heavy_dual_selector_package(extra_byte=True)

    with patch("hmc_mcp.operations.ssh_network.resolve_ssh_names", resolve):
        with pytest.raises(ValueError, match="option package exceeds 4096 UTF-8 bytes"):
            asyncio.run(
                plan_lpar_memopt_scores_operation(
                    _client(), SYSTEM, prioritized, excluded
                )
            )

    assert len(package.encode("utf-8")) == 4097
    resolve.assert_not_awaited()


def test_mcp_rejects_oversized_selector_before_shared_operation():
    operation = AsyncMock()
    prioritized, excluded, package = _quote_heavy_dual_selector_package(extra_byte=True)
    policy = compile_legacy_policy(TOOL_SECURITY, (DEFAULT_CONNECTION_TOKEN,))
    application = create_mcp(policy)

    async def call_tool():
        async with Client(application) as client:
            return await client.call_tool(
                "hmc_plan_lpar_memopt_scores",
                {
                    "system_name_or_uuid": SYSTEM,
                    "prioritized": {"names": list(prioritized.names)},
                    "excluded": {"names": list(excluded.names)},
                },
            )

    with patch.object(server_lpar_config, "plan_lpar_memopt_scores", operation):
        with pytest.raises(ToolError, match="option package exceeds 4096 UTF-8 bytes"):
            asyncio.run(call_tool())

    assert len(package.encode("utf-8")) == 4097
    operation.assert_not_awaited()


@pytest.mark.parametrize(
    ("field", "values"),
    [("names", ["web", "db"]), ("ids", [1, 2])],
)
def test_selector_detaches_from_mutable_caller_sequences(field, values):
    selector = MemoptLparSelector(**{field: values})

    values.append("changed" if field == "names" else 3)

    assert getattr(selector, field) == tuple(values[:2])


@pytest.mark.parametrize(
    ("kwargs", "diagnostic"),
    [
        ({}, "must not be empty"),
        ({"names": ("one",), "ids": (1,)}, "names or ids"),
        ({"names": ("",)}, "names"),
        ({"names": ("   ",)}, "names"),
        ({"names": ("one,two",)}, "names"),
        ({"names": ("one\ntwo",)}, "names"),
        ({"names": ("same", "same")}, "duplicate"),
        ({"ids": (0,)}, "positive"),
        ({"ids": (-1,)}, "positive"),
        ({"ids": (1, 1)}, "duplicate"),
    ],
)
def test_selector_rejects_invalid_values(kwargs, diagnostic):
    with pytest.raises(ValueError, match=diagnostic):
        MemoptLparSelector(**kwargs)


@pytest.mark.parametrize(
    ("operation", "stdout", "expected_command"),
    [
        (
            get_system_memopt_score,
            SYSTEM_CURRENT,
            "lsmemopt -m p10-system -r sys -o currscore",
        ),
        (
            plan_lpar_memopt_scores,
            LPAR_ROWS,
            "lsmemopt -m p10-system -r lpar -o calcscore",
        ),
        (
            plan_system_memopt_score,
            SYSTEM_PREDICTED,
            "lsmemopt -m p10-system -r sys -o calcscore",
        ),
    ],
)
def test_score_operations_use_exact_unselected_commands(
    operation, stdout, expected_command
):
    connection = _connection(stdout)

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=connection):
        asyncio.run(operation(_config(), SYSTEM))

    connection.run.assert_called_once_with(expected_command, check=True, timeout=300.0)


@pytest.mark.parametrize(
    ("prioritized", "excluded", "suffix"),
    [
        (
            MemoptLparSelector(names=("web one", "db'one")),
            None,
            " -p 'web one,db'\"'\"'one'",
        ),
        (MemoptLparSelector(ids=(1, 42)), None, " --id 1,42"),
        (None, MemoptLparSelector(names=("web one",)), " -x 'web one'"),
        (None, MemoptLparSelector(ids=(2, 7)), " --xid 2,7"),
    ],
)
@pytest.mark.parametrize(
    ("operation", "scope", "stdout"),
    [
        (plan_lpar_memopt_scores, "lpar", LPAR_ROWS),
        (plan_system_memopt_score, "sys", SYSTEM_PREDICTED),
    ],
)
def test_planning_selectors_use_exact_flags(
    operation, scope, stdout, prioritized, excluded, suffix
):
    connection = _connection(stdout)

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=connection):
        asyncio.run(
            operation(_config(), SYSTEM, prioritized=prioritized, excluded=excluded)
        )

    connection.run.assert_called_once_with(
        f"lsmemopt -m {SYSTEM} -r {scope} -o calcscore{suffix}",
        check=True,
        timeout=300.0,
    )


def test_planning_combines_disjoint_selectors_in_stable_order():
    connection = _connection(LPAR_ROWS)

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=connection):
        asyncio.run(
            plan_lpar_memopt_scores(
                _config(),
                SYSTEM,
                prioritized=MemoptLparSelector(names=("web",)),
                excluded=MemoptLparSelector(names=("db",)),
            )
        )

    connection.run.assert_called_once_with(
        f"lsmemopt -m {SYSTEM} -r lpar -o calcscore -p web -x db",
        check=True,
        timeout=300.0,
    )


@pytest.mark.parametrize(
    ("prioritized", "excluded", "diagnostic"),
    [
        (
            MemoptLparSelector(names=("web",)),
            MemoptLparSelector(ids=(1,)),
            "prioritized and excluded selectors must use the same representation",
        ),
        (
            MemoptLparSelector(names=("web", "db")),
            MemoptLparSelector(names=("db",)),
            "prioritized and excluded selectors must not overlap",
        ),
        (
            MemoptLparSelector(ids=(1, 2)),
            MemoptLparSelector(ids=(2,)),
            "prioritized and excluded selectors must not overlap",
        ),
    ],
)
def test_planning_rejects_incompatible_selectors_before_transport(
    prioritized, excluded, diagnostic
):
    connection = _connection(LPAR_ROWS)

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=connection):
        with pytest.raises(ValueError, match=diagnostic):
            asyncio.run(
                plan_lpar_memopt_scores(
                    _config(),
                    SYSTEM,
                    prioritized=prioritized,
                    excluded=excluded,
                )
            )

    connection.run.assert_not_called()


def test_current_and_predicted_results_have_distinct_shapes():
    current_connection = _connection(SYSTEM_CURRENT)
    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=current_connection):
        current = asyncio.run(get_system_memopt_score(_config(), SYSTEM))

    predicted_connection = _connection(SYSTEM_PREDICTED)
    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=predicted_connection):
        predicted = asyncio.run(plan_system_memopt_score(_config(), SYSTEM))

    assert current == {"curr_sys_score": "84", "firmware_extension": "kept"}
    assert predicted == {
        "curr_sys_score": "84",
        "predicted_sys_score": "96",
        "firmware_extension": "kept",
        "prediction_guaranteed": False,
    }


def test_lpar_prediction_preserves_extensions_and_marks_each_row():
    connection = _connection(
        "lpar_name=web,lpar_id=1,curr_lpar_score=80,"
        "predicted_lpar_score=95,firmware_extension=kept"
    )

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=connection):
        rows = asyncio.run(plan_lpar_memopt_scores(_config(), SYSTEM))

    assert rows == [
        {
            "lpar_name": "web",
            "lpar_id": "1",
            "curr_lpar_score": "80",
            "predicted_lpar_score": "95",
            "firmware_extension": "kept",
            "prediction_guaranteed": False,
        }
    ]


def test_empty_lpar_prediction_is_an_empty_list():
    connection = _connection("")
    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=connection):
        assert asyncio.run(plan_lpar_memopt_scores(_config(), SYSTEM)) == []


@pytest.mark.parametrize(
    ("operation", "stdout", "missing"),
    [
        (get_system_memopt_score, "other=value", "curr_sys_score"),
        (
            plan_system_memopt_score,
            "curr_sys_score=84",
            "predicted_sys_score",
        ),
        (
            plan_lpar_memopt_scores,
            "lpar_name=web,lpar_id=1,curr_lpar_score=80",
            "predicted_lpar_score",
        ),
    ],
)
def test_score_operations_reject_missing_required_fields(operation, stdout, missing):
    connection = _connection(stdout)

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=connection):
        with pytest.raises(
            HMCCLIError, match=rf"row 1 is missing required fields: {missing}"
        ):
            asyncio.run(operation(_config(), SYSTEM))


@pytest.mark.parametrize(
    ("operation", "stdout", "field"),
    [
        (get_system_memopt_score, "curr_sys_score=", "curr_sys_score"),
        (get_system_memopt_score, "curr_sys_score", "curr_sys_score"),
        (
            plan_system_memopt_score,
            "curr_sys_score=84,predicted_sys_score=,firmware_extension=",
            "predicted_sys_score",
        ),
        (
            plan_system_memopt_score,
            "predicted_sys_score,curr_sys_score=84,firmware_extension=",
            "predicted_sys_score",
        ),
        (
            plan_lpar_memopt_scores,
            "lpar_name=web,lpar_id=1,curr_lpar_score=80,"
            "predicted_lpar_score=,firmware_extension=",
            "predicted_lpar_score",
        ),
        (
            plan_lpar_memopt_scores,
            "predicted_lpar_score,lpar_name=web,lpar_id=1,"
            "curr_lpar_score=80,firmware_extension=",
            "predicted_lpar_score",
        ),
    ],
)
def test_score_operations_reject_empty_required_fields(operation, stdout, field):
    connection = _connection(stdout)

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=connection):
        with pytest.raises(
            HMCCLIError, match=rf"row 1 has empty required fields: {field}"
        ):
            asyncio.run(operation(_config(), SYSTEM))


def test_score_operations_preserve_empty_extension_fields():
    connection = _connection("curr_sys_score=84,firmware_extension=")

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=connection):
        result = asyncio.run(get_system_memopt_score(_config(), SYSTEM))

    assert result == {"curr_sys_score": "84", "firmware_extension": ""}


@pytest.mark.parametrize(
    "operation", [get_system_memopt_score, plan_system_memopt_score]
)
@pytest.mark.parametrize(("stdout", "count"), [("", 0), ("a=1\na=2", 2)])
def test_system_score_operations_require_exactly_one_row(operation, stdout, count):
    connection = _connection(stdout)

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=connection):
        with pytest.raises(
            HMCCLIError, match=rf"returned {count} rows; expected exactly 1"
        ):
            asyncio.run(operation(_config(), SYSTEM))


@pytest.mark.parametrize(
    "diagnostic",
    [
        "The command is not supported on this HMC version.",
        "System has multiple resource groups; system affinity score is unavailable.",
        "Permission denied: user lacks authority for lsmemopt.",
        "HSCL9999 An unexpected lsmemopt failure occurred.",
    ],
)
def test_prediction_failures_retain_command_and_diagnostic_without_fallback(diagnostic):
    connection = _connection()
    connection.run = AsyncMock(side_effect=_process_error(diagnostic))

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=connection):
        with pytest.raises(HMCCLIError) as captured:
            asyncio.run(plan_system_memopt_score(_config(), SYSTEM))

    message = str(captured.value)
    assert "lsmemopt -m p10-system -r sys -o calcscore" in message
    assert diagnostic in message
    assert connection.run.call_count == 1
    assert "currscore" not in connection.run.call_args.args[0]
