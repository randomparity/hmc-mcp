"""Root Typer application and command-group composition for the CLI.

The per-domain command modules expose ``register_commands(group)`` functions.
``cli.py`` is the single composition entry point: it imports those modules and
registers every command on the groups defined here before exposing the root app.
"""

from __future__ import annotations

import typer
from typer._click.core import ParameterSource

from .runtime import GlobalOpts
from .serve import serve

app = typer.Typer(
    name="hmc-mcp",
    help="IBM HMC (Hardware Management Console) MCP server and CLI.",
    no_args_is_help=True,
)

systems_app = typer.Typer(help="Managed systems (Power servers).", no_args_is_help=True)
lpars_app = typer.Typer(help="Logical partitions (LPARs).", no_args_is_help=True)
adapters_app = typer.Typer(
    help="Virtual adapters (network/storage) on LPARs.", no_args_is_help=True
)
storage_app = typer.Typer(
    help="VIOS storage: volume groups, virtual disks, mappings.", no_args_is_help=True
)
cluster_app = typer.Typer(
    help="Clusters / Shared Storage Pools (logical units).", no_args_is_help=True
)
metrics_app = typer.Typer(
    help="PCM performance/capacity metrics.", no_args_is_help=True
)
network_app = typer.Typer(
    help="Virtual networks / switches / bridges.", no_args_is_help=True
)
templates_app = typer.Typer(
    help="Template library (partition templates).", no_args_is_help=True
)
vios_app = typer.Typer(help="Virtual I/O Servers.", no_args_is_help=True)
console_app = typer.Typer(help="The HMC itself.", no_args_is_help=True)
jobs_app = typer.Typer(help="HMC jobs.", no_args_is_help=True)
raw_app = typer.Typer(help="Raw REST escape hatch.", no_args_is_help=True)
snapshot_app = typer.Typer(help="Portable LPAR snapshots.", no_args_is_help=True)

app.add_typer(systems_app, name="systems")
app.add_typer(lpars_app, name="lpars")
app.add_typer(adapters_app, name="adapters")
app.add_typer(storage_app, name="storage")
app.add_typer(cluster_app, name="cluster")
app.add_typer(metrics_app, name="metrics")
app.add_typer(network_app, name="network")
app.add_typer(templates_app, name="templates")
app.add_typer(vios_app, name="vios")
app.add_typer(console_app, name="console")
app.add_typer(jobs_app, name="jobs")
app.add_typer(raw_app, name="raw")
app.add_typer(snapshot_app, name="snapshot")

memory_pools_app = typer.Typer(help="Shared memory pools.", no_args_is_help=True)
app.add_typer(memory_pools_app, name="memory-pools")

config_app = typer.Typer(
    # Not "profile configuration": this group writes two different files. Naming
    # only profiles here would assert the conflation the docs work to refuse, in
    # the surface an operator reaches straight from `serve`'s refusal.
    help="Connection profiles (config.toml) and server access policies "
    "(access-policy.toml).",
    no_args_is_help=True,
)
app.add_typer(config_app, name="config")


@app.callback()
def main(
    ctx: typer.Context,
    host: str | None = typer.Option(
        None, "--host", envvar="HMC_HOST", help="HMC hostname or IP"
    ),
    user: str | None = typer.Option(
        None, "--user", "-u", envvar="HMC_USER", help="HMC user"
    ),
    password: str | None = typer.Option(
        None,
        "--password",
        "-p",
        envvar="HMC_PASSWORD",
        help="HMC password",
        hide_input=True,
    ),
    verify_ssl: bool | None = typer.Option(
        None,
        "--verify-ssl/--no-verify-ssl",
        envvar="HMC_VERIFY_SSL",
        help="Verify the HMC TLS certificate",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        envvar="HMC_PROFILE",
        help="Named profile from ~/.config/hmc-mcp/config.toml (or platform equivalent)",
    ),
) -> None:
    option_names = ("host", "user", "password", "verify_ssl", "profile")
    command_line_options = frozenset(
        name
        for name in option_names
        if ctx.get_parameter_source(name) == ParameterSource.COMMANDLINE
    )
    ctx.obj = GlobalOpts(
        host=host,
        user=user,
        password=password,
        verify_ssl=verify_ssl,
        profile=profile,
        command_line_options=command_line_options,
    )


app.command()(serve)
