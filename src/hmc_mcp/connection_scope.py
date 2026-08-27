"""Dispatch-time authorization of the HMC connection an MCP call selects.

The access policy loaded by ``access_policy.py`` names the connections each grant
allows. This module answers, for one call and **one grant**, which connection the
call actually selects and whether that grant covers it. It never sees the policy
and never iterates grants: ``dispatch_scope`` owns the only loop, because a single
grant must cover the tool, the connection, *and* the targets together, and a
module that could union one dimension across grants is a module that could break
that rule. See docs/adr/0038-dispatch-time-connection-scope.md, and
docs/adr/0039-dispatch-time-target-scope.md for why the loop moved out.

The connection is not the caller's token. ``config.build_config`` discards the
token entirely when ``HMC_HOST`` is set, and ``config.load_profile`` resolves an
ADR 0030 nickname to a different profile, so the token is normalized to the
connection that will actually be selected before it is compared.
"""

from __future__ import annotations

from collections.abc import Container
from typing import Any

from .access_policy import DEFAULT_CONNECTION_TOKEN
from .audit import MAX_VALUE_LENGTH
from .config import ConfigError, env_var_value, list_profiles_and_nicknames


class ConnectionScopeError(Exception):
    """An MCP call selected an HMC connection the access policy does not grant.

    Also raised when the configured connections cannot be read at all, which is
    denial for the same reason: the server cannot show that the call reaches a
    granted HMC.
    """


# Both messages are closed templates. Only the tool name, the policy name, the
# caller's own token, the declared selector's name, and the one clause below are
# substituted, so "carries no secret" is a property of the text rather than a
# claim about it. In particular the ConfigError's own message — which names the
# config path — is chained as __cause__ and never interpolated.
_UNREADABLE = (
    "{tool} cannot be authorized: the configured HMC connections could not be "
    "read."
)
_DENIED = (
    "{tool} is not permitted on connection {connection} by access policy "
    "{policy}. {clause}Grant that connection in a policy grant that already "
    "names {tool}, or call {tool} with a connection the policy grants."
)
_HMC_HOST_CLAUSE = (
    "HMC_HOST (in any casing) is set, so the {argument!r} argument is ignored "
    "and the call was "
    "evaluated as the {default!r} connection. "
)

# The value a token that names no configured connection normalizes to. It can
# never appear in a compiled grant — `access_policy` rejects an empty connection
# entry — so it always denies, and it denies through the *same* template as a
# resolvable-but-withheld token. Two distinguishable messages would be a
# membership oracle over `config.toml`, disclosed through a channel no policy can
# withhold, one entry after ADR 0037 made that inventory withholdable.
UNRESOLVED = ""


def selected_connection(token: Any, *, tool: str) -> str | None:
    """The connection ``build_config`` will select for *token*, in policy terms.

    Returns a profile key; ``None`` for the environment/default connection — the
    value ``access_policy`` compiles ``"<default>"`` to; or :data:`UNRESOLVED`
    when the token names no configured connection, which no grant can contain
    and which therefore denies through the ordinary denial message. Raises
    :class:`ConnectionScopeError` only when the configuration cannot be read at
    all. *tool* names that error; it is not part of the resolution.

    The four rules mirror ``build_config`` and ``load_profile`` in their order:

    0. a token that is not ``str | None`` names no connection, uninspected and
       uncoerced;
    1. ``HMC_HOST`` set and non-empty collapses every token to the environment
       connection, because ``build_config`` gates its whole TOML branch on it.
       Read through :func:`config.env_var_value`, as that gate is: reading it
       exact-case while ``build_config`` reads it case-insensitively would
       resolve the token to a profile key, let a grant naming that profile
       authorize the call, and send the call to the exported host anyway (#531);
    2. a falsy token is the default connection. ``HMC_PROFILE`` and
       ``default_profile`` are deliberately *not* consulted: ADR 0036 fixed
       ``<default>`` as the denotation of the omitted argument and recorded its
       late binding as an operator-facing property, and ADR 0038's third
       rejected alternative records what substituting them would cost;
    3. otherwise the token is a profile key, or a nickname whose target is one.
       Profiles are consulted first, as ``load_profile`` consults ``nicknames``
       only inside ``if name not in profiles:``; reading the nickname table
       first would resolve a profile key shadowed by a nickname to the wrong
       connection, which is a fail-open.
    """
    if token is not None and not isinstance(token, str):
        return UNRESOLVED
    if env_var_value("HMC_HOST"):
        return None
    if not token:
        return None
    try:
        profiles, nicknames = list_profiles_and_nicknames()
    except ConfigError as error:
        # One arm, because the reader converts every failure it can meet — an
        # unresolvable home, an unreadable or non-UTF-8 or unparseable file, a
        # malformed profiles or nicknames table — into a ConfigError. That total
        # conversion is the contract this catch depends on, and
        # tests/unit/test_config.py exercises each of its arms directly.
        raise ConnectionScopeError(_UNREADABLE.format(tool=tool)) from error
    if token in profiles:
        return token
    target = nicknames.get(token)
    if target is not None and target in profiles:
        return target
    # An unknown name and a dangling nickname both make load_profile raise, so
    # refusing here reaches the same outcome earlier and without the config path.
    return UNRESOLVED


def _clause(argument: str, collapsed: bool) -> str:
    """The one explanatory sentence a denial carries, or none.

    Only the ``HMC_HOST`` collapse gets one, and *collapsed* is the flag the
    decision already computed rather than a second read of the environment — the
    message must describe the decision that was made, not one taken afresh. A
    nickname that resolved to a withheld profile deliberately gets no clause:
    saying so would confirm the token is in the operator's nickname table, which
    is the disclosure the single denial template exists to prevent.
    """
    if not collapsed:
        return ""
    return _HMC_HOST_CLAUSE.format(
        argument=argument, default=DEFAULT_CONNECTION_TOKEN
    )


def connection_permitted(connection: str | None, grant_connections: Container) -> bool:
    """True when one grant's ``connections`` covers the resolved *connection*.

    One grant's set, never a union across grants: ``dispatch_scope`` owns the
    loop, so this function cannot see more than one grant and therefore cannot
    combine one grant's connection with another grant's targets.
    """
    return connection in grant_connections


def _bounded(token: Any) -> Any:
    """The caller's token as the denial renders it: absent-or-default, and bounded.

    Bounded to ``audit.MAX_VALUE_LENGTH`` rather than to a second constant, because the
    *same* value in the *same* call is already truncated there — a message and a record
    that disagree about how much of a caller's input they will echo is an asymmetry with
    no reason behind it. Unbounded, an untrusted client could put a multi-megabyte
    ``profile`` into a denial that FastMCP renders back to it and, under the accepted
    #267, prints to stderr as well. That mattered less when only policy-using
    deployments reached this path; ADR 0041 makes it every deployment.
    """
    if token is None or token == "":
        return DEFAULT_CONNECTION_TOKEN
    if isinstance(token, str):
        return token[:MAX_VALUE_LENGTH]
    return token


def connection_denial(
    tool: str, policy_name: str, argument: str, token: Any, connection: str | None
) -> ConnectionScopeError:
    """The error a connection denial raises, from ADR 0038's single template."""
    # Read after the decision and gated on its result, so the clause can only
    # describe a collapse that actually happened: a non-string token normalizes
    # to UNRESOLVED before rule 1 is reached, and an HMC_HOST that changed
    # between the two reads simply yields no clause.
    collapsed = connection is None and bool(env_var_value("HMC_HOST"))
    return ConnectionScopeError(
        _DENIED.format(
            tool=tool,
            # The caller's own token, never the normalized value: under rule 3
            # that value is a profile key read from config.toml, and a denial is
            # one probe. repr() also neutralizes any control character a caller
            # puts in it.
            connection=repr(_bounded(token)),
            policy=repr(policy_name),
            clause=_clause(argument, collapsed),
        )
    )
