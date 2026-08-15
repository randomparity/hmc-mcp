"""Contract tests for client-visible MCP instructions."""

from hmc_mcp._app import create_mcp

_CONVENTIONS_HEADING = "## Resource addressing and asynchronous jobs"
_WORKFLOWS_HEADING = "## Recommended workflows"
_EXPECTED_CONVENTIONS = """## Resource addressing and asynchronous jobs

Parameters ending in `*_name_or_uuid` accept either a resource name or UUID. Parameters
ending in `*_uuid` require a UUID. SSH-passthrough tools resolve UUIDs to HMC CLI names
before running the command.

For tools that expose asynchronous wait controls, `wait=False` is the default and returns
the submitted job for later polling; `wait=True` polls until the job reaches a terminal
state. Install tools use `wait_timeout_seconds=None` by default, deriving the client-side
polling budget from `hmc_timeout_minutes` plus one poll interval. Other wait-capable tools
use `timeout_seconds=300` by default. `poll_interval=5` is the default number of seconds
between status requests.

"""


def test_instructions_publish_resource_and_wait_conventions():
    """Clients receive one complete conventions block before workflow guidance."""
    instructions = create_mcp().instructions

    assert instructions is not None
    conventions_start = instructions.index(_CONVENTIONS_HEADING)
    workflows_start = instructions.index(_WORKFLOWS_HEADING)

    assert conventions_start < workflows_start
    assert instructions[conventions_start:workflows_start] == _EXPECTED_CONVENTIONS
