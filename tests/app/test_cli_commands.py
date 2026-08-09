"""Direct CLI command tests via ``typer.testing.CliRunner``.

The REST-backed commands all funnel through ``cli_app._client`` →
``cli_app.client_from_env`` (imported at ``cli_app.py``), and the SSH-backed
commands through ``cli_app.run_hmc_command``.  These tests monkeypatch those
two factories so every command runs against a scripted fake — no HTTP, no SSH.

This closes the CLI blind spot where only the helpers (``_ssh_config``,
``_output``) were exercised and no command body was ever invoked, so the
highest-complexity commands (lpars power/create/modify/delete, storage
create-vg/create-disk) had zero direct coverage.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from hmc_mcp import cli, cli_app
from hmc_mcp.errors import HMCError

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
        self.fail_on: str | None = None
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
            "Resource": {"GroupName": "rootvg", "FreeSpaceInMBytes": "5120", "GroupCapacity": "102400"},
        }
        self.disk = {"UUID": "66666666-6666-4666-8666-666666666666", "Resource": {"DiskName": "bootvol"}}
        self.job = {"UUID": JOB_UUID, "Resource": {"JobName": "PowerOn", "Status": "running"}}
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
            "Resource": {"StoragePoolName": "pool1", "Capacity": "1024", "FreeSpace": "512"},
        }
        self.template = {"UUID": TEMPLATE_UUID, "Resource": {"templateName": "tpl1"}}
        self.pcm_prefs = {"LongTermMonitorEnabled": True, "AggregationEnabled": False}
        self.metric_links = [
            {"link": "/rest/api/pcm/ManagedSystem/xxx/ProcessedMetrics/1", "title": "m1"}
        ]
        self.metrics_json = {"data": [1, 2, 3]}
        self.fetch_json_404 = False

    def _record(self, name: str, *args, **kwargs) -> None:
        self.calls.append((name, args, kwargs))
        if self.fail_on == name:
            raise HMCError(f"simulated {name} failure", 500, "<xml><Message>boom</Message></xml>")

    async def __aenter__(self) -> "FakeHMC":
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None

    async def list_logical_partitions(self, system_uuid=None):
        self._record("list_logical_partitions", system_uuid)
        return [self.lpar]

    async def get_logical_partition(self, uuid):
        self._record("get_logical_partition", uuid)
        return self.lpar

    async def find_partition_by_name(self, name):
        self._record("find_partition_by_name", name)
        return self.lpar if name == LPAR_NAME else None

    async def get_quick_property(self, resource_type, uuid, property_name):
        self._record("get_quick_property", resource_type, uuid, property_name)
        return "running"

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

    async def create_volume_group(self, vios_uuid, name, physical_volumes):
        self._record("create_volume_group", vios_uuid, name, physical_volumes)
        return self.vg

    async def create_virtual_disk(self, vios_uuid, vg_uuid, disk_name, capacity_mb):
        self._record("create_virtual_disk", vios_uuid, vg_uuid, disk_name, capacity_mb)
        return self.disk

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
        self, cluster_uuid, lu_name, lu_size_gb, lu_type="THIN",
        device_type="VirtualIO_Disk", cloned_from=None,
    ):
        self._record("create_logical_unit", cluster_uuid, lu_name, lu_size_gb, lu_type, device_type, cloned_from)
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
        return self.template if template_uuid == TEMPLATE_UUID else None

    async def deploy_partition_template(self, draft_template_uuid, target_system_uuid):
        self._record("deploy_partition_template", draft_template_uuid, target_system_uuid)
        return self.job

    # -- jobs ------------------------------------------------------------ #
    async def get_job(self, job_uuid):
        self._record("get_job", job_uuid)
        return self.job if job_uuid == JOB_UUID else None

    # -- pcm metrics ----------------------------------------------------- #
    async def get_pcm_preferences(self, category, uuid):
        self._record("get_pcm_preferences", category, uuid)
        return self.pcm_prefs

    async def set_pcm_preferences(self, category, uuid, **flags):
        self._record("set_pcm_preferences", category, uuid, **flags)

    async def get_processed_metric_links(self, category, uuid, start_ts, end_ts=None, no_of_samples=None):
        self._record("get_processed_metric_links", category, uuid, start_ts, end_ts, no_of_samples)
        return self.metric_links

    async def get_aggregated_metric_links(self, category, uuid, start_ts, end_ts=None, no_of_samples=None):
        self._record("get_aggregated_metric_links", category, uuid, start_ts, end_ts, no_of_samples)
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
    return hmc


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
    result = RUNNER.invoke(cli.app, ["lpars", "power-on", LPAR_UUID, "--yes"])

    assert result.exit_code == 0
    assert "Job submitted" in result.stdout
    assert [c[0] for c in fake_hmc.calls] == ["submit_job"]
    path, job_xml = fake_hmc.calls[0][1]
    assert path == f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/PowerOn"
    assert "PowerOn</OperationName>" in job_xml


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
    name, args, _ = fake_hmc.calls[0]
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


def test_lpars_modify_renames(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["lpars", "modify", LPAR_UUID, "--name", "renamed", "--yes"],
    )

    assert result.exit_code == 0
    assert "Modified LPAR" in result.stdout
    name, args, _ = fake_hmc.calls[0]
    assert name == "modify_logical_partition"
    assert args[0] == LPAR_UUID
    assert "renamed" in args[1]


def test_lpars_modify_with_no_options_exits_2(fake_hmc):
    result = RUNNER.invoke(cli.app, ["lpars", "modify", LPAR_UUID])

    assert result.exit_code == 2
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
    result = RUNNER.invoke(cli.app, ["lpars", "delete", LPAR_UUID, "--yes"])

    assert result.exit_code == 0
    assert "Deleted LPAR" in result.stdout
    assert fake_hmc.calls == [("delete_logical_partition", (LPAR_UUID,), {})]


def test_lpars_delete_not_found_exits_1(fake_hmc):
    result = RUNNER.invoke(cli.app, ["lpars", "delete", "ghost", "--yes"])

    assert result.exit_code == 1
    assert "not found" in result.stderr


# --------------------------------------------------------------------------- #
# storage create
# --------------------------------------------------------------------------- #


def test_storage_create_vg(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["storage", "create-vg", VIOS_UUID, "--name", "datavg", "--pvs", "hdisk1, hdisk2", "--yes"],
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
        ["storage", "create-vg", VIOS_UUID, "--name", "datavg", "--pvs", " , ", "--yes"],
    )

    assert result.exit_code == 2
    assert "Provide at least one physical volume" in result.stderr
    assert fake_hmc.calls == []


def test_storage_create_disk(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["storage", "create-disk", VIOS_UUID, "--vg", VG_UUID, "--name", "bootvol", "--size", "1024", "--yes"],
    )

    assert result.exit_code == 0
    assert "Created virtual disk 'bootvol' (1024 MiB)" in result.stdout
    name, args, _ = fake_hmc.calls[0]
    assert name == "create_virtual_disk"
    assert args[1] == VG_UUID
    assert args[3] == 1024


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

    monkeypatch.setattr(cli_app, "run_hmc_command", fake)
    result = RUNNER.invoke(cli.app, ["lpars", "get-description", "lpar1", "sys1"])

    assert result.exit_code == 0
    assert "my lpar description" in result.stdout


def test_lpars_get_msp_via_ssh(monkeypatch):
    async def fake(cfg, cmd):
        return "1\n"

    monkeypatch.setattr(cli_app, "run_hmc_command", fake)
    result = RUNNER.invoke(cli.app, ["lpars", "get-msp", "lpar1", "sys1"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "enabled"


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


def test_systems_show_not_found_exits_1(fake_hmc):
    result = RUNNER.invoke(cli.app, ["systems", "show", "ghost"])

    assert result.exit_code == 1
    assert "not found" in result.stderr
    assert fake_hmc.calls == [("get_managed_system", ("ghost",), {})]


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
    result = RUNNER.invoke(cli.app, ["systems", "power-off", SYSTEM_UUID, "--immediate", "--yes"])

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
    result = RUNNER.invoke(cli.app, ["vios", "power-off", VIOS_UUID, "--immediate", "--yes"])

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
        ["cluster", "create-lu", CLUSTER_UUID, "--name", "vol1", "--size", "50", "--yes"],
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
        ["cluster", "create-lu", CLUSTER_UUID, "--name", "vol1", "--size", "50"],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
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


def test_templates_show(fake_hmc):
    result = RUNNER.invoke(cli.app, ["templates", "show", TEMPLATE_UUID])

    assert result.exit_code == 0
    assert "tpl1" in result.stdout
    assert fake_hmc.calls == [("get_partition_template", (TEMPLATE_UUID,), {})]


def test_templates_show_not_found_exits_1(fake_hmc):
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
    assert "Submitted deploy job" in result.stdout
    assert fake_hmc.calls == [("deploy_partition_template", (TEMPLATE_UUID, SYSTEM_UUID), {})]


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
    assert fake_hmc.calls == [("get_job", (JOB_UUID,), {})]


def test_jobs_show_not_found_exits_1(fake_hmc):
    result = RUNNER.invoke(cli.app, ["jobs", "show", "ghost"])

    assert result.exit_code == 1
    assert "not found" in result.stderr
    assert fake_hmc.calls == [("get_job", ("ghost",), {})]


# --------------------------------------------------------------------------- #
# pcm metrics
# --------------------------------------------------------------------------- #


def test_metrics_prefs(fake_hmc):
    result = RUNNER.invoke(cli.app, ["metrics", "prefs", "ManagedSystem", SYSTEM_UUID])

    assert result.exit_code == 0
    assert "LongTermMonitorEnabled" in result.stdout
    assert fake_hmc.calls == [("get_pcm_preferences", ("ManagedSystem", SYSTEM_UUID), {})]


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
        ["metrics", "set-prefs", "ManagedSystem", SYSTEM_UUID, "--ltm", "--no-aggregation", "--yes"],
    )

    assert result.exit_code == 0
    assert "Updated" in result.stdout
    assert fake_hmc.calls == [
        (
            "set_pcm_preferences",
            ("ManagedSystem", SYSTEM_UUID),
            {"LongTermMonitorEnabled": True, "AggregationEnabled": False},
        )
    ]


def test_metrics_set_prefs_no_flags_exits_2(fake_hmc):
    result = RUNNER.invoke(cli.app, ["metrics", "set-prefs", "ManagedSystem", SYSTEM_UUID])

    assert result.exit_code == 2
    assert "No flags supplied" in result.stderr
    assert fake_hmc.calls == []


def test_metrics_show_processed(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["metrics", "show", "ManagedSystem", SYSTEM_UUID, "--start", "2024-01-01T00:00:00Z"],
    )

    assert result.exit_code == 0
    assert fake_hmc.calls == [
        ("get_processed_metric_links", ("ManagedSystem", SYSTEM_UUID, "2024-01-01T00:00:00Z", None, None), {})
    ]


def test_metrics_show_aggregated(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["metrics", "show", "ManagedSystem", SYSTEM_UUID, "--start", "2024-01-01T00:00:00Z", "--aggregated"],
    )

    assert result.exit_code == 0
    assert fake_hmc.calls == [
        ("get_aggregated_metric_links", ("ManagedSystem", SYSTEM_UUID, "2024-01-01T00:00:00Z", None, None), {})
    ]


def test_metrics_show_fetch_downloads_latest(fake_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["metrics", "show", "ManagedSystem", SYSTEM_UUID, "--start", "2024-01-01T00:00:00Z", "--fetch"],
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
        ["metrics", "show", "ManagedSystem", SYSTEM_UUID, "--start", "2024-01-01T00:00:00Z", "--fetch"],
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
    result = RUNNER.invoke(cli.app, ["memory-pools", "remove", "sys1", "pool1", "--yes"])

    assert result.exit_code == 0
    assert "removed" in result.stdout
    assert "pool removed" in result.stdout


def test_memory_pools_remove_declined_confirm_aborts(monkeypatch):
    result = RUNNER.invoke(cli.app, ["memory-pools", "remove", "sys1", "pool1"], input="n\n")

    assert result.exit_code == 1
    assert "Aborted" in result.stderr
