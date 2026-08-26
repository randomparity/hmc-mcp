"""The ADR 0092 §4 power-ownership guard, read back from a running server.

`authorize_power_operations` fails **open** and `HMCConfig` sets
`extra="ignore"`, so a mistyped profile key or environment variable is dropped
with no error and is observably identical to a correct `false` (#470). These
tests pin the report that makes the effective, post-precedence value readable
from the process that would act on it.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastmcp import Client

from hmc_mcp.access_policy import DEFAULT_CONNECTION_TOKEN, compile_access_policy
from hmc_mcp.server import TOOL_SECURITY, create_mcp
from hmc_mcp.server_permissions import (
    describe,
    resolve_power_guards,
)

ALL_TOOLS_GRANT = [
    {"effects": ["read"], "connections": ["<default>"], "targets": "all-targets"}
]


def _policy(grants: list[dict], name: str = "test"):
    return compile_access_policy(
        {"policies": {name: {"grants": grants}}},
        name,
        TOOL_SECURITY,
        "test-access-policy.toml",
    )


@pytest.fixture(autouse=True)
def no_native_config(monkeypatch, tmp_path):
    """Point config resolution at an empty directory, not the developer's own.

    Every test here decides for itself whether a `config.toml` exists; without
    this the platform-native path leaks a real one into the report.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    monkeypatch.delenv("HMC_HOST", raising=False)


def _write_config(monkeypatch, tmp_path, body: str) -> None:
    directory = tmp_path / "xdg" / "hmc-mcp"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.toml").write_text(body, encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


def _by_connection(guards):
    return {guard.connection: guard for guard in guards}


def test_the_value_is_readable_with_no_config_file_present():
    """#470's acceptance: the env-var-only shape `config show` cannot answer for.

    `config_show` exits 1 before it builds any config when the platform-native
    path is absent (`src/hmc_mcp/cli_config.py:159-161`), which is exactly the
    deployment `docs/environment-variables.md` opens by describing.
    """
    guards = resolve_power_guards(None)

    assert [guard.connection for guard in guards] == [DEFAULT_CONNECTION_TOKEN]
    assert guards[0].authorized is False
    assert guards[0].source == "default"
    assert guards[0].detail is None


def test_an_environment_variable_is_reported_as_such(monkeypatch):
    """The variable is the setting that cannot be selected around, so name it."""
    monkeypatch.setenv("HMC_AUTHORIZE_POWER_OPERATIONS", "true")

    (guard,) = resolve_power_guards(None)

    assert guard.authorized is True
    assert guard.source == "environment"


def test_a_profile_key_is_reported_against_only_the_profile_that_carries_it(
    monkeypatch, tmp_path
):
    """The footgun `docs/environment-variables.md` names, made visible.

    A TOML `authorize_power_operations = true` "applies only to the profile that
    carries it — every other profile stays unguarded", and both the MCP tools and
    the CLI take a caller-supplied profile selector. A single reported value
    would be false for one of these two connections whichever value it chose.
    """
    _write_config(
        monkeypatch,
        tmp_path,
        """
        default_profile = "guarded"

        [profiles.guarded]
        host = "hmc-a.example.com"
        user = "admin"
        authorize_power_operations = true

        [profiles.open]
        host = "hmc-b.example.com"
        user = "admin"
        """,
    )
    policy = _policy([
        {
            "effects": ["read"],
            "connections": ["guarded", "open"],
            "targets": "all-targets",
        }
    ])

    guards = _by_connection(resolve_power_guards(policy))

    assert guards["guarded"].authorized is True
    assert guards["guarded"].source == "profile"
    assert guards["open"].authorized is False
    assert guards["open"].source == "default"


def test_the_environment_variable_overrides_every_profile(monkeypatch, tmp_path):
    """Env-over-TOML on the profile path, reported for every connection."""
    _write_config(
        monkeypatch,
        tmp_path,
        """
        default_profile = "guarded"

        [profiles.guarded]
        host = "hmc-a.example.com"
        user = "admin"
        authorize_power_operations = true
        """,
    )
    monkeypatch.setenv("HMC_AUTHORIZE_POWER_OPERATIONS", "false")
    policy = _policy([
        {"effects": ["read"], "connections": ["guarded"], "targets": "all-targets"}
    ])

    guards = _by_connection(resolve_power_guards(policy))

    assert guards["guarded"].authorized is False
    assert guards["guarded"].source == "environment"


def test_an_ambient_host_makes_a_profile_key_ineffective(monkeypatch, tmp_path):
    """The fail-open direction: `HMC_HOST` skips the profile that carries the key.

    `build_config` goes straight to env-only construction when `HMC_HOST` is set,
    so the TOML value never reaches the config the guard reads. `config show`
    reports it as enabled anyway; this report does not.
    """
    _write_config(
        monkeypatch,
        tmp_path,
        """
        default_profile = "guarded"

        [profiles.guarded]
        host = "hmc-a.example.com"
        user = "admin"
        authorize_power_operations = true
        """,
    )
    monkeypatch.setenv("HMC_HOST", "hmc-c.example.com")
    policy = _policy([
        {"effects": ["read"], "connections": ["guarded"], "targets": "all-targets"}
    ])

    guards = _by_connection(resolve_power_guards(policy))

    assert guards["guarded"].authorized is False
    assert guards["guarded"].source == "default"


def test_a_connection_that_cannot_be_resolved_is_reported_not_raised(
    monkeypatch, tmp_path
):
    """A tool that describes the surface must not break first when it changes."""
    _write_config(
        monkeypatch,
        tmp_path,
        """
        default_profile = "present"

        [profiles.present]
        host = "hmc-a.example.com"
        user = "admin"
        """,
    )
    policy = _policy([
        {"effects": ["read"], "connections": ["absent"], "targets": "all-targets"}
    ])

    guards = _by_connection(resolve_power_guards(policy))

    assert guards["absent"].authorized is None
    assert guards["absent"].source == "unresolved"
    assert "absent" in guards["absent"].detail


def test_an_invalid_setting_is_reported_without_echoing_the_value(monkeypatch):
    """`detail` carries a cause, never a rejected input.

    Pydantic quotes the offending value in a `ValidationError`, and the fields it
    validates include `password`. Only `ConfigError` — whose messages name paths,
    profile names and environment-variable names, never their values — is
    forwarded verbatim.
    """
    monkeypatch.setenv("HMC_PASSWORD", "hunter2")
    monkeypatch.setenv("HMC_AGENT_ID", "has,a,comma")

    (guard,) = resolve_power_guards(None)

    assert guard.authorized is None
    assert guard.source == "unresolved"
    assert guard.detail == "ValidationError"


def test_describe_carries_the_guards_it_is_given():
    """The report is assembled from a resolved value, not resolved inside it.

    `describe` stays a pure function of its arguments: the filesystem and
    environment reads happen at the one call site that owns them.
    """
    guards = resolve_power_guards(None)

    result = describe({}, None, TOOL_SECURITY, guards)

    assert result.power_ownership_guards == guards


def test_the_running_server_answers_for_itself(monkeypatch):
    """#470's outcome end to end: ask the process, not the invoking shell."""
    monkeypatch.setenv("HMC_AUTHORIZE_POWER_OPERATIONS", "true")
    application = create_mcp(_policy(ALL_TOOLS_GRANT))

    async def _call():
        async with Client(application) as client:
            return await client.call_tool("hmc_effective_permissions", {})

    reported = json.loads(asyncio.run(_call()).content[0].text)

    assert reported["power_ownership_guards"] == [
        {
            "connection": DEFAULT_CONNECTION_TOKEN,
            "authorized": True,
            "source": "environment",
            "detail": None,
        }
    ]
