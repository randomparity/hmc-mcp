"""Tests for hmc_install_lpar_os: the HMC CLI installios bridge (ADR 0070).

The InstallLPAR REST job does not exist (ADR 0069); the tool now composes a
detached ``installios`` command and submits it over SSH.
"""

from __future__ import annotations

import httpx
import pytest
from unittest.mock import patch

from conftest import make_config

from hmc_mcp.errors import HMCError
from hmc_mcp.ssh_install import (
    INSTALLIOS_PID_PREFIX,
    build_installios_command,
    parse_installios_pid,
    validate_hmc_name,
    validate_install_source,
    validate_ipv4_address,
    validate_ipv4_subnet_mask,
    validate_mac_address,
    validate_vlan_id,
)

BASE = "https://hmc.test"
LPAR_UUID = "11111111-1111-4111-8111-111111111111"
SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"


def _lpar_feed(name: str, uuid: str = LPAR_UUID) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{uuid}</id>
    <title>LogicalPartition:{name}</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <PartitionName>{name}</PartitionName>
        <PartitionState>not activated</PartitionState>
      </LogicalPartition>
    </content>
  </entry>
</feed>"""


# ---------------------------------------------------------------------- #
# Unit: validators
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value", ["10.0.0.1", "255.255.255.255", "0.0.0.0", "192.168.1.30"]
)
def test_valid_ipv4_addresses_pass(value):
    assert validate_ipv4_address(value) == value


@pytest.mark.parametrize(
    "value", ["999.1.1.1", "10.0.0", "10.0.0.0.1", "a.b.c.d", "", "10.0.0.1 ", "-1.2.3.4"]
)
def test_invalid_ipv4_addresses_rejected(value):
    with pytest.raises(ValueError, match="IPv4"):
        validate_ipv4_address(value)


@pytest.mark.parametrize(
    "value",
    ["255.255.255.0", "255.255.0.0", "0.0.0.0", "255.255.255.255", "128.0.0.0"],
)
def test_valid_subnet_masks_pass(value):
    assert validate_ipv4_subnet_mask(value) == value


@pytest.mark.parametrize("value", ["255.0.255.0", "255.255.1.0", "256.0.0.0"])
def test_invalid_subnet_masks_rejected(value):
    with pytest.raises(ValueError):
        validate_ipv4_subnet_mask(value)


@pytest.mark.parametrize("value", ["0", "1", "4094", "100"])
def test_valid_vlan_ids_pass(value):
    assert validate_vlan_id(value) == value


@pytest.mark.parametrize("value", ["", "4095", "-1", "100.5", "abc", "0x10"])
def test_invalid_vlan_ids_rejected(value):
    with pytest.raises(ValueError):
        validate_vlan_id(value)


def test_valid_mac_address_passes():
    assert validate_mac_address("f2:d4:60:00:d0:03") == "f2:d4:60:00:d0:03"
    assert validate_mac_address("F2:D4:60:00:D0:03") == "F2:D4:60:00:D0:03"


@pytest.mark.parametrize(
    "value", ["", "f2-d4-60-00-d0-03", "f2:d4:60:00:d0", "f2:d4:60:00:d0:zz"]
)
def test_invalid_mac_addresses_rejected(value):
    with pytest.raises(ValueError):
        validate_mac_address(value)


@pytest.mark.parametrize(
    "value",
    [
        "/dev/cdrom",
        "/dev/sr0",
        "/extra/viosimages/VIOS_4.1/dvdimage.v1.iso",
        "/data/viosbackup/nim_resources.tar",
        "server1:/export/nim_resources.tar",
    ],
)
def test_valid_install_sources_pass(value):
    assert validate_install_source(value) == value


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "non-empty"),
        ("-u", "flag"),
        ("/extra/\x01img.iso", "control characters"),
        ("/no/host:/path", "hostname"),
    ],
)
def test_invalid_install_sources_rejected(value, message):
    with pytest.raises(ValueError, match=message):
        validate_install_source(value)


def test_invalid_hmc_names_rejected():
    with pytest.raises(ValueError):
        validate_hmc_name("", "partition_name")
    with pytest.raises(ValueError):
        validate_hmc_name("bad\x02name", "partition_name")


# ---------------------------------------------------------------------- #
# Unit: command composition — exact built command lines
# ---------------------------------------------------------------------- #


def test_build_installios_command_exact_line():
    command, log_path = build_installios_command(
        install_source="/extra/vios.iso",
        client_ip="192.168.1.20",
        subnet_mask="255.255.255.0",
        gateway="192.168.1.1",
        system_name="sys1",
        partition_name="aixprod",
        profile_name="default",
        vlan_id="100",
    )
    assert log_path == "/tmp/hmc-mcp-installios-aixprod.log"
    assert command == (
        "nohup installios -d /extra/vios.iso -i 192.168.1.20 "
        "-S 255.255.255.0 -g 192.168.1.1 -s sys1 -p aixprod "
        f"-r default -V 100 </dev/null >{log_path} 2>&1 "
        f"& echo {INSTALLIOS_PID_PREFIX}$!"
    )


def test_build_installios_command_quotes_and_includes_optional_mac():
    command, _ = build_installios_command(
        install_source="server1:/export/nim_resources.tar",
        client_ip="10.0.0.5",
        subnet_mask="255.255.252.0",
        gateway="10.0.0.1",
        system_name="my system",
        partition_name="vios 1",
        profile_name="default profile",
        mac_address="f2:d4:60:00:d0:03",
    )
    assert "-m f2:d4:60:00:d0:03" in command
    # Values with shell metacharacters arrive quoted; stdin is closed so any
    # interactive prompt fails fast instead of hanging the submission.
    assert "-s 'my system'" in command
    assert "-p 'vios 1'" in command
    assert "-r 'default profile'" in command
    assert "</dev/null" in command


def test_build_installios_command_rejects_bad_values_before_composition():
    with pytest.raises(ValueError):
        build_installios_command(
            install_source="/extra/vios.iso",
            client_ip="not-an-ip",
            subnet_mask="255.255.255.0",
            gateway="192.168.1.1",
            system_name="sys1",
            partition_name="aixprod",
            profile_name="default",
        )


def test_parse_installios_pid_extracts_echoed_pid():
    output = f"some banner\n{INSTALLIOS_PID_PREFIX}4242\n"
    assert parse_installios_pid(output) == 4242


def test_parse_installios_pid_without_tag_raises_hmccli_error():
    with pytest.raises(HMCError, match="no PID"):
        parse_installios_pid("installios: usage error\n")


# ---------------------------------------------------------------------- #
# Error path: installios failure surfaces as HMCCLIError
# ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_installios_ssh_failure_surfaces_as_cli_error():
    from hmc_mcp.ssh_install import run_installios
    from hmc_mcp.ssh import HMCCLIError

    config = make_config()

    async def fail(config, cmd):
        raise HMCCLIError(f"SSH command {cmd!r} failed with exit status 127")

    with patch("hmc_mcp.ssh_install.run_hmc_command", new=fail):
        with pytest.raises(HMCCLIError, match="exit status 127"):
            await run_installios(config, "nohup installios ... & echo pid=$!")


# ---------------------------------------------------------------------- #
# Tool-layer tests for hmc_install_lpar_os
# ---------------------------------------------------------------------- #


def _hmc_env(monkeypatch) -> None:
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


_INSTALL_KWARGS = {
    "install_source": "/extra/viosimages/VIOS_4.1/dvdimage.v1.iso",
    "lpar_ip": "192.168.1.30",
    "nim_subnetmask": "255.255.255.0",
    "nim_gateway": "192.168.1.1",
    "vlan_id": "100",
}


def test_install_lpar_os_tool_submits_detached_installios(monkeypatch, mock_hmc):
    """The tool resolves the target then runs the composed installios command."""
    from hmc_mcp.server import hmc_install_lpar_os

    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/ManagedSystem/search/(SystemName==sys1)").mock(
        return_value=httpx.Response(200, text=_system_feed("sys1"))
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(200, text=_system_feed("sys1"))
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(200, text=_lpar_feed("aixprod"))
    )

    submitted: dict[str, object] = {}

    async def fake_run_hmc_command(config, cmd):
        submitted["config"] = config
        submitted["cmd"] = cmd
        return f"{INSTALLIOS_PID_PREFIX}4242\n"

    with patch("hmc_mcp.ssh_install.run_hmc_command", new=fake_run_hmc_command):
        result = hmc_install_lpar_os("aixprod", "sys1", **_INSTALL_KWARGS)

    assert result["pid"] == 4242
    assert result["partition"] == "aixprod"
    assert result["system"] == "sys1"
    assert result["log_path"] == "/tmp/hmc-mcp-installios-aixprod.log"
    assert "no HMC job exists on this path" in result["message"]
    # The exact command that would have gone over SSH:
    expected, log_path = build_installios_command(
        install_source="/extra/viosimages/VIOS_4.1/dvdimage.v1.iso",
        client_ip="192.168.1.30",
        subnet_mask="255.255.255.0",
        gateway="192.168.1.1",
        system_name="sys1",
        partition_name="aixprod",
        profile_name="default",
        vlan_id="100",
    )
    assert submitted["cmd"] == expected


def test_install_lpar_os_tool_rejects_invalid_arguments_before_any_io(monkeypatch):
    """Validator failures raise before an SSH session is opened."""
    from hmc_mcp.server import hmc_install_lpar_os

    _hmc_env(monkeypatch)
    with pytest.raises(ValueError, match="IPv4"):
        hmc_install_lpar_os(
            "aixprod",
            "sys1",
            install_source="/extra/vios.iso",
            lpar_ip="999.9.9.9",
            nim_subnetmask="255.255.255.0",
            nim_gateway="192.168.1.1",
        )


def test_install_lpar_os_unknown_name_fails_before_submission(monkeypatch, mock_hmc):
    from hmc_mcp.server import hmc_install_lpar_os

    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/ManagedSystem/search/(SystemName==sys1)").mock(
        return_value=httpx.Response(200, text=_system_feed("sys1"))
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(200, text=_system_feed("sys1"))
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(200, text='<?xml version="1.0"?><feed/>')
    )

    async def fail(config, cmd):  # pragma: no cover — must never be reached
        raise AssertionError("run_installios must not be called")

    with patch("hmc_mcp.operations.install.run_installios", new=fail):
        with pytest.raises(ValueError, match="No LPAR named"):
            hmc_install_lpar_os("nosuchlpar", "sys1", **_INSTALL_KWARGS)



def _system_feed(name: str) -> str:
    """Atom feed wrapping one ManagedSystem entry named *name*."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{SYSTEM_UUID}</id>
    <title>ManagedSystem:{name}</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <SystemName>{name}</SystemName>
      </ManagedSystem>
    </content>
  </entry>
</feed>"""


def _system_entry(name: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{SYSTEM_UUID}</id>
  <title>ManagedSystem:{name}</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <SystemName>{name}</SystemName>
    </ManagedSystem>
  </content>
</entry>"""
