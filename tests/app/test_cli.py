"""Tests for CLI helpers — ``ssh_config`` honours invocation options.

SSH-backed CLI commands build their ``HMCConfig`` through ``ssh_config`` so
the global ``--host/--user/--password/--verify-ssl`` flags are honoured even
when the command itself defines no HMC options.  Explicit ``None`` overrides
must be dropped so env vars / ``.env`` still fill the remaining fields (an
explicit init arg would otherwise shadow the environment).
"""

from __future__ import annotations

from hmc_mcp.cli_commands import output as cli_output
from hmc_mcp.cli_commands import runtime as cli_runtime
from hmc_mcp.resource_identity import is_uuid


def test_cross_module_cli_seams_use_public_internal_names() -> None:
    runtime_names = {
        "client",
        "current_options",
        "run",
        "ssh_config",
        "with_client",
    }
    output_names = {
        "fail",
        "first_field",
        "output",
        "partition_not_found",
        "print_json",
        "usage_error",
    }

    assert all(hasattr(cli_runtime, name) for name in runtime_names)
    assert all(hasattr(cli_output, name) for name in output_names)
    assert not any(hasattr(cli_runtime, f"_{name}") for name in runtime_names)
    assert not any(hasattr(cli_output, f"_{name}") for name in output_names)


def test_ssh_config_uses_global_overrides(monkeypatch):
    """Set global flags are passed through to the SSH HMCConfig."""
    options = cli_runtime.GlobalOpts(
        host="flag-host", user="flag-user", password="flag-pass", verify_ssl=True
    )
    monkeypatch.setattr(cli_runtime, "current_options", lambda: options)

    cfg = cli_runtime.ssh_config()

    assert cfg.host == "flag-host"
    assert cfg.user == "flag-user"
    assert cfg.password == "flag-pass"
    assert cfg.verify_ssl is True


def test_ssh_config_keeps_false_verify_ssl(monkeypatch):
    """An explicit ``--no-verify-ssl`` (False) is kept, not dropped as None."""
    monkeypatch.setattr(
        cli_runtime,
        "current_options",
        lambda: cli_runtime.GlobalOpts(verify_ssl=False),
    )
    monkeypatch.delenv("HMC_VERIFY_SSL", raising=False)

    cfg = cli_runtime.ssh_config()

    assert cfg.verify_ssl is False


def test_ssh_config_falls_back_to_env(monkeypatch):
    """None globals fall back to the HMC_* environment variables."""
    monkeypatch.setenv("HMC_HOST", "env-host")
    monkeypatch.setenv("HMC_USER", "env-user")
    monkeypatch.setenv("HMC_PASSWORD", "env-pass")
    monkeypatch.setenv("HMC_VERIFY_SSL", "true")
    monkeypatch.setattr(cli_runtime, "current_options", cli_runtime.GlobalOpts)

    cfg = cli_runtime.ssh_config()

    assert cfg.host == "env-host"
    assert cfg.user == "env-user"
    assert cfg.password == "env-pass"
    assert cfg.verify_ssl is True


def test_output_table_none_honors_empty_msg(capsys):
    """table=None with empty entries prints empty_msg, not JSON []."""
    cli_output.output([], as_json=False, table=None, empty_msg="No widgets found")

    captured = capsys.readouterr()
    assert "No widgets found" in captured.err


def test_output_table_none_prints_json_for_nonempty(capsys):
    """table=None with entries prints the entries as JSON on stdout."""
    cli_output.output([{"widget": 1}], as_json=False, table=None)

    captured = capsys.readouterr()
    assert "widget" in captured.out
    assert captured.err == ""


def test_is_uuid_matches_hex_uuid():
    """is_uuid accepts canonical lowercase/uppercase hex UUIDs."""
    assert is_uuid("11111111-1111-4111-8111-111111111111")
    assert is_uuid("ABCDEFAB-1234-ABCD-EF01-23456789ABCD")


def test_is_uuid_rejects_names_and_non_hex_shapes():
    """is_uuid rejects names and UUID-shaped strings that are not hex.

    The old predicate accepted any 36-char dash-containing string, so a
    partition name (or a typo'd UUID) with that shape silently bypassed
    name resolution.
    """
    assert not is_uuid("lpar1")
    # 36-char dash-containing string with non-hex characters.
    assert not is_uuid("zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz")
    # A hex-shaped UUID with a single non-hex character.
    assert not is_uuid("11111111-1111-4111-8111-11111111111g")
    # Wrong length.
    assert not is_uuid("11111111-1111-4111-8111-11111111111")
