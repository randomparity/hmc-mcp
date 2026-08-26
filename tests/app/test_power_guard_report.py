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

import pytest
from fastmcp import Client

from hmc_mcp import server_permissions
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
    # The unresolved-connection log deduplicates process-wide, so a pair another
    # test already reported would arrive here at DEBUG and vanish from caplog.
    server_permissions._reported_unresolved.clear()


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
    assert guards[0].authorize_power_operations is False
    assert guards[0].source == "default"
    assert guards[0].detail is None


def test_an_unreadable_config_file_reads_as_default_on_the_default_connection(
    monkeypatch, tmp_path, caplog
):
    """Characterization: `build_config` swallows this, so the report cannot see it.

    With no profile named, `build_config` catches the `ConfigError` itself and
    falls through to env-only construction, so `_power_guard` is handed a valid
    config and `source` reads `default` — the label
    `docs/environment-variables.md` otherwise glosses as "nothing you wrote
    arrived". The boolean is right; the operator instruction that paragraph gives
    rests entirely on this behaviour. Pinned here so a change to
    `common.build_config`'s swallow reddens a test rather than rotting a doc.
    """
    _write_config(monkeypatch, tmp_path, "[profiles.a\nhost = 'h'\n")
    policy = _policy(ALL_TOOLS_GRANT)

    with caplog.at_level(logging.DEBUG, logger="hmc_mcp.server_permissions"):
        (guard,) = resolve_power_guards(policy)

    assert guard.connection == DEFAULT_CONNECTION_TOKEN
    assert guard.authorize_power_operations is False
    assert guard.source == "default"
    assert caplog.records == []


def test_an_environment_variable_is_reported_as_such(monkeypatch):
    """The variable is the setting that cannot be selected around, so name it."""
    monkeypatch.setenv("HMC_AUTHORIZE_POWER_OPERATIONS", "true")

    (guard,) = resolve_power_guards(None)

    assert guard.authorize_power_operations is True
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

    assert guards["guarded"].authorize_power_operations is True
    assert guards["guarded"].source == "profile"
    assert guards["open"].authorize_power_operations is False
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

    assert guards["guarded"].authorize_power_operations is False
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
        {
            "effects": ["read"],
            "connections": ["<default>", "guarded"],
            "targets": "all-targets",
        }
    ])

    guards = _by_connection(resolve_power_guards(policy))

    assert guards[DEFAULT_CONNECTION_TOKEN].authorize_power_operations is False
    assert guards[DEFAULT_CONNECTION_TOKEN].source == "default"


def test_a_case_variant_environment_variable_asserts_no_origin(monkeypatch):
    """Neither probe is honest for both paths, so `source` claims neither.

    `HMCConfig` leaves pydantic-settings' `case_sensitive` at `False`, so on the
    env-only path a lower-case spelling sets the field and an exact-key probe
    would fall through to the `model_fields_set` arm and report `profile` — the
    label an operator reads as "my file took effect" — with no file in existence
    at all.
    """
    monkeypatch.setenv("hmc_authorize_power_operations", "true")

    (guard,) = resolve_power_guards(None)

    assert guard.authorize_power_operations is True
    assert guard.source == "ambiguous"
    assert "case variant" in guard.detail


def test_a_case_variant_does_not_claim_a_value_the_environment_lost(
    monkeypatch, tmp_path
):
    """The mirror case, and the reason `environment` needs the exact spelling.

    `_load_profile_from_document` drops a TOML key only when its exact
    upper-case spelling is a key of `os.environ`, so a case variant leaves the
    profile's value in the init kwargs, where pydantic-settings ranks it above
    the environment. The environment loses here — `authorized` is the profile's
    `true`, not the variable's `false` — and a case-insensitive probe would
    report that the environment won.
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
    monkeypatch.setenv("hmc_authorize_power_operations", "false")
    policy = _policy([
        {"effects": ["read"], "connections": ["guarded"], "targets": "all-targets"}
    ])

    guards = _by_connection(resolve_power_guards(policy))

    assert guards["guarded"].authorize_power_operations is True
    assert guards["guarded"].source == "ambiguous"


def test_a_connection_that_cannot_be_resolved_is_reported_not_raised(
    monkeypatch, tmp_path, caplog
):
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
        monkeypatch,
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

    with caplog.at_level(logging.WARNING, logger="hmc_mcp.server_permissions"):
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


def test_the_unresolved_warning_is_said_once_not_once_per_call(
    monkeypatch, tmp_path, caplog
):
    """The MCP client owns the call rate; the operator's log must not.

    `hmc_effective_permissions` is in the `read` effect class, so a policy
    granting `effects = ["read"]` cannot withhold it, and a stale profile fails
    on every call. Undeduplicated, the channel that floods is the one this
    design routes the withheld reason to.
    """
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

    with caplog.at_level(logging.WARNING, logger="hmc_mcp.server_permissions"):
        for _ in range(3):
            resolve_power_guards(policy)

    assert len(caplog.records) == 1


def test_one_malformed_profile_does_not_take_down_the_whole_report(
    monkeypatch, tmp_path
):
    """`build_config` can raise outside any list this module could enumerate.

    A profile key spelled `_env_file` collides with the keyword
    `_load_profile_from_document` passes and raises `TypeError`. This is the only
    path that builds a config for every granted connection in one call, so an
    escaping exception would cost the operator the guard state of the
    connections that resolve fine — in exactly the situation the report exists
    to diagnose.
    """
    _write_config(
        monkeypatch,
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
        monkeypatch,
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


def test_an_ambient_host_with_no_default_grant_reports_nothing():
    """The collapse is an intersection, not a substitution.

    Every token becomes the default connection, and a policy that does not grant
    the default connection then denies every call — so there is no connection
    whose guard state describes a call this server would make.
    """
    policy = _policy([
        {"effects": ["read"], "connections": ["lab"], "targets": "all-targets"}
    ])

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HMC_HOST", "hmc-c.example.com")
        assert resolve_power_guards(policy) == ()


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
            "authorize_power_operations": True,
            "source": "environment",
            "detail": None,
        }
    ]
