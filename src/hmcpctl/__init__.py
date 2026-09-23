"""hmcpctl: MCP server and CLI for the IBM HMC REST API."""

from importlib.metadata import version


def __getattr__(name: str) -> str:
    """Resolve package metadata only when a caller requests the version."""
    if name == "__version__":
        return version("hmcpctl")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> None:
    """Entry point for the `hmcpctl` console script."""
    from .cli import app

    app()
