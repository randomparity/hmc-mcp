"""Composition-time capability ceiling and effective-permission inspection.

Covers docs/workflow/specs/2026-08-18-capability-ceiling-design.md; the decision
record is docs/adr/0037-composition-time-capability-ceiling.md.
"""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP

from hmc_mcp.access_policy import DEFAULT_CONNECTION_TOKEN, compile_access_policy
from hmc_mcp.legacy_policy import compile_legacy_policy
from hmc_mcp.server import TOOL_SECURITY, create_mcp


def _legacy(*, include_arbitrary_command: bool = False):
    """The policy that replaced the unfiltered default composition (ADR 0041)."""
    return compile_legacy_policy(
        TOOL_SECURITY,
        (DEFAULT_CONNECTION_TOKEN,),
        include_arbitrary_command=include_arbitrary_command,
    )


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


def test_a_withheld_tool_cannot_be_called_by_name():
    """R1: absence is the control — unlisted is not enough, it must not dispatch.

    Listing and dispatch read the same registry today, so this holds by
    construction. #222 adds authorization at the dispatch boundary, which is the
    change that could filter the listing while leaving dispatch open; this is the
    invariant it has to preserve.
    """
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    application = create_mcp(_policy(READ_ONLY_GRANT))

    async def _call():
        async with Client(application) as client:
            return await client.call_tool(
                "hmc_delete_lpar", {"lpar_name_or_uuid": "victim"}
            )

    with pytest.raises(ToolError, match="Unknown tool"):
        asyncio.run(_call())


def test_no_policy_applies_no_ceiling():
    """R2, inverted by ADR 0041: there is no unfiltered composition to have a ceiling.

    This asserted that `create_mcp()` registered every tool but `hmc_run_command`. A
    policy is mandatory now, so the ceiling-free registry this described cannot be
    built at all. The claim it made about the *surface* did not disappear with it — it
    moved to the legacy-equivalent policy, where G2 in
    tests/app/test_fail_closed_startup.py pins the same set.
    """
    with pytest.raises(TypeError):
        create_mcp()  # ty: ignore[missing-argument]

    with pytest.raises(TypeError, match="init-access-policy"):
        create_mcp(None)  # ty: ignore[invalid-argument-type]


def test_applications_composed_with_different_policies_are_independent():
    """R3: a restrictive composition does not leak into a later one."""
    restricted = _names(create_mcp(_policy(READ_ONLY_GRANT)))
    unrestricted = _names(create_mcp(_legacy()))
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
    assert "hmc_effective_permissions" in _names(create_mcp(_legacy()))


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
    assert result["enforced_dimensions"] == ["tools", "connections", "targets"]
    assert result["declared_only_dimensions"] == []

    grant = result["declared_grants"][0]
    assert grant["connections"] == ["<default>"]
    assert grant["all_targets"] is True
    assert grant["targets"] == {}
    assert "hmc_list_systems" in grant["tools"]


def _guarded_stub(name: str = "hmc_list_systems"):
    """A registered callable built the way composition builds one.

    Through `authorized` rather than by setting the marker by hand: a stub that
    forged the witness would let the recogniser rot without a test noticing.
    """
    from hmc_mcp.tool_registry import authorized

    def handler(profile: str | None = None) -> str:
        return "ok"

    return authorized(name, TOOL_SECURITY[name], handler, lambda *_args: None)


def test_inspection_reports_no_policy_honestly():
    """R14, R16, retargeted by ADR 0041: no *composed* application reaches this arm.

    The behaviour is unchanged and still worth pinning — `describe` documents itself as
    binding a direct caller, and ADR 0041 deliberately kept its `AccessPolicy | None`
    parameter rather than narrowing the public report's schema. What changed is the
    route: `create_mcp()` no longer produces an application to ask, so the honest
    null-reporting is asserted against `describe` itself.

    Both dimension tuples are empty here and that is not the partition ADR 0047
    imposes elsewhere: with no policy selected nothing is declared, so nothing can
    be declared-only either.
    """
    from dataclasses import asdict

    from hmc_mcp.server_permissions import describe

    result = asdict(describe({"hmc_list_systems": _guarded_stub()}, None, TOOL_SECURITY))

    assert result["policy_name"] is None
    assert result["policy_source"] is None
    assert result["ceiling_enforced"] is False
    assert result["enforced_dimensions"] == ()
    assert result["declared_only_dimensions"] == ()
    assert result["declared_grants"] == ()


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
    application = create_mcp(_legacy())

    def hmc_not_in_the_index() -> str:
        """A tool registered outside the authoritative index."""
        return "ok"

    application.tool(hmc_not_in_the_index)
    result = _inspect(application)
    reported = {tool["name"]: tool for tool in result["tools"]}

    assert reported["hmc_not_in_the_index"]["effect"] == "unknown"
    assert reported["hmc_not_in_the_index"]["operation"] == "unknown"
    assert reported["hmc_not_in_the_index"]["target_kind"] == "unknown"
    assert "unknown" not in result["effects"]


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


def _configure(application, enabled, permits=None, policy=None):
    """Drive the escape-hatch toggle the way `_serve_application` drives it.

    Both gates are required since ADR 0041: this is the one registration site outside
    `create_mcp`, and while they defaulted to None a call here registered
    `hmc_run_command` with no ceiling and no authorizer whatever the policy said.
    `permits` stays a separate parameter because these tests vary the ceiling
    independently of the authorizer to isolate which gate withheld the tool.
    """
    from hmc_mcp.dispatch_scope import dispatch_authorizer
    from hmc_mcp.server_command import configure_arbitrary_command_tool

    effective = policy if policy is not None else _legacy(include_arbitrary_command=True)
    asyncio.run(
        configure_arbitrary_command_tool(
            enabled,
            application,
            permits=permits if permits is not None else effective.permits_tool,
            authorize=dispatch_authorizer(effective),
        )
    )


# Reaches the escape hatch by name (ADR 0036 forbids granting it by effect
# class) and the rest of the read class by effect — including
# hmc_effective_permissions, without which _inspect has no tool to call.
GRANT_RUN_COMMAND = [
    {
        "effects": ["read"],
        "tools": ["hmc_run_command"],
        "connections": ["<default>"],
        "targets": "all-targets",
    }
]


def test_escape_hatch_needs_both_the_flag_and_the_grant():
    """R5: the flag and the ceiling compose conjunctively."""
    granted = _policy(GRANT_RUN_COMMAND)
    application = create_mcp(granted)

    _configure(application, True, granted.permits_tool)
    assert "hmc_run_command" in _names(application)

    _configure(application, False, granted.permits_tool)
    assert "hmc_run_command" not in _names(application)


def test_escape_hatch_is_withheld_when_the_policy_omits_it():
    """R5, R6: an effect-class policy cannot reach it, flag or no flag."""
    every_effect = _policy([
        {
            "effects": ["read", "mutate", "destructive"],
            "connections": ["<default>"],
            "targets": "all-targets",
        }
    ])
    assert every_effect.permits_tool("hmc_run_command") is False

    application = create_mcp(every_effect)
    _configure(application, True, every_effect.permits_tool)

    assert "hmc_run_command" not in _names(application)


def test_inspection_tracks_the_arbitrary_command_toggle():
    """R12, R13: the report follows the registry across a post-composition change."""
    granted = _policy(GRANT_RUN_COMMAND)
    application = create_mcp(granted)
    _configure(application, True, granted.permits_tool)

    result = _inspect(application)
    reported = [tool["name"] for tool in result["tools"]]

    assert reported == sorted(tool.name for tool in asyncio.run(application.list_tools()))
    assert "hmc_run_command" in reported
    assert "arbitrary-command" in result["effects"]
    assert result["ceiling_enforced"] is True


def test_a_registry_that_drifted_past_its_ceiling_demotes_only_the_tool_dimension():
    """R14, R16 as amended by ADR 0047: drift is a fact about one dimension.

    Before that amendment both tuples derived from `ceiling_enforced`, so this
    state reported `enforced_dimensions == []` *and*
    `declared_only_dimensions == []`: all three dimensions vanished from a report
    that went on enumerating connections and targets in `declared_grants`. The
    live denial below is the evidence that the silence was wrong — the connection
    dimension is enforcing, under the very policy the report names.
    """
    from fastmcp.exceptions import ToolError

    policy = _policy(READ_ONLY_GRANT)
    application = create_mcp(policy)
    _configure(application, True)  # no predicate: the fail-open call shape

    result = _inspect(application)

    assert "hmc_run_command" in [tool["name"] for tool in result["tools"]]
    assert result["policy_name"] == "test"
    assert result["ceiling_enforced"] is False
    assert result["enforced_dimensions"] == ["connections", "targets"]
    assert result["declared_only_dimensions"] == ["tools"]

    async def _ungranted_connection():
        from fastmcp import Client

        async with Client(application) as client:
            return await client.call_tool("hmc_list_systems", {"profile": "prod"})

    with pytest.raises(ToolError, match="not permitted on connection 'prod'"):
        asyncio.run(_ungranted_connection())


def test_the_two_dimension_tuples_partition_every_dimension_under_a_policy():
    """ADR 0047: a selected policy never leaves a dimension out of both tuples.

    The property the old encoding broke. Asserted over the enforcing composition
    and the drifted one together, since the bug was visible only in the second.
    """
    from hmc_mcp.server_permissions import DIMENSIONS

    enforcing = create_mcp(_policy(READ_ONLY_GRANT))
    drifted = create_mcp(_policy(READ_ONLY_GRANT))
    _configure(drifted, True)

    for application in (enforcing, drifted):
        result = _inspect(application)
        enforced = result["enforced_dimensions"]
        declared_only = result["declared_only_dimensions"]

        assert set(enforced).isdisjoint(declared_only)
        assert sorted([*enforced, *declared_only]) == sorted(DIMENSIONS)


def test_an_unwrapped_connection_bearing_tool_withholds_the_dispatch_claim():
    """ADR 0047: closes the unsafe direction ADR 0038 left open.

    A registry inside its ceiling whose connection-bearing tool never went
    through `authorized` used to report all three dimensions enforced, because
    `ceiling_enforced` re-checks the tool dimension only. The claim now rests on
    the registered callable, which is the only thing that can carry the check.
    """
    from hmc_mcp.server_permissions import describe

    def hmc_list_systems(profile: str | None = None) -> str:
        return "ok"

    result = describe({"hmc_list_systems": hmc_list_systems}, _legacy(), TOOL_SECURITY)

    assert result.ceiling_enforced is True
    assert result.enforced_dimensions == ("tools",)
    assert result.declared_only_dimensions == ("connections", "targets")


def test_a_name_outside_the_index_withholds_every_enforcement_claim():
    """ADR 0047: nothing establishes an unindexed name is bounded or guarded.

    The same fail-closed default `_permission` applies to `exhaustive_targets`.
    """
    from hmc_mcp.server_permissions import describe

    def hmc_not_in_the_index() -> str:
        return "ok"

    result = describe(
        {"hmc_not_in_the_index": hmc_not_in_the_index}, _legacy(), TOOL_SECURITY
    )

    assert result.ceiling_enforced is False
    assert result.enforced_dimensions == ()
    assert result.declared_only_dimensions == ("tools", "connections", "targets")


def test_targets_without_a_connection_argument_withhold_the_dispatch_claim():
    """ADR 0047: `authorized` keys the wrapper on the connection argument alone.

    So a tool declaring selectors but no connection argument would register
    unwrapped and have its targets enforced by nothing. No such tool is in the
    index — the guard below pins that — and this is the branch that keeps the
    label honest if one is ever added.
    """
    from dataclasses import replace

    from hmc_mcp.server_permissions import describe

    def hmc_list_lpars(profile: str | None = None) -> str:
        return "ok"

    security = TOOL_SECURITY["hmc_list_lpars"]
    assert security.targets, "the fixture needs a tool that declares selectors"
    index = {**TOOL_SECURITY, "hmc_list_lpars": replace(security, connection_argument=None)}

    result = describe({"hmc_list_lpars": hmc_list_lpars}, _legacy(), index)

    assert result.ceiling_enforced is True
    assert result.enforced_dimensions == ("tools",)
    assert result.declared_only_dimensions == ("connections", "targets")


def test_no_indexed_tool_declares_targets_without_a_connection_argument():
    """ADR 0047: the assumption the branch above defends, asserted on the index.

    Target scope is applied by the same wrapper as connection scope, so a tool
    in this state is one whose declared selectors constrain nothing at dispatch.
    """
    unguardable = {
        name
        for name, security in TOOL_SECURITY.items()
        if security.targets and security.connection_argument is None
    }

    assert unguardable == set()


def test_the_authorization_witness_is_not_forged_by_functools_wraps():
    """ADR 0047: `__wrapped__` is set by any decorator; the marker is not.

    `is_authorized_wrapper` is what `describe` believes, so a witness an
    unrelated decorator could set would make the enforcement label meaningless.
    """
    import functools

    from hmc_mcp.tool_registry import authorized, is_authorized_wrapper

    def handler(profile: str | None = None) -> str:
        return "ok"

    @functools.wraps(handler)
    def unrelated(*args, **kwargs):
        return handler(*args, **kwargs)

    security = TOOL_SECURITY["hmc_list_systems"]
    guarded = authorized("hmc_list_systems", security, handler, lambda *_args: None)
    local_only = authorized(
        "hmc_effective_permissions",
        TOOL_SECURITY["hmc_effective_permissions"],
        handler,
        lambda *_args: None,
    )

    assert is_authorized_wrapper(guarded) is True
    assert getattr(unrelated, "__wrapped__", None) is not None
    assert is_authorized_wrapper(unrelated) is False
    assert is_authorized_wrapper(handler) is False
    # A tool with no connection argument registers unwrapped by design.
    assert local_only is handler
    assert is_authorized_wrapper(local_only) is False
    # A registration carrying no callable at all — what `describe` reads when a
    # provider hands back a `Tool` that is not a `FunctionTool`.
    assert is_authorized_wrapper(None) is False


@pytest.mark.parametrize("entry_point", ["main_stdio", "main_http"])
def test_entry_points_serve_a_freshly_composed_filtered_application(entry_point):
    """R9, R9a: main_stdio serves its own application, wired to the ceiling."""
    from unittest.mock import patch

    import hmc_mcp.server as server_module

    every_effect = _policy([
        {
            "effects": ["read", "mutate", "destructive"],
            "connections": ["<default>"],
            "targets": "all-targets",
        }
    ])
    served = {}

    def _capture(self, **_kwargs):
        served["app"] = self
        served["names"] = {tool.name for tool in asyncio.run(self.list_tools())}

    served_apps = []

    def _capture_twice(self, **_kwargs):
        _capture(self, **_kwargs)
        served_apps.append(self)

    with patch.object(FastMCP, "run", _capture_twice):
        getattr(server_module, entry_point)(
            every_effect, enable_arbitrary_command=True
        )
        getattr(server_module, entry_point)(
            every_effect, enable_arbitrary_command=True
        )

    # R9 is object identity, not set difference: an all-three-effects grant
    # resolves to every tool but hmc_run_command, which create_mcp never
    # registers anyway, so the two name sets are deliberately equal here.
    # Two invocations, two applications. Comparing against a freshly-composed one
    # instead would pass unconditionally — `create_mcp` returns a new object every
    # call — so it could not fail even for an entry point that cached and reused one,
    # which is the property this line exists to check.
    assert len(served_apps) == 2
    assert served_apps[0] is not served_apps[1]
    assert "hmc_run_command" not in served["names"]
    assert "hmc_delete_lpar" in served["names"]


def _warnings(tool_count, policy, enable_arbitrary_command=False):
    from hmc_mcp.server import _startup_warnings

    return _startup_warnings(tool_count, policy, enable_arbitrary_command)


DENY_EVERYTHING: list[dict] = []


def test_an_empty_served_surface_is_warned_and_suppresses_the_other_line():
    """R10a: keyed on the served registry, and it replaces R18's line."""
    policy = _policy(DENY_EVERYTHING, name="locked")
    assert len(policy.tools) == 0
    assert _names(create_mcp(policy)) == set()

    lines = _warnings(0, policy)

    assert len(lines) == 1
    assert "no tools" in lines[0]
    # Cause-neutral: R10a covers an empty surface however it arose, and the
    # withheld-inspection line is suppressed rather than printed beside it.
    assert "hmc_effective_permissions" not in lines[0]


def test_a_withheld_inspection_tool_is_warned():
    """R18: named, with the policy that withheld it."""
    policy = _policy(
        [{"tools": ["hmc_list_systems"], "connections": ["lab"], "targets": "all-targets"}]
    )

    lines = _warnings(1, policy)

    assert any("hmc_effective_permissions" in line and "test" in line for line in lines)


def test_an_authored_but_unselected_policy_file_is_warned():
    """R19, retired by ADR 0041: a file can no longer be authored-but-unselected.

    The warning fired when `access-policy.toml` existed and no policy was chosen. A
    server that starts has chosen one, so the condition is unreachable and both the
    line and `_unselected_policy_file` are gone.
    """
    import hmc_mcp.server as server_app

    assert not hasattr(server_app, "_unselected_policy_file")
    assert not any(
        "access-policy.toml" in line for line in _warnings(129, _legacy())
    )


def test_an_unresolvable_policy_path_never_fails_the_start():
    """R19, moved by ADR 0041: the guard is still needed, one layer up.

    `Path.home()` raises under a uid with no passwd entry and no HOME. That used to
    threaten a *warning* rendered during startup; now it threatens the *refusal*
    rendered by `serve`, which resolves the same path to name it. The successor
    assertion is
    tests/app/test_fail_closed_startup.py::test_an_unresolvable_config_home_refuses_without_a_traceback;
    what remains here is that the warning function no longer touches a path at all.
    """
    import inspect as inspect_module

    import hmc_mcp.server as server_app

    source = inspect_module.getsource(server_app._startup_warnings)

    assert "resolve_access_policy_path" not in source


def test_a_withheld_escape_hatch_is_warned_only_when_requested():
    """R19a: the explicit request answered with silence gets a line."""
    policy = _policy(READ_ONLY_GRANT)

    requested = _warnings(90, policy, enable_arbitrary_command=True)
    assert any("hmc_run_command" in line for line in requested)

    assert not any(
        "hmc_run_command" in line for line in _warnings(90, policy)
    )


POLICY_FILE = """
[[policies.lab.grants]]
effects = ["read"]
connections = ["<default>"]
targets = "all-targets"
"""


@pytest.mark.parametrize(
    ("argv", "target"),
    [
        (["serve"], "main_stdio"),
        (["serve", "--http"], "main_http"),
    ],
)
def test_serve_forwards_the_compiled_policy_to_the_entry_point(
    argv, target, tmp_path, monkeypatch
):
    """R7: the selected policy reaches the server, not just the loader."""
    from unittest.mock import patch

    from typer.testing import CliRunner

    import hmc_mcp.access_policy as access_policy_module
    from hmc_mcp.access_policy import AccessPolicy
    from hmc_mcp.cli import app

    path = tmp_path / "access-policy.toml"
    path.write_text(POLICY_FILE, encoding="utf-8")
    monkeypatch.setattr(
        access_policy_module, "resolve_access_policy_path", lambda: path
    )

    with patch(f"hmc_mcp.server.{target}") as entry_point:
        result = CliRunner().invoke(app, [*argv, "--access-policy", "lab"])

    assert result.exit_code == 0, result.output
    forwarded = entry_point.call_args.args[0]
    assert isinstance(forwarded, AccessPolicy)
    assert forwarded.name == "lab"
    assert forwarded.permits_tool("hmc_list_systems")
    assert not forwarded.permits_tool("hmc_delete_lpar")


ESCAPE_HATCH_ONLY = [
    {
        "tools": ["hmc_run_command"],
        "connections": ["<default>"],
        "targets": "all-targets",
    }
]


def test_the_serve_path_counts_after_the_toggle_and_writes_only_to_stderr(capsys):
    """R10a: ordering, suppression, and the stdout constraint, in one test.

    A ceiling of only ``hmc_run_command`` composes zero tools, so counting
    before the toggle would emit a false empty-surface line. Started with the
    flag, the served surface is exactly the escape hatch.
    """
    import hmc_mcp.server as server_app

    policy = _policy(ESCAPE_HATCH_ONLY, name="hatch")
    application = server_app._serve_application(True, policy)

    assert _names(application) == {"hmc_run_command"}

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no tools" not in captured.err
    assert "hmc_effective_permissions" in captured.err


def test_the_serve_path_warns_once_on_a_genuinely_empty_surface(capsys):
    """R10a: the empty-surface line suppresses the withheld-inspection line."""
    import hmc_mcp.server as server_app

    policy = _policy(ESCAPE_HATCH_ONLY, name="hatch")
    application = server_app._serve_application(False, policy)

    assert _names(application) == set()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no tools" in captured.err
    assert "hmc_effective_permissions" not in captured.err


class _BrokenStderr:
    """A stream whose every write raises, as a closed pipe's does."""

    def write(self, _text):
        raise BrokenPipeError("stderr is gone")

    def flush(self):
        raise BrokenPipeError("stderr is gone")


def _unusable_stderr(state, tmp_path):
    """One of the three ways stderr is unusable when the server starts."""
    if state == "broken":
        return _BrokenStderr()
    if state == "none":
        # CPython sets sys.stderr to None when fd 2 is not open at interpreter
        # start, which is what `serve 2>&-` produces.
        return None
    handle = (tmp_path / "closed").open("w", encoding="utf-8")
    handle.close()
    return handle


@pytest.mark.parametrize("state", ["broken", "none", "closed"])
def test_an_unusable_stderr_neither_fails_the_start_nor_reaches_stdout(
    state, tmp_path, monkeypatch, capsys
):
    """R10a: a diagnostic nobody asked for must not abort a start.

    Nor may it land on stdout, which carries JSON-RPC under stdio. Each state
    fails differently — BrokenPipeError is an OSError, a closed file raises
    ValueError, and an absent stream raises nothing at all — so asserting only
    "did not raise" would miss the one that leaks.

    Since ADR 0043 the stream is resolved by ``audit``'s sink rather than by
    ``server._warn``, on a writer thread, so this patches the process-wide
    ``sys.stderr`` and settles the sink before reading stdout. The three states
    are now three counted drops rather than three silent ones.
    """
    import sys

    import hmc_mcp.server as server_app
    from hmc_mcp import audit

    policy = _policy(ESCAPE_HATCH_ONLY, name="hatch")
    monkeypatch.setattr(sys, "stderr", _unusable_stderr(state, tmp_path))

    application = server_app._serve_application(False, policy)
    assert audit._SINK.drain(audit._DRAIN_TIMEOUT), "the sink must settle, not stall"

    assert _names(application) == set()
    assert capsys.readouterr().out == ""


def test_serve_reports_an_unloadable_policy_and_starts_nothing(tmp_path, monkeypatch):
    """R7, R8: an explicit selection that cannot be loaded exits non-zero.

    The file is now *present and malformed* rather than absent. ADR 0041 intercepts the
    absent case earlier and answers it with the migration message, because an operator
    who edited the launcher before generating the file is the likeliest arrival at this
    refusal and `cannot be read: [Errno 2]` named neither the generator nor the remedy.
    tests/app/test_fail_closed_startup.py::test_serve_with_an_absent_policy_file_exits_1
    is where that case is now pinned; this keeps the malformed-content path.
    """
    from typer.testing import CliRunner

    import hmc_mcp.access_policy as access_policy_module
    from hmc_mcp.cli import app

    present = tmp_path / "access-policy.toml"
    present.write_text("this is not = = valid toml\n", encoding="utf-8")
    monkeypatch.setattr(
        access_policy_module, "resolve_access_policy_path", lambda: present
    )
    result = CliRunner().invoke(app, ["serve", "--access-policy", "missing"])

    # `_fail` renders through a rich Console, which hard-folds a non-tty line at
    # 80 columns with no spaces to break on, so a tmp_path substring can fold
    # mid-word between runs. Assert on wrap-proof text instead.
    assert result.exit_code == 1
    assert "TOML parse error" in result.output


def test_inspection_reports_which_tools_a_targets_table_cannot_bound():
    """R19 (#223): `exhaustive_targets` is the only signal an operator gets.

    ADR 0037's ceiling is per-tool and structurally cannot see targets, so a
    policy whose only grant carries a table still registers and advertises the
    tools that grant will always deny. This field is what lets an operator find
    them without calling one and reading the denial.
    """
    result = _inspect(create_mcp(_policy(READ_ONLY_GRANT)))
    by_name = {tool["name"]: tool for tool in result["tools"]}

    # Selector-less, so a table has nothing to bind on.
    assert by_name["hmc_list_systems"]["exhaustive_targets"] is False
    assert by_name["hmc_effective_permissions"]["exhaustive_targets"] is False
    # Selectors that do name every resource the call acts on.
    assert by_name["hmc_get_lpar"]["exhaustive_targets"] is True
    assert by_name["hmc_list_lpars"]["exhaustive_targets"] is True

    reported = {
        tool["name"] for tool in result["tools"] if not tool["exhaustive_targets"]
    }
    assert reported == {
        name
        for name in by_name
        if not TOOL_SECURITY[name].exhaustive_targets
    }


def test_a_name_outside_the_index_is_reported_as_unbounded():
    """R19: the fail-closed default for an unindexed name, which nothing else pins.

    `_permission` returns `exhaustive_targets=False` for a name the authoritative
    index does not carry, on the ground that nothing establishes a table could
    bound it. Mutating that default to `True` left the whole suite green, so the
    justification had no test behind it.
    """
    from hmc_mcp.server_permissions import UNKNOWN, _permission

    reported = _permission("hmc_not_in_the_index", {})

    assert reported.effect == UNKNOWN
    assert reported.exhaustive_targets is False
