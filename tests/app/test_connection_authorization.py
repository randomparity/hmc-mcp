"""Dispatch-boundary connection-scope authorization on a composed application.

Covers docs/workflow/specs/2026-08-19-connection-scope-dispatch-design.md; the
decision record is docs/adr/0038-dispatch-time-connection-scope.md.
"""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from hmc_mcp import server_command, server_lpars
from hmc_mcp.access_policy import compile_access_policy
from hmc_mcp.connection_scope import ConnectionScopeError, connection_authorizer
from hmc_mcp.server import TOOL_SECURITY, create_mcp
from hmc_mcp.tool_registry import ToolSecurity, authorized

SOURCE = "test-access-policy.toml"

LAB_ONLY = [
    {
        "effects": ["read", "mutate", "destructive"],
        "connections": ["lab"],
        "targets": "all-targets",
    }
]


def _policy(grants: list[dict], name: str = "lab-only"):
    return compile_access_policy(
        {"policies": {name: {"grants": grants}}}, name, TOOL_SECURITY, SOURCE
    )


@pytest.fixture(autouse=True)
def lab_profile(tmp_path, monkeypatch):
    """A config.toml holding one profile, at the platform-native path.

    Autouse so no test in this module can accidentally read the developer's own
    configuration, and so ``lab`` normalizes to itself rather than denying.
    """
    from hmc_mcp.config import config_dir

    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("HMC_HOST", raising=False)
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    path = config_dir() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[profiles.lab]\n"
        "host = 'lab-hmc.test'\n"
        "user = 'hscroot'\n"
        "password = 'lab-pass'\n"  # pragma: allowlist secret
        "\n"
        "[profiles.prod]\n"
        "host = 'prod-hmc.test'\n"
        "user = 'hscroot'\n"
        "password = 'prod-pass'\n",  # pragma: allowlist secret
        encoding="utf-8",
    )
    return path


def _call(application, tool: str, arguments: dict):
    async def _go():
        async with Client(application) as client:
            return await client.call_tool(tool, arguments)

    return asyncio.run(_go())


def _registered(application) -> dict:
    async def _go():
        return {
            tool.name: tool for tool in await application.local_provider.list_tools()
        }

    return asyncio.run(_go())


# ---------------------------------------------------------------------------
# R12 — a denied call performs no outbound operation
# ---------------------------------------------------------------------------


# Every name a handler could reach an HMC through, patched at *every* module
# that rebound it at import. Patching only `hmc_mcp.common.build_config` proves
# nothing: `server_vios`, `server_command`, and `_app` each hold their own
# reference, so a call through one of those would sail past an unpatched source
# module and the test would still be green.
_OUTBOUND_NAMES = ("build_config", "client_from_env", "run_hmc_cli", "run_hmc_command")


def _seal_every_outbound_path(monkeypatch, opened: list[str]):
    """Make any attempt to reach an HMC record itself and fail loudly."""
    import importlib
    import pkgutil

    import hmc_mcp
    from hmc_mcp import client as client_module

    def _forbidden(label):
        def _call(*args, **kwargs):
            opened.append(label)
            raise AssertionError(f"a denied call reached {label}")

        return _call

    monkeypatch.setattr(client_module.HMCClient, "__init__", _forbidden("HMCClient"))
    monkeypatch.setattr(
        "httpx.AsyncClient.__init__", _forbidden("httpx.AsyncClient")
    )

    sealed = 0
    for info in pkgutil.iter_modules(hmc_mcp.__path__):
        module = importlib.import_module(f"hmc_mcp.{info.name}")
        for name in _OUTBOUND_NAMES:
            if callable(getattr(module, name, None)):
                monkeypatch.setattr(module, name, _forbidden(f"{info.name}.{name}"))
                sealed += 1
    assert sealed > 10, f"only {sealed} outbound bindings sealed; the sweep missed"


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        pytest.param(
            "hmc_delete_lpar",
            {"system_name_or_uuid": "sys-1", "lpar_name_or_uuid": "victim"},
            id="rest",
        ),
        pytest.param(
            "hmc_backup_vios",
            {"vios_name_or_uuid": "vios-1"},
            id="ssh-passthrough",
        ),
    ],
)
def test_a_denied_call_never_opens_an_hmc_client(monkeypatch, tool_name, arguments):
    """The proof the issue asks for: assert on the constructors, not the error.

    Both transports, because the REST and SSH paths reach their connection
    through different module bindings.
    """
    opened: list[str] = []
    _seal_every_outbound_path(monkeypatch, opened)

    application = create_mcp(_policy(LAB_ONLY))
    with pytest.raises(ToolError) as error:
        _call(application, tool_name, {**arguments, "profile": "prod"})

    assert opened == []
    assert "is not permitted on connection 'prod'" in str(error.value)


def test_the_seal_itself_bites(monkeypatch):
    """A permitted call trips every wire the denial test asserts is untouched."""
    opened: list[str] = []
    _seal_every_outbound_path(monkeypatch, opened)

    application = create_mcp(_policy(LAB_ONLY))
    with pytest.raises(ToolError):
        _call(
            application,
            "hmc_delete_lpar",
            {
                "system_name_or_uuid": "sys-1",
                "lpar_name_or_uuid": "victim",
                "profile": "lab",
            },
        )

    assert opened


def test_a_permitted_call_reaches_the_handler(monkeypatch):
    """The mirror: the wrapper is a gate, not a wall."""
    reached: list[str | None] = []

    def _capture(profile=None, **overrides):
        reached.append(profile)
        raise RuntimeError("stop before any HMC request")

    monkeypatch.setattr(server_lpars, "client_from_env", _capture)

    application = create_mcp(_policy(LAB_ONLY))
    with pytest.raises(ToolError):
        _call(
            application,
            "hmc_delete_lpar",
            {"system_name_or_uuid": "sys-1", "lpar_name_or_uuid": "victim", "profile": "lab"},
        )

    assert reached == ["lab"]


def test_the_denial_reaches_the_client_as_a_tool_error():
    application = create_mcp(_policy(LAB_ONLY))
    with pytest.raises(ToolError) as error:
        _call(
            application,
            "hmc_delete_lpar",
            {"system_name_or_uuid": "sys-1", "lpar_name_or_uuid": "victim", "profile": "prod"},
        )
    message = str(error.value)
    assert "hmc_delete_lpar is not permitted on connection 'prod'" in message
    assert "lab-only" in message
    assert "prod-hmc.test" not in message
    assert "lab-pass" not in message


# ---------------------------------------------------------------------------
# R1, R17 — every connection-bearing registered tool carries the check
# ---------------------------------------------------------------------------


def test_every_connection_bearing_tool_is_wrapped_under_a_policy():
    """R17: the check cannot be skipped, asserted rather than assumed.

    Read after ``configure_arbitrary_command_tool`` so it covers the third
    registration site — the one that registers the arbitrary-command tool.
    """
    policy = _policy(
        LAB_ONLY + [{"tools": ["hmc_run_command"], "connections": ["lab"], "targets": "all-targets"}]
    )
    application = create_mcp(policy)
    asyncio.run(
        server_command.configure_arbitrary_command_tool(
            True,
            application,
            permits=policy.permits_tool,
            authorize=connection_authorizer(policy),
        )
    )

    registered = _registered(application)
    assert "hmc_run_command" in registered
    connection_bearing = [
        name
        for name in registered
        if TOOL_SECURITY[name].connection_argument is not None
    ]
    assert connection_bearing
    for name in connection_bearing:
        assert getattr(registered[name].fn, "__wrapped__", None) is not None, name

    for name in registered:
        if TOOL_SECURITY[name].connection_argument is None:
            assert getattr(registered[name].fn, "__wrapped__", None) is None, name


def test_no_tool_is_wrapped_without_a_policy():
    """R14: a policy's absence is not a denial, and costs no wrapper."""
    registered = _registered(create_mcp())
    assert registered
    for name, tool in registered.items():
        assert getattr(tool.fn, "__wrapped__", None) is None, name


# ---------------------------------------------------------------------------
# R3 — registration is signature-transparent
# ---------------------------------------------------------------------------


def test_the_wrapper_changes_no_tool_schema():
    unfiltered = _registered(create_mcp())
    grant_everything = _policy(
        [
            {
                "effects": ["read", "mutate", "destructive"],
                "connections": ["lab"],
                "targets": "all-targets",
            }
        ]
    )
    guarded = _registered(create_mcp(grant_everything))

    assert set(guarded) == set(unfiltered)
    for name, tool in guarded.items():
        assert tool.name == unfiltered[name].name
        assert tool.description == unfiltered[name].description
        assert tool.parameters == unfiltered[name].parameters


def test_the_arbitrary_command_tool_keeps_its_schema_through_the_wrapper():
    grant = [
        {"tools": ["hmc_run_command"], "connections": ["lab"], "targets": "all-targets"}
    ]
    policy = _policy(grant)

    def _compose(authorize):
        application = create_mcp(policy)
        asyncio.run(
            server_command.configure_arbitrary_command_tool(
                True, application, permits=policy.permits_tool, authorize=authorize
            )
        )
        return _registered(application)["hmc_run_command"]

    guarded = _compose(connection_authorizer(policy))
    plain = _compose(None)

    assert guarded.name == plain.name
    assert guarded.description == plain.description
    assert guarded.parameters == plain.parameters


# ---------------------------------------------------------------------------
# R5 — arguments are bound, not read out of kwargs
# ---------------------------------------------------------------------------


def _probe(**kwargs):
    seen: list[dict] = []

    def authorize(name, security, arguments):
        seen.append(dict(arguments))

    security = ToolSecurity(
        effect="mutate", operation="probe.run", target_kind="console"
    )

    def handler(system_name_or_uuid: str, profile: str | None = None) -> str:
        return "ran"

    return authorized("probe", security, handler, authorize), seen


def test_a_defaulted_selector_is_read_as_none():
    guarded, seen = _probe()
    assert guarded("sys-1") == "ran"
    assert seen == [{"system_name_or_uuid": "sys-1", "profile": None}]


def test_a_positional_selector_is_read():
    guarded, seen = _probe()
    assert guarded("sys-1", "lab") == "ran"
    assert seen == [{"system_name_or_uuid": "sys-1", "profile": "lab"}]


def test_a_malformed_call_fails_before_the_handler():
    """R5: bind rejects it, so the handler never runs and nothing is authorized."""
    guarded, seen = _probe()
    with pytest.raises(TypeError):
        guarded("sys-1", nonexistent=True)
    assert seen == []


# ---------------------------------------------------------------------------
# R15, R16 — independence, and the boundary the CLI and Python API sit outside
# ---------------------------------------------------------------------------


def test_compositions_authorize_independently(monkeypatch):
    reached: list[str | None] = []
    monkeypatch.setattr(
        server_lpars,
        "client_from_env",
        lambda profile=None, **kw: reached.append(profile) or (_ for _ in ()).throw(
            RuntimeError("stop")
        ),
    )

    restricted = create_mcp(_policy(LAB_ONLY))
    unrestricted = create_mcp()

    with pytest.raises(ToolError):
        _call(
            restricted,
            "hmc_delete_lpar",
            {"system_name_or_uuid": "sys-1", "lpar_name_or_uuid": "victim", "profile": "prod"},
        )
    with pytest.raises(ToolError):
        _call(
            unrestricted,
            "hmc_delete_lpar",
            {"system_name_or_uuid": "sys-1", "lpar_name_or_uuid": "victim", "profile": "prod"},
        )

    # The unrestricted application ran the handler; the restricted one did not.
    assert reached == ["prod"]


def test_the_module_level_handler_is_never_wrapped():
    """R16: the CLI and the ADR 0029 Python API reach the unwrapped function."""
    create_mcp(_policy(LAB_ONLY))

    assert getattr(server_lpars.hmc_delete_lpar, "__wrapped__", None) is None
    assert getattr(server_command.hmc_run_command, "__wrapped__", None) is None


def test_the_authorizer_denies_a_tool_no_grant_covers():
    """Reachable when the escape hatch is enabled under no ceiling."""
    authorize = connection_authorizer(_policy(LAB_ONLY))
    with pytest.raises(ConnectionScopeError):
        authorize(
            "hmc_run_command",
            TOOL_SECURITY["hmc_run_command"],
            {"profile": "lab"},
        )


# ---------------------------------------------------------------------------
# R17 — the served path, which is the only path a deployment takes
# ---------------------------------------------------------------------------


def _serve(policy, *, enable_arbitrary_command=True):
    """Compose exactly as `hmc-mcp serve --access-policy NAME` composes."""
    from hmc_mcp import server

    return server._serve_application(enable_arbitrary_command, policy)


ESCAPE_HATCH_GRANT = [
    {"tools": ["hmc_run_command"], "connections": ["lab"], "targets": "all-targets"}
]


def test_the_served_application_wraps_every_connection_bearing_tool():
    """The gate must be on the application `serve` builds, not one a test builds.

    Asserting against a self-composed application cannot observe whether
    `_serve_application` threads the authorizer at all, which leaves the
    arbitrary-command registration site — the highest-risk one — unpinned.
    """
    application = _serve(_policy(LAB_ONLY + ESCAPE_HATCH_GRANT))
    registered = _registered(application)

    assert "hmc_run_command" in registered
    for name, tool in registered.items():
        wrapped = getattr(tool.fn, "__wrapped__", None) is not None
        assert wrapped == (TOOL_SECURITY[name].connection_argument is not None), name


def test_the_served_escape_hatch_denies_a_withheld_connection():
    """`hmc_run_command` runs an arbitrary HMC CLI command; it must be scoped."""
    application = _serve(_policy(LAB_ONLY + ESCAPE_HATCH_GRANT))

    with pytest.raises(ToolError) as error:
        _call(application, "hmc_run_command", {"cmd": "lssyscfg", "profile": "prod"})
    assert "hmc_run_command is not permitted on connection 'prod'" in str(error.value)


def test_the_served_application_without_a_policy_wraps_nothing():
    """R14 on the served path: no policy is still no denial."""
    registered = _registered(_serve(None))

    assert "hmc_run_command" in registered
    for name, tool in registered.items():
        assert getattr(tool.fn, "__wrapped__", None) is None, name
