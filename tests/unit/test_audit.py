"""The audit record's rendering, its bounds, and its sink.

Covers docs/workflow/specs/2026-08-19-authorization-audit-events-design.md; the
decision record is docs/adr/0040-authorization-audit-events.md.

Logging isolation comes from the autouse ``isolate_audit_logging`` fixture in
``tests/conftest.py``. Nothing here may install a sink without it.

Spec test -> node id. This map is checked by
``test_every_spec_numbered_test_named_in_the_header_still_exists`` — keep it true.

  1   test_a_permitted_record_carries_every_field_in_order
  2   test_output_is_one_ascii_line_whatever_the_caller_sends
  3   test_a_long_selector_value_is_truncated_to_the_bound
  4   test_a_long_agent_id_is_truncated_to_the_bound
  5   test_a_non_string_connection_token_is_never_rendered
  6   test_targets_and_resolved_are_null_when_nothing_was_resolved
  6b  test_a_profile_named_unresolved_is_indistinguishable_from_the_sentinel
  7   test_attribution_is_unverified_and_sourced_when_the_env_is_unset
  8   test_reasons_matches_the_literal
  8a  test_only_audit_resolves_the_audit_logger
  9   test_a_record_reaches_stderr_and_not_stdout
  10  test_the_sink_does_not_propagate_to_an_ancestor_handler
  11  test_a_none_stderr_writes_nothing_and_raises_nothing
  12,13 test_a_broken_or_closed_stream_is_survived  (parametrized over both)
  14  test_install_is_idempotent_and_defers_to_what_the_operator_set
  14a test_a_preattached_stdout_handler_is_deferred_to
  14b test_the_singletons_render_as_states_rather_than_raising
  15  test_the_line_equals_the_message_and_records_do_not_share_a_line
  15  test_the_handler_issues_one_write_per_record

Not spec-numbered, each pinning something a review round found:

  test_resolved_connection_is_bound_to_the_sentinel_that_owns_it
  test_events_matches_the_literal_and_both_emitters_use_it
  test_the_module_closes_propagation_at_import          (#272, fresh interpreter)
  test_an_unconfigured_logger_still_reaches_last_resort (#272's other half)
  test_a_foreign_writers_bad_record_does_not_raise_into_them
"""

from __future__ import annotations

import io
import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import get_args

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


def test_every_spec_numbered_test_named_in_the_header_still_exists():
    """The header maps spec numbers to node ids; this makes that map load-bearing.

    Added because it was not. A slice replacement in `22ee201` — rewriting the
    span between two functions — silently swallowed three tests that sat between
    them, including the only pin on #272's import-time ``propagate = False``. The
    suite stayed green at 2011 passed, so deleting a security fix reddened
    nothing, and the loss reached a PR before the orchestrator caught it by
    mutation.

    A deletion is invisible to every other test by construction: nothing fails
    when an assertion simply stops existing. So the header is the inventory and
    this compares it against reality.
    """
    source = Path(__file__).read_text()
    header = source.split('"""')[1]
    named = set(re.findall(r"\b(test_[a-z_0-9]+)", header))
    defined = set(re.findall(r"^def (test_[a-z_0-9]+)", source, re.M))
    assert named, "the header must map spec numbers to node ids"
    missing = named - defined
    assert not missing, f"named in the header but no longer defined: {sorted(missing)}"


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


def test_events_matches_the_literal_and_both_emitters_use_it():
    """The `event` vocabulary is two values, and a checker can see that."""
    assert audit.EVENTS == frozenset(get_args(audit.Event))
    assert audit.EVENTS == {"authorization", "ownership-override"}

    lines = _capture()
    audit.record_ownership_override(system="s", lpar="l", agent_id="a")
    emitted = {json.loads(lines[0])["event"]}
    emitted.add(_authorization()["event"])
    assert emitted == audit.EVENTS, "every declared event must be reachable"


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


def _emit_directly(message: str = '{"event":"probe"}') -> None:
    """Drive ``_AuditHandler.emit`` itself, outside ``audit._emit``'s guard.

    Going through ``record_ownership_override`` cannot test the handler's own
    guards: ``_emit`` wraps the whole logging dispatch in ``except Exception``,
    so deleting all three #221 guards leaves those tests green. Verified by
    doing exactly that. Calling ``emit`` directly is what makes them bite.
    """
    audit._AuditHandler().emit(
        logging.LogRecord("t", logging.WARNING, __file__, 0, message, None, None)
    )


def test_a_none_stderr_writes_nothing_and_raises_nothing(monkeypatch):
    """Spec 11. CPython sets sys.stderr to None when fd 2 is closed at start."""
    monkeypatch.setattr(sys, "stderr", None)
    _emit_directly()  # an unguarded emit raises AttributeError here

    # And end to end, where the record also has to survive the level filter.
    monkeypatch.undo()
    written: list[str] = []

    class _Watch(io.StringIO):
        def write(self, data: str) -> int:
            written.append(data)
            return len(data)

    watcher = _Watch()
    monkeypatch.setattr(sys, "stderr", watcher)
    audit.install_audit_sink()
    monkeypatch.setattr(sys, "stderr", None)
    audit.record_ownership_override(system="s", lpar="l", agent_id="a")
    assert written == [], "an absent stream must not be written to at all"


@pytest.mark.parametrize(
    "error", [ValueError("I/O operation on closed file"), OSError("broken pipe")]
)
def test_a_broken_or_closed_stream_is_survived(monkeypatch, error):
    """Spec 12 and 13. The two guards `server._warn` already applies.

    Asserted through the handler directly *and* end to end: an unguarded emit
    raises out of the first, and the second proves the integrated path also
    survives rather than only the unit.
    """
    handled: list[logging.LogRecord] = []

    class _Hostile(io.StringIO):
        def write(self, _data: str) -> int:
            raise error

    monkeypatch.setattr(sys, "stderr", _Hostile())
    monkeypatch.setattr(
        logging.Handler, "handleError", lambda self, record: handled.append(record)
    )
    _emit_directly()
    assert handled == [], (
        f"{type(error).__name__} must be caught by name, not fall through to "
        "the stdlib error path"
    )

    audit.install_audit_sink()
    audit.record_ownership_override(system="s", lpar="l", agent_id="a")


def test_the_module_closes_propagation_at_import(tmp_path):
    """#272. Asserted in a *fresh interpreter*, because the autouse isolation
    fixture resets `propagate` to True for every test in this session — so an
    in-process assertion would read the fixture's value, not the shipped one."""
    probe = (
        "import logging, hmc_mcp.audit as a; "
        "print(logging.getLogger(a.AUDIT_LOGGER_NAME).propagate)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False", (
        "importing hmc_mcp.audit must close the route to an ancestor handler, "
        "which under stdio may be pointed at the JSON-RPC stream"
    )

def test_an_unconfigured_logger_still_reaches_last_resort(capsys):
    """The other half of #272's fix: closing propagation must not cost a CLI user
    the record. `callHandlers` consults `lastResort` when the walk finds zero
    handlers, which `propagate` does not affect."""
    logger = logging.getLogger(audit.AUDIT_LOGGER_NAME)
    logger.handlers.clear()
    logger.propagate = False
    saved = list(logging.root.handlers)
    logging.root.handlers.clear()
    try:
        audit.record_ownership_override(system="s", lpar="l", agent_id="a")
        captured = capsys.readouterr()
    finally:
        logging.root.handlers[:] = saved
    assert captured.out == ""
    assert json.loads(captured.err.strip())["event"] == "ownership-override"

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
