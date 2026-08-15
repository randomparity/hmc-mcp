"""hmc-mcp: MCP server and CLI for the IBM HMC REST API."""

from importlib.metadata import version

__version__ = version("hmc-mcp")


def main() -> None:
    """Entry point for the `hmc-mcp` console script."""
    from .cli import app

    app()
