"""Tests for the HMC CLI ``installios`` bridge (ADR 0070).

The InstallLPAR and InstallVIOS REST jobs do not exist (ADR 0069); the bridge
composes a detached ``installios`` command and submits it over SSH.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from conftest import make_config

from hmcpctl.errors import HMCError
from hmcpctl.ssh.install import (
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

# ---------------------------------------------------------------------- #
# Unit: validators
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value", ["10.0.0.1", "255.255.255.255", "0.0.0.0", "192.168.1.30"]
)
def test_valid_ipv4_addresses_pass(value):
    assert validate_ipv4_address(value) == value


@pytest.mark.parametrize(
    "value",
    ["999.1.1.1", "10.0.0", "10.0.0.0.1", "a.b.c.d", "", "10.0.0.1 ", "-1.2.3.4"],
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


@pytest.mark.parametrize("value", ["-foo", "--all", "-"])
def test_hmc_name_with_leading_dash_rejected(value):
    with pytest.raises(ValueError, match="partition_name .* starts with '-'"):
        validate_hmc_name(value, "partition_name")


def test_hmc_name_with_inner_dash_accepted():
    assert validate_hmc_name("aix-lpar-1", "partition_name") == "aix-lpar-1"


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
    assert log_path == "/tmp/hmcpctl-installios-aixprod.log"
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
    from hmcpctl.ssh.install import run_installios
    from hmcpctl.ssh.transport import HMCCLIError

    config = make_config()

    async def fail(config, cmd):
        raise HMCCLIError(f"SSH command {cmd!r} failed with exit status 127")

    with (
        patch("hmcpctl.ssh.install.run_hmc_command", new=fail),
        pytest.raises(HMCCLIError, match="exit status 127"),
    ):
        await run_installios(config, "nohup installios ... & echo pid=$!")
