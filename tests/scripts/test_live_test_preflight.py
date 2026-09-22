"""Contract tests for the live-run preflight.

Every case runs in `tmp_path` with `monkeypatch.chdir`, because both the
runner's `.env` reader and the credential bootstrap resolve relative to the
working directory.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
import live_test_preflight as preflight  # noqa: E402
import live_test_runner as runner  # noqa: E402


def _env_text(**overrides: str) -> str:
    """A complete, valid `.env`, derived from `LiveTestConfig`'s own defaults.

    `from_env_file` requires every mandatory key, so a hand-written fragment
    fails for the wrong reason. Building from `_CONFIG_FIELDS` also means a new
    setting cannot silently leave these tests testing a rejected file.
    """
    defaults = runner.LiveTestConfig()
    values = {}
    for key, field in runner.LiveTestConfig._CONFIG_FIELDS.items():
        value = getattr(defaults, field)
        values[key] = ",".join(value) if isinstance(value, tuple) else str(value)
    # Replacing, not appending: a second line for the same key is a duplicate,
    # which `from_env_file` rejects for a reason unrelated to the case at hand.
    values.update(overrides)
    return "".join(f"{key}={value}\n" for key, value in values.items())


_DEDICATED = {
    "LIVE_TEST_DEDICATED_PCIE_SYSTEM_NAME": "sys-R1",
    "LIVE_TEST_DEDICATED_PCIE_LPAR_PREFIX": "live-pcie-",
    "LIVE_TEST_DEDICATED_PCIE_DRC_INDEX": "553713664",
}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """An isolated working directory with no ambient HMC credentials.

    The environ is swapped for a copy rather than only cleared: the credential
    check reaches `runner._load_dotenv`, which assigns into `os.environ`
    directly, and `monkeypatch` cannot take back a key it never recorded.
    """
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.chdir(tmp_path)
    for key in ("HMC_HOST", "HMC_USER", "HMC_PASSWORD", "HMC_SCHEMA_VERSION"):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def test_the_generated_fixture_env_is_one_the_runner_accepts(workspace):
    """Guards every case below: a fixture the validator rejects proves nothing."""
    (workspace / ".env").write_text(_env_text(**_DEDICATED), encoding="utf-8")

    config = runner.LiveTestConfig.from_env_file()

    assert config.dedicated_pcie_system_name == "sys-R1"


def _credentials(monkeypatch, resolved: bool = True) -> None:
    """Stand in for the runner's bootstrap without reading a real profile."""
    monkeypatch.setattr(runner, "_bootstrap_config", lambda: resolved)
    if resolved:
        for key in ("HMC_HOST", "HMC_USER", "HMC_PASSWORD"):
            monkeypatch.setenv(key, f"value-for-{key}")


# ---------------------------------------------------------------------------
# Preflight agrees with the runner's own verdict (Validation 3)
# ---------------------------------------------------------------------------


def test_a_valid_configuration_reports_the_runner_would_start(
    workspace, monkeypatch, capsys
):
    (workspace / ".env").write_text(_env_text(**_DEDICATED), encoding="utf-8")
    _credentials(monkeypatch)

    assert preflight.main(["--skip-hardware"]) == 0
    assert "the runner would start" in capsys.readouterr().out


@pytest.mark.parametrize(
    "env_text",
    [
        _env_text(LIVE_TEST_SYSTEM_NAME=""),
        _env_text(LIVE_TEST_NOT_A_REAL_KEY="x"),
        _env_text() + "LIVE_TEST_SYSTEM_NAME=second\n",
        _env_text(LIVE_TEST_ISO_HTTP_PORT="not-a-number"),
        _env_text(LIVE_TEST_PROVISION_DESIRED_VCPUS="0"),
        _env_text(LIVE_TEST_PROVISION_DESIRED_MEMORY_MIB="99999999"),
    ],
    ids=["empty", "unknown-key", "duplicate", "not-an-int", "not-positive", "inconsistent"],
)
def test_each_rejection_the_runner_makes_is_a_non_zero_exit(
    workspace, monkeypatch, capsys, env_text
):
    """Preflight's verdict is the delegated validator's, not a second opinion.

    The validator's own message is relayed verbatim rather than reworded, so
    the two cannot disagree about why a run will not start.
    """
    (workspace / ".env").write_text(env_text, encoding="utf-8")
    _credentials(monkeypatch)

    with pytest.raises(ValueError) as runner_error:
        runner.LiveTestConfig.from_env_file()

    assert preflight.main(["--skip-hardware"]) == 1
    output = capsys.readouterr().out
    assert "configuration  FAIL" in output
    assert str(runner_error.value) in output


def test_a_configuration_the_runner_accepts_is_never_rejected_here(
    workspace, monkeypatch
):
    """The other direction of Validation 3: no verdict preflight invents alone."""
    (workspace / ".env").write_text(_env_text(**_DEDICATED), encoding="utf-8")
    _credentials(monkeypatch)

    runner.LiveTestConfig.from_env_file()  # the runner accepts it

    assert preflight.main(["--skip-hardware"]) == 0


def test_an_absent_schema_version_is_reported_without_stopping_the_run(
    workspace, monkeypatch, capsys
):
    """#875. The variable is opt-in, so its absence is a report, not a FAIL row."""
    (workspace / ".env").write_text(_env_text(**_DEDICATED), encoding="utf-8")
    monkeypatch.setattr(runner, "_bootstrap_config", lambda: True)
    for key in ("HMC_HOST", "HMC_USER", "HMC_PASSWORD"):
        monkeypatch.setenv(key, f"value-for-{key}")

    assert preflight.main(["--skip-hardware"]) == 0

    output = capsys.readouterr().out
    assert "HMC_SCHEMA_VERSION=not set" in output
    assert "MISSING" not in output
    assert "the runner would start" in output


def test_a_dotenv_only_schema_version_is_reported_as_set(
    workspace, monkeypatch, capsys
):
    """#875. The mirror of the runner's own `.env` load, and for the same reason.

    `_bootstrap_config` reads `.env` only when the TOML profile fails, so a
    resolved profile that does not pin the variable would leave preflight
    naming a request environment the run will not use.
    """
    (workspace / ".env").write_text(
        _env_text(**_DEDICATED) + "HMC_SCHEMA_VERSION=V1_0\n", encoding="utf-8"
    )
    monkeypatch.setattr(runner, "_bootstrap_config", lambda: True)
    monkeypatch.setattr(runner, "_ENV_FILE", workspace / ".env")
    for key in ("HMC_HOST", "HMC_USER", "HMC_PASSWORD"):
        monkeypatch.setenv(key, f"value-for-{key}")

    assert preflight.main(["--skip-hardware"]) == 0

    assert "HMC_SCHEMA_VERSION=set" in capsys.readouterr().out


def test_unresolvable_credentials_are_a_non_zero_exit(workspace, monkeypatch, capsys):
    (workspace / ".env").write_text(_env_text(**_DEDICATED), encoding="utf-8")
    _credentials(monkeypatch, resolved=False)

    assert preflight.main(["--skip-hardware"]) == 1
    output = capsys.readouterr().out
    assert "credentials    FAIL" in output
    assert "HMC_PASSWORD=MISSING" in output


# ---------------------------------------------------------------------------
# No HMC_* value is printed (Validation 4)
# ---------------------------------------------------------------------------


def test_no_credential_value_reaches_either_output_stream(
    workspace, monkeypatch, capsys
):
    """The output is meant to be pasted into an issue when a run will not start."""
    (workspace / ".env").write_text(_env_text(**_DEDICATED), encoding="utf-8")
    monkeypatch.setattr(runner, "_bootstrap_config", lambda: True)
    monkeypatch.setenv("HMC_HOST", "SENTINEL-host-d4f2.internal")
    monkeypatch.setenv("HMC_USER", "SENTINEL-user-d4f2")
    monkeypatch.setenv("HMC_PASSWORD", "SENTINEL-pw-d4f2")
    # A sentinel here too, not `V1_0`: the schema-version row is the newest
    # thing on this output path (#875), and a plausible value gives the
    # assertion below nothing to catch it printing.
    monkeypatch.setenv("HMC_SCHEMA_VERSION", "SENTINEL-schema-d4f2")

    assert preflight.main(["--skip-hardware"]) == 0

    captured = capsys.readouterr()
    assert "SENTINEL" not in captured.out
    assert "SENTINEL" not in captured.err
    assert "HMC_PASSWORD=set" in captured.out


def test_the_bootstrap_s_own_chatter_is_not_relayed(workspace, monkeypatch, capsys):
    """`_bootstrap_config` announces the profile it loaded; that must not escape."""
    (workspace / ".env").write_text(_env_text(**_DEDICATED), encoding="utf-8")

    def chatty_bootstrap() -> bool:
        print("  Credentials loaded from profile SENTINEL-profile-d4f2")
        return True

    monkeypatch.setattr(runner, "_bootstrap_config", chatty_bootstrap)

    preflight.main(["--skip-hardware"])

    assert "SENTINEL" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The verdict names what a mutating arm will touch (Validation 5)
# ---------------------------------------------------------------------------


def test_the_dedicated_verdict_names_system_prefix_and_slot(
    workspace, monkeypatch, capsys
):
    (workspace / ".env").write_text(_env_text(**_DEDICATED), encoding="utf-8")
    _credentials(monkeypatch)

    assert preflight.main(["--group", "dedicated", "--skip-hardware"]) == 0

    output = capsys.readouterr().out
    assert "sys-R1" in output
    assert "live-pcie-" in output
    assert "553713664" in output
    assert "RUNNABLE" in output


def test_an_unconfigured_dedicated_arm_is_predicted_skip_not_a_failure(
    workspace, monkeypatch, capsys
):
    """A missing arm setting stops that arm, not the run."""
    (workspace / ".env").write_text(_env_text(), encoding="utf-8")
    _credentials(monkeypatch)

    assert preflight.main(["--group", "dedicated", "--skip-hardware"]) == 0
    assert "SKIP" in capsys.readouterr().out


def test_a_delimiter_in_an_arm_setting_is_predicted_skip(
    workspace, monkeypatch, capsys
):
    """The arm's own `_dedicated_config` rejects it, so the prediction must too."""
    (workspace / ".env").write_text(
        _env_text(
            LIVE_TEST_DEDICATED_PCIE_SYSTEM_NAME="sys-R1",
            LIVE_TEST_DEDICATED_PCIE_LPAR_PREFIX="live=bad",
        ),
        encoding="utf-8",
    )
    _credentials(monkeypatch)

    assert preflight.main(["--group", "dedicated", "--skip-hardware"]) == 0
    assert "SKIP" in capsys.readouterr().out


def test_every_arm_is_predicted_when_no_group_is_given(workspace, monkeypatch, capsys):
    (workspace / ".env").write_text(_env_text(**_DEDICATED), encoding="utf-8")
    _credentials(monkeypatch)

    assert preflight.main(["--skip-hardware"]) == 0

    output = capsys.readouterr().out
    for group in runner.SUBTASK_GROUPS:
        if group != "all":
            assert group in output
    # "all" is every other group; predicting it too would double every row.
    assert "  all " not in output


# ---------------------------------------------------------------------------
# Hardware findings are advisory (failure model)
# ---------------------------------------------------------------------------


def test_an_unreachable_hmc_is_reported_without_changing_the_exit_status(
    workspace, monkeypatch, capsys
):
    async def unreachable(_config, _system):
        raise OSError("no route to host")

    monkeypatch.setattr(preflight, "require_admitted_environment", unreachable)
    (workspace / ".env").write_text(_env_text(**_DEDICATED), encoding="utf-8")
    _credentials(monkeypatch)

    assert preflight.main(["--group", "dedicated"]) == 0

    output = capsys.readouterr().out
    assert "not admitted or unreachable (OSError)" in output
    assert "the runner would start" in output


def test_an_admitted_system_is_reported_as_in_envelope(workspace, monkeypatch, capsys):
    async def admitted(_config, _system):
        return None

    monkeypatch.setattr(preflight, "require_admitted_environment", admitted)
    (workspace / ".env").write_text(_env_text(**_DEDICATED), encoding="utf-8")
    _credentials(monkeypatch)

    assert preflight.main(["--group", "dedicated"]) == 0
    assert "admitted (ADR 0053 envelope)" in capsys.readouterr().out


def test_skip_hardware_contacts_no_hmc(workspace, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("--skip-hardware must contact no HMC")

    monkeypatch.setattr(preflight, "require_admitted_environment", forbidden)
    (workspace / ".env").write_text(_env_text(**_DEDICATED), encoding="utf-8")
    _credentials(monkeypatch)

    assert preflight.main(["--skip-hardware"]) == 0


def test_no_hmc_is_contacted_when_credentials_are_unresolved(workspace, monkeypatch):
    """There is nothing to connect with, so probing would only produce noise."""

    def forbidden(*_args, **_kwargs):
        raise AssertionError("probed without credentials")

    monkeypatch.setattr(preflight, "require_admitted_environment", forbidden)
    (workspace / ".env").write_text(_env_text(**_DEDICATED), encoding="utf-8")
    _credentials(monkeypatch, resolved=False)

    assert preflight.main(["--group", "dedicated"]) == 1
