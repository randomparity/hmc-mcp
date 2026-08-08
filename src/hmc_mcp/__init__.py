"""hmc-mcp: MCP server and CLI for the IBM HMC REST API."""

__version__ = "0.1.0"


def main() -> None:
    """Entry point for the `hmc-mcp` console script."""
    from .cli import app

    app()
