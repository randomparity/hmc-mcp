"""Contract tests for the bounded LPAR reference-code read (issue #874).

Design: docs/workflow/specs/2026-09-21-bounded-lpar-refcode-read-design.md
"""

import asyncio
import shlex
from unittest.mock import AsyncMock

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.ssh.refcodes import (
    MAX_REFCODE_COUNT,
    REFCODE_FIELDS,
    list_lpar_refcodes,
)
from hmc_mcp.ssh.transport import HMCCLIError


def _run(coroutine):
    return asyncio.run(coroutine)


def _config():
    return HMCConfig.from_mapping({"host": "h", "user": "u", "password": "p"})


def _transport(monkeypatch, stdout="", side_effect=None):
    """Patch the module's transport and return the recording mock."""
    mock = AsyncMock(return_value=stdout, side_effect=side_effect)
    monkeypatch.setattr("hmc_mcp.ssh.refcodes.run_hmc_command", mock)
    return mock


def _sent(mock):
    return mock.await_args.args[1]


@pytest.mark.parametrize("count", [0, -1, MAX_REFCODE_COUNT + 1])
def test_count_bound_is_enforced(monkeypatch, count):
    """Out-of-range counts are refused before any SSH traffic."""
    transport = _transport(monkeypatch)

    with pytest.raises(ValueError, match=f"between 1 and {MAX_REFCODE_COUNT}"):
        _run(list_lpar_refcodes(_config(), "sys1", "web01", count))

    transport.assert_not_awaited()


@pytest.mark.parametrize("count", ["5", 2.0, True, None])
def test_non_integer_count_is_refused(monkeypatch, count):
    """A non-``int`` count never reaches the interpolated command string."""
    transport = _transport(monkeypatch)

    with pytest.raises(TypeError, match="count must be an int"):
        _run(list_lpar_refcodes(_config(), "sys1", "web01", count))

    transport.assert_not_awaited()


def test_command_shape_is_exact(monkeypatch):
    """The composed command is the one the design fixed, value for value."""
    header = ",".join(REFCODE_FIELDS)
    transport = _transport(monkeypatch, f"{header}\nweb01,2026-09-21 10:00:00,C2001150\n")

    rows = _run(list_lpar_refcodes(_config(), "sys1", "web01", 5))

    assert _sent(transport) == (
        "lsrefcode -r lpar -m sys1 --filter lpar_names=web01"
        f" -n 5 -F {header} --header"
    )
    assert rows == [
        {"lpar_name": "web01", "time_stamp": "2026-09-21 10:00:00", "refcode": "C2001150"}
    ]


def test_count_defaults_to_the_hmc_default(monkeypatch):
    """The default is the HMC's own: the current reference code alone."""
    transport = _transport(monkeypatch, "No results were found.\n")

    _run(list_lpar_refcodes(_config(), "sys1", "web01"))

    assert " -n 1 " in _sent(transport)


def test_selector_metacharacters_stay_one_word(monkeypatch):
    """Shell metacharacters stay inside one quoted word for the remote shell."""
    transport = _transport(monkeypatch, "No results were found.\n")
    hostile = "web01; rm -rf / $(id) `id` | cat"

    _run(list_lpar_refcodes(_config(), "sys $(id)", hostile))

    sent = _sent(transport)
    assert shlex.split(sent) == [
        "lsrefcode",
        "-r",
        "lpar",
        "-m",
        "sys $(id)",
        "--filter",
        f"lpar_names={hostile}",
        "-n",
        "1",
        "-F",
        ",".join(REFCODE_FIELDS),
        "--header",
    ]


@pytest.mark.parametrize("hostile", ["web01,web02", "web01=x", 'web01"x'])
def test_selector_record_delimiters_are_refused(monkeypatch, hostile):
    """A value that would rewrite the ``--filter`` record never gets sent.

    A control character is refused earlier, by the name guard above, so it is
    covered there rather than here.
    """
    transport = _transport(monkeypatch)

    with pytest.raises(HMCCLIError):
        _run(list_lpar_refcodes(_config(), "sys1", hostile))

    transport.assert_not_awaited()


@pytest.mark.parametrize("field", ["system_name", "lpar_name"])
@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_selectors_are_refused(monkeypatch, field, blank):
    """A blank selector would compose a filter naming no partition."""
    transport = _transport(monkeypatch)
    names = {"system_name": "sys1", "lpar_name": "web01"}
    names[field] = blank

    with pytest.raises(ValueError, match=f"{field} must be non-empty"):
        _run(list_lpar_refcodes(_config(), names["system_name"], names["lpar_name"]))

    transport.assert_not_awaited()


@pytest.mark.parametrize("field", ["system_name", "lpar_name"])
def test_control_characters_in_a_selector_are_refused(monkeypatch, field):
    """system_name never reaches build_filter, so the name guard covers both."""
    transport = _transport(monkeypatch)
    names = {"system_name": "sys1", "lpar_name": "web01"}
    names[field] = "a\x00b"

    with pytest.raises(ValueError, match=f"{field} must be non-empty"):
        _run(list_lpar_refcodes(_config(), names["system_name"], names["lpar_name"]))

    transport.assert_not_awaited()


def test_rows_for_another_partition_are_refused(monkeypatch):
    """A --filter that failed to narrow must not be reported as the caller's."""
    header = ",".join(REFCODE_FIELDS)
    _transport(
        monkeypatch,
        f"{header}\nweb01,2026-09-21 10:00:00,C2001150\n"
        "db02,2026-09-21 10:00:01,C2001151\n",
    )

    with pytest.raises(HMCCLIError, match="reported partition 'db02'"):
        _run(list_lpar_refcodes(_config(), "sys1", "web01", 2))


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "   \n",
        "No results were found.\n",
        ",".join(REFCODE_FIELDS) + "\n",
    ],
)
def test_empty_results_are_an_empty_list(monkeypatch, stdout):
    """Blank stdout, the HMC's sentinel, and a bare header all mean no rows.

    The bare-header case is also the design's assumed response for a partition
    that does not exist, so this is the test that goes red if that assumption
    is ever replaced with a distinct error.
    """
    _transport(monkeypatch, stdout)

    assert _run(list_lpar_refcodes(_config(), "sys1", "nosuchlpar")) == []


def test_header_mismatch_raises_hmc_cli_error(monkeypatch):
    """A header naming other attributes fails closed with an actionable error."""
    _transport(monkeypatch, "lpar_name,refcode\nweb01,C2001150\n")

    with pytest.raises(HMCCLIError, match=",".join(REFCODE_FIELDS)):
        _run(list_lpar_refcodes(_config(), "sys1", "web01"))


def test_a_parse_failure_carries_its_cause(monkeypatch):
    """A short row reports the row and column counts, not just the field set."""
    header = ",".join(REFCODE_FIELDS)
    _transport(monkeypatch, f"{header}\nweb01,C2001150\n")

    with pytest.raises(HMCCLIError, match=r"row 2 has 2 columns; expected 3"):
        _run(list_lpar_refcodes(_config(), "sys1", "web01"))


def test_transport_errors_are_not_swallowed(monkeypatch):
    """The HMC's refusal of a command surfaces to the caller unchanged."""
    _transport(monkeypatch, side_effect=HMCCLIError("HSCL0000 refused"))

    with pytest.raises(HMCCLIError, match="HSCL0000 refused"):
        _run(list_lpar_refcodes(_config(), "sys1", "web01"))


def test_the_tool_forwards_resolved_names_and_the_count(monkeypatch):
    """The MCP tool body threads count and the resolved names, in order.

    `ssh_with_client` resolves a name-or-UUID to a CLI name before calling the
    tool's body, so this stands in for that seam and checks what the lambda
    hands on.
    """
    from hmc_mcp.server_tools.lpar import lifecycle_boot

    seen: dict[str, object] = {}

    def fake_ssh_with_client(fn, *, system_name_or_uuid, lpar_name_or_uuid, profile):
        seen["system_name_or_uuid"] = system_name_or_uuid
        seen["lpar_name_or_uuid"] = lpar_name_or_uuid
        seen["profile"] = profile
        return _run(fn(_config(), "resolved-sys", "resolved-lpar"))

    transport = _transport(monkeypatch, "No results were found.\n")
    monkeypatch.setattr(lifecycle_boot, "ssh_with_client", fake_ssh_with_client)

    rows = lifecycle_boot.hmc_read_lpar_refcodes(
        "22222222-2222-4222-8222-222222222222", "web01", 7, profile="lab"
    )

    assert rows == []
    assert seen == {
        "system_name_or_uuid": "22222222-2222-4222-8222-222222222222",
        "lpar_name_or_uuid": "web01",
        "profile": "lab",
    }
    assert transport.await_args.args[1] == (
        "lsrefcode -r lpar -m resolved-sys --filter lpar_names=resolved-lpar"
        " -n 7 -F lpar_name,time_stamp,refcode --header"
    )
