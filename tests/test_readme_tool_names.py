"""Guard that every ``hmc_*`` tool name in README and guide prose is a registered tool.

Tool names in user documentation are facts derivable from the registry, so a tool removal must
not be able to strand a prose reference behind it. ADR 0076 deleted
``hmc_remove_ldap_config`` while the documentation kept naming it, because nothing derived the
prose names from ``TOOL_SECURITY``.
"""

import re
from pathlib import Path

from hmc_mcp.server import TOOL_SECURITY

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = (
    "README.md",
    "docs/configuration.md",
    "docs/compatibility.md",
    "docs/python-api.md",
    "docs/development.md",
    "docs/cli.md",
    "docs/mcp-server.md",
    "docs/operations.md",
)

# ``\b`` before the prefix keeps this from matching the tail of a longer identifier.
IDENTIFIER = re.compile(r"\bhmc_[a-z0-9_]+")

# ``hmc_*`` identifiers the documentation names that are not tools. Both invariants below hold
# them to their subject, so neither can outlive it and quietly widen the guard.
NON_TOOL_IDENTIFIERS = frozenset(
    {
        "hmc_mcp",  # the import package, named in library-usage examples
        "hmc_timeout_minutes",  # an argument of the NIM install tools
    }
)


def documentation_identifiers() -> dict[str, str]:
    """Map each identifier to its first source location across the moved guides."""
    found: dict[str, str] = {}
    for document in DOCUMENTS:
        text = (ROOT / document).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            for name in IDENTIFIER.findall(line):
                found.setdefault(name, f"{document}:{lineno}")
    return found


def test_every_documented_tool_name_is_registered() -> None:
    """No documented ``hmc_*`` name may be absent from the tool registry."""
    found = documentation_identifiers()
    unregistered = {
        name: lineno
        for name, lineno in found.items()
        if name not in TOOL_SECURITY and name not in NON_TOOL_IDENTIFIERS
    }
    assert not unregistered, (
        "Documentation names hmc_* identifiers that are neither registered tools nor "
        "listed in NON_TOOL_IDENTIFIERS: "
        + ", ".join(
            f"{name} ({lineno})"
            for name, lineno in sorted(unregistered.items())
        )
        + ". Drop the reference or name a tool that server.TOOL_SECURITY registers."
    )


def test_documentation_names_registered_tools() -> None:
    """The extraction reaches real tool names, so a green run is not a vacuous one."""
    assert documentation_identifiers().keys() & set(TOOL_SECURITY)


def test_non_tool_identifiers_are_still_absent_from_the_registry() -> None:
    """A name that becomes a tool must leave the exemption set."""
    registered = NON_TOOL_IDENTIFIERS & set(TOOL_SECURITY)
    assert not registered, (
        f"NON_TOOL_IDENTIFIERS exempts registered tools: {sorted(registered)}. "
        "Remove them so the guard covers them."
    )


def test_non_tool_identifiers_are_still_in_the_documentation() -> None:
    """An exemption whose subject left the documentation must leave with it."""
    stale = NON_TOOL_IDENTIFIERS - documentation_identifiers().keys()
    assert not stale, (
        f"NON_TOOL_IDENTIFIERS lists identifiers the documentation no longer names: "
        f"{sorted(stale)}. Remove them so the set stays minimal."
    )
