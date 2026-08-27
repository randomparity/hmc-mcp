"""Direct CLI command tests via ``typer.testing.CliRunner``.

The REST-backed commands all funnel through ``cli_app._client`` →
``cli_app.client_from_env`` (imported at ``cli_app.py``), and the SSH-backed
commands through ``ssh_commands.run_hmc_command``. These tests monkeypatch those
two factories so every command runs against a scripted fake — no HTTP, no SSH.

This closes the CLI blind spot where only the helpers (``_ssh_config``,
``_output``) were exercised and no command body was ever invoked, so the
highest-complexity commands (lpars power/create/modify/delete, storage
create-vg/create-disk) had zero direct coverage.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import typer
from click import unstyle
from unittest.mock import AsyncMock
from typer.main import get_command
from typer.testing import CliRunner

from hmc_mcp import cli, cli_app, cli_lpars, operations_lpar, ssh_commands
from hmc_mcp.config import HMCConfig
from hmc_mcp.errors import HMCError
from hmc_mcp.operations_ssh_network import VnicChangeResult, VnicPartialError

LPAR_NAME = "lpar1"
LPAR_UUID = "11111111-1111-4111-8111-111111111111"
SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"
VG_UUID = "33333333-3333-4333-8333-333333333333"
VIOS_UUID = "44444444-4444-4444-8444-444444444444"
JOB_UUID = "55555555-5555-4555-8555-555555555555"
CLUSTER_UUID = "77777777-7777-4777-8777-777777777777"
SSP_UUID = "88888888-8888-4888-8888-888888888888"
TEMPLATE_UUID = "99999999-9999-4999-8999-999999999999"

RUNNER = CliRunner()


class FakeHMC:
    """Scripted async-context-manager stand-in for :class:`HMCClient`.

    Each method records its call on ``calls`` (name, args, kwargs) and returns
    canned data; set ``fail_on`` to a method name to make that method raise
    :class:`HMCError`, exercising the CLI's ``_fail`` error path.
    """

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.config = HMCConfig(host="hmc.test", user="user", _env_file=None)
        self.fail_on: str | None = None
        self.fail_status = 500
        self.lpar = {
            "UUID": LPAR_UUID,
            "Resource": {
                "PartitionName": LPAR_NAME,
                "PartitionID": 1,
                "PartitionState": "running",
                "PartitionType": "AIX/Linux",
                "OperatingSystemVersion": "7.2",
                "ResourceMonitoringControlState": "active",
            },
        }
        self.vg = {
            "UUID": VG_UUID,
            "Resource": {
                "GroupName": "rootvg",
                "FreeSpaceInMBytes": "5120",
                "GroupCapacity": "102400",
            },
        }
        self.disk = {
            "UUID": "66666666-6666-4666-8666-666666666666",
            "Resource": {"DiskName": "bootvol"},
        }
        self.job = {
            "UUID": JOB_UUID,
            "link": f"/jobs/{JOB_UUID}",
            "Resource": {"JobName": "PowerOn", "Status": "running"},
        }
        self.system = {
            "UUID": SYSTEM_UUID,
            "Resource": {
                "SystemName": "sys1",
                "State": "operating",
                "MachineTypeModelSerialNumber": "9119-MHE",
                "IPAddress": "10.0.0.1",
            },
        }
        self.vios = {
            "UUID": VIOS_UUID,
            "Resource": {
                "PartitionName": "vios1",
                "PartitionID": 2,
                "PartitionState": "running",
                "IOSLevel": "3.1.0",
            },
        }
        self.console = {
            "link": "https://hmc/rest/api/uom/ManagementConsole/console",
            "Resource": {"VersionInfo": "V10R1M1010", "ManagementConsoleName": "hmc1"},
        }
        self.cluster = {"UUID": CLUSTER_UUID, "Resource": {"ClusterName": "cl1"}}
        self.ssp = {
            "UUID": SSP_UUID,
            "Resource": {
                "StoragePoolName": "pool1",
                "Capacity": "1024",
                "FreeSpace": "512",
            },
        }
        self.template = {"UUID": TEMPLATE_UUID, "Resource": {"templateName": "tpl1"}}
        self.vios_storage_detail = {"Resource": {}}
        self.pcm_prefs = {"LongTermMonitorEnabled": True, "AggregationEnabled": False}
        self.metric_links = [
            {
                "link": "/rest/api/pcm/ManagedSystem/xxx/ProcessedMetrics/1",
                "title": "m1",
            }
        ]
        self.metrics_json = {"data": [1, 2, 3]}
        self.fetch_json_404 = False
        self.wait_job_status = "COMPLETED"

    def _record(self, name: str, *args, **kwargs) -> None:
        self.calls.append((name, args, kwargs))
        if self.fail_on == name:
            raise HMCError(
                f"simulated {name} failure",
                self.fail_status,
                "<xml><Message>boom</Message></xml>",
            )

    async def __aenter__(self) -> "FakeHMC":
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None

    async def list_logical_partitions(self, system_uuid=None):
        self._record("list_logical_partitions", system_uuid)
        return [self.lpar]

    async def search_uom(self, resource_type, property_name, value):
        self._record("search_uom", resource_type, property_name, value)
        return [self.lpar]

    async def get_logical_partition(self, uuid):
        self._record("get_logical_partition", uuid)
        return self.lpar

    async def find_partition_by_name(self, name, system_uuid=None):
        if system_uuid is None:
            self._record("find_partition_by_name", name)
        else:
            self._record("find_partition_by_name", name, system_uuid=system_uuid)
        return self.lpar if name == LPAR_NAME else None

    async def get_quick_property(self, resource_type, uuid, property_name):
        self._record("get_quick_property", resource_type, uuid, property_name)
        return self.lpar["Resource"]["PartitionState"]

    async def submit_job(self, job_path, job_request_xml):
        self._record("submit_job", job_path, job_request_xml)
        return self.job

    async def create_logical_partition(self, system_uuid, xml):
        self._record("create_logical_partition", system_uuid, xml)
        return self.lpar

    async def modify_logical_partition(self, lpar_uuid, xml):
        self._record("modify_logical_partition", lpar_uuid, xml)
        return self.lpar

    async def delete_logical_partition(self, lpar_uuid):
        self._record("delete_logical_partition", lpar_uuid)

    async def lpar_migrate(self, lpar_uuid, target, profile=None, *, wait_time=None):
        self._record("lpar_migrate", lpar_uuid, target, profile, wait_time=wait_time)
        return self.job

    async def lpar_migrate_validate(
        self, lpar_uuid, target, profile=None, *, wait_time=None
    ):
        self._record(
            "lpar_migrate_validate", lpar_uuid, target, profile, wait_time=wait_time
        )
        return self.job

    async def lpar_migrate_abort(self, lpar_uuid):
        self._record("lpar_migrate_abort", lpar_uuid)
        return self.job

    async def lpar_migrate_recover(self, lpar_uuid):
        self._record("lpar_migrate_recover", lpar_uuid)
        return self.job

    async def lpar_remote_restart(self, lpar_uuid, operation, source, **kwargs):
        self._record("lpar_remote_restart", lpar_uuid, operation, source, **kwargs)
        return self.job

    async def create_volume_group(self, vios_uuid, name, physical_volumes):
        self._record("create_volume_group", vios_uuid, name, physical_volumes)
        return self.vg

    async def create_virtual_disk(self, vios_uuid, vg_uuid, disk_name, capacity_mib):
        self._record("create_virtual_disk", vios_uuid, vg_uuid, disk_name, capacity_mib)
        return self.disk

    async def list_adapters(self, lpar_uuid, adapter_type):
        self._record("list_adapters", lpar_uuid, adapter_type)
        return [{"UUID": "adapter-1", "Resource": {"PortVLANID": "100"}}]

    async def add_network_adapter(self, lpar_uuid, vlan, slot, vswitch, tagged, mac):
        self._record("add_network_adapter", lpar_uuid, vlan, slot, vswitch, tagged, mac)
        return {"UUID": "adapter-1"}

    async def add_vscsi_adapter(
        self, lpar_uuid, vios_partition_id, vios_slot, slot_number=None
    ):
        self._record(
            "add_vscsi_adapter",
            lpar_uuid,
            vios_partition_id,
            vios_slot,
            slot_number,
        )
        return {"UUID": "vscsi-adapter-1"}

    async def delete_adapter(self, lpar_uuid, adapter_type, adapter_uuid):
        self._record("delete_adapter", lpar_uuid, adapter_type, adapter_uuid)

    async def list_virtual_networks(self, system_uuid):
        self._record("list_virtual_networks", system_uuid)
        return [{"UUID": "network-1", "Resource": {"NetworkName": "prod"}}]

    async def list_virtual_switches(self, system_uuid):
        self._record("list_virtual_switches", system_uuid)
        return [{"UUID": "switch-1", "Resource": {"SwitchName": "ETHERNET0"}}]

    async def list_network_bridges(self, system_uuid):
        self._record("list_network_bridges", system_uuid)
        return [{"UUID": "bridge-1", "Resource": {"PortVLANID": "1"}}]

    async def create_virtual_network(self, system_uuid, name, vlan, vswitch, *, tagged):
        self._record(
            "create_virtual_network", system_uuid, name, vlan, vswitch, tagged=tagged
        )
        return {"UUID": "network-1"}

    async def delete_virtual_network(self, system_uuid, network_uuid):
        self._record("delete_virtual_network", system_uuid, network_uuid)

    async def list_volume_groups(self, vios_uuid):
        self._record("list_volume_groups", vios_uuid)
        return [self.vg]

    async def map_storage_to_lpar(self, vios_uuid, kind, disk, lpar_uuid, target):
        self._record("map_storage_to_lpar", vios_uuid, kind, disk, lpar_uuid, target)
        return {"UUID": "mapping-1"}

    async def create_media_repository(self, vios_uuid, vg_uuid, size_mib):
        self._record("create_media_repository", vios_uuid, vg_uuid, size_mib)
        return {"UUID": "repo-1"}

    async def delete_media_repository(self, vios_uuid, vg_uuid):
        self._record("delete_media_repository", vios_uuid, vg_uuid)

    async def list_optical_media(self, vios_uuid, vg_uuid):
        return []

    async def raw_post(self, path, body, content_type="application/xml"):
        self._record("raw_post", path, body, content_type)
        return "<ok/>"

    # -- systems --------------------------------------------------------- #
    async def list_managed_systems(self):
        self._record("list_managed_systems")
        return [self.system]

    async def get_managed_system(self, uuid):
        self._record("get_managed_system", uuid)
        return self.system if uuid == SYSTEM_UUID else None

    async def find_system_by_name(self, name):
        self._record("find_system_by_name", name)
        return self.system if name == "sys1" else None

    async def wait_for_job(
        self,
        job_uuid,
        timeout_seconds=300,
        poll_interval=5,
        *,
        job_href=None,
    ):
        self._record(
            "wait_for_job",
            job_uuid,
            timeout_seconds,
            poll_interval,
            job_href=job_href,
        )
        return {
            **self.job,
            "Resource": {**self.job["Resource"], "Status": self.wait_job_status},
        }

    async def power_on_system(self, system_uuid):
        self._record("power_on_system", system_uuid)
        return self.job

    async def power_off_system(self, system_uuid, immediate=False):
        self._record("power_off_system", system_uuid, immediate)
        return self.job

    # -- vios ------------------------------------------------------------ #
    async def list_vios(self, system_uuid=None):
        self._record("list_vios", system_uuid)
        return [self.vios]

    async def get_vios_storage_detail(self, vios_uuid):
        self._record("get_vios_storage_detail", vios_uuid)
        return self.vios_storage_detail

    async def power_on_vios(self, vios_uuid):
        self._record("power_on_vios", vios_uuid)
        return self.job

    async def power_off_vios(self, vios_uuid, immediate=False):
        self._record("power_off_vios", vios_uuid, immediate)
        return self.job

    # -- console --------------------------------------------------------- #
    async def get_console_info(self):
        self._record("get_console_info")
        return self.console

    # -- cluster / SSP --------------------------------------------------- #
    async def list_clusters(self):
        self._record("list_clusters")
        return [self.cluster]

    async def list_shared_storage_pools(self):
        self._record("list_shared_storage_pools")
        return [self.ssp]

    async def create_logical_unit(
        self,
        cluster_uuid,
        lu_name,
        lu_size_gib,
        lu_type="THIN",
        device_type="VirtualIO_Disk",
        cloned_from=None,
    ):
        self._record(
            "create_logical_unit",
            cluster_uuid,
            lu_name,
            lu_size_gib,
            lu_type,
            device_type,
            cloned_from,
        )
        return self.job

    async def delete_logical_unit(self, cluster_uuid, lu_udid):
        self._record("delete_logical_unit", cluster_uuid, lu_udid)
        return self.job

    # -- templates ------------------------------------------------------- #
    async def list_partition_templates(self):
        self._record("list_partition_templates")
        return [self.template]

    async def get_partition_template(self, template_uuid):
        self._record("get_partition_template", template_uuid)
        if template_uuid != TEMPLATE_UUID:
            raise HMCError("GET partition template failed", 404, "not found")
        return self.template

    async def deploy_partition_template(self, draft_template_uuid, target_system_uuid):
        self._record(
            "deploy_partition_template", draft_template_uuid, target_system_uuid
        )
        return self.job

    # -- jobs ------------------------------------------------------------ #
    async def get_job(self, job_uuid, *, job_href=None):
        self._record("get_job", job_uuid, job_href=job_href)
        return self.job if job_uuid == JOB_UUID else None

    # -- pcm metrics ----------------------------------------------------- #
    async def get_pcm_preferences(self, category, uuid):
        self._record("get_pcm_preferences", category, uuid)
        return self.pcm_prefs

    async def set_pcm_preferences(self, category, uuid, **flags):
        self._record("set_pcm_preferences", category, uuid, **flags)

    async def get_processed_metric_links(
        self,
        category,
        uuid,
        start_ts,
        end_ts=None,
        no_of_samples=None,
        *,
        system_uuid=None,
    ):
        kwargs = {"system_uuid": system_uuid} if system_uuid is not None else {}
        self._record(
            "get_processed_metric_links",
            category,
            uuid,
            start_ts,
            end_ts,
            no_of_samples,
            **kwargs,
        )
        return self.metric_links

    async def get_aggregated_metric_links(
        self,
        category,
        uuid,
        start_ts,
        end_ts=None,
        no_of_samples=None,
        *,
        system_uuid=None,
    ):
        kwargs = {"system_uuid": system_uuid} if system_uuid is not None else {}
        self._record(
            "get_aggregated_metric_links",
            category,
            uuid,
            start_ts,
            end_ts,
            no_of_samples,
            **kwargs,
        )
        return self.metric_links

    async def fetch_json(self, link):
        self._record("fetch_json", link)
        if self.fetch_json_404:
            raise HMCError("GET failed", 404, "not found")
        return self.metrics_json


@pytest.fixture
def fake_hmc(monkeypatch):
    """Wire the CLI's client factory to a scripted FakeHMC for every command."""
    hmc = FakeHMC()
    monkeypatch.setattr(cli_app, "client_from_env", lambda **kwargs: hmc)

    async def legacy_description(*_args):
        return "legacy partition"

    async def stamped(*_args, **_kwargs):
        return "[hmc-mcp owner:hmc-mcp created:2026-08-14]"

    monkeypatch.setattr(operations_lpar, "get_lpar_description", legacy_description)
    monkeypatch.setattr(operations_lpar, "stamp_lpar_ownership", stamped)
    return hmc


def test_connection_options_do_not_leak_between_invocations(monkeypatch):
    seen = []
    hmc = FakeHMC()

    def client_factory(**kwargs):
        seen.append(kwargs)
        return hmc

    monkeypatch.setattr(cli_app, "client_from_env", client_factory)

    first = RUNNER.invoke(
        cli.app,
        ["--host", "first-hmc", "--user", "first-user", "lpars", "list"],
    )
    second = RUNNER.invoke(cli.app, ["lpars", "list"])

    assert first.exit_code == second.exit_code == 0
    assert seen[0]["host"] == "first-hmc"
    assert seen[0]["user"] == "first-user"
    assert seen[1]["host"] is None
    assert seen[1]["user"] is None


@pytest.mark.parametrize(
    ("args", "expected_call", "output"),
    [
        (
            ["adapters", "list", LPAR_UUID, "--json"],
            ("list_adapters", (LPAR_UUID, "ClientNetworkAdapter"), {}),
            "adapter-1",
        ),
        (
            [
                "adapters",
                "add-network",
                LPAR_UUID,
                "--vlan",
                "100",
                "--slot",
                "4",
                "--yes",
            ],
            ("add_network_adapter", (LPAR_UUID, 100, 4, None, False, None), {}),
            "Added network adapter",
        ),
        (
            [
                "adapters",
                "delete",
                LPAR_UUID,
                "--type",
                "ClientNetworkAdapter",
                "--uuid",
                "adapter-1",
                "--yes",
            ],
            ("delete_adapter", (LPAR_UUID, "ClientNetworkAdapter", "adapter-1"), {}),
            "Deleted ClientNetworkAdapter",
        ),
        (
            ["network", "list-switches", SYSTEM_UUID, "--json"],
            ("list_virtual_switches", (SYSTEM_UUID,), {}),
            "switch-1",
        ),
        (
            ["network", "list-networks", SYSTEM_UUID, "--json"],
            ("list_virtual_networks", (SYSTEM_UUID,), {}),
            "network-1",
        ),
        (
            [
                "network",
                "create",
                SYSTEM_UUID,
                "--name",
                "prod",
                "--vlan",
                "100",
                "--virtual-switch-id",
                "2",
                "--tagged",
                "--yes",
            ],
            ("create_virtual_network", (SYSTEM_UUID, "prod", 100, 2), {"tagged": True}),
            "Created virtual network",
        ),
        (
            ["network", "delete", SYSTEM_UUID, "--uuid", "network-1", "--yes"],
            ("delete_virtual_network", (SYSTEM_UUID, "network-1"), {}),
            "Deleted virtual network",
        ),
        (
            ["network", "list-bridges", SYSTEM_UUID, "--json"],
            ("list_network_bridges", (SYSTEM_UUID,), {}),
            "bridge-1",
        ),
        (
            ["storage", "list-vgs", VIOS_UUID, "--json"],
            ("list_volume_groups", (VIOS_UUID,), {}),
            VG_UUID,
        ),
        (
            [
                "storage",
                "map",
                VIOS_UUID,
                "--lpar",
                LPAR_UUID,
                "--disk",
                "bootvol",
                "--target",
                "vtscsi0",
                "--yes",
            ],
            (
                "map_storage_to_lpar",
                (VIOS_UUID, "VirtualDisk", "bootvol", LPAR_UUID, "vtscsi0"),
                {},
            ),
            "Mapped 'bootvol'",
        ),
        (
            [
                "storage",
                "create-media-repo",
                VIOS_UUID,
                VG_UUID,
                "--size-mib",
                "2048",
                "--yes",
            ],
            ("create_media_repository", (VIOS_UUID, VG_UUID, 2048), {}),
            "Created media repository",
        ),
        (
            ["storage", "delete-media-repo", VIOS_UUID, VG_UUID, "--yes"],
            ("delete_media_repository", (VIOS_UUID, VG_UUID), {}),
            "Deleted media repository",
        ),
    ],
)
def test_cli_command_wiring_matrix(fake_hmc, args, expected_call, output):
    result = RUNNER.invoke(cli.app, args)

    assert result.exit_code == 0, result.output
    assert expected_call in fake_hmc.calls
    assert output in result.stdout


def test_network_cli_translates_create_rejection(fake_hmc):
    fake_hmc.fail_on = "create_virtual_network"
    fake_hmc.fail_status = 406
    result = RUNNER.invoke(
        cli.app,
        [
            "network",
            "create",
            SYSTEM_UUID,
            "--name",
            "prod",
            "--vlan",
            "100",
            "--virtual-switch-id",
            "2",
            "--yes",
        ],
    )
    assert result.exit_code == 1
    assert "virtual network create request" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        [
            "adapters",
            "delete",
            LPAR_UUID,
            "--type",
            "ClientNetworkAdapter",
            "--uuid",
            "adapter-1",
        ],
        ["network", "delete", SYSTEM_UUID, "--uuid", "network-1"],
        ["storage", "delete-media-repo", VIOS_UUID, VG_UUID],
    ],
)
def test_destructive_cli_commands_abort_without_confirmation(fake_hmc, args):
    result = RUNNER.invoke(cli.app, args, input="n\n")

    assert result.exit_code == 1
    assert "Aborted" in result.output
    assert fake_hmc.calls == []


# --------------------------------------------------------------------------- #
# lpars list / show / state
# --------------------------------------------------------------------------- #


def test_lpars_list_table(fake_hmc):
    result = RUNNER.invoke(cli.app, ["lpars", "list"])

    assert result.exit_code == 0
    assert LPAR_NAME in result.stdout
    assert "running" in result.stdout
    assert fake_hmc.calls == [("list_logical_partitions", (None,), {})]


def test_lpars_list_json(fake_hmc):
    result = RUNNER.invoke(cli.app, ["lpars", "list", "--json"])

    assert result.exit_code == 0
    assert LPAR_UUID in result.stdout
    assert fake_hmc.calls == [("list_logical_partitions", (None,), {})]


def test_lpars_list_state_filter(fake_hmc):
    result = RUNNER.invoke(cli.app, ["lpars", "list", "--state", "running"])

    assert result.exit_code == 0
    assert LPAR_NAME in result.stdout
    assert fake_hmc.calls == [
        ("search_uom", ("LogicalPartition", "PartitionState", "running"), {})
    ]


def test_lpars_summary_renders_numeric_zero(monkeypatch):
    summary = {
        "name": "zero-lpar",
        "current_memory_mb": 0,
        "desired_memory_mb": 0,
        "current_proc_units": 0.0,
        "desired_proc_units": 0.0,
    }
    monkeypatch.setattr("hmc_mcp.cli_lpars._run", lambda _operation: summary)

    result = RUNNER.invoke(cli.app, ["lpars", "summary", "zero-lpar"])

    assert result.exit_code == 0
    assert "Current Memory (MiB)" in result.stdout
    assert "│ 0" in result.stdout
    assert "Current Proc Units" in result.stdout
    assert "0.0" in result.stdout


def test_lpars_show_by_uuid(fake_hmc):
    result = RUNNER.invoke(cli.app, ["lpars", "show", LPAR_UUID])

    assert result.exit_code == 0
    assert LPAR_NAME in result.stdout
    assert fake_hmc.calls == [("get_logical_partition", (LPAR_UUID,), {})]


def test_lpars_show_by_name_resolves(fake_hmc):
    result = RUNNER.invoke(cli.app, ["lpars", "show", LPAR_NAME])

    assert result.exit_code == 0
    assert LPAR_UUID in result.stdout
    assert fake_hmc.calls == [("find_partition_by_name", (LPAR_NAME,), {})]


def test_lpars_show_not_found_exits_1(fake_hmc):
    result = RUNNER.invoke(cli.app, ["lpars", "show", "ghost"])

    assert result.exit_code == 1
    assert "not found" in result.stderr
    assert fake_hmc.calls == [("find_partition_by_name", ("ghost",), {})]


def test_lpars_state_entry_without_uuid_reports_not_found(fake_hmc, monkeypatch):
    """A resolved entry that lacks a UUID is a miss (None), never an empty
    string — an empty UUID must not flow into the REST path."""

    async def found_no_uuid(name):
        fake_hmc._record("find_partition_by_name", name)
        return {"Resource": {"PartitionName": LPAR_NAME}}

    monkeypatch.setattr(fake_hmc, "find_partition_by_name", found_no_uuid)
    result = RUNNER.invoke(cli.app, ["lpars", "state", LPAR_NAME])

    assert result.exit_code == 1
    assert "not found" in result.stderr
    assert fake_hmc.calls == [("find_partition_by_name", (LPAR_NAME,), {})]


def test_lpars_state(fake_hmc):
    result = RUNNER.invoke(cli.app, ["lpars", "state", LPAR_UUID])

    assert result.exit_code == 0
    assert result.stdout.strip() == "running"
    assert fake_hmc.calls == [
        ("get_quick_property", ("LogicalPartition", LPAR_UUID, "PartitionState"), {})
    ]


# --------------------------------------------------------------------------- #
# lpars power (jobs)
# --------------------------------------------------------------------------- #


def test_lpars_power_on_submits_power_on_job(fake_hmc):
    result = RUNNER.invoke(
        cli.app, ["lpars", "power-on", LPAR_UUID, "--force", "--yes"]
    )

    assert result.exit_code == 0
    assert "Job submitted" in result.stdout
    assert [c[0] for c in fake_hmc.calls] == ["submit_job"]
    path, job_xml = fake_hmc.calls[0][1]
    assert path == f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/PowerOn"
    assert "PowerOn</OperationName>" in job_xml


def test_lpars_power_on_skips_running_partition_without_force(fake_hmc):
    result = RUNNER.invoke(cli.app, ["lpars", "power-on", LPAR_UUID, "--yes"])

    assert result.exit_code == 0
    assert "already running" in result.stdout
    assert [call[0] for call in fake_hmc.calls] == ["get_quick_property"]


def test_lpars_power_off_resolves_name_then_submits(fake_hmc):
    result = RUNNER.invoke(cli.app, ["lpars", "power-off", LPAR_NAME, "--yes"])

    assert result.exit_code == 0
    assert "Job submitted" in result.stdout
    names = [c[0] for c in fake_hmc.calls]
    assert names == ["find_partition_by_name", "submit_job"]
    path = fake_hmc.calls[1][1][0]
    assert "/do/PowerOff" in path
    assert LPAR_UUID in path


def test_lpars_power_off_declined_confirm_aborts(fake_hmc):
    result = RUNNER.invoke(cli.app, ["lpars", "power-off", LPAR_UUID], input="n\n")

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
    # No job submitted — the confirm gates the destructive call. A UUID needs
    # no resolution, so the fake client was never called.
    assert fake_hmc.calls == []


@pytest.mark.parametrize(
    "command",
    [
        ["lpars", "power-on", LPAR_UUID, "--force"],
        ["systems", "power-on", SYSTEM_UUID],
        ["systems", "power-off", SYSTEM_UUID],
        ["vios", "power-on", VIOS_UUID],
        ["vios", "power-off", VIOS_UUID],
    ],
)
def test_power_commands_forward_submission_link_when_waiting(fake_hmc, command):
    result = RUNNER.invoke(
        cli.app,
        [*command, "--yes", "--wait", "--timeout", "90", "--interval", "3"],
    )

    assert result.exit_code == 0
    assert fake_hmc.calls[-1] == (
        "wait_for_job",
        (JOB_UUID, 90, 3),
        {"job_href": f"/jobs/{JOB_UUID}"},
    )


def test_lpars_power_off_confirms_public_selector_then_submits(fake_hmc):
    result = RUNNER.invoke(cli.app, ["lpars", "power-off", LPAR_NAME], input="y\n")

    assert result.exit_code == 0
    assert "Job submitted" in result.stdout
    names = [c[0] for c in fake_hmc.calls]
    assert names == ["find_partition_by_name", "submit_job"]
    path = fake_hmc.calls[1][1][0]
    assert "/do/PowerOff" in path
    assert LPAR_UUID in path


# --------------------------------------------------------------------------- #
# lpars create / modify / delete
# --------------------------------------------------------------------------- #


def test_lpars_create(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["lpars", "create", "newlpar", "--system", SYSTEM_UUID, "--yes"],
    )

    assert result.exit_code == 0
    assert "Created LPAR 'newlpar'" in result.stdout
    assert fake_hmc.calls[0] == ("find_partition_by_name", ("newlpar",), {})
    name, args, _ = fake_hmc.calls[1]
    assert name == "create_logical_partition"
    assert args[0] == SYSTEM_UUID
    assert "newlpar" in args[1]  # the partition XML carries the name


def test_lpars_create_declined_confirm_aborts(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["lpars", "create", "newlpar", "--system", SYSTEM_UUID],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
    assert fake_hmc.calls == []


def test_lpars_create_rejects_duplicate_name(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["lpars", "create", LPAR_NAME, "--system", SYSTEM_UUID, "--yes"],
    )

    assert result.exit_code == 1
    assert "already exists" in result.stderr
    assert fake_hmc.calls == [("find_partition_by_name", (LPAR_NAME,), {})]


def test_lpars_create_rejects_invalid_partition_type_before_client_call(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "lpars",
            "create",
            "newlpar",
            "--system",
            SYSTEM_UUID,
            "--type",
            "Windows",
            "--yes",
        ],
    )

    assert result.exit_code == 2
    assert "--type must be one of" in result.stderr
    assert fake_hmc.calls == []


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("--type", "Windows", "--type must be one of"),
        ("--storage-kind", "Tape", "--storage-kind must be one of"),
    ],
)
def test_lpars_provision_rejects_invalid_vocabulary_before_client_call(
    fake_hmc, option, value, message
):
    result = RUNNER.invoke(
        cli.app,
        [
            "lpars",
            "provision",
            "--system",
            SYSTEM_UUID,
            "--name",
            "newlpar",
            "--vlan",
            "100",
            "--vios-uuid",
            VIOS_UUID,
            "--vios-partition-id",
            "2",
            "--vios-slot",
            "10",
            "--storage-name",
            "rootvg",
            option,
            value,
            "--yes",
        ],
    )

    assert result.exit_code == 2
    assert message in result.stderr
    assert fake_hmc.calls == []


def test_lpars_modify_renames(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "lpars",
            "modify",
            LPAR_UUID,
            "--system",
            SYSTEM_UUID,
            "--name",
            "renamed",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert "Modified LPAR" in result.stdout
    name, args, _ = fake_hmc.calls[-1]
    assert name == "modify_logical_partition"
    assert args[0] == LPAR_UUID
    assert "renamed" in args[1]


def test_lpars_modify_rename_requires_system_before_client_use(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["lpars", "modify", LPAR_UUID, "--name", "renamed", "--yes"],
    )

    assert result.exit_code == 2
    assert "--system is required when renaming an LPAR" in result.stderr
    assert fake_hmc.calls == []


def test_lpars_modify_with_no_options_exits_2(fake_hmc):
    result = RUNNER.invoke(cli.app, ["lpars", "modify", LPAR_UUID])

    assert result.exit_code == 2
    assert "Error:" in result.stderr
    assert "Nothing to change" in result.stderr
    assert fake_hmc.calls == []


def test_lpars_modify_procs_only_keeps_sharing_mode(fake_hmc):
    """A proc-only modify without --dedicated/--capped must not flip the mode."""
    result = RUNNER.invoke(
        cli.app,
        ["lpars", "modify", LPAR_UUID, "--procs", "0.5", "--yes"],
    )

    assert result.exit_code == 0
    body = fake_hmc.calls[0][1][1]
    assert "DesiredProcessingUnits" in body and ">0.5<" in body
    assert "HasDedicatedProcessors" not in body
    assert "SharingMode" not in body


def test_lpars_modify_dedicated_flag_sets_mode(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["lpars", "modify", LPAR_UUID, "--procs", "2", "--dedicated", "--yes"],
    )

    assert result.exit_code == 0
    body = fake_hmc.calls[0][1][1]
    assert "DedicatedProcessorConfiguration" in body
    assert "HasDedicatedProcessors" in body and ">true<" in body


def test_lpars_delete(fake_hmc):
    async def powered_off(*_args):
        return "not activated"

    fake_hmc.get_quick_property = powered_off
    result = RUNNER.invoke(
        cli.app,
        ["lpars", "delete", LPAR_UUID, "--system", SYSTEM_UUID, "--yes"],
    )

    assert result.exit_code == 0
    assert "Deleted LPAR" in result.stdout
    assert fake_hmc.calls[-1] == ("delete_logical_partition", (LPAR_UUID,), {})


def test_lpars_delete_rejects_running_partition(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["lpars", "delete", LPAR_UUID, "--system", SYSTEM_UUID, "--yes"],
    )

    assert result.exit_code == 1
    assert "must be 'not activated' to delete" in result.stderr
    assert all(call[0] != "delete_logical_partition" for call in fake_hmc.calls)


def test_lpars_delete_not_found_exits_1(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["lpars", "delete", "ghost", "--system", SYSTEM_UUID, "--yes"],
    )

    assert result.exit_code == 1
    assert "No LPAR named 'ghost' found" in result.stderr


def test_lpars_delete_denies_foreign_owned_partition_without_transport(
    fake_hmc, monkeypatch
):
    async def foreign_description(*_args):
        return "[hmc-mcp owner:other-agent created:2026-08-14]"

    monkeypatch.setattr(operations_lpar, "get_lpar_description", foreign_description)

    result = RUNNER.invoke(
        cli.app,
        ["lpars", "delete", LPAR_UUID, "--system", SYSTEM_UUID, "--yes"],
    )

    assert result.exit_code == 1
    assert "owned by" in result.stderr
    assert all(call[0] != "delete_logical_partition" for call in fake_hmc.calls)


def test_lpars_decommission_help_lists_cli_contract(fake_hmc):
    root_command = get_command(cli.app)
    decommission_command = root_command.commands["lpars"].commands["decommission"]
    option_names = {
        option
        for parameter in decommission_command.params
        for option in parameter.opts
        if option.startswith("--")
    }

    assert option_names == {
        "--system",
        "--dry-run",
        "--ownership-override",
        "--immediate",
        "--timeout-seconds",
        "--poll-interval",
        "--json",
        "--yes",
    }


def test_lpars_decommission_dry_run_skips_confirmation(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "lpars",
            "decommission",
            LPAR_NAME,
            "--system",
            SYSTEM_UUID,
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.stdout
    assert "power_off" in result.stdout
    assert "dry_run" in result.stdout
    assert "Aborted" not in result.stderr
    assert "delete_logical_partition" not in [name for name, _, _ in fake_hmc.calls]


def test_lpars_decommission_declined_confirmation_aborts(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["lpars", "decommission", LPAR_NAME, "--system", SYSTEM_UUID],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
    assert fake_hmc.calls == []


def test_lpars_decommission_confirmed_prompt_names_target_and_executes(fake_hmc):
    fake_hmc.lpar["Resource"]["PartitionState"] = "not activated"

    result = RUNNER.invoke(
        cli.app,
        ["lpars", "decommission", LPAR_NAME, "--system", SYSTEM_UUID],
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    assert (
        f"Decommission LPAR '{LPAR_NAME}' on system '{SYSTEM_UUID}'?" in result.output
    )
    assert "decommissioned successfully" in result.stdout
    names = [name for name, _, _ in fake_hmc.calls]
    assert "delete_logical_partition" in names
    assert "Aborted" not in result.stderr


def test_lpars_decommission_yes_executes_workflow(fake_hmc):
    fake_hmc.lpar["Resource"]["PartitionState"] = "not activated"

    result = RUNNER.invoke(
        cli.app,
        [
            "lpars",
            "decommission",
            LPAR_NAME,
            "--system",
            SYSTEM_UUID,
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "decommissioned successfully" in result.stdout
    names = [name for name, _, _ in fake_hmc.calls]
    assert "delete_logical_partition" in names
    assert "Aborted" not in result.stderr


def test_lpars_decommission_incomplete_human_result_exits_1_after_rendering(fake_hmc):
    fake_hmc.lpar["Resource"]["PartitionState"] = "not activated"
    fake_hmc.fail_on = "delete_logical_partition"

    result = RUNNER.invoke(
        cli.app,
        [
            "lpars",
            "decommission",
            LPAR_NAME,
            "--system",
            SYSTEM_UUID,
            "--yes",
        ],
    )

    assert result.exit_code == 1
    assert "was not fully decommissioned" in result.stdout
    assert "delete_lpar" in result.stdout
    assert "error" in result.stdout
    assert "simulated delete_logical_partition failure" in result.stdout


def test_lpars_decommission_incomplete_json_result_exits_1_after_rendering(fake_hmc):
    fake_hmc.lpar["Resource"]["PartitionState"] = "not activated"
    fake_hmc.fail_on = "delete_logical_partition"

    result = RUNNER.invoke(
        cli.app,
        [
            "lpars",
            "decommission",
            LPAR_NAME,
            "--system",
            SYSTEM_UUID,
            "--yes",
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["resource_deleted"] is False
    assert payload["workflow_completed"] is False
    assert payload["dry_run"] is False
    assert payload["steps"][-1]["step"] == "delete_lpar"
    assert payload["steps"][-1]["status"] == "error"
    assert (
        "simulated delete_logical_partition failure" in payload["steps"][-1]["result"]
    )


def test_lpars_decommission_denies_foreign_owned_partition(fake_hmc, monkeypatch):
    async def foreign_description(*_args):
        return "[hmc-mcp owner:other-agent created:2026-08-14]"

    monkeypatch.setattr(operations_lpar, "get_lpar_description", foreign_description)

    result = RUNNER.invoke(
        cli.app,
        [
            "lpars",
            "decommission",
            LPAR_UUID,
            "--system",
            SYSTEM_UUID,
            "--yes",
        ],
    )

    assert result.exit_code == 1
    assert "owned by" in result.stderr
    assert "delete_logical_partition" not in [name for name, _, _ in fake_hmc.calls]


def test_lpars_decommission_json_renders_dataclass_shape(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "lpars",
            "decommission",
            LPAR_NAME,
            "--system",
            SYSTEM_UUID,
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload == {
        "resource_deleted": False,
        "workflow_completed": True,
        "lpar_uuid": LPAR_UUID,
        "dry_run": True,
        "steps": [
            {"step": "power_off", "status": "dry_run", "result": {"state": "running"}},
            {
                "step": "detach_adapters",
                "status": "dry_run",
                "result": {
                    "adapters": [
                        {"type": "ClientNetworkAdapter", "uuid": "adapter-1"},
                        {"type": "VirtualSCSIClientAdapter", "uuid": "adapter-1"},
                        {
                            "type": "VirtualFibreChannelClientAdapter",
                            "uuid": "adapter-1",
                        },
                        {"type": "VirtualNICDedicated", "uuid": "adapter-1"},
                    ]
                },
            },
            {
                "step": "delete_lpar",
                "status": "dry_run",
                "result": {"lpar_uuid": LPAR_UUID},
            },
        ],
        "warnings": [],
        "blast_radius": {
            "lpar_uuid": LPAR_UUID,
            "lpar_name": LPAR_NAME,
            "partition_id": 1,
            "state": "running",
            "owner": None,
            "adapters": [
                {"type": "ClientNetworkAdapter", "uuid": "adapter-1"},
                {"type": "VirtualSCSIClientAdapter", "uuid": "adapter-1"},
                {"type": "VirtualFibreChannelClientAdapter", "uuid": "adapter-1"},
                {"type": "VirtualNICDedicated", "uuid": "adapter-1"},
            ],
            "storage_mappings": [],
            "unresolved_storage_mapping_count": 0,
            "unavailable_storage_source_count": 0,
        },
    }


# --------------------------------------------------------------------------- #
# adapter and storage vocabularies
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("command", ["list", "delete"])
def test_adapters_reject_invalid_type_before_client_call(fake_hmc, command):
    args = ["adapters", command, LPAR_UUID, "--type", "UnknownAdapter"]
    if command == "delete":
        args.extend(["--uuid", "adapter-1", "--yes"])

    result = RUNNER.invoke(cli.app, args)

    assert result.exit_code == 2
    assert "UnknownAdapter" in result.stderr
    assert fake_hmc.calls == []


def test_storage_map_rejects_invalid_kind_before_client_call(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "storage",
            "map",
            VIOS_UUID,
            "--lpar",
            LPAR_UUID,
            "--disk",
            "disk1",
            "--kind",
            "UnknownStorage",
            "--yes",
        ],
    )

    assert result.exit_code == 2
    assert "UnknownStorage" in result.stderr
    assert fake_hmc.calls == []


# --------------------------------------------------------------------------- #
# storage create
# --------------------------------------------------------------------------- #


def test_storage_create_vg(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "storage",
            "create-vg",
            VIOS_UUID,
            "--name",
            "datavg",
            "--pvs",
            "hdisk1, hdisk2",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert "Created Volume Group 'datavg'" in result.stdout
    name, args, _ = fake_hmc.calls[0]
    assert name == "create_volume_group"
    assert args[0] == VIOS_UUID
    assert args[2] == ["hdisk1", "hdisk2"]


def test_storage_create_vg_requires_pvs(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "storage",
            "create-vg",
            VIOS_UUID,
            "--name",
            "datavg",
            "--pvs",
            " , ",
            "--yes",
        ],
    )

    assert result.exit_code == 2
    assert "Error:" in result.stderr
    assert "Provide at least one physical volume" in result.stderr
    assert fake_hmc.calls == []


def test_storage_create_disk(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "storage",
            "create-disk",
            VIOS_UUID,
            "--vg",
            VG_UUID,
            "--name",
            "bootvol",
            "--capacity-mib",
            "1024",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert "Created virtual disk 'bootvol' (1024 MiB)" in result.stdout
    name, args, _ = fake_hmc.calls[0]
    assert name == "create_virtual_disk"
    assert args[1] == VG_UUID
    assert args[3] == 1024


def test_storage_attach_disk_runs_workflow(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "storage",
            "attach-disk",
            LPAR_NAME,
            "--vios",
            VIOS_UUID,
            "--vg",
            VG_UUID,
            "--name",
            "bootvol",
            "--capacity-mib",
            "1024",
            "--vios-id",
            "2",
            "--vios-slot",
            "10",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Attached virtual disk 'bootvol'" in result.stdout
    mutations = [
        name
        for name, _, _ in fake_hmc.calls
        if name in {"create_virtual_disk", "add_vscsi_adapter", "map_storage_to_lpar"}
    ]
    assert mutations == [
        "create_virtual_disk",
        "add_vscsi_adapter",
        "map_storage_to_lpar",
    ]


def test_storage_attach_disk_dry_run_does_not_mutate(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "storage",
            "attach-disk",
            LPAR_UUID,
            "--vios",
            VIOS_UUID,
            "--vg",
            VG_UUID,
            "--name",
            "bootvol",
            "--capacity-mib",
            "1024",
            "--vios-id",
            "2",
            "--vios-slot",
            "10",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.stdout
    mutations = {"create_virtual_disk", "add_vscsi_adapter", "map_storage_to_lpar"}
    assert not any(name in mutations for name, _, _ in fake_hmc.calls)


def test_storage_attach_disk_partial_failure_is_visible_and_nonzero(fake_hmc):
    fake_hmc.fail_on = "add_vscsi_adapter"

    result = RUNNER.invoke(
        cli.app,
        [
            "storage",
            "attach-disk",
            LPAR_UUID,
            "--vios",
            VIOS_UUID,
            "--vg",
            VG_UUID,
            "--name",
            "bootvol",
            "--capacity-mib",
            "1024",
            "--vios-id",
            "2",
            "--vios-slot",
            "10",
            "--yes",
        ],
    )

    assert result.exit_code == 1
    assert "create_disk" in result.stdout
    assert "vscsi" in result.stdout
    assert "error" in result.stdout
    assert "simulated add_vscsi_adapter failure" in result.stdout
    assert "storage" in result.stdout
    assert "skipped" in result.stdout


def test_storage_attach_disk_json_incomplete_workflow_exits_1(fake_hmc):
    """The --json branch exits 1 without rendering the step table.

    No operation is patched: ``attach_disk_to_lpar`` returns the frozen
    ``AttachDiskResult`` dataclass, and the command calls ``asdict()`` on it. A
    stub returning a dict or a namespace would raise ``TypeError`` before the
    branch ran, which ``CliRunner`` also reports as exit 1 -- so the JSON body,
    not the exit code, is what proves this branch executed.
    """
    fake_hmc.fail_on = "add_vscsi_adapter"

    result = RUNNER.invoke(
        cli.app,
        [
            "storage",
            "attach-disk",
            LPAR_UUID,
            "--vios",
            VIOS_UUID,
            "--vg",
            VG_UUID,
            "--name",
            "bootvol",
            "--capacity-mib",
            "1024",
            "--vios-id",
            "2",
            "--vios-slot",
            "10",
            "--yes",
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["workflow_completed"] is False
    assert payload["lpar_uuid"] == LPAR_UUID
    assert "Attach-disk steps" not in result.stdout


# --------------------------------------------------------------------------- #
# storage: command bodies (#240)
#
# cli_storage's commands come in three shapes with different injection points:
#   A  _with_client(lambda hmc: op(...))          -> patch hmc_mcp.cli_storage.<op>
#   B  _run(_go) building its own HMCClient, op   -> patch load_profile/HMCClient here
#      imported inside the function                 and the op on operations_storage
#   C  as B, but the op is imported at module top -> patch all three on cli_storage
# Getting the shape wrong yields a test that passes without running the body.
# --------------------------------------------------------------------------- #


class _FakeClientContext:
    """Async context manager standing in for HMCClient in cli_storage._go bodies."""

    def __init__(self) -> None:
        self.entered = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None


@pytest.fixture
def direct_client(monkeypatch):
    """Neutralise load_profile()/HMCClient() for the commands that build their own client."""
    client = _FakeClientContext()
    monkeypatch.setattr("hmc_mcp.cli_storage.load_profile", lambda: None)
    monkeypatch.setattr("hmc_mcp.cli_storage.HMCClient", lambda _config: client)
    return client


def test_storage_list_vgs_renders_a_table(fake_hmc, monkeypatch):
    async def fake_list(_hmc, vios):
        assert vios == VIOS_UUID
        return [
            {
                "UUID": VG_UUID,
                "Resource": {
                    "GroupName": "rootvg",
                    "FreeSpaceInMBytes": "5120",
                    "GroupCapacity": "102400",
                },
            }
        ]

    monkeypatch.setattr("hmc_mcp.cli_storage.list_volume_groups", fake_list)

    result = RUNNER.invoke(cli.app, ["storage", "list-vgs", VIOS_UUID])

    assert result.exit_code == 0
    assert "rootvg" in result.stdout
    assert "Volume Groups" in result.stdout


def test_storage_delete_disk_deletes_when_confirmed(fake_hmc, monkeypatch):
    seen = {}

    async def fake_delete(_hmc, vios, vg, name):
        seen.update(vios=vios, vg=vg, name=name)
        return {"UUID": "disk-1"}

    monkeypatch.setattr("hmc_mcp.cli_storage.delete_virtual_disk", fake_delete)

    result = RUNNER.invoke(
        cli.app,
        [
            "storage",
            "delete-disk",
            VIOS_UUID,
            "--vg",
            VG_UUID,
            "--name",
            "bootvol",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert "Deleted virtual disk 'bootvol'" in result.stdout
    assert seen == {"vios": VIOS_UUID, "vg": VG_UUID, "name": "bootvol"}


def test_storage_delete_disk_declined_confirmation_aborts(fake_hmc, monkeypatch):
    called = []

    async def fake_delete(*args):
        called.append(args)

    monkeypatch.setattr("hmc_mcp.cli_storage.delete_virtual_disk", fake_delete)

    result = RUNNER.invoke(
        cli.app,
        ["storage", "delete-disk", VIOS_UUID, "--vg", VG_UUID, "--name", "bootvol"],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
    assert called == []


def test_storage_map_declined_confirmation_aborts(fake_hmc, monkeypatch):
    called = []

    async def fake_map(*args):
        called.append(args)

    monkeypatch.setattr("hmc_mcp.cli_storage.map_storage", fake_map)

    result = RUNNER.invoke(
        cli.app,
        ["storage", "map", VIOS_UUID, "--lpar", LPAR_UUID, "--disk", "bootvol"],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
    assert called == []


def test_storage_create_media_repo_declined_confirmation_aborts(fake_hmc, monkeypatch):
    called = []

    async def fake_create(*args):
        called.append(args)

    monkeypatch.setattr("hmc_mcp.cli_storage.create_media_repository", fake_create)

    result = RUNNER.invoke(
        cli.app,
        ["storage", "create-media-repo", VIOS_UUID, VG_UUID, "--size-mib", "2048"],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
    assert called == []


def test_storage_create_media_creates_when_confirmed(fake_hmc, monkeypatch):
    seen = {}

    async def fake_create(_hmc, vios, vg, name, size_mib):
        seen.update(vios=vios, vg=vg, name=name, size_mib=size_mib)
        return {"MediaName": "aix.iso"}

    monkeypatch.setattr("hmc_mcp.cli_storage.create_optical_media", fake_create)

    result = RUNNER.invoke(
        cli.app,
        [
            "storage",
            "create-media",
            VIOS_UUID,
            VG_UUID,
            "--name",
            "aix.iso",
            "--size-mib",
            "4096",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert "Created media 'aix.iso'" in result.stdout
    assert seen == {
        "vios": VIOS_UUID,
        "vg": VG_UUID,
        "name": "aix.iso",
        "size_mib": 4096,
    }


def test_storage_create_media_declined_confirmation_aborts(fake_hmc, monkeypatch):
    called = []

    async def fake_create(*args):
        called.append(args)

    monkeypatch.setattr("hmc_mcp.cli_storage.create_optical_media", fake_create)

    result = RUNNER.invoke(
        cli.app,
        [
            "storage",
            "create-media",
            VIOS_UUID,
            VG_UUID,
            "--name",
            "aix.iso",
            "--size-mib",
            "4096",
        ],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
    assert called == []


def test_storage_delete_media_deletes_when_confirmed(fake_hmc, monkeypatch):
    seen = {}

    async def fake_delete(_hmc, vios, vg, media_name):
        seen.update(vios=vios, vg=vg, media_name=media_name)

    monkeypatch.setattr("hmc_mcp.cli_storage.delete_optical_media", fake_delete)

    result = RUNNER.invoke(
        cli.app,
        ["storage", "delete-media", VIOS_UUID, VG_UUID, "aix.iso", "--yes"],
    )

    assert result.exit_code == 0
    assert "Deleted media 'aix.iso'" in result.stdout
    assert seen == {"vios": VIOS_UUID, "vg": VG_UUID, "media_name": "aix.iso"}


def test_storage_delete_media_declined_confirmation_aborts(fake_hmc, monkeypatch):
    called = []

    async def fake_delete(*args):
        called.append(args)

    monkeypatch.setattr("hmc_mcp.cli_storage.delete_optical_media", fake_delete)

    result = RUNNER.invoke(
        cli.app,
        ["storage", "delete-media", VIOS_UUID, VG_UUID, "aix.iso"],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
    assert called == []


def test_storage_get_media_repo_renders_name_and_size(fake_hmc, monkeypatch):
    async def fake_get(_hmc, vios, vg):
        assert (vios, vg) == (VIOS_UUID, VG_UUID)
        return {"Resource": {"RepositoryName": "VMLibrary", "RepositorySize": "10240"}}

    monkeypatch.setattr("hmc_mcp.cli_storage.get_media_repository", fake_get)

    result = RUNNER.invoke(cli.app, ["storage", "get-media-repo", VIOS_UUID, VG_UUID])

    assert result.exit_code == 0
    assert "VMLibrary" in result.stdout
    assert "10240" in result.stdout


def test_storage_get_media_repo_reports_empty(fake_hmc, monkeypatch):
    async def fake_get(_hmc, _vios, _vg):
        return {}

    monkeypatch.setattr("hmc_mcp.cli_storage.get_media_repository", fake_get)

    result = RUNNER.invoke(cli.app, ["storage", "get-media-repo", VIOS_UUID, VG_UUID])

    assert result.exit_code == 0
    assert "No media repository found" in result.stdout


def test_storage_get_media_repo_json(fake_hmc, monkeypatch):
    async def fake_get(_hmc, _vios, _vg):
        return {"Resource": {"RepositoryName": "VMLibrary"}}

    monkeypatch.setattr("hmc_mcp.cli_storage.get_media_repository", fake_get)

    result = RUNNER.invoke(
        cli.app, ["storage", "get-media-repo", VIOS_UUID, VG_UUID, "--json"]
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"Resource": {"RepositoryName": "VMLibrary"}}


def test_storage_list_optical_media_renders_a_table(fake_hmc, monkeypatch):
    async def fake_list(_hmc, vios, vg):
        assert (vios, vg) == (VIOS_UUID, VG_UUID)
        return [{"MediaName": "aix.iso", "MediaSize": 4096, "MediaType": "ISO"}]

    monkeypatch.setattr("hmc_mcp.cli_storage.list_optical_media", fake_list)

    result = RUNNER.invoke(
        cli.app, ["storage", "list-optical-media", VIOS_UUID, VG_UUID]
    )

    assert result.exit_code == 0
    assert "aix.iso" in result.stdout
    assert "4096" in result.stdout


def test_storage_list_optical_media_reports_empty(fake_hmc, monkeypatch):
    async def fake_list(_hmc, _vios, _vg):
        return []

    monkeypatch.setattr("hmc_mcp.cli_storage.list_optical_media", fake_list)

    result = RUNNER.invoke(
        cli.app, ["storage", "list-optical-media", VIOS_UUID, VG_UUID]
    )

    assert result.exit_code == 0
    assert "No optical media found" in result.stdout


def test_storage_list_optical_media_json(fake_hmc, monkeypatch):
    async def fake_list(_hmc, _vios, _vg):
        return [{"MediaName": "aix.iso"}]

    monkeypatch.setattr("hmc_mcp.cli_storage.list_optical_media", fake_list)

    result = RUNNER.invoke(
        cli.app, ["storage", "list-optical-media", VIOS_UUID, VG_UUID, "--json"]
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [{"MediaName": "aix.iso"}]


def test_storage_list_mappings_renders_virtual_disk(direct_client, monkeypatch):
    async def fake_mappings(_hmc, vios, lpar):
        assert (vios, lpar) == (VIOS_UUID, None)
        return [
            {
                "UUID": "map-1",
                "AssociatedLogicalPartition": {"PartitionName": "lpar1"},
                "Storage": {"VirtualDisk": {"DiskName": "bootvol"}},
            }
        ]

    monkeypatch.setattr(
        "hmc_mcp.operations_storage.list_storage_mappings", fake_mappings
    )

    result = RUNNER.invoke(cli.app, ["storage", "list-mappings", VIOS_UUID])

    assert result.exit_code == 0
    assert "map-1" in result.stdout
    assert "bootvol" in result.stdout
    assert "VirtualDisk" in result.stdout
    assert direct_client.entered


def test_storage_list_mappings_renders_physical_volume(direct_client, monkeypatch):
    async def fake_mappings(_hmc, _vios, lpar):
        assert lpar == LPAR_UUID
        return [
            {
                "UUID": "map-2",
                "AssociatedLogicalPartition": {"PartitionName": "lpar1"},
                "Storage": {"PhysicalVolume": {"VolumeName": "hdisk9"}},
            }
        ]

    monkeypatch.setattr(
        "hmc_mcp.operations_storage.list_storage_mappings", fake_mappings
    )

    result = RUNNER.invoke(
        cli.app, ["storage", "list-mappings", VIOS_UUID, "--lpar", LPAR_UUID]
    )

    assert result.exit_code == 0
    assert "hdisk9" in result.stdout
    assert "PhysicalVolume" in result.stdout


def test_storage_list_mappings_json(direct_client, monkeypatch):
    async def fake_mappings(_hmc, _vios, _lpar):
        return [{"UUID": "map-1"}]

    monkeypatch.setattr(
        "hmc_mcp.operations_storage.list_storage_mappings", fake_mappings
    )

    result = RUNNER.invoke(cli.app, ["storage", "list-mappings", VIOS_UUID, "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [{"UUID": "map-1"}]


def test_storage_detach_mapping_deletes_when_confirmed(direct_client, monkeypatch):
    seen = {}

    async def fake_detach(_hmc, vios, mapping_uuid):
        seen.update(vios=vios, mapping_uuid=mapping_uuid)

    monkeypatch.setattr(
        "hmc_mcp.operations_storage.detach_storage_mapping", fake_detach
    )

    result = RUNNER.invoke(
        cli.app, ["storage", "detach-mapping", VIOS_UUID, "map-1", "--confirm"]
    )

    assert result.exit_code == 0
    assert "Deleted storage mapping map-1" in result.stdout
    assert seen == {"vios": VIOS_UUID, "mapping_uuid": "map-1"}
    assert direct_client.entered


def test_storage_detach_mapping_reports_one_failure_and_exits_1(
    direct_client, monkeypatch
):
    """A failing detach reports once, on stderr, and exits 1.

    The command used to wrap ``_run`` in ``except Exception``, which caught
    ``_run``'s own ``typer.Exit`` sentinel -- ``typer.Exit`` subclasses
    ``RuntimeError`` -- and printed a second, information-free line on stdout.
    """

    async def fake_detach(_hmc, vios, mapping_uuid):
        raise HMCError("mapping is in use")

    monkeypatch.setattr(
        "hmc_mcp.operations_storage.detach_storage_mapping", fake_detach
    )

    result = RUNNER.invoke(
        cli.app, ["storage", "detach-mapping", VIOS_UUID, "map-1", "--confirm"]
    )

    assert result.exit_code == 1
    assert "Error: mapping is in use" in result.stderr
    assert "Failed to delete storage mapping" not in result.stdout
    assert "Deleted storage mapping" not in result.stdout


def test_run_propagates_a_typer_exit_code_unchanged():
    """``_run`` must not rewrite a closure's chosen exit code to 1.

    ``typer.Exit`` subclasses ``RuntimeError``, so the catch-all would otherwise
    route the sentinel through ``_fail`` and report exit 1 -- turning a usage
    error (code 2) into a generic failure.
    """

    async def _go() -> None:
        raise typer.Exit(code=2)

    with pytest.raises(typer.Exit) as excinfo:
        cli_app._run(_go)

    assert excinfo.value.exit_code == 2


def test_with_client_propagates_a_typer_exit_code_unchanged(monkeypatch):
    """``_with_client`` shares ``_run``'s control-flow passthrough."""

    def boom(coroutine):
        coroutine.close()
        raise typer.Exit(code=2)

    monkeypatch.setattr("hmc_mcp.cli_app.asyncio.run", boom)

    with pytest.raises(typer.Exit) as excinfo:
        cli_app._with_client(lambda hmc: None)

    assert excinfo.value.exit_code == 2


def test_storage_upload_iso_reports_an_existing_duplicate(direct_client, monkeypatch):
    async def fake_upload(_hmc, vios, vg, media_name, iso_source):
        assert (vios, vg, media_name) == (VIOS_UUID, VG_UUID, "aix.iso")
        assert iso_source == "https://images.test/aix.iso"
        return {
            "status": "existing",
            "media_name": "aix.iso",
            "media_size_bytes": 1048576,
            "sha256": "abc123",
            "existing_name": "aix-old.iso",
            "media": {"MediaName": "aix.iso"},
        }

    monkeypatch.setattr("hmc_mcp.cli_storage.upload_iso", fake_upload)

    result = RUNNER.invoke(
        cli.app,
        [
            "storage",
            "upload-iso",
            VIOS_UUID,
            VG_UUID,
            "aix.iso",
            "https://images.test/aix.iso",
        ],
    )

    assert result.exit_code == 0
    assert "Upload status: existing" in result.stdout
    assert "aix-old.iso" in result.stdout
    assert "1,048,576 bytes" in result.stdout


def test_storage_upload_iso_json(direct_client, monkeypatch):
    async def fake_upload(_hmc, _vios, _vg, _media_name, _iso_source):
        return {"status": "uploaded", "media_name": "aix.iso"}

    monkeypatch.setattr("hmc_mcp.cli_storage.upload_iso", fake_upload)

    result = RUNNER.invoke(
        cli.app,
        [
            "storage",
            "upload-iso",
            VIOS_UUID,
            VG_UUID,
            "aix.iso",
            "https://images.test/aix.iso",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"status": "uploaded", "media_name": "aix.iso"}


# --------------------------------------------------------------------------- #
# failure path (_fail)
# --------------------------------------------------------------------------- #


def test_client_error_reports_and_exits_1(fake_hmc):
    fake_hmc.fail_on = "list_logical_partitions"
    result = RUNNER.invoke(cli.app, ["lpars", "list"])

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "boom" in result.stderr


# --------------------------------------------------------------------------- #
# SSH-backed commands (run_hmc_command seam)
# --------------------------------------------------------------------------- #


def test_lpars_get_description_via_ssh(monkeypatch):
    async def fake(cfg, cmd):
        return "my lpar description\n"

    monkeypatch.setattr(ssh_commands, "run_hmc_command", fake)
    result = RUNNER.invoke(cli.app, ["lpars", "get-description", "lpar1", "sys1"])

    assert result.exit_code == 0
    assert "my lpar description" in result.stdout


def test_lpars_get_msp_via_ssh(monkeypatch):
    async def fake(cfg, cmd):
        return "1\n"

    monkeypatch.setattr(ssh_commands, "run_hmc_command", fake)
    result = RUNNER.invoke(cli.app, ["lpars", "get-msp", "lpar1", "sys1"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "enabled"


def test_lpars_memopt_score_via_ssh(monkeypatch):
    captured: dict[str, str] = {}

    async def fake(cfg, cmd):
        captured["cmd"] = cmd
        return "lpar_name=lpar1,lpar_id=1,curr_lpar_score=100\n"

    monkeypatch.setattr(ssh_commands, "run_hmc_command", fake)
    result = RUNNER.invoke(cli.app, ["lpars", "memopt-score", "lpar1", "sys1"])

    assert result.exit_code == 0
    assert captured["cmd"] == (
        "lsmemopt -m sys1 -r lpar -o currscore --filter lpar_names=lpar1"
    )
    assert "100" in result.stdout


def test_lpars_memopt_score_json(monkeypatch):
    async def fake(cfg, cmd):
        return "lpar_name=lpar1,lpar_id=1,curr_lpar_score=none\n"

    monkeypatch.setattr(ssh_commands, "run_hmc_command", fake)
    result = RUNNER.invoke(
        cli.app, ["lpars", "memopt-score", "lpar1", "sys1", "--json"]
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "lpar_name": "lpar1",
        "lpar_id": "1",
        "curr_lpar_score": "none",
    }


def test_lpars_memopt_score_error(monkeypatch):
    async def fake(cfg, cmd):
        raise ssh_commands.HMCCLIError("The partition named lpar1 was not found.")

    monkeypatch.setattr(ssh_commands, "run_hmc_command", fake)
    result = RUNNER.invoke(cli.app, ["lpars", "memopt-score", "lpar1", "sys1"])

    assert result.exit_code == 1
    assert "Error:" in result.stderr


def test_lpars_memopt_scores_via_ssh(monkeypatch):
    captured: dict[str, str] = {}

    async def fake(cfg, cmd):
        captured["cmd"] = cmd
        return (
            "lpar_name=lpar1,lpar_id=1,curr_lpar_score=100\n"
            "lpar_name=lpar2,lpar_id=2,curr_lpar_score=95\n"
        )

    monkeypatch.setattr(ssh_commands, "run_hmc_command", fake)
    result = RUNNER.invoke(cli.app, ["lpars", "memopt-scores", "sys1"])

    assert result.exit_code == 0
    assert captured["cmd"] == "lsmemopt -m sys1 -r lpar -o currscore"
    assert "lpar2" in result.stdout


def test_lpars_memopt_scores_with_lpar_filter(monkeypatch):
    captured: dict[str, str] = {}

    async def fake(cfg, cmd):
        captured["cmd"] = cmd
        return "lpar_name=lpar1,lpar_id=1,curr_lpar_score=100\n"

    monkeypatch.setattr(ssh_commands, "run_hmc_command", fake)
    result = RUNNER.invoke(
        cli.app, ["lpars", "memopt-scores", "sys1", "--lpar", "lpar1", "--json"]
    )

    assert result.exit_code == 0
    assert captured["cmd"] == (
        "lsmemopt -m sys1 -r lpar -o currscore --filter lpar_names=lpar1"
    )
    data = json.loads(result.stdout)
    assert len(data) == 1
    assert data[0]["lpar_name"] == "lpar1"


def test_lpars_memopt_scores_missing_system(monkeypatch):
    result = RUNNER.invoke(cli.app, ["lpars", "memopt-scores"])

    assert result.exit_code == 2
    assert "Missing" in result.stderr


def test_lpars_memopt_scores_empty(monkeypatch):
    async def fake(cfg, cmd):
        return ""

    monkeypatch.setattr(ssh_commands, "run_hmc_command", fake)
    result = RUNNER.invoke(cli.app, ["lpars", "memopt-scores", "sys1"])

    assert result.exit_code == 0
    assert "No memory-optimization scores reported" in result.stdout


@pytest.mark.parametrize(
    ("command", "operation", "payload", "expected"),
    [
        (
            "system-memopt-score",
            "get_system_memopt_score",
            {"curr_sys_score": "72"},
            "current: 72",
        ),
        (
            "plan-memopt-scores",
            "plan_lpar_memopt_scores",
            [
                {
                    "lpar_name": "web",
                    "lpar_id": "3",
                    "curr_lpar_score": "61",
                    "predicted_lpar_score": "84",
                    "prediction_guaranteed": False,
                }
            ],
            "prediction guaranteed: no",
        ),
        (
            "plan-system-memopt-score",
            "plan_system_memopt_score",
            {
                "curr_sys_score": "70",
                "predicted_sys_score": "88",
                "prediction_guaranteed": False,
            },
            "predicted: 88",
        ),
    ],
)
def test_affinity_cli_human_output(monkeypatch, command, operation, payload, expected):
    async def fake_operation(*_args, **_kwargs):
        return payload

    monkeypatch.setattr(cli_lpars, operation, fake_operation)
    result = RUNNER.invoke(cli.app, ["lpars", command, "sys1"])

    assert result.exit_code == 0
    assert expected in unstyle(result.stdout)


def test_affinity_planning_cli_json_and_name_selectors(monkeypatch):
    captured = {}

    async def fake_operation(_config, system, prioritized, excluded):
        captured.update(system=system, prioritized=prioritized, excluded=excluded)
        return [{"prediction_guaranteed": False}]

    monkeypatch.setattr(cli_lpars, "plan_lpar_memopt_scores", fake_operation)
    result = RUNNER.invoke(
        cli.app,
        [
            "lpars",
            "plan-memopt-scores",
            "sys1",
            "--prioritize-name",
            "web",
            "--prioritize-name",
            "api",
            "--exclude-name",
            "batch",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [{"prediction_guaranteed": False}]
    assert captured["system"] == "sys1"
    assert captured["prioritized"].names == ("web", "api")
    assert captured["excluded"].names == ("batch",)


def test_affinity_planning_cli_id_selectors(monkeypatch):
    captured = {}

    async def fake_operation(_config, _system, prioritized, excluded):
        captured.update(prioritized=prioritized, excluded=excluded)
        return {"prediction_guaranteed": False}

    monkeypatch.setattr(cli_lpars, "plan_system_memopt_score", fake_operation)
    result = RUNNER.invoke(
        cli.app,
        [
            "lpars",
            "plan-system-memopt-score",
            "sys1",
            "--prioritize-id",
            "2",
            "--exclude-id",
            "9",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert captured["prioritized"].ids == (2,)
    assert captured["excluded"].ids == (9,)


def test_affinity_planning_cli_rejects_mixed_selector_forms(monkeypatch):
    called = False

    async def fake_operation(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(cli_lpars, "plan_lpar_memopt_scores", fake_operation)
    result = RUNNER.invoke(
        cli.app,
        [
            "lpars",
            "plan-memopt-scores",
            "sys1",
            "--prioritize-name",
            "web",
            "--exclude-id",
            "9",
        ],
    )

    assert result.exit_code == 2
    assert "must use the same representation" in result.output
    assert called is False


def test_affinity_planning_cli_rejects_oversized_selector_before_operation(
    monkeypatch,
):
    operation = AsyncMock()
    monkeypatch.setattr(cli_lpars, "plan_lpar_memopt_scores", operation)

    result = RUNNER.invoke(
        cli.app,
        [
            "lpars",
            "plan-memopt-scores",
            "sys1",
            "--prioritize-name",
            "'" * 400 + "a" * 42,
            "--exclude-name",
            "'" * 400 + "b" * 43,
        ],
    )

    assert result.exit_code == 2
    assert "option package exceeds 4096 UTF-8 bytes" in result.output
    operation.assert_not_awaited()


def test_affinity_planning_cli_rejects_overlapping_selectors(monkeypatch):
    called = False

    async def fake_operation(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(cli_lpars, "plan_system_memopt_score", fake_operation)
    result = RUNNER.invoke(
        cli.app,
        [
            "lpars",
            "plan-system-memopt-score",
            "sys1",
            "--prioritize-name",
            "web",
            "--exclude-name",
            "web",
        ],
    )

    assert result.exit_code == 2
    assert "must not overlap" in result.output
    assert called is False


def test_affinity_cli_propagates_hmc_errors(monkeypatch):
    async def fail(*_args, **_kwargs):
        raise ssh_commands.HMCCLIError("calcscore unavailable")

    monkeypatch.setattr(cli_lpars, "plan_system_memopt_score", fail)
    result = RUNNER.invoke(cli.app, ["lpars", "plan-system-memopt-score", "sys1"])

    assert result.exit_code == 1
    assert "calcscore unavailable" in result.output


@pytest.mark.parametrize(
    ("args", "expected_call"),
    [
        (
            [
                "lpars",
                "migrate",
                LPAR_NAME,
                "--target",
                "sys1",
                "--profile",
                "target-profile",
                "--wait-time",
                "60",
                "--yes",
            ],
            (
                "lpar_migrate",
                (LPAR_UUID, "sys1", "target-profile"),
                {"wait_time": 60},
            ),
        ),
        (
            ["lpars", "migrate-validate", LPAR_NAME, "--target", "sys1", "--yes"],
            ("lpar_migrate_validate", (LPAR_UUID, "sys1", None), {"wait_time": None}),
        ),
        (
            ["lpars", "migrate-abort", LPAR_NAME, "--yes"],
            ("lpar_migrate_abort", (LPAR_UUID,), {}),
        ),
        (
            ["lpars", "migrate-recover", LPAR_NAME, "--yes"],
            ("lpar_migrate_recover", (LPAR_UUID,), {}),
        ),
        (
            [
                "lpars",
                "remote-restart",
                LPAR_NAME,
                "--operation",
                "restart",
                "--system",
                "sys1",
                "--target",
                "target",
                "--yes",
            ],
            (
                "lpar_remote_restart",
                (LPAR_UUID, "restart", "sys1"),
                {
                    "target_managed_system": "target",
                    "target_managed_system_uuid": None,
                    "use_current_data": False,
                    "retain_devices": False,
                },
            ),
        ),
    ],
)
def test_lpm_commands_delegate_resolved_arguments(fake_hmc, args, expected_call):
    result = RUNNER.invoke(cli.app, args)

    assert result.exit_code == 0, result.output
    assert expected_call in fake_hmc.calls
    assert "Submitted" in result.stdout
    assert LPAR_UUID in result.stdout


def test_migrate_cli_defaults_to_validation_first(fake_hmc):
    result = RUNNER.invoke(
        cli.app, ["lpars", "migrate", LPAR_NAME, "--target", "sys1", "--yes"]
    )

    assert result.exit_code == 0, result.output
    names = [name for name, _args, _kwargs in fake_hmc.calls]
    assert names.index("lpar_migrate_validate") < names.index("lpar_migrate")
    assert "wait_for_job" in names


def test_migrate_cli_can_bypass_validation(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "lpars",
            "migrate",
            LPAR_NAME,
            "--target",
            "sys1",
            "--no-validate-first",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    names = [name for name, _args, _kwargs in fake_hmc.calls]
    assert "lpar_migrate_validate" not in names
    assert "lpar_migrate" in names


def test_migrate_cli_rejects_effective_wait_timing_before_confirmation(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["lpars", "migrate", LPAR_NAME, "--target", "sys1", "--interval", "0"],
    )

    assert result.exit_code == 1
    assert fake_hmc.calls == []


@pytest.mark.parametrize(
    "args",
    [
        ["lpars", "migrate-abort", LPAR_NAME, "--yes"],
        ["lpars", "migrate-recover", LPAR_NAME, "--yes"],
        [
            "lpars",
            "remote-restart",
            LPAR_NAME,
            "--operation",
            "restart",
            "--system",
            "sys1",
            "--target",
            "target",
            "--yes",
        ],
    ],
)
def test_lpm_recovery_commands_forward_wait_timing(fake_hmc, args):
    result = RUNNER.invoke(
        cli.app, [*args, "--wait", "--timeout", "60", "--interval", "2"]
    )

    assert result.exit_code == 0, result.output
    assert (
        "wait_for_job",
        (JOB_UUID, 60, 2),
        {"job_href": f"/jobs/{JOB_UUID}"},
    ) in fake_hmc.calls


def test_lpm_recovery_command_renders_timeout_outcome(fake_hmc):
    fake_hmc.wait_job_status = "running"
    result = RUNNER.invoke(
        cli.app,
        [
            "lpars",
            "migrate-abort",
            LPAR_NAME,
            "--yes",
            "--wait",
            "--timeout",
            "0",
            "--interval",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert f'"job_id": "{JOB_UUID}"' in result.stdout
    assert '"status": "running"' in result.stdout
    assert '"timed_out": true' in result.stdout
    assert '"error": null' in result.stdout
    assert '"job": {' in result.stdout


def test_lpm_recovery_command_rejects_invalid_timing_before_submission(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "lpars",
            "migrate-abort",
            LPAR_NAME,
            "--yes",
            "--wait",
            "--timeout",
            "-1",
        ],
    )

    assert result.exit_code == 1
    assert fake_hmc.calls == []


@pytest.mark.parametrize(
    ("args", "command_fragments"),
    [
        (
            ["lpars", "set-description", "lpar1", "sys1", "new text", "--yes"],
            ("chsyscfg", "name=lpar1", "description=new text"),
        ),
        (
            ["lpars", "set-msp", "lpar1", "sys1", "true", "--yes"],
            ("chsyscfg", "name=lpar1", "msp=1"),
        ),
        (
            ["lpars", "set-proc-compat", "lpar1", "sys1", "POWER10", "--yes"],
            ("chsyscfg", "name=lpar1", "lpar_proc_compat_mode=POWER10"),
        ),
        (
            ["network", "set-sriov-mode", "sys1", "P1-C1", "sriov"],
            ("lshwres", "sriov", "adapter"),
        ),
    ],
)
def test_destructive_ssh_commands_delegate_valid_arguments(
    monkeypatch, fake_hmc, args, command_fragments
):
    commands: list[str] = []

    async def fake(_config, command):
        commands.append(command)
        if command == "lshmc -V":
            return "V10R3 M1060 build 2408210051\n"
        if "-r sys" in command and "type_model" in command:
            return "8375-42A\n"
        if "--rsubtype adapter" in command:
            fields = "adapter_id,slot_id,config_state,functional_state,phys_loc,phys_ports,logical_ports,adapter_max_logical_ports,sriov_status"
            return f"{fields}\nP1-C1,1,sriov,1,U,2,120,120,running\n"
        if command.startswith("lssyscfg"):
            return "vioserver\n"
        return "updated\n"

    monkeypatch.setattr(ssh_commands, "run_hmc_command", fake)

    result = RUNNER.invoke(cli.app, args)

    assert result.exit_code == 0, result.output
    assert all(fragment in commands[-1] for fragment in command_fragments)


def test_add_vnic_cli_replaces_legacy_options():
    result = RUNNER.invoke(cli.app, ["network", "add-vnic", "--help"])

    assert result.exit_code == 0
    output = unstyle(result.output)
    for option in (
        "--vios-name",
        "--vios-lpar-id",
        "--adapter-id",
        "--physical-port-id",
        "--capacity-percent",
        "--port-vlan-id",
    ):
        assert option in output
    for legacy in ("--backing-devices", "--virtual-switch-name", "--capacity "):
        assert legacy not in output


def test_remove_vnic_cli_names_slot_selector():
    result = RUNNER.invoke(cli.app, ["network", "remove-vnic", "--help"])

    assert result.exit_code == 0
    assert "slot_num" in result.output
    assert "vnic_id" not in result.output


@pytest.mark.parametrize("command", ["add-vnic", "remove-vnic"])
def test_vnic_mutation_cli_exposes_ownership_override(command):
    result = RUNNER.invoke(cli.app, ["network", command, "--help"])

    assert result.exit_code == 0
    assert "--ownership-override" in unstyle(result.output)


def _vnic_result(operation: str) -> VnicChangeResult:
    return VnicChangeResult(
        operation=operation,  # type: ignore[arg-type]
        mutation_dispatched=True,
        changed=True,
        selector=None,
        slot_num="4",
        vnic_before=(),
        backing_before=(),
        vnic_after=(),
        backing_after=(),
        vnic_after_read_succeeded=True,
        backing_after_read_succeeded=True,
        output="done",
        errors=(),
    )


def test_add_vnic_cli_default_confirmation_keeps_stdout_json(monkeypatch):
    operation = AsyncMock(return_value=_vnic_result("add"))
    monkeypatch.setattr("hmc_mcp.cli_network.add_vnic", operation)
    monkeypatch.setattr(
        "hmc_mcp.cli_network._with_client", lambda fn: asyncio.run(fn(object()))
    )

    result = RUNNER.invoke(
        cli.app,
        [
            "network",
            "add-vnic",
            "system",
            "lpar",
            "--vios-name",
            "vios1",
            "--vios-lpar-id",
            "2",
            "--adapter-id",
            "1",
            "--physical-port-id",
            "0",
            "--capacity-percent",
            "20.25",
            "--port-vlan-id",
            "100",
            "--ownership-override",
        ],
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["operation"] == "add"
    assert "Add vNIC" in result.stderr
    assert operation.await_args.kwargs == {"ownership_override": True}


def test_remove_vnic_cli_default_confirmation_keeps_partial_stdout_json(monkeypatch):
    partial = VnicPartialError("incomplete", _vnic_result("remove"))
    operation = AsyncMock(side_effect=partial)
    monkeypatch.setattr("hmc_mcp.cli_network.remove_vnic", operation)
    monkeypatch.setattr(
        "hmc_mcp.cli_network._with_client", lambda fn: asyncio.run(fn(object()))
    )

    result = RUNNER.invoke(
        cli.app,
        [
            "network",
            "remove-vnic",
            "system",
            "lpar",
            "4",
            "--ownership-override",
        ],
        input="y\n",
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["operation"] == "remove"
    assert "Remove vNIC" in result.stderr
    assert operation.await_args.kwargs == {"ownership_override": True}


# --------------------------------------------------------------------------- #
# network I/O slots (SSH-backed)
# --------------------------------------------------------------------------- #


def test_network_list_io_slots_via_ssh(monkeypatch):
    async def fake(cfg, cmd):
        return "drc_name=U78DA.ND1.ABC1234-P1-C1,pci_class=0200,lpar_name=lpar1\n"

    monkeypatch.setattr(ssh_commands, "run_hmc_command", fake)
    result = RUNNER.invoke(cli.app, ["network", "list-io-slots", "sys1"])

    assert result.exit_code == 0
    assert "U78DA.ND1.ABC1234-P1-C1" in result.stdout
    assert "lpar1" in result.stdout


def test_network_list_io_slots_invalid_pci_class_exits_2(monkeypatch):
    async def fake(cfg, cmd):
        return ""

    monkeypatch.setattr(ssh_commands, "run_hmc_command", fake)
    result = RUNNER.invoke(
        cli.app, ["network", "list-io-slots", "sys1", "--pci-class", "bogus"]
    )

    assert result.exit_code == 2
    assert "bogus" in result.stderr
    assert "nvme" in result.stderr


# --------------------------------------------------------------------------- #
# raw REST escape hatch
# --------------------------------------------------------------------------- #


def test_raw_post_requires_confirmation(fake_hmc):
    """raw post is gated by the same --yes/confirm convention as other mutating commands."""
    result = RUNNER.invoke(
        cli.app,
        ["raw", "post", "/rest/api/uom/LogicalPartition", "<lpar/>"],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
    assert fake_hmc.calls == []


def test_raw_post_with_yes_sends_request(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["raw", "post", "/rest/api/uom/LogicalPartition", "<lpar/>", "--yes"],
    )

    assert result.exit_code == 0
    name, args, _ = fake_hmc.calls[0]
    assert name == "raw_post"
    assert args[0] == "/rest/api/uom/LogicalPartition"
    assert args[1] == "<lpar/>"


def test_raw_post_file_missing_errors(fake_hmc, tmp_path):
    """A @file body that cannot be read exits via the CLI Error path, no request sent."""
    result = RUNNER.invoke(
        cli.app,
        [
            "raw",
            "post",
            "/rest/api/uom/LogicalPartition",
            f"@{tmp_path / 'nope.xml'}",
            "--yes",
        ],
    )

    assert result.exit_code == 1
    assert "Error" in result.stderr
    assert "cannot read body file" in result.stderr
    assert fake_hmc.calls == []


def test_raw_post_file_body_sent(fake_hmc, tmp_path):
    """A @file body is read from disk and posted verbatim."""
    body_file = tmp_path / "body.xml"
    body_file.write_text("<lpar><name>x</name></lpar>", encoding="utf-8")

    result = RUNNER.invoke(
        cli.app,
        ["raw", "post", "/rest/api/uom/LogicalPartition", f"@{body_file}", "--yes"],
    )

    assert result.exit_code == 0
    name, args, _ = fake_hmc.calls[0]
    assert name == "raw_post"
    assert args[0] == "/rest/api/uom/LogicalPartition"
    assert args[1] == "<lpar><name>x</name></lpar>"


# --------------------------------------------------------------------------- #
# systems
# --------------------------------------------------------------------------- #


def test_systems_list_table(fake_hmc):
    result = RUNNER.invoke(cli.app, ["systems", "list"])

    assert result.exit_code == 0
    assert "sys1" in result.stdout
    assert "9119-MHE" in result.stdout
    assert fake_hmc.calls == [("list_managed_systems", (), {})]


def test_systems_list_json(fake_hmc):
    result = RUNNER.invoke(cli.app, ["systems", "list", "--json"])

    assert result.exit_code == 0
    assert SYSTEM_UUID in result.stdout
    assert fake_hmc.calls == [("list_managed_systems", (), {})]


def test_systems_show(fake_hmc):
    result = RUNNER.invoke(cli.app, ["systems", "show", SYSTEM_UUID])

    assert result.exit_code == 0
    assert "sys1" in result.stdout
    assert fake_hmc.calls == [("get_managed_system", (SYSTEM_UUID,), {})]


def test_systems_show_by_name(fake_hmc):
    result = RUNNER.invoke(cli.app, ["systems", "show", "sys1"])

    assert result.exit_code == 0
    assert "sys1" in result.stdout
    assert fake_hmc.calls == [("find_system_by_name", ("sys1",), {})]


def test_systems_show_not_found_exits_1(fake_hmc):
    result = RUNNER.invoke(cli.app, ["systems", "show", "ghost"])

    assert result.exit_code == 1
    assert "not found" in result.stderr
    assert fake_hmc.calls == [("find_system_by_name", ("ghost",), {})]


def test_systems_power_on(fake_hmc):
    result = RUNNER.invoke(cli.app, ["systems", "power-on", SYSTEM_UUID, "--yes"])

    assert result.exit_code == 0
    assert "Submitted PowerOn" in result.stdout
    assert fake_hmc.calls == [("power_on_system", (SYSTEM_UUID,), {})]


def test_systems_power_on_declined_confirm_aborts(fake_hmc):
    result = RUNNER.invoke(cli.app, ["systems", "power-on", SYSTEM_UUID], input="n\n")

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
    assert fake_hmc.calls == []


def test_systems_power_off(fake_hmc):
    result = RUNNER.invoke(cli.app, ["systems", "power-off", SYSTEM_UUID, "--yes"])

    assert result.exit_code == 0
    assert "Submitted PowerOff" in result.stdout
    assert fake_hmc.calls == [("power_off_system", (SYSTEM_UUID, False), {})]


def test_systems_power_off_immediate(fake_hmc):
    result = RUNNER.invoke(
        cli.app, ["systems", "power-off", SYSTEM_UUID, "--immediate", "--yes"]
    )

    assert result.exit_code == 0
    assert "Immediate PowerOff" in result.stdout
    assert fake_hmc.calls == [("power_off_system", (SYSTEM_UUID, True), {})]


def test_systems_power_off_declined_confirm_aborts(fake_hmc):
    result = RUNNER.invoke(cli.app, ["systems", "power-off", SYSTEM_UUID], input="n\n")

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
    assert fake_hmc.calls == []


# --------------------------------------------------------------------------- #
# vios
# --------------------------------------------------------------------------- #


def test_vios_list_table(fake_hmc):
    result = RUNNER.invoke(cli.app, ["vios", "list"])

    assert result.exit_code == 0
    assert "vios1" in result.stdout
    assert "3.1.0" in result.stdout
    assert fake_hmc.calls == [("list_vios", (None,), {})]


def test_vios_list_json(fake_hmc):
    result = RUNNER.invoke(cli.app, ["vios", "list", "--json"])

    assert result.exit_code == 0
    assert VIOS_UUID in result.stdout
    assert fake_hmc.calls == [("list_vios", (None,), {})]


def test_vios_list_restricted_to_system(fake_hmc):
    result = RUNNER.invoke(cli.app, ["vios", "list", "--system", SYSTEM_UUID])

    assert result.exit_code == 0
    assert fake_hmc.calls == [("list_vios", (SYSTEM_UUID,), {})]


def test_vios_power_on(fake_hmc):
    result = RUNNER.invoke(cli.app, ["vios", "power-on", VIOS_UUID, "--yes"])

    assert result.exit_code == 0
    assert "Submitted PowerOn" in result.stdout
    assert fake_hmc.calls == [("power_on_vios", (VIOS_UUID,), {})]


def test_vios_power_on_declined_confirm_aborts(fake_hmc):
    result = RUNNER.invoke(cli.app, ["vios", "power-on", VIOS_UUID], input="n\n")

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
    assert fake_hmc.calls == []


def test_vios_power_off(fake_hmc):
    result = RUNNER.invoke(cli.app, ["vios", "power-off", VIOS_UUID, "--yes"])

    assert result.exit_code == 0
    assert "Submitted PowerOff" in result.stdout
    assert fake_hmc.calls == [("power_off_vios", (VIOS_UUID, False), {})]


def test_vios_power_off_immediate(fake_hmc):
    result = RUNNER.invoke(
        cli.app, ["vios", "power-off", VIOS_UUID, "--immediate", "--yes"]
    )

    assert result.exit_code == 0
    assert "Immediate PowerOff" in result.stdout
    assert fake_hmc.calls == [("power_off_vios", (VIOS_UUID, True), {})]


def test_vios_power_off_declined_confirm_aborts(fake_hmc):
    result = RUNNER.invoke(cli.app, ["vios", "power-off", VIOS_UUID], input="n\n")

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
    assert fake_hmc.calls == []


# --------------------------------------------------------------------------- #
# console
# --------------------------------------------------------------------------- #


def test_console_info(fake_hmc):
    result = RUNNER.invoke(cli.app, ["console", "info"])

    assert result.exit_code == 0
    assert "V10R1M1010" in result.stdout
    assert fake_hmc.calls == [("get_console_info", (), {})]


def test_console_info_json(fake_hmc):
    result = RUNNER.invoke(cli.app, ["console", "info", "--json"])

    assert result.exit_code == 0
    assert "V10R1M1010" in result.stdout
    assert fake_hmc.calls == [("get_console_info", (), {})]


def test_console_info_none_reports_empty(fake_hmc):
    fake_hmc.console = None
    result = RUNNER.invoke(cli.app, ["console", "info"])

    assert result.exit_code == 0
    assert "No ManagementConsole data returned" in result.stderr
    assert fake_hmc.calls == [("get_console_info", (), {})]


# --------------------------------------------------------------------------- #
# cluster / SSP
# --------------------------------------------------------------------------- #


def test_cluster_list_table(fake_hmc):
    result = RUNNER.invoke(cli.app, ["cluster", "list"])

    assert result.exit_code == 0
    assert "cl1" in result.stdout
    assert fake_hmc.calls == [("list_clusters", (), {})]


def test_cluster_list_json(fake_hmc):
    result = RUNNER.invoke(cli.app, ["cluster", "list", "--json"])

    assert result.exit_code == 0
    assert CLUSTER_UUID in result.stdout
    assert fake_hmc.calls == [("list_clusters", (), {})]


def test_cluster_list_ssps(fake_hmc):
    result = RUNNER.invoke(cli.app, ["cluster", "list-ssps"])

    assert result.exit_code == 0
    assert "pool1" in result.stdout
    assert "1024" in result.stdout
    assert fake_hmc.calls == [("list_shared_storage_pools", (), {})]


def test_cluster_create_lu(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "cluster",
            "create-lu",
            CLUSTER_UUID,
            "--name",
            "vol1",
            "--lu-size-gib",
            "50",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert "Submitted CreateLogicalUnit job" in result.stdout
    name, args, _ = fake_hmc.calls[0]
    assert name == "create_logical_unit"
    assert args[0] == CLUSTER_UUID
    assert args[1] == "vol1"
    assert args[2] == 50
    assert args[3] == "THIN"  # default lu_type
    assert args[4] == "VirtualIO_Disk"  # default device_type
    assert args[5] is None  # no clone source


def test_cluster_create_lu_declined_confirm_aborts(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "cluster",
            "create-lu",
            CLUSTER_UUID,
            "--name",
            "vol1",
            "--lu-size-gib",
            "50",
        ],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
    assert fake_hmc.calls == []


@pytest.mark.parametrize(
    "option,value",
    [("--type", "SPARSE"), ("--device-type", "PhysicalDisk")],
)
def test_cluster_create_lu_rejects_invalid_vocabulary(fake_hmc, option, value):
    result = RUNNER.invoke(
        cli.app,
        [
            "cluster",
            "create-lu",
            CLUSTER_UUID,
            "--name",
            "vol1",
            "--lu-size-gib",
            "50",
            option,
            value,
            "--yes",
        ],
    )

    assert result.exit_code == 2
    assert value in result.stderr
    assert fake_hmc.calls == []


def test_cluster_delete_lu(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["cluster", "delete-lu", CLUSTER_UUID, "--udid", "udid-1", "--yes"],
    )

    assert result.exit_code == 0
    assert "Submitted DeleteLogicalUnit job" in result.stdout
    assert fake_hmc.calls == [("delete_logical_unit", (CLUSTER_UUID, "udid-1"), {})]


def test_cluster_delete_lu_declined_confirm_aborts(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["cluster", "delete-lu", CLUSTER_UUID, "--udid", "udid-1"],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
    assert fake_hmc.calls == []


# --------------------------------------------------------------------------- #
# templates
# --------------------------------------------------------------------------- #


def test_templates_list_table(fake_hmc):
    result = RUNNER.invoke(cli.app, ["templates", "list"])

    assert result.exit_code == 0
    assert "tpl1" in result.stdout
    assert fake_hmc.calls == [("list_partition_templates", (), {})]


def test_templates_list_json(fake_hmc):
    result = RUNNER.invoke(cli.app, ["templates", "list", "--json"])

    assert result.exit_code == 0
    assert TEMPLATE_UUID in result.stdout
    assert fake_hmc.calls == [("list_partition_templates", (), {})]


def test_templates_cli_translates_not_licensed_error(fake_hmc):
    fake_hmc.fail_on = "list_partition_templates"
    fake_hmc.fail_status = 406

    result = RUNNER.invoke(cli.app, ["templates", "list"])

    assert result.exit_code == 1
    assert "not licensed or not supported" in result.stderr


def test_templates_show(fake_hmc):
    result = RUNNER.invoke(cli.app, ["templates", "show", TEMPLATE_UUID])

    assert result.exit_code == 0
    assert "tpl1" in result.stdout
    assert fake_hmc.calls == [("get_partition_template", (TEMPLATE_UUID,), {})]


def test_templates_show_propagates_not_found_error(fake_hmc):
    result = RUNNER.invoke(cli.app, ["templates", "show", "ghost"])

    assert result.exit_code == 1
    assert "not found" in result.stderr
    assert fake_hmc.calls == [("get_partition_template", ("ghost",), {})]


def test_templates_deploy(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["templates", "deploy", TEMPLATE_UUID, "--system", SYSTEM_UUID, "--yes"],
    )

    assert result.exit_code == 0
    assert "Deploy job" in result.stdout
    assert "ownership stamp not attempted" in result.stdout
    assert fake_hmc.calls == [
        ("deploy_partition_template", (TEMPLATE_UUID, SYSTEM_UUID), {})
    ]


def test_templates_deploy_rejects_invalid_wait_timing_before_client_use(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "templates",
            "deploy",
            TEMPLATE_UUID,
            "--system",
            SYSTEM_UUID,
            "--wait",
            "--poll-interval",
            "0",
            "--yes",
        ],
    )

    assert result.exit_code == 2
    assert "poll_interval must be greater than 0" in result.stderr
    assert fake_hmc.calls == []


def test_templates_deploy_waits_through_shared_workflow(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "templates",
            "deploy",
            TEMPLATE_UUID,
            "--system",
            SYSTEM_UUID,
            "--wait",
            "--timeout",
            "60",
            "--poll-interval",
            "1",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert fake_hmc.calls == [
        ("list_logical_partitions", (SYSTEM_UUID,), {}),
        ("deploy_partition_template", (TEMPLATE_UUID, SYSTEM_UUID), {}),
        (
            "wait_for_job",
            (JOB_UUID, 60, 1),
            {"job_href": f"/jobs/{JOB_UUID}"},
        ),
        ("list_logical_partitions", (SYSTEM_UUID,), {}),
    ]


def test_templates_deploy_declined_confirm_aborts(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["templates", "deploy", TEMPLATE_UUID, "--system", SYSTEM_UUID],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
    assert fake_hmc.calls == []


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #


def test_jobs_show(fake_hmc):
    result = RUNNER.invoke(cli.app, ["jobs", "show", JOB_UUID])

    assert result.exit_code == 0
    assert "PowerOn" in result.stdout
    assert fake_hmc.calls == [("get_job", (JOB_UUID,), {"job_href": None})]


def test_jobs_show_not_found_exits_1(fake_hmc):
    result = RUNNER.invoke(cli.app, ["jobs", "show", "ghost"])

    assert result.exit_code == 1
    assert "not found" in result.stderr
    assert fake_hmc.calls == [("get_job", ("ghost",), {"job_href": None})]


def test_jobs_show_forwards_self_link(fake_hmc):
    href = f"/jobs/{JOB_UUID}"
    result = RUNNER.invoke(cli.app, ["jobs", "show", JOB_UUID, "--job-href", href])

    assert result.exit_code == 0
    assert fake_hmc.calls == [("get_job", (JOB_UUID,), {"job_href": href})]


def test_jobs_list_rejects_negative_limit_before_client_call(fake_hmc):
    result = RUNNER.invoke(cli.app, ["jobs", "list", "--limit", "-1"])

    assert result.exit_code == 2
    assert "--limit must be greater than or equal to 0" in result.stderr
    assert fake_hmc.calls == []


def test_jobs_wait(fake_hmc):
    result = RUNNER.invoke(cli.app, ["jobs", "wait", JOB_UUID])

    assert result.exit_code == 0
    assert "COMPLETED" in result.stdout
    assert fake_hmc.calls == [("wait_for_job", (JOB_UUID, 300, 5), {"job_href": None})]


# --------------------------------------------------------------------------- #
# pcm metrics
# --------------------------------------------------------------------------- #


def test_metrics_prefs(fake_hmc):
    result = RUNNER.invoke(cli.app, ["metrics", "prefs", "ManagedSystem", SYSTEM_UUID])

    assert result.exit_code == 0
    assert "LongTermMonitorEnabled" in result.stdout
    assert fake_hmc.calls == [
        ("get_pcm_preferences", ("ManagedSystem", SYSTEM_UUID), {})
    ]


def test_metrics_cli_translates_authority_error(fake_hmc):
    fake_hmc.fail_on = "get_pcm_preferences"
    fake_hmc.fail_status = 403

    result = RUNNER.invoke(cli.app, ["metrics", "prefs", "ManagedSystem", SYSTEM_UUID])

    assert result.exit_code == 1
    assert "does not have PCM authority" in result.stderr


def test_metrics_set_prefs_requires_confirmation(fake_hmc):
    """metrics set-prefs is gated by the same --yes/confirm convention as other mutating commands."""
    result = RUNNER.invoke(
        cli.app,
        ["metrics", "set-prefs", "ManagedSystem", SYSTEM_UUID, "--ltm"],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
    assert fake_hmc.calls == []


def test_metrics_set_prefs_builds_flags(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "metrics",
            "set-prefs",
            "ManagedSystem",
            SYSTEM_UUID,
            "--ltm",
            "--no-aggregation",
            "--compute-ltm",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert "Updated" in result.stdout
    assert fake_hmc.calls == [
        (
            "set_pcm_preferences",
            ("ManagedSystem", SYSTEM_UUID),
            {
                "LongTermMonitorEnabled": True,
                "AggregationEnabled": False,
                "ComputeLTMEnabled": True,
            },
        )
    ]


def test_metrics_set_prefs_no_flags_exits_2(fake_hmc):
    result = RUNNER.invoke(
        cli.app, ["metrics", "set-prefs", "ManagedSystem", SYSTEM_UUID]
    )

    assert result.exit_code == 2
    assert "Error:" in result.stderr
    assert "No flags supplied" in result.stderr
    assert fake_hmc.calls == []


def test_network_set_sriov_mode_rejects_invalid_mode(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["network", "set-sriov-mode", "system-1", "adapter-1", "invalid"],
    )

    assert result.exit_code == 2
    assert "invalid" in result.stderr
    assert "sriov" in result.stderr
    assert "dedicated" in result.stderr
    assert fake_hmc.calls == []


def test_metrics_show_processed(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "metrics",
            "show",
            "ManagedSystem",
            SYSTEM_UUID,
            "--start",
            "2024-01-01T00:00:00Z",
        ],
    )

    assert result.exit_code == 0
    assert fake_hmc.calls == [
        (
            "get_processed_metric_links",
            ("ManagedSystem", SYSTEM_UUID, "2024-01-01T00:00:00Z", None, None),
            {},
        )
    ]


def test_metrics_show_aggregated(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "metrics",
            "show",
            "ManagedSystem",
            SYSTEM_UUID,
            "--start",
            "2024-01-01T00:00:00Z",
            "--aggregated",
        ],
    )

    assert result.exit_code == 0
    assert fake_hmc.calls == [
        (
            "get_aggregated_metric_links",
            ("ManagedSystem", SYSTEM_UUID, "2024-01-01T00:00:00Z", None, None),
            {},
        )
    ]


def test_metrics_show_logical_partition_forwards_owning_system(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "metrics",
            "show",
            "LogicalPartition",
            LPAR_UUID,
            "--system",
            SYSTEM_UUID,
            "--start",
            "2024-01-01T00:00:00Z",
        ],
    )

    assert result.exit_code == 0
    assert fake_hmc.calls == [
        (
            "get_processed_metric_links",
            ("LogicalPartition", LPAR_UUID, "2024-01-01T00:00:00Z", None, None),
            {"system_uuid": SYSTEM_UUID},
        )
    ]


def test_metrics_show_logical_partition_requires_owning_system(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "metrics",
            "show",
            "LogicalPartition",
            LPAR_UUID,
            "--start",
            "2024-01-01T00:00:00Z",
        ],
    )

    assert result.exit_code == 1
    assert "system_name_or_uuid" in str(result.exception)
    assert fake_hmc.calls == []


def test_metrics_show_rejects_system_selector_for_managed_system(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "metrics",
            "show",
            "ManagedSystem",
            SYSTEM_UUID,
            "--system",
            SYSTEM_UUID,
            "--start",
            "2024-01-01T00:00:00Z",
        ],
    )

    assert result.exit_code == 1
    assert "only for LogicalPartition" in str(result.exception)
    assert fake_hmc.calls == []


@pytest.mark.parametrize("command", ["prefs", "set-prefs"])
def test_metrics_preference_help_is_managed_system_only(command):
    result = RUNNER.invoke(cli.app, ["metrics", command, "--help"])

    assert result.exit_code == 0
    assert "ManagedSystem" in result.stdout
    assert "preferences are" in result.stdout
    assert "unavailable" in result.stdout


def test_metrics_show_fetch_downloads_latest(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        [
            "metrics",
            "show",
            "ManagedSystem",
            SYSTEM_UUID,
            "--start",
            "2024-01-01T00:00:00Z",
            "--fetch",
        ],
    )

    assert result.exit_code == 0
    assert '"data"' in result.stdout
    names = [c[0] for c in fake_hmc.calls]
    assert names == ["get_processed_metric_links", "fetch_json"]


def test_metrics_show_fetch_404_reports_empty(fake_hmc):
    """A 404 from fetch_json (metrics aged out of retention) is no data, not an error."""
    fake_hmc.fetch_json_404 = True
    result = RUNNER.invoke(
        cli.app,
        [
            "metrics",
            "show",
            "ManagedSystem",
            SYSTEM_UUID,
            "--start",
            "2024-01-01T00:00:00Z",
            "--fetch",
        ],
    )

    assert result.exit_code == 0
    assert "{}" in result.stdout
    assert "Error" not in result.stderr


# --------------------------------------------------------------------------- #
# memory pools (SSH-backed)
# --------------------------------------------------------------------------- #


def test_memory_pools_list_table(monkeypatch):
    async def fake_list(config, system_name):
        return [{"pool_name": "pool1", "size": "1024", "lpar_names": "lpar1"}]

    monkeypatch.setattr("hmc_mcp.cli_memory_pools.list_memory_pools", fake_list)
    result = RUNNER.invoke(cli.app, ["memory-pools", "list", "sys1"])

    assert result.exit_code == 0
    assert "pool1" in result.stdout
    assert "1024" in result.stdout


def test_memory_pools_list_json(monkeypatch):
    async def fake_list(config, system_name):
        return [{"pool_name": "pool1", "size": "1024"}]

    monkeypatch.setattr("hmc_mcp.cli_memory_pools.list_memory_pools", fake_list)
    result = RUNNER.invoke(cli.app, ["memory-pools", "list", "sys1", "--json"])

    assert result.exit_code == 0
    assert '"pool_name": "pool1"' in result.stdout


def test_memory_pools_list_empty(monkeypatch):
    async def fake_list(config, system_name):
        return []

    monkeypatch.setattr("hmc_mcp.cli_memory_pools.list_memory_pools", fake_list)
    result = RUNNER.invoke(cli.app, ["memory-pools", "list", "sys1"])

    assert result.exit_code == 0
    assert "No memory pools found" in result.stderr


def test_memory_pools_remove_with_yes(monkeypatch):
    async def fake_remove(config, system_name, pool_name):
        return "pool removed\n"

    monkeypatch.setattr("hmc_mcp.cli_memory_pools.remove_memory_pool", fake_remove)
    result = RUNNER.invoke(
        cli.app, ["memory-pools", "remove", "sys1", "pool1", "--yes"]
    )

    assert result.exit_code == 0
    assert "removed" in result.stdout
    assert "pool removed" in result.stdout


def test_memory_pools_remove_declined_confirm_aborts(monkeypatch):
    result = RUNNER.invoke(
        cli.app, ["memory-pools", "remove", "sys1", "pool1"], input="n\n"
    )

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
