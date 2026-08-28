"""The legacy-equivalent access policy: built, rendered, and compiled.

Since ADR 0041 a server cannot be composed without an access policy, so every existing
deployment needs one that grants what the unpolicied server granted. This module is where
that document is authored. It is a different job from ``access_policy.py``, which loads,
validates, and compiles an operator's own file; this one writes the operator's first file
for them, and ``cli_commands.config`` is what puts it on disk.

The grant names its tools explicitly rather than granting effect classes. An ``effects``
grant would silently confer every tool a later release adds; a named list pins the surface
to what was legacy at generation time and leaves a new tool ungranted until the operator
regenerates and reads the diff. That is the fail-closed direction, and it is what makes the
file reviewable rather than merely permissive.

Like ``compile_access_policy``, this module takes ``tool_security`` as a parameter rather
than importing it: ``server`` imports the access-policy layer, so the dependency runs one
way. See docs/adr/0041-fail-closed-startup-and-legacy-policy-generation.md.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from typing import Any, Final

from ..access_policy import (
    ALL_TARGETS_TOKEN,
    DEFAULT_CONNECTION_TOKEN,
    AccessPolicy,
    compile_access_policy,
)
from ..config import list_profiles_and_nicknames
from ..tool_registry import ToolSecurity

#: The name the generated policy carries, and therefore the name an operator passes to
#: ``--access-policy``.
LEGACY_POLICY_NAME: Final = "legacy-equivalent"

#: What :func:`compile_legacy_policy` reports as the policy's origin. A fixed label rather
#: than a path, so a message rendering it — a denial, a startup warning — discloses no
#: filesystem location for a document that was never on the filesystem.
GENERATED_SOURCE: Final = "<generated legacy-equivalent policy>"

#: The escape hatch is never in a rendered document. ADR 0041 keeps
#: ``--enable-arbitrary-command`` insufficient on its own (epic #218 requirement 6), which a
#: generated grant naming this tool would undo.
ARBITRARY_COMMAND_TOOL: Final = "hmc_run_command"

# TOML's basic-string escapes, in the spelling the format names. Every other character
# below U+0020, plus U+007F, is escaped as \uXXXX by `_escape`.
_SHORT_ESCAPES: Final[Mapping[str, str]] = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}

_HEADER: Final = f"""\
# hmc-mcp access policy — generated, and meant to be read before it is used.
#
# This is the {LEGACY_POLICY_NAME!r} policy: it grants exactly what an hmc-mcp server
# granted before an access policy became mandatory. It is a MIGRATION AID, not a
# recommended posture — it names every ordinary tool, including every destructive one, on
# every configured connection, with no target restriction. A new deployment should start
# from the read-only example in the README instead and add what it needs.
#
# Activate it with:  hmc-mcp serve --access-policy {LEGACY_POLICY_NAME}
# Inspect it live with the hmc_effective_permissions tool.
#
# {ARBITRARY_COMMAND_TOOL} is deliberately absent. It executes arbitrary HMC CLI commands,
# so it stays a separate decision: add it to the tools list below AND start the server with
# --enable-arbitrary-command. Both are required.
#
# REGENERATING. This command never overwrites, so regenerate to a scratch path and merge by
# hand:
#
#     hmc-mcp config init-access-policy --output /tmp/access-policy.new
#     diff /tmp/access-policy.new <this file>
#
# Compare BOTH the tools and the connections arrays: a tool added by an upgrade and a
# profile added to config.toml are each ungranted until you add them here. If you added
# {ARBITRARY_COMMAND_TOOL} by hand it will always show as a deletion in that diff — the
# generator cannot emit it — and that is expected.
#
# config.toml holds HMC connection profiles; this file holds server access policies. They
# are separate files with separate lifecycles, and the connections below are profile KEYS.
"""


def _escape(value: str) -> str:
    """Render *value* as the body of a TOML basic string.

    Total by construction: every character either passes through or has an escape, so no
    profile key can terminate its string early. That is what makes "a generated file always
    parses, and holds exactly the keys ``config.toml`` holds" a property rather than a hope
    — an unescaped quote would produce an unparseable file, and an unescaped ``"]`` plus a
    newline could open a grant table inside the document an operator is told to review.

    The boundary is TOML's own and was checked against this interpreter's ``tomllib``:
    U+0000-U+001F and U+007F are forbidden raw in a basic string, while C1 (U+0080-U+009F)
    is ordinary. So DEL is escaped and C1 is left alone; escaping C1 too would be harmless
    but would misstate where the rule actually falls.
    """
    out = []
    for char in value:
        if (short := _SHORT_ESCAPES.get(char)) is not None:
            out.append(short)
        elif char < " " or char == "\x7f":
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return "".join(out)


def _array(values: Sequence[str]) -> str:
    """Render a TOML array of basic strings with one entry per line."""
    body = "".join(f'\n    "{_escape(value)}",' for value in values)
    return f"[{body}\n]"


def legacy_tools(
    tool_security: Mapping[str, ToolSecurity],
    *,
    include_arbitrary_command: bool = False,
) -> tuple[str, ...]:
    """Every tool the unpolicied server registered, sorted.

    *include_arbitrary_command* is not reachable from any CLI path and
    :func:`render_legacy_policy` does not accept it, so no file this module writes can
    grant the escape hatch. ``scripts/live_test_runner.py`` is its one caller: that harness
    drives ``hmc_run_command`` against a real HMC, and passes the gates it composed under,
    so without the opt-in its own ``--enable-arbitrary-command`` toggle would register
    nothing.
    """
    names = set(tool_security)
    if not include_arbitrary_command:
        names.discard(ARBITRARY_COMMAND_TOOL)
    return tuple(sorted(names))


def legacy_connections() -> tuple[str, ...]:
    """The default connection, then every configured profile key.

    Read through ``list_profiles_and_nicknames`` for its single read: every reader now
    converts every failure into a ``ConfigError`` — an unresolvable home, an unreadable or
    non-UTF-8 or unparseable file, a malformed table — and a generator that swallowed one
    would write a policy granting less than it claims. The nicknames half is discarded:
    ADR 0030 resolves an alias to its target before ADR 0038 compares it, so a granted
    nickname could never match.

    ``DEFAULT_CONNECTION_TOKEN`` is always present — under ``HMC_HOST`` every token
    collapses to it, and an omitted ``profile`` argument means it — and is filtered out of
    the profile half, because ``[profiles."<default>"]`` is a legal TOML key and the
    resulting duplicate is one ``access_policy`` refuses. An unconditional prepend colliding
    with the operator's own data would be this module's defect, not theirs, and would leave
    that deployment unable to generate a policy at all.
    """
    profiles, _nicknames = list_profiles_and_nicknames()
    keys = sorted(name for name in profiles if name != DEFAULT_CONNECTION_TOKEN)
    return (DEFAULT_CONNECTION_TOKEN, *keys)


def legacy_document(
    tool_security: Mapping[str, ToolSecurity],
    connections: Sequence[str],
    *,
    include_arbitrary_command: bool = False,
) -> dict[str, Any]:
    """The generated policy as the mapping ``tomllib`` would produce for it."""
    return {
        "policies": {
            LEGACY_POLICY_NAME: {
                "grants": [
                    {
                        "tools": list(
                            legacy_tools(
                                tool_security,
                                include_arbitrary_command=include_arbitrary_command,
                            )
                        ),
                        "connections": list(connections),
                        "targets": ALL_TARGETS_TOKEN,
                    }
                ]
            }
        }
    }


def render_legacy_policy(
    tool_security: Mapping[str, ToolSecurity], connections: Sequence[str]
) -> str:
    """The generated policy as TOML text, header comment included.

    Hand-rolled rather than serialized: epic #218 requirement 11 forbids a new runtime
    dependency and ``tomllib`` only reads. The document is one table with three keys, and
    :func:`_escape` covers the only untrusted input in it.

    Takes no ``include_arbitrary_command``, deliberately — see :func:`legacy_tools`.
    """
    tools = legacy_tools(tool_security)
    return (
        f"{_HEADER}\n"
        f"[[policies.{LEGACY_POLICY_NAME}.grants]]\n"
        f"tools = {_array(tools)}\n"
        f"connections = {_array(list(connections))}\n"
        f'targets = "{ALL_TARGETS_TOKEN}"\n'
    )


def compile_legacy_policy(
    tool_security: Mapping[str, ToolSecurity],
    connections: Sequence[str],
    *,
    include_arbitrary_command: bool = False,
) -> AccessPolicy:
    """Compile the generated policy without touching the filesystem.

    Raises :class:`~.access_policy.AccessPolicyError` for a document that would not load,
    which is how the generator learns before it writes: escaping makes the rendered text
    *parse*, and ADR 0036 enforces rules on entry content that escaping cannot satisfy.
    """
    return compile_access_policy(
        legacy_document(
            tool_security,
            connections,
            include_arbitrary_command=include_arbitrary_command,
        ),
        LEGACY_POLICY_NAME,
        tool_security,
        GENERATED_SOURCE,
    )


def compile_rendered_policy(
    text: str, tool_security: Mapping[str, ToolSecurity]
) -> AccessPolicy:
    """Compile rendered text, so the generator writes only what a server could load.

    The rendered document rather than the mapping it came from, because the rendering is
    the artifact: this is what proves the escaping produced something ``tomllib`` accepts
    and ``compile_access_policy`` admits, on the operator's own connection list, before any
    file exists.
    """
    return compile_access_policy(
        tomllib.loads(text), LEGACY_POLICY_NAME, tool_security, GENERATED_SOURCE
    )
