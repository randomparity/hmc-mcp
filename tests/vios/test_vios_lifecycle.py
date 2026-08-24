"""Tests for VIOS lifecycle tools: create, delete, install (CLI bridge)."""

import httpx
import pytest
from unittest.mock import patch

from conftest import make_config

from hmc_mcp.errors import HMCError
from hmc_mcp.ssh_commands import (
    INSTALLIOS_PID_PREFIX,
    build_installios_command,
)
from hmc_mcp.documents import LparResources, build_vios_document

BASE = "https://hmc.test"

VIOS_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:00000000-0000-0000-0000-000000000003</id>
  <title>LogicalPartition:vios1</title>
  <link rel="SELF" href="{base}/rest/api/uom/LogicalPartition/00000000-0000-0000-0000-000000000003"/>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <PartitionName>vios1</PartitionName>
      <PartitionType>Virtual IO Server</PartitionType>
      <PartitionState>not activated</PartitionState>
    </LogicalPartition>
  </content>
</entry>
""".format(base=BASE)


# ---------------------------------------------------------------------- #
# Unit: build_vios_document
# ---------------------------------------------------------------------- #


def test_build_vios_document_minimal():
    xml = build_vios_document(name="vios1")
    assert "Virtual IO Server" in xml
    assert "<PartitionName" in xml and "vios1" in xml
    assert "PartitionMemoryConfiguration" in xml
    assert "PartitionProcessorConfiguration" in xml
    assert "SharedProcessorConfiguration" in xml


def test_build_vios_document_custom_resources():
    xml = build_vios_document(
        name="vios2",
        resources=LparResources(
            min_memory=1024,
            desired_memory=8192,
            max_memory=16384,
            desired_vcpus=4,
            min_vcpus=2,
            max_vcpus=8,
            desired_procs=1.0,
            min_procs=0.5,
            max_procs=2.0,
            uncapped=True,
        ),
    )
    assert "vios2" in xml
    assert "Virtual IO Server" in xml
    assert "8192" in xml
    assert "16384" in xml
    assert "1024" in xml


# ---------------------------------------------------------------------- #
# Unit: installios command composition (shared with hmc_install_vios)
# ---------------------------------------------------------------------- #


def test_build_installios_command_exact_line_for_vios():
    command, log_path = build_installios_command(
        install_source="/extra/viosimages/VIOS_4.1/dvdimage.v1.iso",
        client_ip="192.168.1.20",
        subnet_mask="255.255.255.0",
        gateway="192.168.1.1",
        system_name="sys1",
        partition_name="vios1",
        profile_name="default",
        vlan_id="100",
    )
    assert log_path == "/tmp/hmc-mcp-installios-vios1.log"
    assert command == (
        "nohup installios -d /extra/viosimages/VIOS_4.1/dvdimage.v1.iso "
        "-i 192.168.1.20 -S 255.255.255.0 -g 192.168.1.1 -s sys1 -p vios1 "
        f"-r default -V 100 </dev/null >{log_path} 2>&1 "
        f"& echo {INSTALLIOS_PID_PREFIX}$!"
    )


# ---------------------------------------------------------------------- #
# Tool-layer tests for hmc_install_vios
# ---------------------------------------------------------------------- #

VIOS_UUID = "00000000-0000-0000-0000-000000000003"
SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"

_INSTALL_KWARGS = {
    "install_source": "/extra/viosimages/VIOS_4.1/dvdimage.v1.iso",
    "vios_ip": "192.168.1.20",
    "nim_subnetmask": "255.255.255.0",
    "nim_gateway": "192.168.1.1",
    "vlan_id": "100",
}


def _system_feed(name: str) -> str:
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


def _mock_resolution(mock_hmc) -> None:
    mock_hmc.get("/rest/api/uom/ManagedSystem/search/(SystemName==sys1)").mock(
        return_value=httpx.Response(200, text=_system_feed("sys1"))
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(200, text=_system_feed("sys1"))
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualIOServer").mock(
        return_value=httpx.Response(200, text=VIOS_ENTRY)
    )


def test_install_vios_accepts_partition_name(monkeypatch, mock_hmc):
    """The public VIOS target is resolved before the install submission."""
    from hmc_mcp.server import hmc_install_vios

    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "test-password")
    _mock_resolution(mock_hmc)
    submitted = {}

    async def fake_run_hmc_command(config, cmd):
        submitted["cmd"] = cmd
        return f"{INSTALLIOS_PID_PREFIX}4242\n"

    with (
        patch("hmc_mcp.ssh_commands.run_hmc_command", new=fake_run_hmc_command),
        patch(
            "hmc_mcp.server_vios.build_config",
            new=lambda profile=None: make_config(),
        ),
    ):
        result = hmc_install_vios("vios1", "sys1", **_INSTALL_KWARGS)

    assert result["partition"] == "vios1"
    assert result["pid"] == 4242
    assert result["log_path"] == "/tmp/hmc-mcp-installios-vios1.log"
    assert "no HMC job exists on this path" in result["message"]
    expected, _ = build_installios_command(
        install_source="/extra/viosimages/VIOS_4.1/dvdimage.v1.iso",
        client_ip="192.168.1.20",
        subnet_mask="255.255.255.0",
        gateway="192.168.1.1",
        system_name="sys1",
        partition_name="vios1",
        profile_name="default",
        vlan_id="100",
    )
    assert submitted["cmd"] == expected


def test_install_vios_tool_rejects_invalid_arguments_before_any_io(monkeypatch):
    """Validator failures raise before an SSH session is opened."""
    from hmc_mcp.server import hmc_install_vios

    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "test-password")
    with pytest.raises(ValueError, match="VLAN"):
        hmc_install_vios(
            "vios1",
            "sys1",
            install_source="/extra/vios.iso",
            vios_ip="192.168.1.20",
            nim_subnetmask="255.255.255.0",
            nim_gateway="192.168.1.1",
            vlan_id="4095",
        )


def test_install_vios_unknown_name_fails_before_submission(monkeypatch, mock_hmc):
    from hmc_mcp.server import hmc_install_vios

    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "test-password")
    mock_hmc.get("/rest/api/uom/ManagedSystem/search/(SystemName==sys1)").mock(
        return_value=httpx.Response(200, text=_system_feed("sys1"))
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualIOServer").mock(
        return_value=httpx.Response(200, text='<?xml version="1.0"?><feed/>')
    )

    async def fail(config, cmd):  # pragma: no cover — must never be reached
        raise AssertionError("run_installios must not be called")

    with (
        patch("hmc_mcp.server_vios.run_installios", new=fail),
        patch(
            "hmc_mcp.server_vios.build_config",
            new=lambda profile=None: make_config(),
        ),
    ):
        with pytest.raises(ValueError, match="No VIOS named"):
            hmc_install_vios("nosuchvios", "sys1", **_INSTALL_KWARGS)


def test_install_vios_ssh_failure_surfaces_as_cli_error(monkeypatch, mock_hmc):
    """A failed installios submission raises HMCError out of the tool."""
    from hmc_mcp.server import hmc_install_vios

    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "test-password")
    _mock_resolution(mock_hmc)

    async def fail(config, cmd):
        raise HMCError("SSH command timed out after 30s")

    with (
        patch("hmc_mcp.ssh_commands.run_hmc_command", new=fail),
        patch(
            "hmc_mcp.server_vios.build_config",
            new=lambda profile=None: make_config(),
        ),
    ):
        with pytest.raises(HMCError, match="timed out"):
            hmc_install_vios("vios1", "sys1", **_INSTALL_KWARGS)
