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
import logging
import os
import sys

import pytest
from fastmcp import Client

import hmc_mcp.config as config_module
import hmc_mcp.server_tools.permissions as permissions_module
from hmc_mcp.authorization.access_policy import (
    DEFAULT_CONNECTION_TOKEN,
    compile_access_policy,
)
from hmc_mcp.authorization.connection_scope import selected_connection
from hmc_mcp.server import TOOL_SECURITY, create_mcp
from hmc_mcp.server_tools.permissions import (
    build_effective_permissions,
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

    Every test here decides for itself whether a ``config.toml`` exists; without
    this the platform-native path leaks a real one into the report.

    ``sys.platform`` is patched to ``"linux"`` so ``resolve_config_path`` always
    uses ``XDG_CONFIG_HOME`` regardless of the host OS.  This is the same
    technique ``test_cli_config.py``'s ``_generate()`` uses.
    """
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    for name in tuple(os.environ):
        if name.casefold() == "hmc_host":
            monkeypatch.delenv(name)


def _write_config(tmp_path, body: str) -> None:
    """Write the config file under the directory ``no_native_config`` points at.

    ``no_native_config`` sets ``XDG_CONFIG_HOME`` to ``tmp_path / "xdg"`` and
    patches ``sys.platform`` to ``"linux"``, so ``resolve_config_path`` resolves
    to ``tmp_path / "xdg" / "hmc-mcp" / "config.toml"`` on every OS.
    """
    directory = tmp_path / "xdg" / "hmc-mcp"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.toml").write_text(body, encoding="utf-8")


def _by_connection(guards):
    return {guard.connection: guard for guard in guards}


def test_the_value_is_readable_with_no_config_file_present():
    """#470's acceptance: the env-var-only shape `config show` cannot answer for.

    `config_show` exits 1 before it builds any config when the platform-native
    path is absent (`src/hmc_mcp/cli_commands/config.py:159-161`), which is exactly the
    deployment `docs/environment-variables.md` opens by describing.
    """
    guards = resolve_power_guards(None)

    assert [guard.connection for guard in guards] == [DEFAULT_CONNECTION_TOKEN]
    assert guards[0].authorize_power_operations is False
    assert guards[0].source == "default"
    assert guards[0].detail is None


def test_a_malformed_config_file_is_reported_as_unresolved(
    tmp_path, caplog
):
    """Authored configuration failures remain visible to the operator."""
    _write_config(tmp_path, "[profiles.a\nhost = 'h'\n")
    policy = _policy(ALL_TOOLS_GRANT)

    with caplog.at_level(logging.DEBUG, logger="hmc_mcp.server_tools.permissions"):
        (guard,) = resolve_power_guards(policy)

    assert guard.connection == DEFAULT_CONNECTION_TOKEN
    assert guard.authorize_power_operations is None
    assert guard.source == "unresolved"
    assert guard.detail == "ConfigError"
    assert len(caplog.records) == 1
    assert "TOML parse error" in caplog.records[0].message


def test_one_malformed_document_is_classified_for_every_connection(
    monkeypatch, tmp_path, caplog
):
    """A shared read failure retains one unresolved row per connection."""
    _write_config(tmp_path, "[profiles.a\nhost = 'h'\n")
    policy = _policy([
        {
            "effects": ["read"],
            "connections": ["a", "b"],
            "targets": "all-targets",
        }
    ])
    original = config_module._read_config_document
    reads = 0

    def counting_reader(path):
        nonlocal reads
        reads += 1
        return original(path)

    monkeypatch.setattr(config_module, "_read_config_document", counting_reader)

    with caplog.at_level(logging.WARNING, logger="hmc_mcp.server_tools.permissions"):
        guards = resolve_power_guards(policy)

    assert [(guard.connection, guard.detail) for guard in guards] == [
        ("a", "ConfigError"),
        ("b", "ConfigError"),
    ]
    assert reads == 1
    assert len(caplog.records) == 2


def test_snapshot_path_failures_keep_the_existing_classification(monkeypatch):
    """Snapshot creation must not normalize build_config's path failures."""

    def fail_path_resolution():
        raise RuntimeError("cannot find home")

    monkeypatch.setattr(config_module, "resolve_config_path", fail_path_resolution)

    (guard,) = resolve_power_guards(None)

    assert guard.authorize_power_operations is None
    assert guard.source == "unresolved"
    assert guard.detail == "RuntimeError"


def test_an_environment_variable_is_reported_as_such(monkeypatch):
    """The variable is the setting that cannot be selected around, so name it."""
    monkeypatch.setenv("HMC_AUTHORIZE_POWER_OPERATIONS", "true")

    (guard,) = resolve_power_guards(None)

    assert guard.authorize_power_operations is True
    assert guard.source == "environment"


def test_a_profile_key_is_reported_against_only_the_profile_that_carries_it(tmp_path):
    """The footgun `docs/environment-variables.md` names, made visible.

    A TOML `authorize_power_operations = true` "applies only to the profile that
    carries it — every other profile stays unguarded", and both the MCP tools and
    the CLI take a caller-supplied profile selector. A single reported value
    would be false for one of these two connections whichever value it chose.
    """
    _write_config(
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

    assert guards["guarded"].authorize_power_operations is True
    assert guards["guarded"].source == "profile"
    assert guards["open"].authorize_power_operations is False
    assert guards["open"].source == "default"


def test_one_report_reads_the_config_document_once_for_multiple_profiles(
    monkeypatch, tmp_path
):
    """All rows in one report come from one parsed document snapshot."""
    _write_config(
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
    original = config_module._read_config_document
    reads = 0

    def counting_reader(path):
        nonlocal reads
        reads += 1
        return original(path)

    monkeypatch.setattr(config_module, "_read_config_document", counting_reader)

    guards = _by_connection(resolve_power_guards(policy))

    assert guards["guarded"].authorize_power_operations is True
    assert guards["open"].authorize_power_operations is False
    assert reads == 1


def test_snapshot_decision_reuses_the_resolver_host_sample(monkeypatch, tmp_path):
    """The resolver cannot skip its snapshot after keeping named connections."""
    _write_config(
        tmp_path,
        """
        [profiles.a]
        host = "hmc-a.example.com"
        user = "admin"
        [profiles.b]
        host = "hmc-b.example.com"
        user = "admin"
        """,
    )
    policy = _policy([
        {
            "effects": ["read"],
            "connections": ["a", "b"],
            "targets": "all-targets",
        }
    ])
    host_checks = iter([None, "changed.example.com"])
    checks = 0

    def changing_host(name):
        nonlocal checks
        if name != "HMC_HOST":
            return config_module.env_var_value(name)
        checks += 1
        return next(host_checks)

    original = config_module._read_config_document
    reads = 0

    def counting_reader(path):
        nonlocal reads
        reads += 1
        return original(path)

    monkeypatch.setattr(permissions_module, "env_var_value", changing_host)
    monkeypatch.setattr(config_module, "_read_config_document", counting_reader)

    guards = resolve_power_guards(policy)

    assert [guard.connection for guard in guards] == ["a", "b"]
    assert checks == 1
    assert reads == 1


def test_separate_reports_read_fresh_config_documents(monkeypatch, tmp_path):
    """The invocation snapshot is not retained between report calls."""
    original = config_module._read_config_document
    reads = 0

    def counting_reader(path):
        nonlocal reads
        reads += 1
        return original(path)

    monkeypatch.setattr(config_module, "_read_config_document", counting_reader)
    policy = _policy([
        {"effects": ["read"], "connections": ["guarded"], "targets": "all-targets"}
    ])
    _write_config(
        tmp_path,
        """
        default_profile = "guarded"
        [profiles.guarded]
        host = "hmc-a.example.com"
        user = "admin"
        authorize_power_operations = true
        """,
    )

    first = resolve_power_guards(policy)
    _write_config(
        tmp_path,
        """
        default_profile = "guarded"
        [profiles.guarded]
        host = "hmc-a.example.com"
        user = "admin"
        authorize_power_operations = false
        """,
    )
    second = resolve_power_guards(policy)

    assert first[0].authorize_power_operations is True
    assert second[0].authorize_power_operations is False
    assert reads == 2


def test_the_environment_variable_overrides_every_profile(monkeypatch, tmp_path):
    """Env-over-TOML on the profile path, reported for every connection."""
    _write_config(
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

    assert guards["guarded"].authorize_power_operations is False
    assert guards["guarded"].source == "environment"


def test_an_ambient_host_makes_a_profile_key_ineffective(monkeypatch, tmp_path):
    """The fail-open direction: `HMC_HOST` skips the profile that carries the key.

    `build_config` goes straight to env-only construction when `HMC_HOST` is set,
    so the TOML value never reaches the config the guard reads. `config show`
    reports it as enabled anyway; this report does not.
    """
    _write_config(
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
        {
            "effects": ["read"],
            "connections": ["<default>", "guarded"],
            "targets": "all-targets",
        }
    ])

    guards = _by_connection(resolve_power_guards(policy))

    assert guards[DEFAULT_CONNECTION_TOKEN].authorize_power_operations is False
    assert guards[DEFAULT_CONNECTION_TOKEN].source == "default"


def test_a_case_variant_environment_variable_reports_environment(monkeypatch):
    """Case-insensitive config matching and source attribution agree."""
    monkeypatch.setenv("hmc_authorize_power_operations", "true")

    (guard,) = resolve_power_guards(None)

    assert guard.authorize_power_operations is True
    assert guard.source == "environment"
    assert guard.detail is None


def test_a_case_variant_overrides_a_profiles_value(monkeypatch, tmp_path):
    """The profile loader and source reporter use the same casing rule."""
    _write_config(
        tmp_path,
        """
        default_profile = "guarded"

        [profiles.guarded]
        host = "hmc-a.example.com"
        user = "admin"
        authorize_power_operations = true
        """,
    )
    monkeypatch.setenv("hmc_authorize_power_operations", "false")
    policy = _policy([
        {"effects": ["read"], "connections": ["guarded"], "targets": "all-targets"}
    ])

    guards = _by_connection(resolve_power_guards(policy))

    assert guards["guarded"].authorize_power_operations is False
    assert guards["guarded"].source == "environment"
    assert guards["guarded"].detail is None


@pytest.mark.parametrize(
    ("first_name", "first_value", "last_name", "last_value", "expected"),
    [
        (
            "HMC_AUTHORIZE_POWER_OPERATIONS",
            "true",
            "hmc_authorize_power_operations",
            "false",
            False,
        ),
        (
            "hmc_authorize_power_operations",
            "false",
            "HMC_AUTHORIZE_POWER_OPERATIONS",
            "true",
            True,
        ),
    ],
)
def test_last_case_insensitive_environment_spelling_wins(
    monkeypatch, first_name, first_value, last_name, last_value, expected
):
    """Source attribution follows pydantic-settings' ordered environment fold."""
    monkeypatch.setenv(first_name, first_value)
    monkeypatch.setenv(last_name, last_value)

    (guard,) = resolve_power_guards(None)

    assert guard.authorize_power_operations is expected
    assert guard.source == "environment"
    assert guard.detail is None


def test_a_connection_that_cannot_be_resolved_is_reported_not_raised(tmp_path, caplog):
    """A tool that describes the surface must not break first when it changes.

    And it must not answer with `config.toml`'s inventory while doing so:
    `ConfigError`'s message names every profile and nickname key in the file
    plus its absolute path, which is the connection inventory
    `connection_scope`'s closed denial templates and ADR 0038 refuse to
    disclose. Here the grant names a profile the file does not carry — ordinary
    drift after a rename — and two of the three profiles it would list are
    connections this policy does not even grant.
    """
    _write_config(
        tmp_path,
        """
        default_profile = "present"

        [profiles.present]
        host = "hmc-a.example.com"
        user = "admin"

        [profiles.prod-secret-hmc]
        host = "hmc-b.example.com"
        user = "admin"

        [nicknames]
        p = "present"
        """,
    )
    policy = _policy([
        {"effects": ["read"], "connections": ["absent"], "targets": "all-targets"}
    ])

    with caplog.at_level(logging.WARNING, logger="hmc_mcp.server_tools.permissions"):
        guards = _by_connection(resolve_power_guards(policy))

    assert guards["absent"].authorize_power_operations is None
    assert guards["absent"].source == "unresolved"
    assert guards["absent"].detail == "ConfigError"
    rendered = repr(guards)
    for withheld in ("present", "prod-secret-hmc", "nickname", str(tmp_path)):
        assert withheld not in rendered
    # The operator's own channel still carries the reason the caller is not told.
    assert "prod-secret-hmc" in caplog.text


def test_an_invalid_setting_names_its_field_without_echoing_the_value(monkeypatch):
    """`detail` carries a cause and a field name, never a rejected input.

    Pydantic quotes the offending value in `input` and `msg`, and the fields it
    validates include `password`; `loc` carries only the `HMCConfig` field name,
    which is a compiled-in identifier. Without it the report an operator is now
    told to trust for this variable answers a malformed
    `HMC_AUTHORIZE_POWER_OPERATIONS` with a word naming no setting.
    """
    monkeypatch.setenv("HMC_PASSWORD", "hunter2")
    monkeypatch.setenv("HMC_AUTHORIZE_POWER_OPERATIONS", "")

    (guard,) = resolve_power_guards(None)

    assert guard.authorize_power_operations is None
    assert guard.source == "unresolved"
    assert guard.detail == "ValidationError: authorize_power_operations"


def test_the_unresolved_warning_is_said_once_not_once_per_call(tmp_path, caplog):
    """The MCP client owns the call rate; the operator's log must not.

    `hmc_effective_permissions` is in the `read` effect class, so a policy
    granting `effects = ["read"]` cannot withhold it, and a stale profile fails
    on every call. Undeduplicated, the channel that floods is the one this
    design routes the withheld reason to.
    """
    _write_config(
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

    with caplog.at_level(logging.WARNING, logger="hmc_mcp.server_tools.permissions"):
        reported: set[tuple[str, str]] = set()
        for _ in range(3):
            resolve_power_guards(policy, reported)

    assert len(caplog.records) == 1


@pytest.mark.asyncio
async def test_each_application_has_its_own_unresolved_warning_history(
    tmp_path, caplog
):
    """A fresh application emits its own startup-generation diagnostics."""
    _write_config(
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
    applications = (create_mcp(policy), create_mcp(policy))

    with caplog.at_level(logging.WARNING, logger="hmc_mcp.server_tools.permissions"):
        for application in applications:
            async with Client(application) as client:
                await client.call_tool("hmc_effective_permissions", {})

    unresolved = [
        record
        for record in caplog.records
        if "reported as unresolved" in record.getMessage()
    ]
    assert len(unresolved) == 2


def test_one_malformed_profile_does_not_take_down_the_whole_report(tmp_path):
    """`build_config` can raise outside any list this module could enumerate.

    A profile key spelled `_env_file` collides with the keyword
    `_load_profile_from_document` passes and raises `TypeError`. This is the only
    path that builds a config for every granted connection in one call, so an
    escaping exception would cost the operator the guard state of the
    connections that resolve fine — in exactly the situation the report exists
    to diagnose.
    """
    _write_config(
        tmp_path,
        """
        default_profile = "sound"

        [profiles.sound]
        host = "hmc-a.example.com"
        user = "admin"
        authorize_power_operations = true

        [profiles.broken]
        host = "hmc-b.example.com"
        user = "admin"
        _env_file = "/etc/passwd"
        """,
    )
    policy = _policy([
        {
            "effects": ["read"],
            "connections": ["sound", "broken"],
            "targets": "all-targets",
        }
    ])

    guards = _by_connection(resolve_power_guards(policy))

    assert guards["broken"].authorize_power_operations is None
    assert guards["broken"].source == "unresolved"
    assert guards["broken"].detail == "TypeError"
    assert guards["sound"].authorize_power_operations is True
    assert guards["sound"].source == "profile"


def test_an_ambient_host_collapses_the_reported_set_to_the_default(
    monkeypatch, tmp_path
):
    """`HMC_HOST` collapses every token to the default connection at dispatch.

    `connection_scope.selected_connection` rule 1, because `build_config` gates
    its whole TOML branch on it. Without the same collapse here the report would
    list rows for named profiles nothing can reach and omit the one every
    permitted call resolves to.
    """
    _write_config(
        tmp_path,
        """
        default_profile = "guarded"

        [profiles.guarded]
        host = "hmc-a.example.com"
        user = "admin"

        [profiles.open]
        host = "hmc-b.example.com"
        user = "admin"
        """,
    )
    monkeypatch.setenv("HMC_HOST", "hmc-c.example.com")
    policy = _policy([
        {
            "effects": ["read"],
            "connections": ["<default>", "guarded", "open"],
            "targets": "all-targets",
        }
    ])

    guards = resolve_power_guards(policy)

    assert [guard.connection for guard in guards] == [DEFAULT_CONNECTION_TOKEN]


@pytest.mark.parametrize("name", ["hmc_host", "Hmc_Host"])
def test_an_ambient_host_case_variant_collapses_report_like_dispatch(
    monkeypatch, name
):
    monkeypatch.setenv(name, "hmc-c.example.com")
    policy = _policy([
        {
            "effects": ["read"],
            "connections": ["<default>", "guarded"],
            "targets": "all-targets",
        }
    ])

    guards = resolve_power_guards(policy)

    assert selected_connection("guarded", tool="hmc_get_metrics") is None
    assert [guard.connection for guard in guards] == [DEFAULT_CONNECTION_TOKEN]


@pytest.mark.parametrize("name", ["HMC_HOST", "hmc_host", "Hmc_Host"])
def test_an_ambient_host_does_not_read_the_config_document(monkeypatch, name):
    def unexpected_read(path):
        pytest.fail(f"environment-only resolution read {path}")

    monkeypatch.setattr(config_module, "_read_config_document", unexpected_read)
    monkeypatch.setenv(name, "hmc-c.example.com")

    guards = resolve_power_guards(None)

    assert [guard.connection for guard in guards] == [DEFAULT_CONNECTION_TOKEN]


@pytest.mark.parametrize("name", ["HMC_HOST", "hmc_host", "Hmc_Host"])
def test_an_ambient_host_with_no_default_grant_reports_nothing(name):
    """The collapse is an intersection, not a substitution.

    Every token becomes the default connection, and a policy that does not grant
    the default connection then denies every call — so there is no connection
    whose guard state describes a call this server would make.
    """
    policy = _policy([
        {"effects": ["read"], "connections": ["lab"], "targets": "all-targets"}
    ])

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(name, "hmc-c.example.com")
        assert resolve_power_guards(policy) == ()


def test_an_empty_connection_set_does_not_read_the_config_document(monkeypatch):
    def unexpected_read(path):
        pytest.fail(f"empty report read {path}")

    monkeypatch.setattr(config_module, "_read_config_document", unexpected_read)
    policy = _policy([])

    assert resolve_power_guards(policy) == ()


def test_an_empty_ambient_host_does_not_collapse_named_connections(monkeypatch):
    monkeypatch.setenv("hmc_host", "")
    policy = _policy([
        {"effects": ["read"], "connections": ["lab"], "targets": "all-targets"}
    ])

    guards = resolve_power_guards(policy)

    assert [guard.connection for guard in guards] == ["lab"]


def test_a_connection_no_grant_names_is_not_reported():
    """The set is the policy's connection dimension, not that plus the default.

    A call resolving to a connection no grant names is denied at dispatch
    (ADR 0038), so an entry for it would describe a call this server refuses —
    and would resolve a profile the policy withholds in order to say so.
    """
    policy = _policy([
        {"effects": ["read"], "connections": ["lab"], "targets": "all-targets"}
    ])

    guards = resolve_power_guards(policy)

    assert [guard.connection for guard in guards] == ["lab"]


def test_describe_carries_the_guards_it_is_given():
    """The report is assembled from a resolved value, not resolved inside it.

    `build_effective_permissions` stays a pure function of its arguments: the filesystem and
    environment reads happen at the one call site that owns them.
    """
    guards = resolve_power_guards(None)

    result = build_effective_permissions({}, None, TOOL_SECURITY, guards)

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
            "authorize_power_operations": True,
            "source": "environment",
            "detail": None,
        }
    ]
