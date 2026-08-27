"""NIM install command validation and execution over SSH."""

from __future__ import annotations

import re
import shlex

from .config import HMCConfig
from .ssh import HMCCLIError, run_hmc_command

_IPV4_OCTET = r"(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
_IPV4_PATTERN = re.compile(rf"^{_IPV4_OCTET}(\.{_IPV4_OCTET}){{3}}$")
_MAC_ADDRESS_PATTERN = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")
_VLAN_MIN, _VLAN_MAX = 0, 4094
_LOG_SLUG_PATTERN = re.compile(r"[^A-Za-z0-9._-]")
_INSTALLIOS_LOG_TEMPLATE = "/tmp/hmc-mcp-installios-{slug}.log"

INSTALLIOS_PID_PREFIX = "HMC_MCP_INSTALLIOS_PID="


def validate_ipv4_address(value: str) -> str:
    """Return *value* when it is a dotted-quad IPv4 address, else raise.

    Each octet must be 0-255 without a sign or surrounding whitespace; this is
    the grammar every installios network flag accepts (ADR 0070).
    """
    if not isinstance(value, str) or not _IPV4_PATTERN.match(value):
        raise ValueError(f"{value!r} is not a valid IPv4 address")
    return value


def validate_ipv4_subnet_mask(value: str) -> str:
    """Return *value* when it is a contiguous dotted-quad subnet mask.

    A valid mask is one run of set bits followed by one run of clear bits
    (``255.255.0.0``, not ``255.0.255.0``); installios hands the value straight
    to the client's network configuration, where a discontiguous mask can only
    fail late, mid-install.
    """
    validate_ipv4_address(value)
    bits = "".join(f"{int(octet):08b}" for octet in value.split("."))
    if "01" in bits:
        raise ValueError(
            f"{value!r} is not a contiguous subnet mask "
            "(set bits must precede clear bits)"
        )
    return value


def validate_vlan_id(value: str) -> str:
    """Return *value* when it names an installios VLAN tag, else raise.

    The man page admits 0 (untagged) through 4094 for ``-V`` (ADR 0070).
    """
    if (
        not isinstance(value, str)
        or not value.isdigit()
        or not _VLAN_MIN <= int(value) <= _VLAN_MAX
    ):
        raise ValueError(
            f"{value!r} is not a valid VLAN tag identifier "
            f"(expected an integer from {_VLAN_MIN} to {_VLAN_MAX})"
        )
    return value


def validate_mac_address(value: str) -> str:
    """Return *value* when it is a colon-separated MAC address, else raise."""
    if not isinstance(value, str) or not _MAC_ADDRESS_PATTERN.match(value):
        raise ValueError(
            f"{value!r} is not a valid MAC address "
            "(expected six colon-separated hex octets, e.g. f2:d4:60:00:d0:03)"
        )
    return value


def validate_install_source(value: str) -> str:
    """Return *value* when it fits the ``installios -d`` source grammar.

    Three shapes are admitted by the man page: a device path (``/dev/cdrom``,
    or an ``lsmediadev`` USB device name), an absolute path on the HMC to a
    ``backupios`` nim_resources tarball or VIOS ISO, or ``server:/path`` for an
    NFS-served backup. Values are shlex-quoted into the command, so validation
    is defense in depth rather than the injection boundary — what it buys is a
    fast, named rejection for values that could never be a source (control
    characters, flag-like leading dashes) instead of a confusing remote parse.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("install_source must be a non-empty string")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError(
            "install_source contains control characters; only printable text "
            "is accepted"
        )
    if value.startswith("-"):
        raise ValueError(
            f"install_source {value!r} starts with '-'; it would be parsed as "
            "an installios flag, not a source path"
        )
    host, sep, remote_path = value.partition(":")
    if sep and ("/" in host or not host.strip()):
        raise ValueError(
            f"install_source {value!r} looks like an NFS location but its "
            "server part is not a hostname"
        )
    return value


def validate_hmc_name(value: str, field: str) -> str:
    """Return *value* when it can name an HMC object, else raise.

    HMC object names are free-form on the console side, so the only hard rule
    here is printable, non-empty text: anything else cannot be a name, and
    everything legitimate survives ``shlex.quote`` intact.
    """
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError(f"{field} must be non-empty printable text")
    return value


def build_installios_command(
    *,
    install_source: str,
    client_ip: str,
    subnet_mask: str,
    gateway: str,
    system_name: str,
    partition_name: str,
    profile_name: str,
    vlan_id: str = "0",
    mac_address: str | None = None,
) -> tuple[str, str]:
    """Compose the detached ``installios`` invocation for one partition.

    Returns ``(command, log_path)``. The invocation runs under ``nohup`` with
    stdin closed and output redirected to a per-partition log, then echoes the
    backgrounded PID tagged with :data:`INSTALLIOS_PID_PREFIX` — submit-and-
    detach semantics, so the SSH exec returns immediately and the NIM install
    continues after the connection closes (ADR 0070).

    Every interpolated value is validated here as well as at the tool layer:
    this function is the injection boundary, and it does not trust its callers.
    """
    validate_install_source(install_source)
    validate_ipv4_address(client_ip)
    validate_ipv4_subnet_mask(subnet_mask)
    validate_ipv4_address(gateway)
    validate_hmc_name(system_name, "system_name")
    validate_hmc_name(partition_name, "partition_name")
    validate_hmc_name(profile_name, "profile_name")
    validate_vlan_id(vlan_id)

    flags = [
        "-d",
        shlex.quote(install_source),
        "-i",
        shlex.quote(client_ip),
        "-S",
        shlex.quote(subnet_mask),
        "-g",
        shlex.quote(gateway),
        "-s",
        shlex.quote(system_name),
        "-p",
        shlex.quote(partition_name),
        "-r",
        shlex.quote(profile_name),
        "-V",
        vlan_id,
    ]
    if mac_address is not None:
        flags += ["-m", shlex.quote(validate_mac_address(mac_address))]

    slug = _LOG_SLUG_PATTERN.sub("_", partition_name)
    log_path = _INSTALLIOS_LOG_TEMPLATE.format(slug=slug)
    installios = "installios " + " ".join(flags)
    command = (
        f"nohup {installios} </dev/null >{shlex.quote(log_path)} 2>&1 "
        f"& echo {INSTALLIOS_PID_PREFIX}$!"
    )
    return command, log_path


def parse_installios_pid(raw_output: str) -> int:
    """Extract the PID echoed by :func:`build_installios_command`'s command.

    Raises:
        HMCCLIError: If the submission output carries no PID tag, which means
            the shell never got far enough to background the command.
    """
    prefix_len = len(INSTALLIOS_PID_PREFIX)
    for line in raw_output.splitlines():
        candidate = line.strip()
        if candidate.startswith(INSTALLIOS_PID_PREFIX):
            digits = candidate[prefix_len:].strip()
            if digits.isdigit():
                return int(digits)
    raise HMCCLIError(
        "installios submission produced no PID; the HMC shell did not report "
        f"a backgrounded process. Output: {raw_output.strip()[:200]!r}"
    )


async def run_installios(config: HMCConfig, command: str) -> int:
    """Submit a composed ``installios`` command over SSH, return its PID.

    Only the submission is bounded by ``config.ssh_timeout``; the install
    itself runs detached on the HMC and outlives the SSH session (ADR 0070).
    Failures surface as :class:`HMCCLIError` per the transport's conventions.
    """
    return parse_installios_pid(await run_hmc_command(config, command))
