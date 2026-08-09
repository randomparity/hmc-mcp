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
