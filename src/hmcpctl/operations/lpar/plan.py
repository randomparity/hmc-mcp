"""The LPAR plan behind ``hmc_plan_lpar`` (ADR 0198).

Reads what provisioning would need, through the delegated reads ADR 0198 names,
and reports whether a request fits, which targets it would use and what it would
change. It writes nothing and reserves nothing. The contract is
``docs/workflow/specs/2026-10-02-lpar-plan-design.md``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import ipaddress
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlparse

from ...documents import LparResources, PartitionType, StorageKind
from ...documents.lpar import shared_units_over_vcpus, validate_partition_type
from ...documents.storage import STORAGE_KINDS, validate_virtual_disk
from ...resource_identity import is_uuid
from ...ssh.affinity import MinimumAffinityPolicy, validate_minimum_affinity_policy
from ...ssh.lpar import validate_caller_token
from ..affinity.rest import ProvisionAffinityAssessment, validate_affinity_request
from .assignments import LparPcieAssignments

InstallProfile = Literal["ubuntu-26.04.1", "rocky-9.8"]
INSTALL_PROFILES: tuple[str, ...] = ("ubuntu-26.04.1", "rocky-9.8")
Boot = Literal["immediate", "deferred"]

MAX_SELECTORS = 16
MAX_ROUTES = 16
MAX_DNS = 3
MAX_KEYS = 16
MAX_KEY_LENGTH = 8192
MAX_PRODUCER_RESULT_BYTES = 64 * 1024
_DEFAULT_ROUTE = ipaddress.IPv4Network("0.0.0.0/0")
_MAC = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}")
_LOGIN_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}")


@dataclass(frozen=True)
class PlanAdapters:
    """The client virtual Ethernet adapter provisioning would add."""

    port_vlan_id: int = field(
        metadata={"description": "VLAN identifier, 1 to 4094, for the adapter."}
    )
    mac: str | None = field(
        default=None,
        metadata={
            "description": "Adapter MAC in lower-case colon form; prepared media only."
        },
    )


@dataclass(frozen=True)
class PlanStorage:
    """The VIOS-backed storage provisioning would map."""

    storage_name: str = field(
        metadata={"description": "Virtual-disk or physical-volume name to map."}
    )
    kind: StorageKind = field(
        default="VirtualDisk", metadata={"description": "Storage resource kind."}
    )
    vios_uuid: str | None = field(
        default=None,
        metadata={"description": "VIOS UUID to use; omit to let the plan choose."},
    )
    vg_uuid: str | None = field(
        default=None,
        metadata={"description": "Volume-group UUID to use; requires vios_uuid."},
    )
    capacity_mib: int | None = field(
        default=None,
        metadata={
            "description": "Create a new virtual disk of this size; omit to map "
            "existing storage."
        },
    )


@dataclass(frozen=True)
class InstallRoute:
    """One IPv4 route the installed guest gets."""

    destination: str = field(
        metadata={"description": "IPv4 network in CIDR form; 0.0.0.0/0 is default."}
    )
    gateway: str = field(
        metadata={"description": "IPv4 gateway inside the address's network."}
    )


@dataclass(frozen=True)
class InstallNetwork:
    """The installed guest's static IPv4 configuration."""

    address: str = field(
        metadata={"description": "IPv4 interface address in CIDR form."}
    )
    routes: tuple[InstallRoute, ...] = field(
        metadata={"description": "1 to 16 routes, exactly one of them default."}
    )
    dns: tuple[str, ...] = field(
        default=(), metadata={"description": "0 to 3 IPv4 DNS servers."}
    )


@dataclass(frozen=True)
class InstallMedia:
    """Where the installer media comes from (ADR 0191)."""

    mode: Literal["built", "prepared"] = field(
        metadata={"description": "built by hmcpctl, or prepared by a producer."}
    )
    url: str | None = field(
        default=None,
        metadata={"description": "Prepared media's http(s) URL; prepared only."},
    )
    producer_result: dict[str, Any] | None = field(
        default=None,
        metadata={
            "description": "The producer's JSON result, at most 64 KiB; prepared only."
        },
    )


@dataclass(frozen=True)
class LparInstall:
    """The operating-system installation provisioning would start (ADR 0194)."""

    profile: InstallProfile = field(metadata={"description": "Install profile."})
    network: InstallNetwork = field(
        metadata={"description": "Static network configuration."}
    )
    media: InstallMedia = field(metadata={"description": "Installer media."})
    ssh_authorized_keys: tuple[str, ...] = field(
        default=(),
        metadata={"description": "1 to 16 single-line keys; built media only."},
    )
    login_user: str | None = field(
        default=None, metadata={"description": "Guest login user; built media only."}
    )


@dataclass(frozen=True)
class Placement:
    """Choose the system: from these selectors, or from the whole connection."""

    systems: tuple[str, ...] | None = field(
        default=None,
        metadata={
            "description": "1 to 16 system names or UUIDs; omit to enumerate up to 16."
        },
    )


@dataclass(frozen=True)
class PlanRequest:
    """Provisioning's inputs, less what only an execution has (spec, *Inputs*)."""

    name: str
    adapters: PlanAdapters
    storage: PlanStorage
    resources: LparResources
    partition_type: PartitionType = "AIX/Linux"
    system_name_or_uuid: str | None = None
    placement: Placement | None = None
    assignments: LparPcieAssignments = LparPcieAssignments()
    caller_token: str | None = None
    minimum_affinity_policy: MinimumAffinityPolicy | None = None
    affinity_assessment: ProvisionAffinityAssessment | None = None
    power_on: bool | None = None
    boot: Boot = "immediate"
    install: LparInstall | None = None
    exclusive_writer_window: bool = False


def _blank(value: str | None) -> str | None:
    # ADR 0094: a client may send an unset optional string as "".
    return None if value is None or not value.strip() else value


def _normalized(request: PlanRequest) -> PlanRequest:
    return dataclasses.replace(
        request,
        system_name_or_uuid=_blank(request.system_name_or_uuid),
        adapters=dataclasses.replace(
            request.adapters, mac=_blank(request.adapters.mac)
        ),
        storage=dataclasses.replace(
            request.storage,
            vios_uuid=_blank(request.storage.vios_uuid),
            vg_uuid=_blank(request.storage.vg_uuid),
        ),
    )


def _check_selectors(request: PlanRequest) -> None:
    if (request.system_name_or_uuid is None) == (request.placement is None):
        raise ValueError("pass exactly one of system_name_or_uuid and placement")
    systems = request.placement.systems if request.placement else None
    if systems is not None and not (
        1 <= len(systems) <= MAX_SELECTORS
        and all(isinstance(value, str) and value.strip() for value in systems)
    ):
        raise ValueError(
            f"placement.systems: pass 1 to {MAX_SELECTORS} non-empty names or "
            "UUIDs, or omit it to enumerate the connection's systems"
        )


def _check_storage(storage: PlanStorage) -> None:
    if not storage.storage_name:
        raise ValueError("storage.storage_name: must not be empty")
    if storage.kind not in STORAGE_KINDS:
        raise ValueError(f"storage.kind: must be one of {sorted(STORAGE_KINDS)}")
    for label, value in (
        ("vios_uuid", storage.vios_uuid),
        ("vg_uuid", storage.vg_uuid),
    ):
        if value is not None and not is_uuid(value):
            raise ValueError(f"storage.{label}: must be a UUID")
    if storage.vg_uuid is not None and storage.vios_uuid is None:
        raise ValueError(
            "storage.vg_uuid: requires storage.vios_uuid, because a volume group "
            "belongs to one VIOS"
        )
    if storage.capacity_mib is not None:
        if storage.kind != "VirtualDisk":
            raise ValueError(
                "storage.capacity_mib: creates a VirtualDisk; kind must be VirtualDisk"
            )
        try:
            validate_virtual_disk(storage.storage_name, storage.capacity_mib)
        except ValueError as exc:
            raise ValueError(f"storage.capacity_mib: {exc}") from None


def _check_mac(request: PlanRequest) -> None:
    mac = request.adapters.mac
    prepared = request.install is not None and request.install.media.mode == "prepared"
    if mac is None:
        if prepared:
            raise ValueError("adapters.mac: prepared media requires the adapter MAC")
        return
    if not prepared:
        raise ValueError("adapters.mac: accepted only with prepared install media")
    if not _MAC.fullmatch(mac) or int(mac[:2], 16) & 1:
        raise ValueError(
            "adapters.mac: must be lower-case colon form, such as "
            "02:00:00:00:00:01, with no group bit"
        )


def _check_affinity(request: PlanRequest) -> None:
    policy = request.minimum_affinity_policy
    if policy is not None:
        validate_minimum_affinity_policy(policy)
    assessment = request.affinity_assessment
    if assessment is None:
        return
    if (
        request.system_name_or_uuid is None
        or assessment.system_name_or_uuid != request.system_name_or_uuid
        or assessment.lpar_name != request.name
    ):
        raise ValueError(
            "affinity_assessment: its system and LPAR identities must equal "
            "system_name_or_uuid and name, so it requires system_name_or_uuid"
        )
    validate_affinity_request(
        assessment, policy.min_affinity_score if policy is not None else None
    )


def _ipv4_address(text: str, label: str) -> ipaddress.IPv4Address:
    try:
        return ipaddress.IPv4Address(text)
    except ValueError:
        raise ValueError(f"{label}: must be an IPv4 address") from None


def _check_network(network: InstallNetwork) -> None:
    label = "install.network"
    try:
        interface = ipaddress.IPv4Interface(network.address)
    except ValueError:
        interface = None
    if (
        interface is None
        or "/" not in network.address
        or not 1 <= interface.network.prefixlen <= 32
    ):
        raise ValueError(
            f"{label}.address: must be an IPv4 interface in CIDR form, prefix 1 to 32"
        )
    if not 1 <= len(network.routes) <= MAX_ROUTES:
        raise ValueError(f"{label}.routes: pass 1 to {MAX_ROUTES} routes")
    defaults = 0
    for index, route in enumerate(network.routes):
        try:
            destination = ipaddress.IPv4Network(route.destination, strict=True)
        except ValueError:
            raise ValueError(
                f"{label}.routes[{index}].destination: must be an IPv4 network in "
                "CIDR form"
            ) from None
        defaults += destination == _DEFAULT_ROUTE
        gateway = _ipv4_address(route.gateway, f"{label}.routes[{index}].gateway")
        if gateway not in interface.network:
            raise ValueError(
                f"{label}.routes[{index}].gateway: must be inside "
                f"{network.address}'s network"
            )
    if defaults != 1:
        raise ValueError(f"{label}.routes: exactly one route must be 0.0.0.0/0")
    if len(network.dns) > MAX_DNS:
        raise ValueError(f"{label}.dns: pass at most {MAX_DNS} servers")
    for index, server in enumerate(network.dns):
        _ipv4_address(server, f"{label}.dns[{index}]")


def _check_login(install: LparInstall) -> None:
    keys = install.ssh_authorized_keys
    if not 1 <= len(keys) <= MAX_KEYS:
        raise ValueError(
            f"install.ssh_authorized_keys: built media needs 1 to {MAX_KEYS} keys"
        )
    for index, key in enumerate(keys):
        if (
            not isinstance(key, str)
            or not 1 <= len(key) <= MAX_KEY_LENGTH
            or "\n" in key
            or "\r" in key
        ):
            raise ValueError(
                f"install.ssh_authorized_keys[{index}]: must be one line of 1 to "
                f"{MAX_KEY_LENGTH} characters"
            )
    if install.login_user is None or not _LOGIN_USER.fullmatch(install.login_user):
        raise ValueError(
            "install.login_user: built media needs 1 to 32 characters matching "
            "[a-z_][a-z0-9_-]*"
        )


def _check_prepared(install: LparInstall) -> None:
    if install.ssh_authorized_keys:
        raise ValueError(
            "install.ssh_authorized_keys: prepared media carries its own keys; omit it"
        )
    if install.login_user is not None:
        raise ValueError(
            "install.login_user: prepared media carries its own user; omit it"
        )
    media = install.media
    if media.url is None or urlparse(media.url).scheme not in ("http", "https"):
        raise ValueError("install.media.url: prepared media needs an http or https URL")
    result = media.producer_result
    if result is None:
        raise ValueError(
            "install.media.producer_result: prepared media needs the producer's "
            "JSON object"
        )
    size = len(json.dumps(result, separators=(",", ":")).encode())
    if size > MAX_PRODUCER_RESULT_BYTES:
        raise ValueError(
            f"install.media.producer_result: {size} bytes exceeds "
            f"{MAX_PRODUCER_RESULT_BYTES}"
        )


def _check_install(request: PlanRequest) -> None:
    install = request.install
    if install is None:
        if request.boot != "immediate":
            raise ValueError("boot: deferred applies only with install")
        return
    if request.storage.capacity_mib is None:
        raise ValueError(
            "storage.capacity_mib: install needs a new disk (ADR 0191 Decision 6)"
        )
    if request.power_on:
        raise ValueError("power_on: with install, boot governs power-on; omit power_on")
    if install.profile not in INSTALL_PROFILES:
        raise ValueError(f"install.profile: must be one of {list(INSTALL_PROFILES)}")
    _check_network(install.network)
    if install.media.mode == "built":
        if install.media.url is not None or install.media.producer_result is not None:
            raise ValueError(
                "install.media.url: built media takes no url or producer_result"
            )
        _check_login(install)
    elif install.media.mode == "prepared":
        _check_prepared(install)
    else:
        raise ValueError("install.media.mode: must be built or prepared")


def check_request(request: PlanRequest) -> PlanRequest:
    """Return *request* with blank selectors as ``None``; raise naming the first bad input.

    Messages never echo a key, URL or producer-result content.
    """
    request = _normalized(request)
    if not request.name:
        raise ValueError("name: must not be empty")
    _check_selectors(request)
    validate_partition_type(request.partition_type)
    if request.resources.dedicated is not True and (
        refusal := shared_units_over_vcpus(request.resources)
    ):
        raise ValueError(f"resources: {refusal}")
    if not 1 <= request.adapters.port_vlan_id <= 4094:
        raise ValueError("adapters.port_vlan_id: must be 1 to 4094")
    _check_storage(request.storage)
    if request.caller_token is not None:
        validate_caller_token(request.caller_token)
    _check_affinity(request)
    if request.boot not in ("immediate", "deferred"):
        raise ValueError("boot: must be immediate or deferred")
    _check_install(request)
    _check_mac(request)
    return request


DIGEST_FORMAT = "hmc-lpar-plan-v1"


@dataclass(frozen=True)
class PlanSystem:
    """The managed system a plan targets. ``id`` is ``<connection>/<uuid>``."""

    id: str
    uuid: str
    name: str | None


@dataclass(frozen=True)
class PlanResource:
    """A VIOS or volume group a plan targets."""

    uuid: str
    name: str | None


@dataclass(frozen=True)
class PlanTargets:
    """The resolved targets: the system, and the VIOS and volume group when known."""

    system: PlanSystem
    vios: PlanResource | None
    volume_group: PlanResource | None


def _canonical(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"plan_digest cannot encode {type(value).__name__}")


def plan_digest(request: PlanRequest, targets: PlanTargets, connection: str) -> str:
    """SHA-256 over the canonical request and resolved targets (#1225 recomputes it)."""
    system = targets.system.uuid.lower()
    vios = targets.vios.uuid.lower() if targets.vios else None
    group = targets.volume_group.uuid.lower() if targets.volume_group else None
    body = asdict(_normalized(request))
    del body["placement"]
    body["system_name_or_uuid"] = system
    body["storage"]["vios_uuid"] = vios
    body["storage"]["vg_uuid"] = group
    document = {
        "format": DIGEST_FORMAT,
        "connection": connection,
        "request": body,
        "targets": {"system": system, "vios": vios, "volume_group": group},
    }
    text = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_canonical,
    )
    return hashlib.sha256(text.encode()).hexdigest()
