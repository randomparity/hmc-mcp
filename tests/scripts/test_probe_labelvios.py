"""Operator probe host verification and per-run opt-out behavior."""

from __future__ import annotations

import importlib.util
import runpy
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

MODULE_PATH = Path(__file__).parents[2] / "scripts" / "probe_labelvios.py"
MODULE_SPEC = importlib.util.spec_from_file_location("probe_labelvios", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
probe = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = probe
MODULE_SPEC.loader.exec_module(probe)


def test_probe_verifies_by_default(capsys):
    profile = probe.Profile("test", "hmc.test", "test", "test-password")
    kwargs = probe.connect_kwargs(profile)
    assert kwargs["known_hosts"] == str(Path.home() / ".ssh" / "known_hosts")
    assert kwargs["preferred_auth"] == "password"
    assert kwargs["client_keys"] == []
    assert not capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize("insecure", [False, True])
async def test_probe_main_propagates_opt_out_before_connect(insecure, monkeypatch, capsys):
    profile = probe.Profile("test", "hmc.test", "test", "test-password")
    monkeypatch.setattr(probe, "load_profiles", lambda: [profile])
    observed = []

    def connect(**kwargs):
        observed.append(kwargs)
        warning = capsys.readouterr().err
        assert ("SSH host-key verification disabled" in warning) is insecure
        if insecure:
            assert profile.host in warning
            assert profile.password not in warning
        raise OSError("test connection failure")

    monkeypatch.setattr(probe.asyncssh, "connect", connect)
    await probe.main(insecure=insecure)
    assert len(observed) == 1
    expected = None if insecure else str(Path.home() / ".ssh" / "known_hosts")
    assert observed[0]["known_hosts"] == expected
    assert "CONNECTION ERROR: test connection failure" in capsys.readouterr().out


@pytest.mark.parametrize("arguments, insecure", [([], False), (["--insecure"], True)])
def test_probe_cli_parses_per_run_choice(arguments, insecure, monkeypatch, tmp_path, capsys):
    config_path = tmp_path / ".config" / "hmc-mcp" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '[profiles.test]\nhost = "hmc.test"\nuser = "test"\n'
        'password = "fixture"\n'  # pragma: allowlist secret - mocked probe fixture
        'ssh_verify_host_key = false\n'
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    observed = []

    def connect(**kwargs):
        observed.append(kwargs)
        raise OSError("test connection failure")

    monkeypatch.setattr(sys, "argv", [str(MODULE_PATH), *arguments])
    monkeypatch.setattr(probe.asyncssh, "connect", connect)
    runpy.run_path(str(MODULE_PATH), run_name="__main__")
    assert len(observed) == 1
    assert (observed[0]["known_hosts"] is None) is insecure
    assert ("SSH host-key verification disabled" in capsys.readouterr().err) is insecure


@pytest.mark.asyncio
async def test_probe_profile_default_does_not_opt_out(monkeypatch):
    connection = AsyncMock()
    connection.run.return_value.stdout = ""
    connection.run.return_value.stderr = ""
    connection.run.return_value.exit_status = 0
    connection.__aenter__.return_value = connection
    observed = []

    def connect(**kwargs):
        observed.append(kwargs)
        return connection

    monkeypatch.setattr(probe.asyncssh, "connect", connect)
    result = await probe.probe_profile(probe.Profile("test", "hmc.test", "test", "test-password"))
    assert "error" not in result
    assert observed[0]["known_hosts"] is not None
