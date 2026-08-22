"""Tests for multi-agent LPAR ownership helpers (issue #132)."""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from hmc_mcp import audit, operations_lpar
from hmc_mcp.config import validate_agent_id
from hmc_mcp.operations_lpar import (
    authorize_decommission_lpar_ownership_snapshot,
    authorize_lpar_mutation,
)


# ---------------------------------------------------------------------------
# validate_agent_id
# ---------------------------------------------------------------------------


def test_validate_agent_id_valid():
    validate_agent_id("alice")  # plain name
    validate_agent_id("agent-1")  # hyphens ok
    validate_agent_id("agent.1")  # dots ok
    validate_agent_id("a" * 64)  # max length


def test_validate_agent_id_reserved():
    with pytest.raises(ValueError, match="reserved"):
        validate_agent_id("hmc-mcp")


def test_validate_agent_id_empty():
    with pytest.raises(ValueError, match="empty"):
        validate_agent_id("")


def test_validate_agent_id_too_long():
    with pytest.raises(ValueError, match="64"):
        validate_agent_id("a" * 65)


def test_validate_agent_id_comma():
    with pytest.raises(ValueError, match="comma"):
        validate_agent_id("alice,eve")


def test_validate_agent_id_equals():
    with pytest.raises(ValueError, match="="):
        validate_agent_id("key=val")


def test_validate_agent_id_bracket():
    with pytest.raises(ValueError, match="bracket"):
        validate_agent_id("alice[1]")


def test_validate_agent_id_slash():
    with pytest.raises(ValueError, match="HMC REST API"):
        validate_agent_id("team/agent")


def test_validate_agent_id_colon():
    with pytest.raises(ValueError, match="colon"):
        validate_agent_id("team:agent")


def test_validate_agent_id_space():
    with pytest.raises(ValueError, match="space"):
        validate_agent_id("alice smith")


def test_validate_agent_id_double_quote():
    with pytest.raises(ValueError, match="double quote"):
        validate_agent_id('agent"x')


def test_validate_agent_id_backslash():
    with pytest.raises(ValueError, match="backslash"):
        validate_agent_id("agent\\x")


def test_validate_agent_id_non_ascii():
    with pytest.raises(ValueError, match="printable ASCII"):
        validate_agent_id("alicé")


def test_validate_agent_id_control_char():
    with pytest.raises(ValueError, match="printable ASCII"):
        validate_agent_id("alice\n")


# ---------------------------------------------------------------------------
# stamp_lpar_ownership
# ---------------------------------------------------------------------------


from hmc_mcp.ssh_commands import stamp_lpar_ownership  # noqa: E402 (after validate tests)
from hmc_mcp.config import HMCConfig  # noqa: E402


def _config():
    return HMCConfig(host="hmc.test", user="u", password="p", _env_file=None)


def test_stamp_returns_token_on_success():
    config = _config()
    with patch(
        "hmc_mcp.ssh_commands.set_lpar_description", new=AsyncMock(return_value="")
    ) as mock_set:
        token = asyncio.run(
            stamp_lpar_ownership(config, "sys1", "lpar1", agent_id="alice")
        )
    today = datetime.date.today().isoformat()
    assert token == f"[hmc-mcp owner:alice created:{today}]"
    mock_set.assert_awaited_once()
    # verify set_lpar_description was called with the token as the description arg
    call_args = mock_set.call_args.args
    assert call_args[3] == token


def test_stamp_default_agent_id():
    config = _config()
    with patch(
        "hmc_mcp.ssh_commands.set_lpar_description", new=AsyncMock(return_value="")
    ):
        token = asyncio.run(
            stamp_lpar_ownership(config, "sys1", "lpar1")  # no agent_id
        )
    assert token is not None
    assert "owner:hmc-mcp" in token


def test_stamp_returns_none_on_ssh_error():
    config = _config()
    from hmc_mcp.ssh_commands import HMCCLIError

    with patch(
        "hmc_mcp.ssh_commands.set_lpar_description",
        new=AsyncMock(side_effect=HMCCLIError("SSH failed")),
    ):
        token = asyncio.run(
            stamp_lpar_ownership(config, "sys1", "lpar1", agent_id="alice")
        )
    assert token is None  # swallowed — best-effort


def test_token_format():
    config = _config()
    today = datetime.date.today().isoformat()
    with patch(
        "hmc_mcp.ssh_commands.set_lpar_description", new=AsyncMock(return_value="")
    ):
        token = asyncio.run(
            stamp_lpar_ownership(config, "sys1", "lpar1", agent_id="my-agent")
        )
    assert token == f"[hmc-mcp owner:my-agent created:{today}]"
    # token must pass the existing description validator
    from hmc_mcp.ssh_commands import validate_lpar_description

    validate_lpar_description(token)  # no exception


@pytest.mark.parametrize(
    ("description", "agent_id", "allowed"),
    [
        ("legacy partition", "alice", True),
        ("[hmc-mcp owner:alice created:2026-08-14]", "alice", True),
        ("[hmc-mcp owner:bob created:2026-08-14]", "alice", False),
        ("[hmc-mcp owner:broken]", "alice", False),
    ],
)
def test_authorize_lpar_mutation(description, agent_id, allowed):
    hmc = type(
        "StubHMC", (), {"config": _config().model_copy(update={"agent_id": agent_id})}
    )()
    with patch(
        "hmc_mcp.operations_lpar.get_lpar_description",
        new=AsyncMock(return_value=description),
    ):
        if allowed:
            asyncio.run(authorize_lpar_mutation(hmc, "sys1", "lpar1"))
        else:
            with pytest.raises(PermissionError, match="ownership_override=true"):
                asyncio.run(authorize_lpar_mutation(hmc, "sys1", "lpar1"))


# Spec test -> node id (docs/workflow/specs/2026-08-19-authorization-audit-events-design.md)
#   26a test_authorize_lpar_mutation_override_is_audited
#   26b test_the_override_record_is_bounded_and_escaped
#   26c test_operations_lpar_does_not_resolve_the_audit_logger
#   26d test_the_override_still_reaches_stderr_without_a_sink
#   26e test_both_override_call_sites_emit_and_normal_access_does_not


def _override_records(caplog):
    """Parsed audit records captured at WARNING on the audit logger."""
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == audit.AUDIT_LOGGER_NAME
    ]


def test_authorize_lpar_mutation_override_is_audited(caplog):
    """Spec 26a. Replaces the pre-convergence test that read `extra=` attributes
    off `hmc_mcp.operations_lpar` — exactly what convergence removes."""
    hmc = type("StubHMC", (), {"config": _config()})()
    read = AsyncMock()
    with (
        patch("hmc_mcp.operations_lpar.get_lpar_description", new=read),
        caplog.at_level(logging.WARNING),
    ):
        asyncio.run(
            authorize_lpar_mutation(hmc, "sys1", "lpar1", ownership_override=True)
        )
    read.assert_not_awaited()

    records = _override_records(caplog)
    assert len(records) == 1, "an absence assertion over an empty capture proves nothing"
    assert records[0] == {
        "time": records[0]["time"],
        "event": "ownership-override",
        "system": "sys1",
        "lpar": "lpar1",
        "host": "hmc.test",
        "attribution": {
            "claim": "hmc-mcp",
            "source": "config:agent_id",
            "verified": False,
        },
    }
    assert [r for r in caplog.records if r.name == "hmc_mcp.operations_lpar"] == []


def test_the_override_record_host_comes_from_the_client_config(caplog):
    """#271. `host` is the emitter's own ``HMCConfig.host``, and an unset one —
    the config default — renders as the empty string that it is."""
    hmc = type(
        "StubHMC",
        (),
        {"config": _config().model_copy(update={"host": "", "agent_id": None})},
    )()
    with (
        patch("hmc_mcp.operations_lpar.get_lpar_description", new=AsyncMock()),
        caplog.at_level(logging.WARNING),
    ):
        asyncio.run(
            authorize_lpar_mutation(hmc, "sys1", "lpar1", ownership_override=True)
        )
    records = _override_records(caplog)
    assert len(records) == 1, "an absence assertion over an empty capture proves nothing"
    assert records[0]["host"] == ""


def test_the_override_record_is_bounded_and_escaped(caplog):
    """Spec 26b. The same bound and the same escaping as the other record."""
    hmc = type("StubHMC", (), {"config": _config()})()
    hostile = "A" * 500
    with (
        patch("hmc_mcp.operations_lpar.get_lpar_description", new=AsyncMock()),
        caplog.at_level(logging.WARNING),
    ):
        asyncio.run(
            authorize_lpar_mutation(
                hmc, hostile, "x\ny‮z", ownership_override=True
            )
        )
    raw = [
        r.getMessage() for r in caplog.records if r.name == audit.AUDIT_LOGGER_NAME
    ]
    assert len(raw) == 1
    assert raw[0].isascii() and "\n" not in raw[0]
    record = json.loads(raw[0])
    assert len(record["system"]) == audit.MAX_VALUE_LENGTH


def test_operations_lpar_does_not_resolve_the_audit_logger():
    """Spec 26c. `audit` is the only module that names the reserved logger."""
    source = Path(operations_lpar.__file__).read_text()
    assert audit.AUDIT_LOGGER_NAME not in source


def test_the_override_still_reaches_stderr_without_a_sink(caplog, capsys):
    """Spec 26d. A CLI user, whose process never called `install_audit_sink`.

    The mechanism is `logging.lastResort`, which `callHandlers` consults only
    after an ancestor walk finds zero handlers — and pytest keeps one on the
    root, so the root handlers must be cleared for this to mean anything.
    Written naively it passes for an unrelated reason.
    """
    hmc = type("StubHMC", (), {"config": _config()})()
    saved = list(logging.root.handlers)
    logging.root.handlers.clear()
    try:
        with patch("hmc_mcp.operations_lpar.get_lpar_description", new=AsyncMock()):
            asyncio.run(
                authorize_lpar_mutation(hmc, "sys1", "lpar1", ownership_override=True)
            )
        captured = capsys.readouterr()
    finally:
        logging.root.handlers[:] = saved
    assert captured.out == ""
    assert json.loads(captured.err.strip())["event"] == "ownership-override"


def test_both_override_call_sites_emit_and_normal_access_does_not(caplog):
    """Spec 26e."""
    hmc = type("StubHMC", (), {"config": _config()})()
    # The decommission path reads a description before authorizing it, so the
    # stub must return a real one; the mutation path never awaits it at all.
    read = AsyncMock(return_value="legacy partition")
    with (
        patch("hmc_mcp.operations_lpar.get_lpar_description", new=read),
        caplog.at_level(logging.WARNING),
    ):
        asyncio.run(
            authorize_lpar_mutation(hmc, "sys1", "lpar1", ownership_override=True)
        )
        asyncio.run(
            authorize_decommission_lpar_ownership_snapshot(
                hmc, "sys2", "lpar2", ownership_override=True
            )
        )
    assert [record["system"] for record in _override_records(caplog)] == [
        "sys1",
        "sys2",
    ]


def test_authorize_lpar_mutation_normal_access_has_no_override_audit(caplog):
    hmc = type("StubHMC", (), {"config": _config()})()
    with (
        patch(
            "hmc_mcp.operations_lpar.get_lpar_description",
            new=AsyncMock(return_value="legacy partition"),
        ),
        caplog.at_level(logging.WARNING),
    ):
        asyncio.run(authorize_lpar_mutation(hmc, "sys1", "lpar1"))

    assert _override_records(caplog) == []


from hmc_mcp.ssh_commands import validate_caller_token  # noqa: E402


def test_validate_caller_token_accepts_tracker_ids():
    validate_caller_token("CHG12345")          # ticket key
    validate_caller_token("2026/08/batch-7")   # slashes, digits
    validate_caller_token("owner@team:42")     # colon round-trips (spec guarantee 6)
    validate_caller_token("a" * 64)            # length boundary


@pytest.mark.parametrize(
    "bad",
    [
        "",               # empty string is a violation, not an omission
        "a" * 65,         # too long
        "a,b",            # comma: -i record delimiter
        "a=b",            # equals: -i record delimiter
        'a"b',            # double quote: -i record escape
        "a[b",            # bracket: breaks the [caller ...] framing
        "a]b",
        "a\\b",           # backslash: unverified -i behaviour (ADR 0045)
        "a b",            # whitespace
        "alicé",          # non-ASCII
        "a\nb",           # control character
    ],
)
def test_validate_caller_token_rejects(bad):
    with pytest.raises(ValueError, match="caller_token"):
        validate_caller_token(bad)


def test_validate_caller_token_rejects_non_string():
    with pytest.raises(ValueError, match="string"):
        validate_caller_token(42)  # type: ignore[arg-type]


def test_stamp_composes_caller_segment():
    config = _config()
    today = datetime.date.today().isoformat()
    with patch(
        "hmc_mcp.ssh_commands.set_lpar_description", new=AsyncMock(return_value="")
    ) as mock_set:
        token = asyncio.run(
            stamp_lpar_ownership(
                config, "sys1", "lpar1", agent_id="alice", caller_token="CHG-1"
            )
        )
    assert token == f"[hmc-mcp owner:alice created:{today}] [caller CHG-1]"
    assert mock_set.call_args.args[3] == token
    # still a valid HMC description
    from hmc_mcp.ssh_commands import validate_lpar_description

    validate_lpar_description(token)


def test_stamp_without_caller_token_unchanged():
    config = _config()
    today = datetime.date.today().isoformat()
    with patch(
        "hmc_mcp.ssh_commands.set_lpar_description", new=AsyncMock(return_value="")
    ):
        token = asyncio.run(stamp_lpar_ownership(config, "sys1", "lpar1"))
    assert token == f"[hmc-mcp owner:hmc-mcp created:{today}]"


@pytest.mark.parametrize("character", ['"', "\\"])
def test_agent_id_breaking_stamp_grammar_rejected_at_construction(character):
    """An agent_id that would break the stamp's grammar never configures.

    validate_agent_id forbids '"' and '\\' (ADR 0065), mirroring
    validate_caller_token: HMCConfig construction raises ValueError instead
    of letting every ADR 0011 ownership stamp silently degrade to None in
    stamp_lpar_ownership's best-effort catch.
    """
    agent_id = f"agent{character}x"
    with pytest.raises(ValueError):
        validate_agent_id(agent_id)
    with pytest.raises(ValueError):
        HMCConfig(
            host="hmc.test", user="u", password="p", agent_id=agent_id,
            _env_file=None,
        )


def test_stamp_bad_caller_token_raises_unswallowed():
    config = _config()
    with patch(
        "hmc_mcp.ssh_commands.set_lpar_description", new=AsyncMock(return_value="")
    ) as mock_set:
        with pytest.raises(ValueError, match="caller_token"):
            asyncio.run(
                stamp_lpar_ownership(
                    config, "sys1", "lpar1", caller_token=""
                )
            )
    mock_set.assert_not_awaited()  # rejected before any SSH traffic


from hmc_mcp.operations_lpar import parse_lpar_ownership_caller_token  # noqa: E402


def test_parse_caller_token_round_trip():
    description = (
        "[hmc-mcp owner:alice created:2026-08-21] [caller JIRA-1:x/y]"
    )
    assert parse_lpar_ownership_caller_token(description) == "JIRA-1:x/y"


def test_parse_caller_token_absent():
    assert parse_lpar_ownership_caller_token("[hmc-mcp owner:a created:2026-08-21]") is None
    assert parse_lpar_ownership_caller_token("plain legacy description") is None


@pytest.mark.parametrize(
    "description",
    [
        "[caller JIRA-1] [hmc-mcp owner:a created:2026-08-21]",   # misordered
        "[hmc-mcp owner:a created:2026-08-21] [caller X] [caller Y]",  # duplicated
        "[hmc-mcp owner:a created:2026-08-21][caller X]",         # missing space
        "[hmc-mcp owner:a created:2026-08-21] [caller ]",         # empty segment
        "[hmc-mcp owner:bogus created:x] [caller X]",             # malformed anchor
        "[hmc-mcp owner:a created:2026-08-21] [caller]",          # bare bracket, no space
        "[hmc-mcp owner:a created:2026-08-21] [Caller X]",        # lowercased prefix mismatch
    ],
)
def test_parse_caller_token_spoofed_yields_none(description):
    assert parse_lpar_ownership_caller_token(description) is None


def test_owner_parse_unaffected_by_caller_segment():
    """ADR 0011 ownership parse keeps working on combined descriptions (spec g5)."""
    from hmc_mcp.operations_lpar import parse_lpar_ownership_owner

    description = "[hmc-mcp owner:alice created:2026-08-21] [caller JIRA-1]"
    assert parse_lpar_ownership_owner(description) == "alice"
