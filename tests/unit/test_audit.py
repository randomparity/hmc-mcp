"""The audit record's rendering, its bounds, and its sink.

Covers docs/workflow/specs/2026-08-19-authorization-audit-events-design.md; the
decision record is docs/adr/0040-authorization-audit-events.md.

Logging isolation comes from the autouse ``isolate_audit_logging`` fixture in
``tests/conftest.py``. Nothing here may install a sink without it.

Spec test -> node id:
  1  test_a_permitted_record_carries_every_field_in_order
  2  test_output_is_one_ascii_line_whatever_the_caller_sends
  3  test_a_long_selector_value_is_truncated_to_the_bound
  4  test_a_long_agent_id_is_truncated_to_the_bound
  5  test_a_non_string_connection_token_is_never_rendered
  6  test_targets_and_resolved_are_null_when_nothing_was_resolved
  6b test_a_profile_named_unresolved_is_indistinguishable_from_the_sentinel
  7  test_attribution_is_unverified_and_sourced_when_the_env_is_unset
  8  test_reasons_matches_the_literal
  8a test_only_audit_resolves_the_audit_logger
  9  test_a_record_reaches_stderr_and_not_stdout
  10 test_the_sink_does_not_propagate_to_an_ancestor_handler
  11 test_a_none_stderr_writes_nothing_and_raises_nothing
  12 test_a_closed_stream_is_survived
  13 test_a_broken_pipe_is_survived
  14 test_install_is_idempotent_and_defers_to_what_the_operator_set
  14a test_a_preattached_stdout_handler_is_deferred_to
  14b test_the_singletons_render_as_states_rather_than_raising
  15 test_the_line_equals_the_message_and_records_do_not_share_a_line
"""

from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path

import pytest

from hmc_mcp import audit

SENTINEL = "SENTINEL-DO-NOT-LOG-9c1f"


def _capture() -> list[str]:
    """Attach a list-collecting handler and return the list it fills."""
    lines: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(record.getMessage())

    logger = logging.getLogger(audit.AUDIT_LOGGER_NAME)
    logger.addHandler(_Collect())
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return lines


def _one(lines: list[str]) -> dict:
    assert len(lines) == 1, f"expected exactly one record, got {len(lines)}"
    return json.loads(lines[0])


def _authorization(**overrides) -> dict:
    """Emit one authorization record with sane defaults and return it parsed."""
    lines = _capture()
    kwargs = {
        "policy": "lab-only",
        "tool": "hmc_power_off_lpar",
        "effect": "destructive",
        "decision": "allow",
        "reason": "permitted",
        "token": "lab",
        "resolved": audit.resolved_connection("lab"),
        "targets": (
            audit.AuditTarget(
                kind="lpar", argument="lpar_name_or_uuid", state="present", value="db-01"
            ),
        ),
    }
    kwargs.update(overrides)
    audit.record_authorization(**kwargs)
    return _one(lines)


def test_a_permitted_record_carries_every_field_in_order():
    """Spec 1."""
    lines = _capture()
    audit.record_authorization(
        policy="lab-only",
        tool="hmc_power_off_lpar",
        effect="destructive",
        decision="allow",
        reason="permitted",
        token="lab",
        resolved=audit.resolved_connection("lab"),
        targets=(
            audit.AuditTarget(
                kind="lpar", argument="lpar_name_or_uuid", state="present", value="db-01"
            ),
        ),
    )
    record = _one(lines)
    assert list(record) == [
        "time", "event", "policy", "tool", "effect", "decision", "reason",
        "connection", "targets", "attribution",
    ]
    assert record["event"] == "authorization"
    assert record["policy"] == "lab-only"
    assert record["tool"] == "hmc_power_off_lpar"
    assert record["effect"] == "destructive"
    assert record["decision"] == "allow"
    assert record["reason"] == "permitted"
    assert record["connection"] == {
        "state": "present", "selector": "lab", "resolved": "lab"
    }
    assert record["targets"] == [
        {
            "kind": "lpar",
            "argument": "lpar_name_or_uuid",
            "state": "present",
            "value": "db-01",
        }
    ]
    assert record["time"].endswith("+00:00")


def test_output_is_one_ascii_line_whatever_the_caller_sends():
    """Spec 2. A caller cannot forge a line, move a cursor, or reorder one."""
    hostile = "a\nb\rc\td\x1be‮f g"
    lines = _capture()
    audit.record_authorization(
        policy="p", tool="t", effect="mutate", decision="deny",
        reason="target-not-granted", token=hostile,
        resolved=audit.resolved_connection(""),
        targets=(
            audit.AuditTarget(
                kind="lpar", argument="lpar_name_or_uuid", state="present", value=hostile
            ),
        ),
    )
    assert len(lines) == 1
    line = lines[0]
    assert "\n" not in line and "\r" not in line
    assert line.isascii(), "ensure_ascii=True must leave no non-ASCII byte"
    assert "‮" not in line and " " not in line
    record = json.loads(line)
    assert record["connection"]["selector"] == hostile


def test_a_long_selector_value_is_truncated_to_the_bound():
    """Spec 3. Both the connection token and a target value are bounded."""
    long = "A" * 500
    record = _authorization(
        token=long,
        targets=(
            audit.AuditTarget(
                kind="lpar", argument="lpar_name_or_uuid", state="present", value=long
            ),
        ),
    )
    assert len(record["connection"]["selector"]) == audit.MAX_VALUE_LENGTH == 128
    assert len(record["targets"][0]["value"]) == audit.MAX_VALUE_LENGTH
    assert not record["targets"][0]["value"].endswith("…"), "no truncation marker"


def test_a_long_agent_id_is_truncated_to_the_bound(monkeypatch):
    """Spec 4. The raw environment value, unvalidated but bounded."""
    monkeypatch.setenv(audit.ATTRIBUTION_ENV, "B" * 500)
    record = _authorization()
    assert len(record["attribution"]["claim"]) == audit.MAX_VALUE_LENGTH


def test_a_non_string_connection_token_is_never_rendered():
    """Spec 5. An arbitrary object's repr is not the caller's token."""

    class Hostile:
        def __repr__(self) -> str:  # pragma: no cover - must never be called
            return SENTINEL

    record = _authorization(token=Hostile())
    assert record["connection"]["state"] == "unreadable"
    assert record["connection"]["selector"] is None
    assert SENTINEL not in json.dumps(record)


def test_targets_and_resolved_are_null_when_nothing_was_resolved():
    """Spec 6. `null` means not evaluated; `[]` means the tool declares none."""
    unreadable = _authorization(
        reason="configuration-unreadable", decision="deny", resolved=None, targets=None
    )
    assert unreadable["targets"] is None
    assert unreadable["connection"]["resolved"] is None

    selectorless = _authorization(targets=())
    assert selectorless["targets"] == []

    for empty in (None, ""):
        assert _authorization(
            token=empty, resolved=audit.resolved_connection(None)
        )["connection"] == {
            "state": "absent", "selector": None, "resolved": "<default>"
        }, f"an absent token must render a null selector, got {empty!r}"
    assert (
        _authorization(token="nope", resolved=audit.resolved_connection(""))[
            "connection"
        ]["resolved"]
        == "<unresolved>"
    )


def test_resolved_connection_is_bound_to_the_sentinel_that_owns_it():
    """`audit` restates `""` as a literal; `connection_scope` owns the value.

    `audit.py` may import nothing from the package, so the coupling cannot be an
    import — but this test may, and without it a change to `UNRESOLVED` would
    silently make every unresolved token render as a profile key instead. The
    target half avoids the same drift by putting `audit_state` in `target_scope`;
    this is the connection half's equivalent, paid for in a test rather than a
    dependency.
    """
    from hmc_mcp import connection_scope

    assert audit.resolved_connection(connection_scope.UNRESOLVED) == (
        audit.UNRESOLVED_RENDERING
    )
    assert audit.resolved_connection(None) == audit.DEFAULT_RENDERING
    assert audit.resolved_connection("lab") == "lab"


def test_a_profile_named_unresolved_is_indistinguishable_from_the_sentinel():
    """Spec 6b. A reserved rendering shares a string space with legal keys."""
    named = audit.resolved_connection(audit.UNRESOLVED_RENDERING)
    nothing = audit.resolved_connection("")
    assert named == nothing == audit.UNRESOLVED_RENDERING


def test_attribution_is_unverified_and_sourced_when_the_env_is_unset(monkeypatch):
    """Spec 7."""
    monkeypatch.delenv(audit.ATTRIBUTION_ENV, raising=False)
    record = _authorization()
    assert record["attribution"] == {
        "claim": None,
        "source": "environment:HMC_AGENT_ID",
        "verified": False,
    }


def test_reasons_matches_the_literal():
    """Spec 8. The vocabulary is closed."""
    from typing import get_args

    assert audit.REASONS == frozenset(get_args(audit.Reason))
    assert audit.REASONS == {
        "permitted",
        "configuration-unreadable",
        "connection-not-granted",
        "target-selector-unreadable",
        "target-unboundable",
        "target-selector-absent",
        "target-not-granted",
    }


def test_only_audit_resolves_the_audit_logger():
    """Spec 8a. The logger is reserved, and `audit` imports nothing from us."""
    package = Path(audit.__file__).parent
    offenders = [
        path.name
        for path in package.glob("*.py")
        if path.name != "audit.py" and audit.AUDIT_LOGGER_NAME in path.read_text()
    ]
    assert offenders == [], f"{offenders} name the reserved audit logger"

    source = Path(audit.__file__).read_text()
    assert "from ." not in source and "from hmc_mcp" not in source, (
        "audit.py must import nothing from the package: target_scope imports "
        "Reason from it, so any import back is a cycle"
    )


def test_a_record_reaches_stderr_and_not_stdout(capsys):
    """Spec 9."""
    audit.install_audit_sink()
    audit.record_ownership_override(system="sys-a", lpar="db-01", agent_id="agent-7")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err.strip())["event"] == "ownership-override"


def test_the_sink_does_not_propagate_to_an_ancestor_handler(capsys):
    """Spec 10. The in-process route to a root stdout handler is closed."""
    root_lines: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            root_lines.append(record.getMessage())

    collector = _Collect()
    logging.root.addHandler(collector)
    try:
        audit.install_audit_sink()
        assert logging.getLogger(audit.AUDIT_LOGGER_NAME).propagate is False
        audit.record_ownership_override(system="s", lpar="l", agent_id="a")
        capsys.readouterr()
        assert root_lines == [], "a root handler must not receive audit records"
    finally:
        logging.root.removeHandler(collector)


def test_a_none_stderr_writes_nothing_and_raises_nothing(monkeypatch, capsys):
    """Spec 11. CPython sets sys.stderr to None when fd 2 is closed at start."""
    audit.install_audit_sink()
    monkeypatch.setattr(sys, "stderr", None)
    audit.record_ownership_override(system="s", lpar="l", agent_id="a")


@pytest.mark.parametrize(
    "error", [ValueError("I/O operation on closed file"), OSError("broken pipe")]
)
def test_a_broken_or_closed_stream_is_survived(monkeypatch, error):
    """Spec 12 and 13. The two guards `server._warn` already applies."""

    class _Hostile(io.StringIO):
        def write(self, _data: str) -> int:
            raise error

    audit.install_audit_sink()
    monkeypatch.setattr(sys, "stderr", _Hostile())
    audit.record_ownership_override(system="s", lpar="l", agent_id="a")


def test_a_foreign_writers_bad_record_does_not_raise_into_them(capsys):
    """A stdlib handler never raises into its caller, and this is an attachment
    point the operator documentation invites others to use."""
    audit.install_audit_sink()
    logger = logging.getLogger(audit.AUDIT_LOGGER_NAME)

    class Hostile:
        def __str__(self) -> str:
            raise RuntimeError("this record cannot be rendered")

    # Raised while the handler renders the message, not while audit builds it, so
    # audit._emit's guard is not what saves the caller here.
    logger.warning("%s", Hostile())
    capsys.readouterr()


def test_install_is_idempotent_and_defers_to_what_the_operator_set():
    """Spec 14. Configured wins; unconfigured gets a default."""
    logger = logging.getLogger(audit.AUDIT_LOGGER_NAME)
    audit.install_audit_sink()
    audit.install_audit_sink()
    assert len(logger.handlers) == 1
    assert logger.level == logging.INFO

    logger.handlers.clear()
    logger.setLevel(logging.WARNING)
    audit.install_audit_sink()
    assert logger.level == logging.WARNING, "an operator's level must survive"


def test_a_preattached_stdout_handler_is_deferred_to(capsys):
    """Spec 14a. The deferral is a chosen behaviour, not an accident."""
    logger = logging.getLogger(audit.AUDIT_LOGGER_NAME)
    logger.addHandler(logging.StreamHandler(sys.stdout))
    audit.install_audit_sink()
    assert len(logger.handlers) == 1, "install must not add a second handler"
    logger.setLevel(logging.INFO)
    audit.record_ownership_override(system="s", lpar="l", agent_id="a")
    assert json.loads(capsys.readouterr().out.strip())["event"] == "ownership-override"


def test_the_singletons_render_as_states_rather_than_raising():
    """Spec 14b. Asserted on record content: the guard swallows a raising build,
    so "did not raise" is true of correct and M7-mutated code alike."""
    record = _authorization(
        decision="deny",
        reason="target-selector-absent",
        targets=(
            audit.AuditTarget(
                kind="lpar", argument="lpar_name_or_uuid", state="absent", value=None
            ),
            audit.AuditTarget(
                kind="vios", argument="vios_partition_id", state="unreadable", value=None
            ),
        ),
    )
    assert record["targets"] == [
        {
            "kind": "lpar",
            "argument": "lpar_name_or_uuid",
            "state": "absent",
            "value": None,
        },
        {
            "kind": "vios",
            "argument": "vios_partition_id",
            "state": "unreadable",
            "value": None,
        },
    ]


def test_the_line_equals_the_message_and_records_do_not_share_a_line(capsys):
    """Spec 15, and the StreamHandler.terminator premise it rests on.

    A custom ``logging.Handler`` inherits no ``terminator``, so the newline is
    written explicitly — and in the same ``write`` call, since nothing serialises
    this handler against FastMCP's traceback panel on the same stream.
    """
    assert not hasattr(logging.Handler, "terminator")
    assert logging.StreamHandler.terminator == "\n"

    audit.install_audit_sink()
    audit.record_ownership_override(system="one", lpar="l", agent_id="a")
    audit.record_ownership_override(system="two", lpar="l", agent_id="a")
    err = capsys.readouterr().err
    lines = err.splitlines()
    assert len(lines) == 2, "two records must not share a physical line"
    assert [json.loads(line)["system"] for line in lines] == ["one", "two"]

    handler = logging.getLogger(audit.AUDIT_LOGGER_NAME).handlers[0]
    assert handler.formatter is None, "no Formatter may wrap the record"


def test_the_handler_issues_one_write_per_record(monkeypatch):
    """Spec 15, second half. One call, so nothing can land between a record and
    its newline."""
    writes: list[str] = []

    class _Counting(io.StringIO):
        def write(self, data: str) -> int:
            writes.append(data)
            return len(data)

    audit.install_audit_sink()
    monkeypatch.setattr(sys, "stderr", _Counting())
    audit.record_ownership_override(system="s", lpar="l", agent_id="a")
    assert len(writes) == 1
    assert writes[0].endswith("\n")
