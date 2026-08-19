"""Composition-time capability ceiling and effective-permission inspection.

Covers docs/workflow/specs/2026-08-18-capability-ceiling-design.md; the decision
record is docs/adr/0037-composition-time-capability-ceiling.md.
"""

from __future__ import annotations

import asyncio

from hmc_mcp.access_policy import compile_access_policy
from hmc_mcp.server import TOOL_SECURITY, create_mcp

SOURCE = "test-access-policy.toml"


def _policy(grants: list[dict], name: str = "test"):
    """Compile one named policy from grant tables, against the live tool index."""
    return compile_access_policy(
        {"policies": {name: {"grants": grants}}}, name, TOOL_SECURITY, SOURCE
    )


def _names(application) -> set[str]:
    return {tool.name for tool in asyncio.run(application.list_tools())}


READ_ONLY_GRANT = [
    {"effects": ["read"], "connections": ["<default>"], "targets": "all-targets"}
]


def test_a_read_only_policy_registers_only_read_tools():
    """R1: the registry is exactly the ceiling, minus the escape hatch."""
    policy = _policy(READ_ONLY_GRANT)
    names = _names(create_mcp(policy))

    assert names == {
        name for name in TOOL_SECURITY if policy.permits_tool(name)
    } - {"hmc_run_command"}
    assert names
    assert all(TOOL_SECURITY[name].effect == "read" for name in names)
    assert "hmc_delete_lpar" not in names


def test_no_policy_applies_no_ceiling():
    """R2: the default composition is unfiltered."""
    names = _names(create_mcp())

    assert names == set(TOOL_SECURITY) - {"hmc_run_command"}


def test_applications_composed_with_different_policies_are_independent():
    """R3: a restrictive composition does not leak into a later one."""
    restricted = _names(create_mcp(_policy(READ_ONLY_GRANT)))
    unrestricted = _names(create_mcp())
    restricted_again = _names(create_mcp(_policy(READ_ONLY_GRANT)))

    assert restricted == restricted_again
    assert restricted < unrestricted


def _inspect(application):
    """Call the registered inspection tool through the application.

    Reads ``structured_content`` (a plain dict), not ``data`` — on
    fastmcp 3.4.7 ``data`` is a pydantic model reconstructed from the output
    schema, which is neither subscriptable nor iterable.
    """
    from fastmcp import Client

    async def _go():
        async with Client(application) as client:
            result = await client.call_tool("hmc_effective_permissions", {})
            return result.structured_content

    return asyncio.run(_go())


def test_inspection_is_registered_and_classified():
    """R10: an ordinary read-effect record with no connection argument."""
    security = TOOL_SECURITY["hmc_effective_permissions"]

    assert security.effect == "read"
    assert security.operation == "permissions.describe"
    assert security.target_kind == "none"
    assert security.targets == ()
    assert security.connection_argument is None
    assert "hmc_effective_permissions" in _names(create_mcp())


def test_inspection_is_subject_to_the_ceiling():
    """R11: an effects grant reaches it; a tools-only grant can withhold it."""
    assert "hmc_effective_permissions" in _names(create_mcp(_policy(READ_ONLY_GRANT)))

    withheld = _policy(
        [{"tools": ["hmc_list_systems"], "connections": ["lab"], "targets": "all-targets"}]
    )
    assert "hmc_effective_permissions" not in _names(create_mcp(withheld))


def test_inspection_matches_the_registry_and_the_policy():
    """R12: equal to the live registry, and to the policy-derived expectation."""
    policy = _policy(READ_ONLY_GRANT)
    application = create_mcp(policy)

    reported = [tool["name"] for tool in _inspect(application)["tools"]]
    live = sorted(tool.name for tool in asyncio.run(application.list_tools()))
    expected = sorted(
        {name for name in TOOL_SECURITY if policy.permits_tool(name)} - {"hmc_run_command"}
    )

    assert reported == live == expected


def test_inspection_reports_effects_source_and_dimensions():
    """R13, R14, R15, R16: what the payload says about the policy."""
    policy = _policy(READ_ONLY_GRANT)
    result = _inspect(create_mcp(policy))

    assert result["effects"] == ["read"]
    assert result["policy_name"] == "test"
    assert result["policy_source"] == SOURCE
    assert result["ceiling_enforced"] is True
    assert result["enforced_dimensions"] == ["tools"]
    assert result["declared_only_dimensions"] == ["connections", "targets"]

    grant = result["declared_grants"][0]
    assert grant["connections"] == ["<default>"]
    assert grant["all_targets"] is True
    assert grant["targets"] == {}
    assert "hmc_list_systems" in grant["tools"]


def test_inspection_reports_no_policy_honestly():
    """R14, R16: nothing is enforced and nothing is declared."""
    result = _inspect(create_mcp())

    assert result["policy_name"] is None
    assert result["policy_source"] is None
    assert result["ceiling_enforced"] is False
    assert result["enforced_dimensions"] == []
    assert result["declared_only_dimensions"] == []
    assert result["declared_grants"] == []


def test_inspection_reports_grants_separately_without_merging():
    """R15: one entry per grant, in document order, never unioned."""
    policy = _policy([
        {"effects": ["read"], "connections": ["lab"], "targets": "all-targets"},
        {
            "tools": ["hmc_delete_lpar"],
            "connections": ["scratch"],
            "targets": {"lpar": ["db-01"], "managed_system": ["sys-a"]},
        },
    ])
    grants = _inspect(create_mcp(policy))["declared_grants"]

    assert [grant["connections"] for grant in grants] == [["lab"], ["scratch"]]
    assert grants[1]["all_targets"] is False
    assert grants[1]["targets"] == {"lpar": ["db-01"], "managed_system": ["sys-a"]}


def test_inspection_does_not_raise_on_a_tool_outside_the_index():
    """R13: an unindexed registered name is reported, not fatal."""
    application = create_mcp()

    def hmc_not_in_the_index() -> str:
        """A tool registered outside the authoritative index."""
        return "ok"

    application.tool(hmc_not_in_the_index)
    reported = {tool["name"]: tool for tool in _inspect(application)["tools"]}

    assert reported["hmc_not_in_the_index"]["effect"] == "unknown"
    assert reported["hmc_not_in_the_index"]["operation"] == "unknown"
    assert reported["hmc_not_in_the_index"]["target_kind"] == "unknown"
    assert "unknown" not in _inspect(application)["effects"]


def test_inspection_carries_only_allowlisted_value_sources(monkeypatch):
    """R17: no config.toml value and no HMC_* environment value reaches the payload.

    The sentinels must exist in the process for their absence to mean anything —
    an unset variable is absent from every payload, correct or leaking — and they
    are checked as *values*, since a leak of HMC_PASSWORD carries its value and
    not its name.
    """
    monkeypatch.setenv("HMC_HOST", "sentinel-host-do-not-leak")
    monkeypatch.setenv("HMC_USER", "sentinel-user-do-not-leak")
    monkeypatch.setenv("HMC_PASSWORD", "sentinel-password-do-not-leak")

    policy = _policy(READ_ONLY_GRANT)
    result = _inspect(create_mcp(policy))

    assert set(result) == {
        "policy_name",
        "policy_source",
        "ceiling_enforced",
        "effects",
        "tools",
        "declared_grants",
        "enforced_dimensions",
        "declared_only_dimensions",
    }
    rendered = repr(result)
    for sentinel in (
        "sentinel-host-do-not-leak",
        "sentinel-user-do-not-leak",
        "sentinel-password-do-not-leak",
    ):
        assert sentinel not in rendered
