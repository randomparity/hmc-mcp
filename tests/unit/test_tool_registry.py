"""Direct contract tests for module-local MCP tool collection."""

from __future__ import annotations

import asyncio
import inspect

import pytest

from hmc_mcp._app import create_mcp
from hmc_mcp.tool_registry import (
    ToolSecurity,
    annotations_for,
    build_tool_security,
    tool_module,
    validate_security,
)


def _tool_names(application) -> set[str]:
    return {tool.name for tool in asyncio.run(application.list_tools())}


def test_tool_modules_collect_definitions_in_isolation():
    first_tool, first_register, _ = tool_module()
    second_tool, second_register, _ = tool_module()

    @first_tool(
        effect="read",
        operation="first.read",
        target_kind="console",
        connection_argument=None,
    )
    def first_handler() -> str:
        return "first"

    @second_tool(
        effect="read",
        operation="second.read",
        target_kind="console",
        connection_argument=None,
    )
    def second_handler() -> str:
        return "second"

    first_application = create_mcp()
    second_application = create_mcp()
    first_register(first_application)
    second_register(second_application)

    assert _tool_names(first_application) == {"first_handler"}
    assert _tool_names(second_application) == {"second_handler"}


def test_tool_module_derives_annotations_and_preserves_handler():
    tool, register_tools, security = tool_module()

    @tool(
        effect="read",
        operation="status.read",
        target_kind="console",
        connection_argument=None,
    )
    def status() -> str:
        return "ok"

    application = create_mcp()
    register_tools(application)
    registered = asyncio.run(application.list_tools())

    assert status() == "ok"
    assert len(registered) == 1
    assert registered[0].name == "status"
    assert registered[0].annotations == annotations_for("read")
    assert security()["status"].operation == "status.read"


def test_same_definitions_register_on_independent_applications():
    tool, register_tools, _ = tool_module()

    @tool(
        effect="read",
        operation="ping.read",
        target_kind="console",
        connection_argument=None,
    )
    def ping() -> str:
        return "pong"

    first = create_mcp()
    second = create_mcp()
    register_tools(first)
    register_tools(second)

    assert _tool_names(first) == {"ping"}
    assert _tool_names(second) == {"ping"}


def test_targets_are_built_from_the_argument_table():
    tool, _register, security = tool_module()

    @tool(effect="mutate", operation="lpar.migrate", target_kind="lpar")
    def migrate(
        lpar_name_or_uuid: str,
        target_system_name_or_uuid: str,
        system_name_or_uuid: str | None = None,
        profile: str | None = None,
    ) -> str:
        return "ok"

    targets = security()["migrate"].targets
    assert [(t.kind, t.argument, t.required) for t in targets] == [
        ("lpar", "lpar_name_or_uuid", True),
        ("managed_system", "target_system_name_or_uuid", True),
        ("managed_system", "system_name_or_uuid", False),
    ]


def test_extra_targets_supply_a_kind_the_table_cannot_name():
    tool, _register, security = tool_module()

    @tool(
        effect="destructive",
        operation="user.delete",
        target_kind="user",
        extra_targets=(("user", "name"),),
    )
    def remove_user(name: str, profile: str | None = None) -> str:
        return "ok"

    targets = security()["remove_user"].targets
    assert [(t.kind, t.argument, t.required) for t in targets] == [("user", "name", True)]


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"effect": "nonsense", "operation": "a.b", "target_kind": "console"}, "effect"),
        ({"effect": "read", "operation": "a.b", "target_kind": "nowhere"}, "target_kind"),
        ({"effect": "read", "operation": "nodot", "target_kind": "console"}, "operation"),
        (
            {
                "effect": "read",
                "operation": "a.b",
                "target_kind": "console",
                "extra_targets": (("lpar", "absent_argument"),),
            },
            "absent_argument",
        ),
        (
            {
                "effect": "read",
                "operation": "a.b",
                "target_kind": "console",
                "connection_argument": "absent_profile",
            },
            "absent_profile",
        ),
        ({"effect": "read", "operation": "a.b", "target_kind": "none"}, "none"),
        ({"effect": "read", "operation": "a.b", "target_kind": "user"}, "user"),
        (
            {
                "effect": "read",
                "operation": "a.b",
                "target_kind": "console",
                "extra_targets": (("lpar", "lpar_name_or_uuid"),),
            },
            "lpar_name_or_uuid",
        ),
    ],
    ids=[
        "v2-effect",
        "v3-kind",
        "v4-operation",
        "v5-extra-arg",
        "v6-connection",
        "v7-none",
        "v8-no-subject",
        "v9-duplicate",
    ],
)
def test_tool_rejects_contradictory_declarations(kwargs, message):
    tool, _register, _security = tool_module()

    with pytest.raises(ValueError, match=message):

        @tool(**kwargs)
        def sample(lpar_name_or_uuid: str, profile: str | None = None) -> str:
            return "ok"


def test_tool_requires_the_three_mandatory_fields():
    tool, _register, _security = tool_module()

    with pytest.raises(TypeError):

        @tool(effect="read")
        def sample(profile: str | None = None) -> str:
            return "ok"


def test_annotations_cover_exactly_the_effect_vocabulary():
    assert annotations_for("read").readOnlyHint is True
    assert annotations_for("mutate").readOnlyHint is False
    assert annotations_for("mutate").destructiveHint is None
    assert annotations_for("destructive").destructiveHint is True
    assert annotations_for("destructive").readOnlyHint is False
    assert annotations_for("arbitrary-command").destructiveHint is True
    with pytest.raises(KeyError):
        annotations_for("invented")  # ty: ignore[invalid-argument-type]


def test_required_uses_absence_of_a_default_not_a_none_default():
    tool, _register, security = tool_module()

    @tool(effect="read", operation="pinned.read", target_kind="managed_system")
    def pinned(system_name_or_uuid: str = "Server-1", profile: str | None = None) -> str:
        return "ok"

    selector = security()["pinned"].targets[0]
    assert selector.required is False
    assert inspect.signature(pinned).parameters["system_name_or_uuid"].default == "Server-1"


def test_build_tool_security_rejects_duplicate_names_and_operations():
    console = ToolSecurity(effect="read", operation="a.read", target_kind="console")
    other = ToolSecurity(effect="read", operation="b.read", target_kind="console")

    with pytest.raises(ValueError, match="duplicate tool name"):
        build_tool_security([{"one": console}, {"one": other}], {})

    with pytest.raises(ValueError, match="duplicate operation"):
        build_tool_security([{"one": console}, {"two": console}], {})

    with pytest.raises(ValueError, match="duplicate tool name"):
        build_tool_security([{"one": console}], {"one": other})


def test_build_tool_security_merges_modules_and_extras():
    first = ToolSecurity(effect="read", operation="a.read", target_kind="console")
    second = ToolSecurity(effect="mutate", operation="b.write", target_kind="console")

    index = build_tool_security([{"one": first}], {"two": second})

    assert index == {"one": first, "two": second}


def test_module_classifications_are_read_only():
    tool, _register, security = tool_module()

    @tool(
        effect="read",
        operation="probe.read",
        target_kind="console",
        connection_argument=None,
    )
    def probe() -> str:
        return "ok"

    with pytest.raises(TypeError):
        security()["probe"] = ToolSecurity(
            effect="destructive", operation="probe.wipe", target_kind="console"
        )


def test_a_handler_whose_signature_cannot_be_read_is_named_in_the_error():
    tool, _register, _security = tool_module()

    class Unreadable:
        __name__ = "hmc_unreadable_tool"

        @property
        def __signature__(self):
            raise RuntimeError("signature unavailable")

        def __call__(self, profile: str | None = None) -> str:
            return "ok"

    with pytest.raises(RuntimeError, match="hmc_unreadable_tool"):
        tool(effect="read", operation="a.b", target_kind="console")(Unreadable())


def test_validate_security_accepts_a_console_declaration_with_no_targets():
    def handler(profile: str | None = None) -> str:
        return "ok"

    validate_security(
        ToolSecurity(effect="read", operation="console.info", target_kind="console"),
        handler,
    )
