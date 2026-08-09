"""Tests for CLI helpers — ``_ssh_config`` honours global options.

SSH-backed CLI commands build their ``HMCConfig`` through ``_ssh_config`` so
the global ``--host/--user/--password/--verify-ssl`` flags are honoured even
when the command itself defines no HMC options.  Explicit ``None`` overrides
must be dropped so env vars / ``.env`` still fill the remaining fields (an
explicit init arg would otherwise shadow the environment).
"""

from __future__ import annotations

from hmc_mcp import cli, cli_app


def test_ssh_config_uses_global_overrides(monkeypatch):
    """Set global flags are passed through to the SSH HMCConfig."""
    monkeypatch.setattr(
        cli_app,
        "GLOBALS",
        cli_app.GlobalOpts(host="flag-host", user="flag-user", password="flag-pass", verify_ssl=True),
    )

    cfg = cli._ssh_config()

    assert cfg.host == "flag-host"
    assert cfg.user == "flag-user"
    assert cfg.password == "flag-pass"
    assert cfg.verify_ssl is True


def test_ssh_config_keeps_false_verify_ssl(monkeypatch):
    """An explicit ``--no-verify-ssl`` (False) is kept, not dropped as None."""
    monkeypatch.setattr(cli_app, "GLOBALS", cli_app.GlobalOpts(verify_ssl=False))
    monkeypatch.delenv("HMC_VERIFY_SSL", raising=False)

    cfg = cli._ssh_config()

    assert cfg.verify_ssl is False


def test_ssh_config_falls_back_to_env(monkeypatch):
    """None globals fall back to the HMC_* environment variables."""
    monkeypatch.setenv("HMC_HOST", "env-host")
    monkeypatch.setenv("HMC_USER", "env-user")
    monkeypatch.setenv("HMC_PASSWORD", "env-pass")
    monkeypatch.setenv("HMC_VERIFY_SSL", "true")

    cfg = cli._ssh_config()

    assert cfg.host == "env-host"
    assert cfg.user == "env-user"
    assert cfg.password == "env-pass"
    assert cfg.verify_ssl is True


def test_output_table_none_honors_empty_msg(capsys):
    """table=None with empty entries prints empty_msg, not JSON []."""
    cli_app._output([], as_json=False, table=None, empty_msg="No widgets found")

    captured = capsys.readouterr()
    assert "No widgets found" in captured.err


def test_output_table_none_prints_json_for_nonempty(capsys):
    """table=None with entries prints the entries as JSON on stdout."""
    cli_app._output([{"widget": 1}], as_json=False, table=None)

    captured = capsys.readouterr()
    assert "widget" in captured.out
    assert captured.err == ""
