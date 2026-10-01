"""The default ``name=value`` record parser against captured HMC output (#1202)."""

from __future__ import annotations

import pytest
from conftest import live_fixture

from hmcpctl.ssh.commands import _parse_lshwres_output
from hmcpctl.ssh.transport import HMCCLIError


def test_quoted_list_pair_is_one_attribute():
    """A list value prints as one quoted pair; its commas stay inside the value."""
    (row,) = _parse_lshwres_output(live_fixture("cli-sys-attrs")["stdout"])

    assert row["lpar_proc_compat_modes"] == "default,POWER7,POWER8,POWER9,POWER9_base"
    assert row["curr_sys_keylock"] == "manual"
    assert row["description"] == ""
    assert not [key for key in row if '"' in key]


def test_empty_result_sentinel_is_no_rows():
    """An empty read exits 0 and prints the sentinel, which is not a row."""
    assert _parse_lshwres_output(live_fixture("cli-mempool-empty")["stdout"]) == []


def test_unbalanced_quote_is_refused():
    """A quoted pair that never closes is malformed, not a value to guess at."""
    with pytest.raises(HMCCLIError, match="malformed HMC name=value record"):
        _parse_lshwres_output('name=a,"curr_lpar_names=x,y\n')
