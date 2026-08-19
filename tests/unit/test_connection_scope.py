"""Normalization and grant evaluation for dispatch-time connection scope (#222).

These tests exercise the real platform path resolution under a monkeypatched
home directory rather than patching ``resolve_config_path``, so the selection
order they pin is the one a deployment actually gets.
"""

from __future__ import annotations

import pytest

from hmc_mcp.access_policy import compile_access_policy
from hmc_mcp.config import config_dir
from hmc_mcp.connection_scope import (
    UNRESOLVED,
    ConnectionScopeError,
    connection_authorizer,
    selected_connection,
)
from hmc_mcp.tool_registry import ToolSecurity

# `prod` is both a profile key and a nickname targeting `lab`; `load_profile`
# resolves it to the profile, and a nicknames-first normalizer would answer
# `lab` — the fail-open this fixture exists to catch.
CONFIG_TOML = """\
default_profile = "prod"

[profiles.prod]
host = "prod-hmc.example.com"
user = "admin"
password = "prodpass"    # pragma: allowlist secret

[profiles.lab]
host = "lab-hmc.example.com"
user = "admin"
password = "labpass"    # pragma: allowlist secret

[profiles.scratch]
host = "scratch-hmc.example.com"
user = "admin"
password = "scratchpass"    # pragma: allowlist secret

[nicknames]
prod = "lab"
big-iron = "lab"
dangling = "absent-profile"
"""

SECURITY = ToolSecurity(effect="mutate", operation="lpar.delete", target_kind="console")


@pytest.fixture
def no_config(tmp_path, monkeypatch):
    """A home directory holding no config.toml at all.

    The real platform resolution runs against it — ``config_dir()``'s own
    branching — rather than ``resolve_config_path`` being patched away, so the
    selection order these tests pin is the one a deployment actually gets.
    """
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("HMC_HOST", raising=False)
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def config(no_config):
    """The same isolated home, holding CONFIG_TOML at the platform-native path."""
    path = config_dir() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CONFIG_TOML, encoding="utf-8")
    return path


def _policy(*connections: str, tools=("hmc_delete_lpar",), name="test"):
    document = {
        "policies": {
            name: {
                "grants": [
                    {
                        "tools": list(tools),
                        "connections": list(connections),
                        "targets": "all-targets",
                    }
                ]
            }
        }
    }
    security = {tool: SECURITY for tool in tools}
    return compile_access_policy(document, name, security, "test-policy.toml")


# ---------------------------------------------------------------------------
# R6 — rule 0: a non-string token denies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token", [7, 1.5, ["lab"], {"profile": "lab"}, object(), 0, [], {}]
)
def test_non_string_token_resolves_to_nothing(config, token):
    """No grant can hold the unresolved sentinel, so it always denies."""
    assert selected_connection(token, tool="hmc_delete_lpar") == UNRESOLVED


def test_non_string_token_denies_even_under_hmc_host(config, monkeypatch):
    """Rule 0 runs before rule 1, so the collapse cannot launder a bad type."""
    monkeypatch.setenv("HMC_HOST", "env-hmc.example.com")
    assert selected_connection(7, tool="hmc_delete_lpar") == UNRESOLVED
    authorize = connection_authorizer(_policy("<default>"))
    with pytest.raises(ConnectionScopeError):
        authorize("hmc_delete_lpar", SECURITY, {"profile": 7})


# ---------------------------------------------------------------------------
# R7 — rule 1: HMC_HOST collapses the token space
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", [None, "", "lab", "prod", "never-configured"])
def test_hmc_host_collapses_every_token_to_the_default(config, monkeypatch, token):
    monkeypatch.setenv("HMC_HOST", "env-hmc.example.com")
    assert selected_connection(token, tool="hmc_delete_lpar") is None


def test_empty_hmc_host_does_not_collapse(config, monkeypatch):
    """build_config tests truthiness, so HMC_HOST='' leaves the TOML branch live."""
    monkeypatch.setenv("HMC_HOST", "")
    assert selected_connection("lab", tool="hmc_delete_lpar") == "lab"


# ---------------------------------------------------------------------------
# R8 — rule 2: a falsy token is the default connection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", [None, ""])
def test_falsy_token_is_the_default_connection(config, token):
    assert selected_connection(token, tool="hmc_delete_lpar") is None


def test_default_is_not_resolved_through_hmc_profile(config, monkeypatch):
    """ADR 0036 fixed <default> as the denotation; it is not substituted."""
    monkeypatch.setenv("HMC_PROFILE", "lab")
    assert selected_connection(None, tool="hmc_delete_lpar") is None


# ---------------------------------------------------------------------------
# R9 — rule 3: profiles first, then nicknames, one level
# ---------------------------------------------------------------------------


def test_a_profile_key_resolves_to_itself(config):
    assert selected_connection("lab", tool="hmc_delete_lpar") == "lab"


def test_a_profile_key_wins_over_a_same_named_nickname(config):
    """Mirrors load_profile's `if name not in profiles:` gate."""
    assert selected_connection("prod", tool="hmc_delete_lpar") == "prod"


def test_a_nickname_resolves_to_its_target_profile(config):
    assert selected_connection("big-iron", tool="hmc_delete_lpar") == "lab"


# ---------------------------------------------------------------------------
# R10 — normalization fails closed on anything else
# ---------------------------------------------------------------------------


def test_unknown_token_resolves_to_nothing(config):
    assert selected_connection("never-configured", tool="hmc_delete_lpar") == UNRESOLVED


def test_dangling_nickname_resolves_to_nothing(config):
    """load_profile raises here too; refusing reaches the outcome earlier."""
    assert selected_connection("dangling", tool="hmc_delete_lpar") == UNRESOLVED


def test_the_unresolved_sentinel_can_never_appear_in_a_grant(config):
    """It denies structurally: access_policy rejects an empty connection entry."""
    from hmc_mcp.access_policy import AccessPolicyError

    with pytest.raises(AccessPolicyError, match="empty or padded"):
        _policy(UNRESOLVED)


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(
            lambda text: text.replace('big-iron = "lab"', "big-iron = 7"),
            id="nicknames-not-a-string",
        ),
        pytest.param(lambda text: "nicknames = 3\n", id="nicknames-not-a-table"),
        pytest.param(
            lambda text: "profiles = 'not-a-table'\n", id="profiles-not-a-table"
        ),
        pytest.param(lambda text: "this is [not valid toml ][[[", id="unparseable"),
        pytest.param(lambda text: "\udcff", id="not-utf-8"),
    ],
)
def test_an_unreadable_configuration_denies_without_leaking_the_path(config, corrupt):
    """R13: no filesystem path and no raw exception text reaches the caller."""
    config.write_bytes(corrupt(CONFIG_TOML).encode("utf-8", "surrogateescape"))
    with pytest.raises(ConnectionScopeError) as error:
        selected_connection("lab", tool="hmc_delete_lpar")
    message = str(error.value)
    assert message == (
        "hmc_delete_lpar cannot be authorized: the configured HMC connections "
        "could not be read."
    )
    assert str(config) not in message
    assert error.value.__cause__ is not None


def test_an_unreadable_file_denies_without_leaking_the_path(config):
    """The OSError arm: exists() succeeds and read_text does not."""
    config.chmod(0o000)
    try:
        with pytest.raises(ConnectionScopeError) as error:
            selected_connection("lab", tool="hmc_delete_lpar")
    finally:
        config.chmod(0o600)
    assert str(config) not in str(error.value)
    assert "Permission denied" not in str(error.value)


def test_named_token_resolves_to_nothing_when_no_config_file_exists(no_config):
    assert selected_connection("lab", tool="hmc_delete_lpar") == UNRESOLVED


def test_default_connection_survives_a_missing_config_file(no_config):
    """An env-var-only deployment still has the connection <default> denotes."""
    assert selected_connection(None, tool="hmc_delete_lpar") is None


# ---------------------------------------------------------------------------
# R11 — evaluation is one predicate per grant
# ---------------------------------------------------------------------------


def test_a_granted_connection_permits(config):
    authorize = connection_authorizer(_policy("lab"))
    assert authorize("hmc_delete_lpar", SECURITY, {"profile": "lab"}) is None


def test_a_withheld_connection_denies(config):
    authorize = connection_authorizer(_policy("lab"))
    with pytest.raises(ConnectionScopeError):
        authorize("hmc_delete_lpar", SECURITY, {"profile": "prod"})


def test_the_default_connection_is_granted_by_its_token(config):
    authorize = connection_authorizer(_policy("<default>"))
    assert authorize("hmc_delete_lpar", SECURITY, {"profile": None}) is None


def test_a_profile_grant_does_not_cover_an_omitted_argument(config):
    """The mirror ADR 0036 recorded: omitting the argument means <default>."""
    authorize = connection_authorizer(_policy("prod"))
    with pytest.raises(ConnectionScopeError):
        authorize("hmc_delete_lpar", SECURITY, {"profile": None})


def test_a_nickname_cannot_launder_reach_to_a_withheld_profile(config):
    """`big-iron` targets `lab`; a policy granting only the alias must not permit it."""
    authorize = connection_authorizer(_policy("big-iron"))
    with pytest.raises(ConnectionScopeError):
        authorize("hmc_delete_lpar", SECURITY, {"profile": "big-iron"})


def test_a_nickname_reaching_a_granted_profile_permits(config):
    authorize = connection_authorizer(_policy("lab"))
    assert authorize("hmc_delete_lpar", SECURITY, {"profile": "big-iron"}) is None


def test_hmc_host_denies_a_policy_that_names_only_profiles(config, monkeypatch):
    monkeypatch.setenv("HMC_HOST", "env-hmc.example.com")
    authorize = connection_authorizer(_policy("lab"))
    with pytest.raises(ConnectionScopeError):
        authorize("hmc_delete_lpar", SECURITY, {"profile": "lab"})


def test_hmc_host_permits_a_policy_granting_the_default(config, monkeypatch):
    monkeypatch.setenv("HMC_HOST", "env-hmc.example.com")
    authorize = connection_authorizer(_policy("<default>"))
    assert authorize("hmc_delete_lpar", SECURITY, {"profile": "lab"}) is None


def test_a_tool_no_grant_covers_is_denied(config):
    authorize = connection_authorizer(_policy("lab"))
    with pytest.raises(ConnectionScopeError):
        authorize("hmc_run_command", SECURITY, {"profile": "lab"})


def test_a_grant_for_another_tool_does_not_supply_the_connection(config):
    """ADR 0036: one grant must cover the tool and the connection together.

    With one dimension evaluated, the per-grant loop and a cross-grant union of
    connections are extensionally identical, so this cannot witness the loop
    shape — only that `grants_for` filters by tool first. #223's target
    dimension is what makes the difference observable.
    """
    document = {
        "policies": {
            "split": {
                "grants": [
                    {
                        "tools": ["hmc_list_lpars"],
                        "connections": ["prod"],
                        "targets": "all-targets",
                    },
                    {
                        "tools": ["hmc_delete_lpar"],
                        "connections": ["lab"],
                        "targets": "all-targets",
                    },
                ]
            }
        }
    }
    security = {"hmc_list_lpars": SECURITY, "hmc_delete_lpar": SECURITY}
    policy = compile_access_policy(document, "split", security, "test-policy.toml")
    authorize = connection_authorizer(policy)
    assert authorize("hmc_list_lpars", SECURITY, {"profile": "prod"}) is None
    with pytest.raises(ConnectionScopeError):
        authorize("hmc_delete_lpar", SECURITY, {"profile": "prod"})


def test_a_tool_without_a_connection_argument_is_permitted(config):
    """No connection is selected, so there is no connection to scope."""
    security = ToolSecurity(
        effect="read",
        operation="permissions.describe",
        target_kind="none",
        connection_argument=None,
    )
    authorize = connection_authorizer(_policy("lab"))
    assert authorize("hmc_effective_permissions", security, {}) is None


# ---------------------------------------------------------------------------
# R13 — the denial message is a closed template
# ---------------------------------------------------------------------------


def test_denial_names_the_tool_the_policy_and_the_callers_own_token(config):
    authorize = connection_authorizer(_policy("lab", name="lab-only"))
    with pytest.raises(ConnectionScopeError) as error:
        authorize("hmc_delete_lpar", SECURITY, {"profile": "prod"})
    assert str(error.value) == (
        "hmc_delete_lpar is not permitted on connection 'prod' by access policy "
        "'lab-only'. Grant that connection in a policy grant that already names "
        "hmc_delete_lpar, or call hmc_delete_lpar with a connection the policy grants."
    )


@pytest.mark.parametrize("token", [None, ""])
def test_denial_renders_an_omitted_argument_as_the_default_token(config, token):
    authorize = connection_authorizer(_policy("lab", name="lab-only"))
    with pytest.raises(ConnectionScopeError) as error:
        authorize("hmc_delete_lpar", SECURITY, {"profile": token})
    assert "connection '<default>'" in str(error.value)


def test_denial_renders_a_falsy_non_string_as_itself(config):
    """R13 renders what the caller named, and 0 is not the default connection."""
    authorize = connection_authorizer(_policy("lab", name="lab-only"))
    with pytest.raises(ConnectionScopeError) as error:
        authorize("hmc_delete_lpar", SECURITY, {"profile": 0})
    assert "connection 0 " in str(error.value)
    assert "<default>" not in str(error.value)


def test_denial_explains_the_hmc_host_collapse(config, monkeypatch):
    monkeypatch.setenv("HMC_HOST", "env-hmc.example.com")
    authorize = connection_authorizer(_policy("lab", name="lab-only"))
    with pytest.raises(ConnectionScopeError) as error:
        authorize("hmc_delete_lpar", SECURITY, {"profile": "prod"})
    message = str(error.value)
    assert "HMC_HOST is set, so the 'profile' argument is ignored" in message
    assert "evaluated as the '<default>' connection" in message
    assert "env-hmc.example.com" not in message


def test_the_hmc_host_clause_names_the_declared_selector(config, monkeypatch):
    """The clause is not allowed to hardcode a selector name the metadata owns."""
    monkeypatch.setenv("HMC_HOST", "env-hmc.example.com")
    security = ToolSecurity(
        effect="mutate",
        operation="lpar.delete",
        target_kind="console",
        connection_argument="connection",
    )
    authorize = connection_authorizer(_policy("lab", name="lab-only"))
    with pytest.raises(ConnectionScopeError) as error:
        authorize("hmc_delete_lpar", security, {"connection": "prod"})
    assert "the 'connection' argument is ignored" in str(error.value)


def test_a_nickname_and_an_unknown_token_are_denied_identically(config):
    """R13: one template, so a denial is not a config.toml membership oracle.

    `big-iron` is in the nickname table and `never-configured` is in neither
    table. A caller that could tell them apart could enumerate the operator's
    configuration one probe at a time, through a channel no policy can withhold.
    """
    authorize = connection_authorizer(_policy("prod", name="prod-only"))

    def _denial(token: str) -> str:
        with pytest.raises(ConnectionScopeError) as error:
            authorize("hmc_delete_lpar", SECURITY, {"profile": token})
        return str(error.value)

    known = _denial("big-iron")
    unknown = _denial("never-configured")
    assert known.replace("big-iron", "TOKEN") == unknown.replace(
        "never-configured", "TOKEN"
    )
    assert "nickname" not in known
    assert "lab" not in known


def test_denial_never_enumerates_the_granted_connections(config):
    authorize = connection_authorizer(_policy("lab", "prod", name="both"))
    with pytest.raises(ConnectionScopeError) as error:
        authorize("hmc_delete_lpar", SECURITY, {"profile": "scratch"})
    message = str(error.value)
    assert "lab" not in message
    assert "prod" not in message


def test_denial_neutralizes_control_characters_in_a_caller_token(config):
    """The token is caller-controlled, so it is rendered with repr, not raw."""
    authorize = connection_authorizer(_policy("lab", name="lab-only"))
    with pytest.raises(ConnectionScopeError) as error:
        authorize("hmc_delete_lpar", SECURITY, {"profile": "a\nb"})
    assert "\n" not in str(error.value)


def test_a_missing_selector_key_is_a_malformed_call_not_an_omitted_argument(config):
    """`authorized` applies defaults, so an absent key means the mapping is wrong.

    Treating it as omitted would silently make it the `<default>` connection,
    which a policy granting `<default>` would then permit.
    """
    authorize = connection_authorizer(_policy("<default>"))
    with pytest.raises(KeyError):
        authorize("hmc_delete_lpar", SECURITY, {})


def test_a_non_string_token_under_hmc_host_gets_no_collapse_clause(config, monkeypatch):
    """Rule 0 runs first, so the call was never evaluated as the default."""
    monkeypatch.setenv("HMC_HOST", "env-hmc.example.com")
    authorize = connection_authorizer(_policy("<default>"))
    with pytest.raises(ConnectionScopeError) as error:
        authorize("hmc_delete_lpar", SECURITY, {"profile": 7})
    assert "HMC_HOST is set" not in str(error.value)
