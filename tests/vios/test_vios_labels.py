"""Command-boundary tests for bounded VIOS FC label administration."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import AsyncMock, call

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.operations import vios_labels as label_operations
from hmc_mcp.server_tools import vios_labels as label_tools
from hmc_mcp.ssh.transport import HMCCLIError
from hmc_mcp.ssh.vios_labels import (
    create_vios_vfc_group_label,
    list_vios_fc_port_labels,
    list_vios_vfc_group_labels,
    remove_vios_fc_port_label,
    remove_vios_vfc_group_label,
    set_vios_fc_port_label,
    update_vios_vfc_group_label,
)

CONFIG = HMCConfig.from_mapping({})
FIXTURES = Path(__file__).parents[1] / "fixtures" / "vios_labels"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "command"),
    [
        (
            lambda: list_vios_fc_port_labels(CONFIG, "system-a"),
            "lslabelvios -r fcport -m system-a -F --header",
        ),
        (
            lambda: list_vios_fc_port_labels(CONFIG, "system-a", vios_name="vios-a"),
            "lslabelvios -r fcport -m system-a --filter vios_names=vios-a -F --header",
        ),
        (
            lambda: list_vios_fc_port_labels(CONFIG, "system-a", vios_id=2),
            "lslabelvios -r fcport -m system-a --filter vios_ids=2 -F --header",
        ),
        (
            lambda: list_vios_vfc_group_labels(CONFIG, "system-a"),
            "lslabelvios -r group -m system-a --filter resources=vfc -F --header",
        ),
        (
            lambda: set_vios_fc_port_label(
                CONFIG, "system-a", "port-label", "fcs0", vios_id=2
            ),
            "labelvios -m system-a -o s -l port-label -i resource=fcport,port_name=fcs0,vios_ids=2",
        ),
        (
            lambda: set_vios_fc_port_label(
                CONFIG, "system-a", "port-label", "fcs0", vios_name="vios-a"
            ),
            "labelvios -m system-a -o s -l port-label -i resource=fcport,port_name=fcs0,vios_names=vios-a",
        ),
        (
            lambda: remove_vios_fc_port_label(
                CONFIG, "system-a", "fcs0", vios_name="vios-a"
            ),
            "labelvios -m system-a -o r -i resource=fcport,port_name=fcs0,vios_names=vios-a",
        ),
        (
            lambda: remove_vios_fc_port_label(CONFIG, "system-a", "fcs0", vios_id=2),
            "labelvios -m system-a -o r -i resource=fcport,port_name=fcs0,vios_ids=2",
        ),
        (
            lambda: create_vios_vfc_group_label(
                CONFIG, "system-a", "group-a", vios_names=["vios-a", "vios-b"]
            ),
            "labelvios -m system-a -o a -l group-a -i 'resource=vfc,\"vios_names=vios-a,vios-b\"'",
        ),
        (
            lambda: create_vios_vfc_group_label(
                CONFIG, "system-a", "group-a", vios_ids=[2, 3]
            ),
            "labelvios -m system-a -o a -l group-a -i 'resource=vfc,\"vios_ids=2,3\"'",
        ),
        (
            lambda: update_vios_vfc_group_label(
                CONFIG, "system-a", "group-a", "add-members", vios_ids=[2, 3]
            ),
            "labelvios -m system-a -o s -l group-a -i '\"vios_ids+=2,3\"'",
        ),
        (
            lambda: update_vios_vfc_group_label(
                CONFIG,
                "system-a",
                "group-a",
                "add-members",
                vios_names=["vios-a", "vios-b"],
            ),
            "labelvios -m system-a -o s -l group-a -i '\"vios_names+=vios-a,vios-b\"'",
        ),
        (
            lambda: update_vios_vfc_group_label(
                CONFIG,
                "system-a",
                "group-a",
                "remove-members",
                vios_names=["vios-a", "vios-b"],
            ),
            "labelvios -m system-a -o s -l group-a -i '\"vios_names-=vios-a,vios-b\"'",
        ),
        (
            lambda: update_vios_vfc_group_label(
                CONFIG, "system-a", "group-a", "remove-members", vios_ids=[2, 3]
            ),
            "labelvios -m system-a -o s -l group-a -i '\"vios_ids-=2,3\"'",
        ),
        (
            lambda: update_vios_vfc_group_label(
                CONFIG, "system-a", "group-a", "rename", new_name="group-b"
            ),
            "labelvios -m system-a -o s -l group-a -i new_name=group-b",
        ),
        (
            lambda: remove_vios_vfc_group_label(CONFIG, "system-a", "group-a"),
            "labelvios -m system-a -o r -l group-a",
        ),
    ],
)
async def test_exact_commands(
    monkeypatch, call: Callable[[], Awaitable[object]], command: str
):
    run = AsyncMock(return_value="ok\n")
    monkeypatch.setattr("hmc_mcp.ssh.vios_labels.run_hmc_command", run)
    await call()
    run.assert_awaited_once_with(CONFIG, command)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fixture", "case"),
    [
        ("hmc-v11r2-power10-fcport.csv", "hmc-v11r2-power10"),
        ("hmc-v11r2-power11-fcport.csv", "hmc-v11r2-power11"),
    ],
    ids=lambda value: value if value.startswith("hmc-") else None,
)
async def test_live_survey_fc_port_projection(monkeypatch, fixture: str, case: str):
    del case
    run = AsyncMock(return_value=(FIXTURES / fixture).read_text())
    monkeypatch.setattr("hmc_mcp.ssh.vios_labels.run_hmc_command", run)
    rows = await list_vios_fc_port_labels(CONFIG, "system-a")
    assert tuple(rows[0]) == (
        "name",
        "lpar_id",
        "port_name",
        "port_phys_loc",
        "port_label",
    )
    assert rows[0]["port_label"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["", "\n\n", "No results were found.\n"])
async def test_empty_list_results(monkeypatch, output: str):
    monkeypatch.setattr(
        "hmc_mcp.ssh.vios_labels.run_hmc_command", AsyncMock(return_value=output)
    )
    assert await list_vios_vfc_group_labels(CONFIG, "system-a") == []


@pytest.mark.asyncio
async def test_dynamic_headers_and_quoted_values(monkeypatch):
    output = 'name,vios_names,future\nfabric-a,"vios-a,vios-b",kept\n'
    monkeypatch.setattr(
        "hmc_mcp.ssh.vios_labels.run_hmc_command", AsyncMock(return_value=output)
    )
    assert await list_vios_vfc_group_labels(CONFIG, "system-a") == [
        {"name": "fabric-a", "vios_names": "vios-a,vios-b", "future": "kept"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("output", "condition"),
    [
        (",name\nx,y\n", "blank"),
        ("name,name\nx,y\n", "duplicate"),
        ('name,other\n"unterminated,x\n', "malformed"),
        ("name,other\nx\n", "columns"),
    ],
)
async def test_malformed_list_output(monkeypatch, output: str, condition: str):
    monkeypatch.setattr(
        "hmc_mcp.ssh.vios_labels.run_hmc_command", AsyncMock(return_value=output)
    )
    with pytest.raises(HMCCLIError, match=rf"list VIOS vFC group labels.*{condition}"):
        await list_vios_vfc_group_labels(CONFIG, "system-a")


@pytest.mark.asyncio
async def test_header_names_are_preserved_byte_for_byte(monkeypatch):
    output = "UPPER,Mixed-Case, padded \na,b,c\n"
    monkeypatch.setattr(
        "hmc_mcp.ssh.vios_labels.run_hmc_command", AsyncMock(return_value=output)
    )
    assert await list_vios_vfc_group_labels(CONFIG, "system-a") == [
        {"UPPER": "a", "Mixed-Case": "b", " padded ": "c"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        lambda: list_vios_fc_port_labels(CONFIG, " "),
        lambda: list_vios_fc_port_labels(CONFIG, "system-a", vios_name=" "),
        lambda: list_vios_fc_port_labels(CONFIG, "system-a", vios_name="a", vios_id=2),
        lambda: list_vios_fc_port_labels(CONFIG, "system-a", vios_id=0),
        lambda: set_vios_fc_port_label(CONFIG, "system-a", " ", "fcs0", vios_id=2),
        lambda: set_vios_fc_port_label(CONFIG, "system-a", "label", " ", vios_id=2),
        lambda: set_vios_fc_port_label(CONFIG, "system-a", "label", "fcs0"),
        lambda: set_vios_fc_port_label(
            CONFIG, "system-a", "label", "fcs0", vios_name="a", vios_id=2
        ),
        lambda: remove_vios_fc_port_label(CONFIG, "system-a", "fcs0"),
        lambda: create_vios_vfc_group_label(CONFIG, "system-a", "label"),
        lambda: create_vios_vfc_group_label(CONFIG, "system-a", "label", vios_names=[]),
        lambda: create_vios_vfc_group_label(
            CONFIG, "system-a", "label", vios_names=["a"], vios_ids=[2]
        ),
        lambda: create_vios_vfc_group_label(
            CONFIG, "system-a", "label", vios_names=["a", "a"]
        ),
        lambda: create_vios_vfc_group_label(
            CONFIG, "system-a", "label", vios_ids=[2, 2]
        ),
        lambda: create_vios_vfc_group_label(CONFIG, "system-a", "label", vios_ids=[0]),
        lambda: create_vios_vfc_group_label(
            CONFIG, "system-a", "label", vios_names=["bad,name"]
        ),
        lambda: create_vios_vfc_group_label(
            CONFIG, "system-a", "label", vios_names=['bad"name']
        ),
        lambda: create_vios_vfc_group_label(
            CONFIG, "system-a", "label", vios_names=["bad=name"]
        ),
        lambda: create_vios_vfc_group_label(
            CONFIG, "system-a", "label", vios_names=["bad\nname"]
        ),
        lambda: update_vios_vfc_group_label(CONFIG, "system-a", "label", "rename"),
        lambda: update_vios_vfc_group_label(
            CONFIG, "system-a", "label", "rename", new_name=" "
        ),
        lambda: update_vios_vfc_group_label(
            CONFIG, "system-a", "label", "rename", new_name="new", vios_ids=[2]
        ),
        lambda: update_vios_vfc_group_label(
            CONFIG, "system-a", "label", "add-members", new_name="new", vios_ids=[2]
        ),
        lambda: update_vios_vfc_group_label(CONFIG, "system-a", "label", "add-members"),
        lambda: update_vios_vfc_group_label(CONFIG, "system-a", "label", "invalid"),
    ],
)
async def test_invalid_input_does_not_dispatch(monkeypatch, call):
    run = AsyncMock()
    monkeypatch.setattr("hmc_mcp.ssh.vios_labels.run_hmc_command", run)
    with pytest.raises(HMCCLIError):
        await call()
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonblank_standalone_values_preserve_spaces_and_shell_quote(monkeypatch):
    run = AsyncMock(return_value="done\n")
    monkeypatch.setattr("hmc_mcp.ssh.vios_labels.run_hmc_command", run)
    result = await remove_vios_vfc_group_label(CONFIG, " system;$x ", " group label ")
    run.assert_awaited_once_with(
        CONFIG, "labelvios -m ' system;$x ' -o r -l ' group label '"
    )
    assert result["system_name"] == " system;$x "
    assert result["label"] == " group label "


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (
            lambda: set_vios_fc_port_label(
                CONFIG, "system-a", "label", "fcs0", vios_name="vios-a"
            ),
            {
                "operation": "set-vios-fc-port-label",
                "system_name": "system-a",
                "label": "label",
                "port_name": "fcs0",
                "vios_name": "vios-a",
                "output": "accepted",
            },
        ),
        (
            lambda: remove_vios_fc_port_label(CONFIG, "system-a", "fcs0", vios_id=2),
            {
                "operation": "remove-vios-fc-port-label",
                "system_name": "system-a",
                "port_name": "fcs0",
                "vios_id": 2,
                "output": "accepted",
            },
        ),
        (
            lambda: create_vios_vfc_group_label(
                CONFIG, "system-a", "group", vios_ids=[2, 3]
            ),
            {
                "operation": "create-vios-vfc-group-label",
                "system_name": "system-a",
                "label": "group",
                "vios_ids": [2, 3],
                "output": "accepted",
            },
        ),
        (
            lambda: update_vios_vfc_group_label(
                CONFIG, "system-a", "group", "add-members", vios_names=["a", "b"]
            ),
            {
                "operation": "add-members-vios-vfc-group-label",
                "system_name": "system-a",
                "label": "group",
                "vios_names": ["a", "b"],
                "output": "accepted",
            },
        ),
        (
            lambda: update_vios_vfc_group_label(
                CONFIG, "system-a", "group", "rename", new_name="renamed"
            ),
            {
                "operation": "rename-vios-vfc-group-label",
                "system_name": "system-a",
                "label": "group",
                "new_name": "renamed",
                "output": "accepted",
            },
        ),
        (
            lambda: remove_vios_vfc_group_label(CONFIG, "system-a", "group"),
            {
                "operation": "remove-vios-vfc-group-label",
                "system_name": "system-a",
                "label": "group",
                "output": "accepted",
            },
        ),
    ],
)
async def test_mutation_receipts(monkeypatch, call, expected):
    monkeypatch.setattr(
        "hmc_mcp.ssh.vios_labels.run_hmc_command",
        AsyncMock(return_value=" accepted \n"),
    )
    assert await call() == expected


@pytest.mark.asyncio
async def test_operation_resolves_system_uuid_and_preserves_arguments(monkeypatch):
    resolve = AsyncMock(return_value="system-a")
    dispatch = AsyncMock(return_value={"operation": "set"})
    monkeypatch.setattr(label_operations, "resolve_system_name", resolve)
    monkeypatch.setattr(label_operations.ssh, "set_vios_fc_port_label", dispatch)

    result = await label_operations.set_vios_fc_port_label(
        CONFIG, "00000000-0000-0000-0000-000000000001", " label ", "fcs0", vios_id=2
    )

    assert result == {"operation": "set"}
    resolve.assert_awaited_once_with(CONFIG, "00000000-0000-0000-0000-000000000001")
    dispatch.assert_awaited_once_with(
        CONFIG, "system-a", " label ", "fcs0", vios_name=None, vios_id=2
    )


@pytest.mark.asyncio
async def test_operation_resolution_failure_prevents_dispatch(monkeypatch):
    monkeypatch.setattr(
        label_operations, "resolve_system_name", AsyncMock(return_value=None)
    )
    dispatch = AsyncMock()
    monkeypatch.setattr(label_operations.ssh, "list_vios_vfc_group_labels", dispatch)
    with pytest.raises(HMCCLIError, match="resolve managed system"):
        await label_operations.list_vios_vfc_group_labels(CONFIG, "missing")
    dispatch.assert_not_awaited()


@pytest.mark.parametrize(
    ("handler", "operation_name", "arguments", "expected", "operation_call"),
    [
        (
            label_tools.hmc_list_vios_fc_port_labels,
            "list_vios_fc_port_labels",
            {"vios_name": "a"},
            [{"name": "a"}],
            call(CONFIG, "system-a", vios_name="a", vios_id=None),
        ),
        (
            label_tools.hmc_set_vios_fc_port_label,
            "set_vios_fc_port_label",
            {"label": "l", "port_name": "fcs0", "vios_id": 2},
            {"operation": "set"},
            call(
                CONFIG,
                "system-a",
                "l",
                "fcs0",
                vios_name=None,
                vios_id=2,
            ),
        ),
        (
            label_tools.hmc_remove_vios_fc_port_label,
            "remove_vios_fc_port_label",
            {"port_name": "fcs0", "vios_id": 2},
            {"operation": "remove"},
            call(CONFIG, "system-a", "fcs0", vios_name=None, vios_id=2),
        ),
        (
            label_tools.hmc_list_vios_vfc_group_labels,
            "list_vios_vfc_group_labels",
            {},
            [{"name": "g"}],
            call(CONFIG, "system-a"),
        ),
        (
            label_tools.hmc_create_vios_vfc_group_label,
            "create_vios_vfc_group_label",
            {"label": "g", "vios_names": ["a"]},
            {"operation": "create"},
            call(CONFIG, "system-a", "g", vios_names=["a"], vios_ids=None),
        ),
        (
            label_tools.hmc_update_vios_vfc_group_label,
            "update_vios_vfc_group_label",
            {"label": "g", "action": "rename", "new_name": "h"},
            {"operation": "rename"},
            call(
                CONFIG,
                "system-a",
                "g",
                "rename",
                new_name="h",
                vios_names=None,
                vios_ids=None,
            ),
        ),
        (
            label_tools.hmc_remove_vios_vfc_group_label,
            "remove_vios_vfc_group_label",
            {"label": "g"},
            {"operation": "remove-group"},
            call(CONFIG, "system-a", "g"),
        ),
    ],
)
def test_mcp_handlers_pass_through_profile_and_results(
    monkeypatch,
    handler,
    operation_name: str,
    arguments: dict[str, object],
    expected: object,
    operation_call,
):
    captured: dict[str, object] = {}

    def fake_with_config(fn, *, profile=None):
        captured["profile"] = profile
        return __import__("asyncio").run(fn(CONFIG))

    operation = AsyncMock(return_value=expected)
    monkeypatch.setattr(label_tools, "with_config", fake_with_config)
    monkeypatch.setattr(label_tools.operations, operation_name, operation)
    assert handler("system-a", profile="lab", **arguments) == expected
    assert captured == {"profile": "lab"}
    assert operation.await_args == operation_call


def test_all_mcp_handlers_expose_managed_system_selector():
    import inspect

    handlers = [
        label_tools.hmc_list_vios_fc_port_labels,
        label_tools.hmc_set_vios_fc_port_label,
        label_tools.hmc_remove_vios_fc_port_label,
        label_tools.hmc_list_vios_vfc_group_labels,
        label_tools.hmc_create_vios_vfc_group_label,
        label_tools.hmc_update_vios_vfc_group_label,
        label_tools.hmc_remove_vios_vfc_group_label,
    ]
    assert all(
        "system_name_or_uuid" in inspect.signature(handler).parameters
        for handler in handlers
    )


def test_vios_label_tool_security_is_exact_and_exhaustive():
    expected = {
        "hmc_list_vios_fc_port_labels": ("read", "vios_label.list_fc_ports"),
        "hmc_set_vios_fc_port_label": ("mutate", "vios_label.set_fc_port"),
        "hmc_remove_vios_fc_port_label": (
            "destructive",
            "vios_label.remove_fc_port",
        ),
        "hmc_list_vios_vfc_group_labels": ("read", "vios_label.list_vfc_groups"),
        "hmc_create_vios_vfc_group_label": (
            "mutate",
            "vios_label.create_vfc_group",
        ),
        "hmc_update_vios_vfc_group_label": (
            "mutate",
            "vios_label.update_vfc_group",
        ),
        "hmc_remove_vios_vfc_group_label": (
            "destructive",
            "vios_label.remove_vfc_group",
        ),
    }
    security = label_tools.tool_security()
    assert set(security) == set(expected)
    for name, (effect, operation) in expected.items():
        record = security[name]
        assert (record.effect, record.operation, record.target_kind) == (
            effect,
            operation,
            "managed_system",
        )
        assert record.exhaustive_targets is True
        assert [(target.kind, target.path) for target in record.targets] == [
            ("managed_system", "system_name_or_uuid")
        ]
