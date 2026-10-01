"""Tests for VIOS lifecycle tools: create, delete, install (CLI bridge)."""

import xml.etree.ElementTree as ET
from unittest.mock import patch

import httpx
import pytest
from conftest import live_fixture, make_config

from hmcpctl.client.core import HMCClient
from hmcpctl.documents import LparResources, build_vios_document
from hmcpctl.documents.lpar import _partition_body, lpar_envelope
from hmcpctl.errors import HMCError
from hmcpctl.ssh.install import (
    INSTALLIOS_PID_PREFIX,
    build_installios_command,
)

BASE = "https://hmc.test"
UOM_NS = "http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"

# The captured V10R3 VirtualIOServer entry, read while the VIOS ran; the install
# preflight needs it powered off, so only the PartitionState text is changed.
VIOS_ENTRY = live_fixture("rest-vios-entry")["body"].replace(
    '<PartitionState kxe="false" kb="ROO">running<',
    '<PartitionState kxe="false" kb="ROO">not activated<',
)


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
# hmc_create_vios: the VirtualIOServer collection (#1214)
# ---------------------------------------------------------------------- #

CREATED = live_fixture("rest-vios-create")
REFUSED = live_fixture("rest-vios-create-lpar-path-refused")
CREATE_SYSTEM = "00000003-abcd-4ef0-8abc-000000000003"


def test_build_vios_document_is_rooted_at_virtual_io_server():
    root = ET.fromstring(build_vios_document(name="vios1").encode())
    assert root.tag == f"{{{UOM_NS}}}VirtualIOServer"
    assert root.findtext(f"{{{UOM_NS}}}PartitionType") == "Virtual IO Server"


def test_create_vios_puts_to_the_virtual_io_server_collection(monkeypatch, mock_hmc):
    """V10R3 creates a VIOS only from a VirtualIOServer PUT to that collection."""
    from hmcpctl.server_tools.vios.core import hmc_create_vios

    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "test-password")
    route = mock_hmc.put(CREATED["path"]).mock(
        return_value=httpx.Response(
            CREATED["status"],
            text=CREATED["body"],
            headers={"Content-Type": CREATED["content_type"]},
        )
    )

    result = hmc_create_vios(CREATE_SYSTEM, "sys-R1-pcie-v1214")

    request = route.calls.last.request
    assert request.headers["Content-Type"].endswith("; type=VirtualIOServer")
    assert request.headers["Accept"] == "*/*"
    assert "X-HMC-Schema-Version" not in request.headers
    root = ET.fromstring(request.content)
    assert root.tag == f"{{{UOM_NS}}}VirtualIOServer"
    assert result["UUID"] == "00000057-ABCD-4EF0-8ABC-000000000057"
    assert result["Resource"]["PartitionState"] == "not activated"


@pytest.mark.asyncio
async def test_logical_partition_create_of_a_vios_surfaces_rest0140(mock_hmc):
    """The LogicalPartition collection refuses a VIOS; the HMC's Message reaches the caller.

    ``build_lpar_document`` now refuses this type before any request (#1179), so the
    captured request body is composed from the shared partition body directly.
    """
    mock_hmc.put(REFUSED["path"]).mock(
        return_value=httpx.Response(REFUSED["status"], text=REFUSED["body"])
    )

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as raised:
            await hmc.create_logical_partition(
                CREATE_SYSTEM,
                lpar_envelope(
                    _partition_body("sys-R1-pcie-v1214", "Virtual IO Server")
                ),
            )

    assert raised.value.status_code == 500
    assert "REST0140 Invalid Partition Type associated with LogicalPartition" in str(
        raised.value
    )


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
    assert log_path == "/tmp/hmcpctl-installios-vios1.log"
    assert command == (
        "nohup installios -d /extra/viosimages/VIOS_4.1/dvdimage.v1.iso "
        "-i 192.168.1.20 -S 255.255.255.0 -g 192.168.1.1 -s sys1 -p vios1 "
        f"-r default -V 100 </dev/null >{log_path} 2>&1 "
        f"& echo {INSTALLIOS_PID_PREFIX}$!"
    )


# ---------------------------------------------------------------------- #
# Tool-layer tests for hmc_install_vios
# ---------------------------------------------------------------------- #

VIOS_UUID = "00000005-ABCD-4EF0-8ABC-000000000005"
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
    lpar_path = live_fixture("rest-lpar-path-vios")
    mock_hmc.get(lpar_path["path"]).mock(
        return_value=httpx.Response(lpar_path["status"], text=lpar_path["body"])
    )
    mock_hmc.get(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}").mock(
        return_value=httpx.Response(200, text=VIOS_ENTRY)
    )


def test_install_vios_accepts_partition_name(monkeypatch, mock_hmc):
    """The public VIOS target is resolved before the install submission."""
    from hmcpctl.server_tools.vios.core import hmc_install_vios

    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "test-password")
    _mock_resolution(mock_hmc)
    submitted = {}

    async def fake_run_hmc_command(config, cmd):
        submitted["cmd"] = cmd
        return f"{INSTALLIOS_PID_PREFIX}4242\n"

    with patch("hmcpctl.ssh.install.run_hmc_command", new=fake_run_hmc_command):
        result = hmc_install_vios("sys-R1-vios1", "sys1", **_INSTALL_KWARGS)

    assert result["partition"] == "sys-R1-vios1"
    assert result["pid"] == 4242
    assert result["log_path"] == "/tmp/hmcpctl-installios-sys-R1-vios1.log"
    assert "no HMC job exists on this path" in result["message"]
    expected, _ = build_installios_command(
        install_source="/extra/viosimages/VIOS_4.1/dvdimage.v1.iso",
        client_ip="192.168.1.20",
        subnet_mask="255.255.255.0",
        gateway="192.168.1.1",
        system_name="sys1",
        partition_name="sys-R1-vios1",
        profile_name="default",
        vlan_id="100",
    )
    assert submitted["cmd"] == expected


def test_install_vios_tool_rejects_invalid_arguments_before_any_io(monkeypatch):
    """Validator failures raise before an SSH session is opened."""
    from hmcpctl.server_tools.vios.core import hmc_install_vios

    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "test-password")
    with pytest.raises(ValueError, match="VLAN"):
        hmc_install_vios(
            "sys-R1-vios1",
            "sys1",
            install_source="/extra/vios.iso",
            vios_ip="192.168.1.20",
            nim_subnetmask="255.255.255.0",
            nim_gateway="192.168.1.1",
            vlan_id="4095",
        )


def test_install_vios_unknown_name_fails_before_submission(monkeypatch, mock_hmc):
    from hmcpctl.server_tools.vios.core import hmc_install_vios

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
        patch("hmcpctl.operations.vios.install.run_installios", new=fail),
        pytest.raises(ValueError, match="No VIOS named"),
    ):
        hmc_install_vios("nosuchvios", "sys1", **_INSTALL_KWARGS)


def test_install_vios_ssh_failure_surfaces_as_cli_error(monkeypatch, mock_hmc):
    """A failed installios submission raises HMCError out of the tool."""
    from hmcpctl.server_tools.vios.core import hmc_install_vios

    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "test-password")
    _mock_resolution(mock_hmc)

    async def fail(config, cmd):
        raise HMCError("SSH command timed out after 30s")

    with (
        patch("hmcpctl.ssh.install.run_hmc_command", new=fail),
        pytest.raises(HMCError, match="timed out"),
    ):
        hmc_install_vios("sys-R1-vios1", "sys1", **_INSTALL_KWARGS)
