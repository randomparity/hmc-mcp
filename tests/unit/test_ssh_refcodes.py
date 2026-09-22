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


@pytest.mark.parametrize("hostile", ["web01,web02", "web01=x", 'web01"x', "web01\nx"])
def test_selector_record_delimiters_are_refused(monkeypatch, hostile):
    """A value that would rewrite the ``--filter`` record never gets sent."""
    transport = _transport(monkeypatch)

    with pytest.raises(HMCCLIError):
        _run(list_lpar_refcodes(_config(), "sys1", hostile))

    transport.assert_not_awaited()


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


def test_transport_errors_are_not_swallowed(monkeypatch):
    """The HMC's refusal of a command surfaces to the caller unchanged."""
    _transport(monkeypatch, side_effect=HMCCLIError("HSCL0000 refused"))

    with pytest.raises(HMCCLIError, match="HSCL0000 refused"):
        _run(list_lpar_refcodes(_config(), "sys1", "web01"))
