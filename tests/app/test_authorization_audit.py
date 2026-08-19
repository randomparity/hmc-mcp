"""Audit records emitted at the dispatch boundary, and what they must not carry.

Covers docs/workflow/specs/2026-08-19-authorization-audit-events-design.md; the
decision record is docs/adr/0040-authorization-audit-events.md.

Logging isolation comes from the autouse ``isolate_audit_logging`` fixture in
``tests/conftest.py``.

Spec test -> node id:
  16  test_a_permitted_call_emits_one_record_describing_it
  17  test_each_deny_reason_code_is_reachable
  18  test_exactly_one_record_per_call
  19  test_a_connectionless_tool_emits_nothing
  20  test_a_malformed_call_emits_nothing_and_still_raises
  14c test_a_failing_sink_leaves_the_outcome_and_its_error_unchanged
  22  test_credentials_never_appear
  23  test_a_non_selector_argument_never_appears
  24  test_command_text_never_appears
  25  test_a_response_body_never_appears
  26  test_no_file_path_appears
  27  test_agent_id_is_recorded_as_unverified_attribution
  28  test_the_outcome_is_invariant_under_agent_id
  8b  test_no_module_on_the_decision_path_reads_the_agent_identity
  31  test_an_info_record_does_not_reach_stderr_without_a_sink
  32  test_gates_returns_nothing_without_a_policy
  33  test_authorized_leaves_the_connectionless_handlers_unwrapped
  34  test_the_no_policy_warning_needs_an_existing_policy_file
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from hmc_mcp import audit, server as server_app
from hmc_mcp.access_policy import compile_access_policy
from hmc_mcp.connection_scope import ConnectionScopeError
from hmc_mcp.dispatch_scope import dispatch_authorizer
from hmc_mcp.server import TOOL_SECURITY
from hmc_mcp.target_scope import TargetScopeError
from hmc_mcp.tool_registry import authorized

SOURCE = "test-access-policy.toml"

# Sentinels. Chosen to be low-entropy so detect-secrets' KeywordDetector is the
# only plugin that could fire, and marked where that matters.
SENTINEL_PASSWORD = "SENTINEL-CREDENTIAL-DO-NOT-LOG"  # pragma: allowlist secret
SENTINEL_USER = "SENTINEL-USER-DO-NOT-LOG"
SENTINEL_ARG = "SENTINEL-ARGUMENT-DO-NOT-LOG"
SENTINEL_CMD = "SENTINEL-COMMAND-TEXT-DO-NOT-LOG"
SENTINEL_BODY = "SENTINEL-RESPONSE-BODY-DO-NOT-LOG"

LAB_TARGETS = [
    {
        "effects": ["read", "mutate", "destructive"],
        "connections": ["lab"],
        "targets": {"lpar": ["db-01"], "managed_system": ["sys-a"]},
    }
]
LAB_ALL = [
    {
        "effects": ["read", "mutate", "destructive"],
        "tools": ["hmc_run_command"],
        "connections": ["lab"],
        "targets": "all-targets",
    }
]


def _policy(grants: list[dict], name: str = "lab-scoped"):
    return compile_access_policy(
        {"policies": {name: {"grants": grants}}}, name, TOOL_SECURITY, SOURCE
    )


@pytest.fixture(autouse=True)
def lab_profile(tmp_path, monkeypatch):
    """A config.toml holding one profile, at the platform-native path."""
    from hmc_mcp.config import config_dir

    for name in ("XDG_CONFIG_HOME", "APPDATA", "HMC_HOST", "HMC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(audit.ATTRIBUTION_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    path = config_dir() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[profiles.lab]\n"
        'host = "lab.invalid"\n'
        f'user = "{SENTINEL_USER}"\n'
        f'password = "{SENTINEL_PASSWORD}"\n'  # pragma: allowlist secret
        "\n[profiles.prod]\n"
        'host = "prod.invalid"\n'
        f'user = "{SENTINEL_USER}"\n'
        f'password = "{SENTINEL_PASSWORD}"\n'  # pragma: allowlist secret
    )
    monkeypatch.setenv("HMC_PASSWORD", SENTINEL_PASSWORD)
    monkeypatch.setenv("HMC_USER", SENTINEL_USER)
    return path


@pytest.fixture
def records() -> list[dict]:
    """Every audit record emitted during the test, parsed."""
    parsed: list[dict] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            parsed.append(json.loads(record.getMessage()))

    logger = logging.getLogger(audit.AUDIT_LOGGER_NAME)
    logger.addHandler(_Collect())
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return parsed


def _authorize(grants=None, tool="hmc_power_off_lpar", **arguments):
    """Run one authorization, returning any error it raised."""
    security = TOOL_SECURITY[tool]
    call = {"lpar_name_or_uuid": "db-01", "system_name_or_uuid": "sys-a",
            "profile": "lab"}
    call.update(arguments)
    bound = {key: call.get(key) for key in
             {selector.argument for selector in security.targets}
             | {security.connection_argument} - {None}}
    try:
        dispatch_authorizer(_policy(grants or LAB_TARGETS))(tool, security, bound)
    except (ConnectionScopeError, TargetScopeError) as error:
        return error
    return None


def test_a_permitted_call_emits_one_record_describing_it(records):
    """Spec 16."""
    assert _authorize() is None
    assert len(records) == 1
    record = records[0]
    assert record["event"] == "authorization"
    assert record["decision"] == "allow"
    assert record["reason"] == "permitted"
    assert record["policy"] == "lab-scoped"
    assert record["tool"] == "hmc_power_off_lpar"
    assert record["effect"] == "destructive"
    assert record["connection"] == {
        "state": "present", "selector": "lab", "resolved": "lab"
    }
    named = {entry["argument"]: entry for entry in record["targets"]}
    assert named["lpar_name_or_uuid"]["value"] == "db-01"
    assert named["lpar_name_or_uuid"]["state"] == "present"


@pytest.mark.parametrize(
    ("reason", "kwargs"),
    [
        ("connection-not-granted", {"profile": "prod"}),
        ("target-not-granted", {"lpar_name_or_uuid": "other-01"}),
        ("target-selector-absent", {"system_name_or_uuid": None}),
        ("target-selector-unreadable", {"lpar_name_or_uuid": object()}),
    ],
)
def test_each_deny_reason_code_is_reachable(records, reason, kwargs):
    """Spec 17, four of the six deny codes, through the real authorizer."""
    assert _authorize(**kwargs) is not None
    assert len(records) == 1
    assert records[0]["decision"] == "deny"
    assert records[0]["reason"] == reason


def test_target_unboundable_is_reachable(records):
    """Spec 17, fifth deny code — and the shape it takes is ADR 0039's residual.

    `_compile_grant` refuses a *named* tool that no table can bound, so this code
    is unreachable that way. It is reachable through `effects`, which resolves
    tools without that per-tool check: `hmc_console_info` declares no selector, so
    the `lpar` table below compiles (another granted tool declares that kind) and
    then bounds nothing for it. 24 tools in this checkout are in that position.
    """
    security = TOOL_SECURITY["hmc_console_info"]
    assert not security.exhaustive_targets
    with pytest.raises(TargetScopeError):
        dispatch_authorizer(_policy(LAB_TARGETS))(
            "hmc_console_info", security, {"profile": "lab"}
        )
    assert records[0]["reason"] == "target-unboundable"


def test_configuration_unreadable_is_reachable(records, lab_profile):
    """Spec 17, sixth deny code, and the only arm with null targets."""
    lab_profile.write_text("this is not valid toml [[[")
    error = _authorize()
    assert isinstance(error, ConnectionScopeError)
    assert len(records) == 1
    assert records[0]["reason"] == "configuration-unreadable"
    assert records[0]["targets"] is None
    assert records[0]["connection"]["resolved"] is None


def test_exactly_one_record_per_call(records):
    """Spec 18. Not zero, not two, on either path."""
    _authorize()
    assert len(records) == 1
    _authorize(profile="prod")
    assert len(records) == 2


def test_a_connectionless_tool_emits_nothing(records):
    """Spec 19. The boundary declines to scope it, so it records nothing."""
    security = TOOL_SECURITY["hmc_effective_permissions"]
    assert security.connection_argument is None
    dispatch_authorizer(_policy(LAB_ALL))("hmc_effective_permissions", security, {})
    assert records == []


def test_a_malformed_call_emits_nothing_and_still_raises(records):
    """Spec 20. A KeyError is a registration defect, not a decision."""
    security = TOOL_SECURITY["hmc_power_off_lpar"]
    with pytest.raises(KeyError):
        dispatch_authorizer(_policy(LAB_TARGETS))(
            "hmc_power_off_lpar", security, {"lpar_name_or_uuid": "db-01"}
        )
    assert records == []


def test_a_failing_sink_leaves_the_outcome_and_its_error_unchanged(monkeypatch):
    """Spec 14c. Emission is total on both sides — build and write."""
    clean = _authorize(profile="prod")
    assert isinstance(clean, ConnectionScopeError)

    def explode(*_args, **_kwargs):
        raise RuntimeError("renderer is broken")

    monkeypatch.setattr(audit, "_connection", explode)
    broken = _authorize(profile="prod")
    assert isinstance(broken, ConnectionScopeError)
    assert str(broken) == str(clean)

    monkeypatch.undo()
    monkeypatch.setattr(logging.Logger, "log", explode)
    still = _authorize(profile="prod")
    assert isinstance(still, ConnectionScopeError)
    assert str(still) == str(clean)


def _all_text(records: list[dict]) -> str:
    assert records, "an absence assertion over an empty capture proves nothing"
    return json.dumps(records)


def test_credentials_never_appear(records):
    """Spec 22. Structurally absent: `authorize` is handed no config at all."""
    _authorize()
    _authorize(profile="prod")
    text = _all_text(records)
    assert SENTINEL_PASSWORD not in text
    assert SENTINEL_USER not in text


def test_a_non_selector_argument_never_appears(records):
    """Spec 23. `name` is deliberately excluded from REQUIRED_TARGET_ARGUMENTS."""
    security = TOOL_SECURITY["hmc_create_lpar"]
    assert "name" not in {selector.argument for selector in security.targets}
    dispatch_authorizer(_policy(LAB_TARGETS))(
        "hmc_create_lpar",
        security,
        {"name": SENTINEL_ARG, "system_name_or_uuid": "sys-a", "profile": "lab"},
    )
    text = _all_text(records)
    assert SENTINEL_ARG not in text
    assert "sys-a" in text, "a declared selector *is* recorded"


def test_command_text_never_appears(records):
    """Spec 24. `hmc_run_command` declares no selector, so `cmd` is never read."""
    security = TOOL_SECURITY["hmc_run_command"]
    assert security.targets == ()
    dispatch_authorizer(_policy(LAB_ALL))(
        "hmc_run_command", security, {"cmd": SENTINEL_CMD, "profile": "lab"}
    )
    with pytest.raises(ConnectionScopeError):
        dispatch_authorizer(_policy(LAB_ALL))(
            "hmc_run_command", security, {"cmd": SENTINEL_CMD, "profile": "prod"}
        )
    assert SENTINEL_CMD not in _all_text(records)


def test_a_response_body_never_appears(records):
    """Spec 25. The record precedes the handler, so no body exists yet."""
    handler_ran: list[str] = []

    def handler(lpar_name_or_uuid, system_name_or_uuid=None, profile=None):
        handler_ran.append(SENTINEL_BODY)
        return {"body": SENTINEL_BODY}

    security = TOOL_SECURITY["hmc_power_off_lpar"]
    guarded = authorized(
        "hmc_power_off_lpar", security, handler,
        dispatch_authorizer(_policy(LAB_TARGETS)),
    )
    assert guarded("db-01", system_name_or_uuid="sys-a", profile="lab") == {
        "body": SENTINEL_BODY
    }
    assert handler_ran == [SENTINEL_BODY], "the handler did run"
    assert SENTINEL_BODY not in _all_text(records)


def test_no_file_path_appears(records, lab_profile, tmp_path):
    """Spec 26. Both paths — they are reached by different failures."""
    policy_path = tmp_path / "access-policy.toml"
    lab_profile.write_text("not toml [[[")
    _authorize()
    text = _all_text(records)
    assert str(lab_profile) not in text
    assert str(policy_path) not in text
    assert "config.toml" not in text
    assert "access-policy.toml" not in text


def test_agent_id_is_recorded_as_unverified_attribution(records, monkeypatch):
    """Spec 27."""
    monkeypatch.setenv(audit.ATTRIBUTION_ENV, "agent-seven")
    _authorize()
    assert records[0]["attribution"] == {
        "claim": "agent-seven",
        "source": "environment:HMC_AGENT_ID",
        "verified": False,
    }


@pytest.mark.parametrize(
    "value", [None, "lab-scoped", "lab", "prod", "hmc-mcp", "a" * 300]
)
def test_the_outcome_is_invariant_under_agent_id(records, monkeypatch, value):
    """Spec 28. The behavioural sample; test 8b is the invariant."""
    if value is None:
        monkeypatch.delenv(audit.ATTRIBUTION_ENV, raising=False)
    else:
        monkeypatch.setenv(audit.ATTRIBUTION_ENV, value)
    assert _authorize() is None
    assert _authorize(profile="prod") is not None
    assert [record["reason"] for record in records] == [
        "permitted",
        "connection-not-granted",
    ]


def test_no_module_on_the_decision_path_reads_the_agent_identity():
    """Spec 8b. The invariant behind A4, and bounded: a textual scan catches a
    literal read, not an identity threaded in as a parameter."""
    package = Path(audit.__file__).parent
    for module in (
        "dispatch_scope", "connection_scope", "target_scope", "access_policy",
        "tool_registry",
    ):
        source = (package / f"{module}.py").read_text()
        assert audit.ATTRIBUTION_ENV not in source, f"{module} names HMC_AGENT_ID"
        assert "agent_id" not in source, f"{module} reads an agent identity"


def test_an_info_record_does_not_reach_stderr_without_a_sink(capsys):
    """Spec 31. The consequence this package owns from the logging-tree facts.

    Asserted as the consequence rather than as `fastmcp` carrying a RichHandler,
    which would pin a dependency's internals. The root handlers are cleared for
    the duration because `logging.lastResort` is consulted only after an ancestor
    walk finds none, and pytest keeps one there.
    """
    saved = list(logging.root.handlers)
    logging.root.handlers.clear()
    try:
        audit.record_authorization(
            policy="p", tool="t", effect="read", decision="allow",
            reason="permitted", token="lab",
            resolved=audit.resolved_connection("lab"), targets=(),
        )
        assert capsys.readouterr().err == ""
    finally:
        logging.root.handlers[:] = saved


def test_gates_returns_nothing_without_a_policy():
    """Spec 32. Why a default `hmc-mcp serve` emits no authorization record.

    Issue #225 is expected to change this; a future failure here reads as #225
    landing, not as a regression.
    """
    assert server_app._gates(None) == (None, None)


def test_authorized_leaves_the_connectionless_handlers_unwrapped():
    """Spec 33. The structural basis for the no-record cases."""

    def handler(profile=None):
        return None

    for name in ("hmc_list_configured_hosts", "hmc_effective_permissions"):
        security = TOOL_SECURITY[name]
        assert security.connection_argument is None
        assert authorized(name, security, handler, lambda *_: None) is handler

    bearing = TOOL_SECURITY["hmc_power_off_lpar"]
    assert authorized("t", bearing, handler, lambda *_: None) is not handler
    assert authorized("t", bearing, handler, None) is handler


def test_the_no_policy_warning_needs_an_existing_policy_file(tmp_path, monkeypatch):
    """Spec 34. An operator with no policy file gets neither warning nor record.

    Issue #225 is expected to change this too.
    """
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)

    def said_it(lines) -> bool:
        return any("no access policy was selected" in line for line in lines)

    assert not said_it(server_app._startup_warnings(55, None, False))
    path = server_app.resolve_access_policy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    assert said_it(server_app._startup_warnings(55, None, False))


def test_a_declared_selector_is_recorded_but_bounded(records):
    """Spec 3's boundary half, and L4's in-suite twin."""
    _authorize(lpar_name_or_uuid="Z" * 500)
    entry = next(
        item for item in records[0]["targets"]
        if item["argument"] == "lpar_name_or_uuid"
    )
    assert len(entry["value"]) == audit.MAX_VALUE_LENGTH


def test_an_unreadable_connection_token_has_no_reason_code_of_its_own(records):
    """The asymmetry, asserted so it cannot drift silently.

    `reason` names the decision and `state` names the input, and ADR 0038 gave
    the connection dimension one denial template on purpose.
    """
    _authorize(profile=object())
    assert records[0]["reason"] == "connection-not-granted"
    assert records[0]["connection"]["state"] == "unreadable"
    assert records[0]["connection"]["selector"] is None
