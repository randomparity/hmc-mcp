"""LPAR planning behind hmc_plan_lpar (#1221, ADR 0198)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from hmcpctl.documents import LparResources
from hmcpctl.operations.affinity.rest import ProvisionAffinityAssessment
from hmcpctl.operations.lpar.assignments import (
    DedicatedPcieAssignment,
    LparPcieAssignments,
    SriovLogicalPortAssignment,
)
from hmcpctl.operations.lpar.plan import (
    InstallMedia,
    InstallNetwork,
    InstallRoute,
    LparInstall,
    Placement,
    PlanAdapters,
    PlanRequest,
    PlanResource,
    PlanStorage,
    PlanSystem,
    PlanTargets,
    check_request,
    plan_digest,
)
from hmcpctl.ssh.affinity import MinimumAffinityPolicy

_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPlanKeyMaterial user@host"
_URL = "https://iso.example.test/secret-path/installer.iso"
_MAC = "02:00:00:00:00:01"


def _uuid(number: int) -> str:
    return f"{number:08x}-0000-4000-8000-000000000000"


def _network(**changes: Any) -> InstallNetwork:
    network = InstallNetwork(
        address="10.0.0.5/24",
        routes=(InstallRoute(destination="0.0.0.0/0", gateway="10.0.0.1"),),
        dns=("10.0.0.2",),
    )
    return dataclasses.replace(network, **changes)


def _built(**changes: Any) -> LparInstall:
    install = LparInstall(
        profile="ubuntu-26.04.1",
        network=_network(),
        media=InstallMedia(mode="built"),
        ssh_authorized_keys=(_KEY,),
        login_user="ubuntu",
    )
    return dataclasses.replace(install, **changes)


def _prepared(**changes: Any) -> LparInstall:
    install = LparInstall(
        profile="rocky-9.8",
        network=_network(),
        media=InstallMedia(
            mode="prepared", url=_URL, producer_result={"token": "producer-secret"}
        ),
    )
    return dataclasses.replace(install, **changes)


def _request(**changes: Any) -> PlanRequest:
    request = PlanRequest(
        name="web1",
        adapters=PlanAdapters(port_vlan_id=100),
        storage=PlanStorage(storage_name="web1_root"),
        resources=LparResources(desired_memory=4096, desired_procs=0.5),
        system_name_or_uuid="sys1",
    )
    return dataclasses.replace(request, **changes)


def _install_request(install: LparInstall, **changes: Any) -> PlanRequest:
    mac = _MAC if install.media.mode == "prepared" else None
    fields: dict[str, Any] = {
        "adapters": PlanAdapters(port_vlan_id=100, mac=mac),
        "storage": PlanStorage(storage_name="web1_root", capacity_mib=20480),
        "install": install,
        "exclusive_writer_window": True,
    }
    return _request(**{**fields, **changes})


def _assessment(**changes: Any) -> ProvisionAffinityAssessment:
    assessment = ProvisionAffinityAssessment(
        system_name_or_uuid="sys1",
        lpar_name="web1",
        captured_score=90,
        captured_policy_state="configured",
        captured_minimum=None,
        captured_at=datetime(2026, 10, 2, tzinfo=UTC),
        stale_after_seconds=3600,
        response="warn",
        regression_threshold=10,
        optimization_threshold=5,
    )
    return dataclasses.replace(assessment, **changes)


def _storage(**changes: Any) -> PlanStorage:
    return dataclasses.replace(PlanStorage(storage_name="web1_root"), **changes)


_REJECTED: list[tuple[str, Any, str]] = [
    ("no selector", lambda: _request(system_name_or_uuid=None), "placement"),
    (
        "both selectors",
        lambda: _request(placement=Placement()),
        "placement",
    ),
    (
        "no placement selectors",
        lambda: _request(system_name_or_uuid=None, placement=Placement(systems=())),
        "placement.systems",
    ),
    (
        "17 placement selectors",
        lambda: _request(
            system_name_or_uuid=None,
            placement=Placement(systems=tuple(f"s{i}" for i in range(17))),
        ),
        "placement.systems",
    ),
    (
        "empty placement selector",
        lambda: _request(system_name_or_uuid=None, placement=Placement(systems=("",))),
        "placement.systems",
    ),
    ("empty name", lambda: _request(name=""), "name"),
    ("VLAN 0", lambda: _request(adapters=PlanAdapters(port_vlan_id=0)), "port_vlan_id"),
    (
        "VLAN 4095",
        lambda: _request(adapters=PlanAdapters(port_vlan_id=4095)),
        "port_vlan_id",
    ),
    (
        "vg without vios",
        lambda: _request(storage=_storage(vg_uuid=_uuid(9))),
        "storage.vg_uuid",
    ),
    (
        "vios not a UUID",
        lambda: _request(storage=_storage(vios_uuid="vios1")),
        "storage.vios_uuid",
    ),
    (
        "capacity zero",
        lambda: _request(storage=_storage(capacity_mib=0)),
        "storage.capacity_mib",
    ),
    (
        "capacity negative",
        lambda: _request(storage=_storage(capacity_mib=-1024)),
        "storage.capacity_mib",
    ),
    (
        "capacity with physical volume",
        lambda: _request(storage=_storage(kind="PhysicalVolume", capacity_mib=1024)),
        "storage.capacity_mib",
    ),
    (
        "install without capacity",
        lambda: _install_request(_built(), storage=_storage()),
        "storage.capacity_mib",
    ),
    (
        "unknown profile",
        lambda: _install_request(_built(profile="debian-13")),
        "install.profile",
    ),
    (
        "non-CIDR address",
        lambda: _install_request(_built(network=_network(address="10.0.0.5"))),
        "install.network.address",
    ),
    (
        "prefix 0",
        lambda: _install_request(_built(network=_network(address="10.0.0.5/0"))),
        "install.network.address",
    ),
    (
        "prefix 33",
        lambda: _install_request(_built(network=_network(address="10.0.0.5/33"))),
        "install.network.address",
    ),
    (
        "no routes",
        lambda: _install_request(_built(network=_network(routes=()))),
        "install.network.routes",
    ),
    (
        "17 routes",
        lambda: _install_request(
            _built(
                network=_network(
                    routes=(
                        InstallRoute("0.0.0.0/0", "10.0.0.1"),
                        *(
                            InstallRoute(f"192.168.{i}.0/24", "10.0.0.1")
                            for i in range(16)
                        ),
                    )
                )
            )
        ),
        "install.network.routes",
    ),
    (
        "no default route",
        lambda: _install_request(
            _built(
                network=_network(routes=(InstallRoute("192.168.0.0/24", "10.0.0.1"),))
            )
        ),
        "install.network.routes",
    ),
    (
        "two default routes",
        lambda: _install_request(
            _built(
                network=_network(
                    routes=(
                        InstallRoute("0.0.0.0/0", "10.0.0.1"),
                        InstallRoute("0.0.0.0/0", "10.0.0.3"),
                    )
                )
            )
        ),
        "install.network.routes",
    ),
    (
        "gateway outside network",
        lambda: _install_request(
            _built(network=_network(routes=(InstallRoute("0.0.0.0/0", "10.1.0.1"),)))
        ),
        "install.network.routes[0].gateway",
    ),
    (
        "four DNS servers",
        lambda: _install_request(
            _built(
                network=_network(dns=("10.0.0.2", "10.0.0.3", "10.0.0.4", "10.0.0.6"))
            )
        ),
        "install.network.dns",
    ),
    (
        "IPv6 DNS server",
        lambda: _install_request(_built(network=_network(dns=("fd00::1",)))),
        "install.network.dns",
    ),
    (
        "built without keys",
        lambda: _install_request(_built(ssh_authorized_keys=())),
        "install.ssh_authorized_keys",
    ),
    (
        "built without user",
        lambda: _install_request(_built(login_user=None)),
        "install.login_user",
    ),
    (
        "built with URL",
        lambda: _install_request(_built(media=InstallMedia(mode="built", url=_URL))),
        "install.media.url",
    ),
    (
        "prepared with keys",
        lambda: _install_request(_prepared(ssh_authorized_keys=(_KEY,))),
        "install.ssh_authorized_keys",
    ),
    (
        "prepared with user",
        lambda: _install_request(_prepared(login_user="ubuntu")),
        "install.login_user",
    ),
    (
        "prepared without URL",
        lambda: _install_request(
            _prepared(media=InstallMedia(mode="prepared", producer_result={"a": 1}))
        ),
        "install.media.url",
    ),
    (
        "prepared with ftp URL",
        lambda: _install_request(
            _prepared(
                media=InstallMedia(
                    mode="prepared",
                    url="ftp://iso.example.test/secret-path/x.iso",
                    producer_result={"a": 1},
                )
            )
        ),
        "install.media.url",
    ),
    (
        "prepared without producer result",
        lambda: _install_request(
            _prepared(media=InstallMedia(mode="prepared", url=_URL))
        ),
        "install.media.producer_result",
    ),
    (
        "producer result over 64 KiB",
        lambda: _install_request(
            _prepared(
                media=InstallMedia(
                    mode="prepared",
                    url=_URL,
                    producer_result={"token": "producer-secret" + "x" * 65536},
                )
            )
        ),
        "install.media.producer_result",
    ),
    (
        "prepared without mac",
        lambda: _install_request(_prepared(), adapters=PlanAdapters(port_vlan_id=100)),
        "adapters.mac",
    ),
    (
        "mac with built media",
        lambda: _install_request(
            _built(), adapters=PlanAdapters(port_vlan_id=100, mac=_MAC)
        ),
        "adapters.mac",
    ),
    (
        "mac without install",
        lambda: _request(adapters=PlanAdapters(port_vlan_id=100, mac=_MAC)),
        "adapters.mac",
    ),
    (
        "upper-case mac",
        lambda: _install_request(
            _prepared(),
            adapters=PlanAdapters(port_vlan_id=100, mac="02:00:00:00:00:AB"),
        ),
        "adapters.mac",
    ),
    (
        "multicast mac",
        lambda: _install_request(
            _prepared(),
            adapters=PlanAdapters(port_vlan_id=100, mac="03:00:00:00:00:01"),
        ),
        "adapters.mac",
    ),
    (
        "malformed mac",
        lambda: _install_request(
            _prepared(),
            adapters=PlanAdapters(port_vlan_id=100, mac="02-00-00-00-00-01"),
        ),
        "adapters.mac",
    ),
    (
        "key over 8192 characters",
        lambda: _install_request(_built(ssh_authorized_keys=(_KEY + "A" * 8192,))),
        "install.ssh_authorized_keys[0]",
    ),
    (
        "key with a newline",
        lambda: _install_request(_built(ssh_authorized_keys=(_KEY + "\n" + _KEY,))),
        "install.ssh_authorized_keys[0]",
    ),
    (
        "17 keys",
        lambda: _install_request(_built(ssh_authorized_keys=(_KEY,) * 17)),
        "install.ssh_authorized_keys",
    ),
    (
        "bad login user",
        lambda: _install_request(_built(login_user="Root")),
        "install.login_user",
    ),
    (
        "shared units above vcpus",
        lambda: _request(resources=LparResources(desired_procs=2.0, desired_vcpus=1)),
        "virtual processor",
    ),
    (
        "VIOS partition type",
        lambda: _request(partition_type="Virtual IO Server"),
        "VIOS",
    ),
    ("empty caller token", lambda: _request(caller_token=""), "caller"),
    ("caller token with a space", lambda: _request(caller_token="a b"), "caller"),
    (
        "affinity score 101",
        lambda: _request(
            minimum_affinity_policy=MinimumAffinityPolicy(
                min_affinity_score=101, min_affinity_score_action="warn"
            )
        ),
        "min_affinity_score",
    ),
    (
        "affinity assessment with placement",
        lambda: _request(
            system_name_or_uuid=None,
            placement=Placement(),
            affinity_assessment=_assessment(),
        ),
        "affinity_assessment",
    ),
    (
        "affinity assessment for another LPAR",
        lambda: _request(affinity_assessment=_assessment(lpar_name="web2")),
        "affinity_assessment",
    ),
    (
        "power_on with install",
        lambda: _install_request(_built(), power_on=True),
        "power_on",
    ),
    ("unknown boot", lambda: _request(boot="later"), "boot"),
    ("deferred boot without install", lambda: _request(boot="deferred"), "boot"),
]


@pytest.mark.parametrize(
    ("build", "fragment"),
    [pytest.param(build, fragment, id=case) for case, build, fragment in _REJECTED],
)
def test_check_request_rejects(build: Any, fragment: str) -> None:
    with pytest.raises(ValueError, match=r".") as info:
        check_request(build())
    message = str(info.value)
    assert fragment in message
    assert "AAAAC3NzaC1lZDI1NTE5" not in message
    assert "secret-path" not in message
    assert "producer-secret" not in message


def test_check_request_accepts_valid_requests() -> None:
    for request in (
        _request(),
        _request(system_name_or_uuid=None, placement=Placement()),
        _request(system_name_or_uuid=None, placement=Placement(systems=("a", "b"))),
        _request(affinity_assessment=_assessment(), caller_token="ticket-42"),
        _request(storage=_storage(vios_uuid=_uuid(5), vg_uuid=_uuid(6))),
        _request(storage=_storage(kind="PhysicalVolume", vios_uuid=_uuid(5))),
        _install_request(_built()),
        _install_request(_built(), power_on=False),
        _install_request(_prepared(), boot="deferred"),
    ):
        assert check_request(request) == request


def test_check_request_normalizes_blank_selectors() -> None:
    request = _request(
        system_name_or_uuid=" ",
        placement=Placement(),
        storage=_storage(vios_uuid="", vg_uuid=""),
    )
    normalized = check_request(request)
    assert normalized.system_name_or_uuid is None
    assert normalized.storage.vios_uuid is None
    assert normalized.storage.vg_uuid is None
    install = _install_request(
        _built(), adapters=PlanAdapters(port_vlan_id=100, mac="")
    )
    assert check_request(install).adapters.mac is None
    with pytest.raises(ValueError, match="caller"):
        check_request(_request(caller_token=""))


def _targets(
    system: int = 1, vios: int | None = 5, group: int | None = 6
) -> PlanTargets:
    return PlanTargets(
        system=PlanSystem(id=f"lab/{_uuid(system)}", uuid=_uuid(system), name="sys1"),
        vios=PlanResource(uuid=_uuid(vios), name="vios1") if vios else None,
        volume_group=PlanResource(uuid=_uuid(group), name="rootvg") if group else None,
    )


def test_plan_digest_ignores_selector_spelling_and_placement() -> None:
    targets = _targets()
    by_name = plan_digest(_request(), targets, "lab")
    by_uuid = plan_digest(
        _request(system_name_or_uuid=_uuid(1).upper()), targets, "lab"
    )
    by_placement = plan_digest(
        _request(system_name_or_uuid=None, placement=Placement(systems=("sys1",))),
        targets,
        "lab",
    )
    named_storage = plan_digest(
        _request(storage=_storage(vios_uuid=_uuid(5).upper(), vg_uuid=_uuid(6))),
        targets,
        "lab",
    )
    assert by_name == by_uuid == by_placement == named_storage
    blank = _install_request(_built(), adapters=PlanAdapters(port_vlan_id=100, mac=""))
    absent = _install_request(_built())
    assert plan_digest(blank, targets, "lab") == plan_digest(absent, targets, "lab")


_CHANGES: list[tuple[str, Any]] = [
    ("name", lambda: _request(name="web2")),
    ("vlan", lambda: _request(adapters=PlanAdapters(port_vlan_id=101))),
    ("storage name", lambda: _request(storage=_storage(storage_name="other"))),
    ("kind", lambda: _request(storage=_storage(kind="PhysicalVolume"))),
    ("capacity", lambda: _request(storage=_storage(capacity_mib=1024))),
    (
        "resources",
        lambda: _request(
            resources=LparResources(desired_memory=8192, desired_procs=0.5)
        ),
    ),
    ("partition type", lambda: _request(partition_type="OS400")),
    (
        "dedicated assignment",
        lambda: _request(
            assignments=LparPcieAssignments(
                dedicated=(DedicatedPcieAssignment("default", "21010001"),)
            )
        ),
    ),
    (
        "sriov assignment",
        lambda: _request(
            assignments=LparPcieAssignments(
                sriov=(
                    SriovLogicalPortAssignment(
                        "default", "1", "0", "2", Decimal("2.5")
                    ),
                )
            )
        ),
    ),
    ("caller token", lambda: _request(caller_token="ticket-42")),
    (
        "minimum affinity",
        lambda: _request(
            minimum_affinity_policy=MinimumAffinityPolicy(
                min_affinity_score=50, min_affinity_score_action="warn"
            )
        ),
    ),
    ("affinity assessment", lambda: _request(affinity_assessment=_assessment())),
    ("power_on", lambda: _request(power_on=False)),
    ("writer window", lambda: _request(exclusive_writer_window=True)),
]
_INSTALL_CHANGES: list[tuple[str, Any]] = [
    ("install profile", lambda: _install_request(_built(profile="rocky-9.8"))),
    (
        "address",
        lambda: _install_request(_built(network=_network(address="10.0.0.6/24"))),
    ),
    ("key", lambda: _install_request(_built(ssh_authorized_keys=(_KEY + "2",)))),
    ("media mode", lambda: _install_request(_prepared())),
    ("boot", lambda: _install_request(_built(), boot="deferred")),
]


@pytest.mark.parametrize(
    "build", [pytest.param(build, id=case) for case, build in _CHANGES]
)
def test_plan_digest_changes_with_each_field(build: Any) -> None:
    assert plan_digest(build(), _targets(), "lab") != plan_digest(
        _request(), _targets(), "lab"
    )


@pytest.mark.parametrize(
    "build", [pytest.param(build, id=case) for case, build in _INSTALL_CHANGES]
)
def test_plan_digest_changes_with_each_install_field(build: Any) -> None:
    base = plan_digest(_install_request(_built()), _targets(), "lab")
    assert plan_digest(build(), _targets(), "lab") != base


@pytest.mark.parametrize(
    ("targets", "connection"),
    [
        pytest.param(_targets(), "other", id="connection"),
        pytest.param(_targets(system=2), "lab", id="system"),
        pytest.param(_targets(vios=7), "lab", id="vios"),
        pytest.param(_targets(group=8), "lab", id="volume group"),
    ],
)
def test_plan_digest_changes_with_targets(
    targets: PlanTargets, connection: str
) -> None:
    assert plan_digest(_request(), targets, connection) != plan_digest(
        _request(), _targets(), "lab"
    )


def test_plan_digest_is_pinned() -> None:
    # A SHA-256 digest, not a credential.
    expected = "32f5ecbbdc8c604c44e916cfc6d56bcd3fdf004121aba91b74730e8d988cab9d"  # pragma: allowlist secret
    assert plan_digest(_request(), _targets(), "lab") == expected
