"""MCP server exposing the IBM HMC REST API as MCP tools.

Run:
    hmc-mcp config init-access-policy          # once, then review the file it writes
    hmc-mcp serve --access-policy NAME         # stdio transport (default, for agents)
    hmc-mcp serve --access-policy NAME --http  # streamable HTTP on 127.0.0.1:8000

``--access-policy`` is required: since ADR 0041 no application can be composed without
one, and ``serve`` refuses rather than starting unbounded.

The HTTP transport is UNAUTHENTICATED: it exposes every enabled tool,
including user administration, to anyone who can reach the port. Bind only
to loopback (the default). ``hmc-mcp serve --http`` refuses to bind beyond
loopback unless ``--allow-remote`` is passed; even then, gate the endpoint
with an authenticated reverse proxy (MCP gateway or HTTPS proxy with
bearer-token auth). Never expose it directly on a network. The arbitrary
``hmc_run_command`` escape hatch is disabled unless serve is started with
``--enable-arbitrary-command``.

Authentication:
    REST tools authenticate via HMC_USER/HMC_PASSWORD (see
    ``client_from_env``). SSH-passthrough tools (those that run HMC CLI
    commands via ``run_hmc_command``) use the same env-var configuration as
    ``hmc_run_command``: set HMC_SSH_KEY_FILE for key-based auth, otherwise
    HMC_PASSWORD is used.

Addressing:
    Public tools generally accept a resource name or UUID where their parameter
    is named ``*_name_or_uuid``. Parameters explicitly named ``*_uuid`` require
    a UUID. Most SSH-passthrough tools resolve UUIDs to CLI names before running
    the HMC command. VIOS backup catalog tools are different: listing uses a VIOS
    UUID directly, while backup and restore pass a direct system name and VIOS
    UUID through without REST. A VIOS name or backup/restore system UUID requires
    REST resolution and has no ``lssyscfg`` fallback; a system UUID resolves to
    its unique MTMS identity rather than its CLI name.

This module is the MCP composition and serving bootstrap boundary. Tool handlers
live in domain adapters under ``server_tools/``, and
``create_mcp`` explicitly registers each domain on a fresh application instance.
The serving entry points also validate startup policy, emit startup diagnostics,
and configure the logging boundaries for stdio and HTTP transports.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Callable, Mapping
from typing import Final

from fastmcp import FastMCP

# The exact logger object FastMCP renders a failed tool call through, taken by
# reference rather than by name so that a FastMCP that moves it breaks loudly at
# import instead of quietly restoring the traceback panel #267 is about.
from fastmcp.server.server import logger as _fastmcp_logger

from ._app import (
    ceiling_aware_instructions,
    create_mcp as _create_base_mcp,
)
from .access_policy import AccessPolicy, unboundable_effect_tools
from .audit_sink import (
    StreamSafeFormatter,
    install_audit_sink,
    set_audit_level,
    sink_handler,
    write_diagnostic,
)
from .connection_scope import ConnectionScopeError
from .dispatch_scope import dispatch_authorizer
from .target_scope import TargetScopeError
from .tool_registry import Authorize, ToolSecurity, build_tool_security
from .server_tools import (
    adapters as server_adapters,
    capacity as server_capacity,
    composite as server_composite,
    console as server_console,
    health as server_health,
    jobs as server_jobs,
    lpar_config as server_lpar_config,
    lpars as server_lpars,
    lpm as server_lpm,
    metrics as server_metrics,
    network as server_network,
    profiles as server_profiles,
    provision as server_provision,
    snapshot as server_snapshot,
    storage as server_storage,
    system_resources as server_system_resources,
    systems as server_systems,
    templates as server_templates,
    updates as server_updates,
    users as server_users,
    vios as server_vios,
)
from .server_tools.command import (
    HMC_RUN_COMMAND_SECURITY,
    configure_arbitrary_command_tool,
)
from .server_tools.permissions import (
    EFFECTIVE_PERMISSIONS_SECURITY,
    TOOL_NAME as PERMISSIONS_TOOL_NAME,
    register_permissions_tool,
)


TOOL_MODULES = (
    server_systems,
    server_capacity,
    server_jobs,
    server_health,
    server_lpars,
    server_vios,
    server_adapters,
    server_storage,
    server_network,
    server_lpm,
    server_templates,
    server_metrics,
    server_users,
    server_updates,
    server_profiles,
    server_snapshot,
    server_lpar_config,
    server_system_resources,
    server_composite,
    server_provision,
    server_console,
)


TOOL_SECURITY: Mapping[str, ToolSecurity] = build_tool_security(
    [module.tool_security() for module in TOOL_MODULES],
    {
        "hmc_run_command": HMC_RUN_COMMAND_SECURITY,
        "hmc_effective_permissions": EFFECTIVE_PERMISSIONS_SECURITY,
    },
)


def _gates(policy: AccessPolicy) -> tuple[Callable[[str], bool], Authorize]:
    """The registration-time and dispatch-time questions *policy* answers.

    Derived together and always passed together: a site given one without the
    other registers tools it does not authorize, which is the drift ADR 0038's
    registry assertion exists to catch. Neither is optional since ADR 0041 — a
    policy is now mandatory, so there is no composition for a ``None`` gate to
    describe.
    """
    return policy.permits_tool, dispatch_authorizer(policy)


def create_mcp(policy: AccessPolicy) -> FastMCP:
    """Compose a fresh MCP application bounded by *policy*.

    A policy is mandatory (ADR 0041). This is the only composer in the package,
    which is what makes "no unbounded application exists" a property of the code
    rather than of the serve path alone: the stdio and HTTP transports, the
    arbitrary-command toggle, the smoke script, and the live-test runner all
    arrive here. ``hmc-mcp config init-access-policy`` writes a policy granting
    what an unpolicied server used to grant, for a deployment that needs one.

    The ``None`` check is explicit because an annotation refuses nothing at
    runtime: a caller holding a ``None`` from elsewhere would otherwise compose
    an application with no ceiling and no authorizer, which is exactly the state
    this signature exists to remove.

    Both gates are passed to each registration site rather than checked here, so
    no site can be given a policy it does not apply; ADR 0038's registry
    assertion is what checks that it did.

    The ceiling reaches the base application's ``instructions`` as well as its
    registry (ADR 0048). The string is fixed at construction and shipped whole at
    ``initialize``, so the qualification has to be decided here, where the policy
    and the authoritative tool index are both in hand.
    """
    if policy is None:
        raise TypeError(
            "create_mcp requires an access policy; composing without one is no longer "
            "supported. Run 'hmc-mcp config init-access-policy' to generate a policy "
            "granting what an unpolicied server used to grant, review it, then pass it "
            "here or select it with 'hmc-mcp serve --access-policy NAME'."
        )
    permits, authorize = _gates(policy)
    application = _create_base_mcp(ceiling_aware_instructions(permits, TOOL_SECURITY))
    for module in TOOL_MODULES:
        module.register_tools(application, permits=permits, authorize=authorize)
    register_permissions_tool(
        application, policy, TOOL_SECURITY, permits=permits, authorize=authorize
    )
    return application


def _startup_warnings(
    tool_count: int,
    access_policy: AccessPolicy,
    enable_arbitrary_command: bool,
) -> tuple[str, ...]:
    """The stderr lines describing what this server will and will not expose.

    Every input exists only here — the served registry, the policy, and the
    escape-hatch flag — which is why these warnings share one function. An
    empty surface already implies the inspection tool is absent, so it replaces
    that line rather than printing beside it.

    ADR 0041 retired a fourth: a policy file that existed but had not been
    selected. No server starts without a policy now, so the condition it
    described is unreachable, and ``_unselected_policy_file`` went with it.

    #279 adds a fifth: one line per grant whose targets table cannot bind part
    of what it reaches, most often through an effect class. `access_policy.py`
    already refuses a grant outright when a table binds *none* of what it
    reaches; a grant reaching a mix is deliberately not refused (that would
    discard a working majority to diagnose an unreachable minority), so its
    dead subset is surfaced here instead — this is where a compiled policy's
    consequences are already reported, and where an operator already looks.
    """
    lines: list[str] = []
    if tool_count == 0:
        lines.append(
            "warning: this server exposes no tools. Nothing it is asked to do will "
            "succeed."
        )
    elif not access_policy.permits_tool(PERMISSIONS_TOOL_NAME):
        lines.append(
            f"warning: access policy {access_policy.name!r} withholds "
            f"{PERMISSIONS_TOOL_NAME}, so this server cannot report its own "
            "effective permissions to a client."
        )
    if enable_arbitrary_command and not access_policy.permits_tool("hmc_run_command"):
        lines.append(
            "warning: --enable-arbitrary-command was requested, but access policy "
            f"{access_policy.name!r} does not grant hmc_run_command, so it is not "
            "exposed. Name it in a grant's tools to allow it."
        )
    lines.extend(
        f"warning: {message}"
        for message in unboundable_effect_tools(access_policy, TOOL_SECURITY)
    )
    return tuple(lines)


def _warn(lines: tuple[str, ...]) -> None:
    """Write startup diagnostics to stderr, or to nowhere at all.

    Never to stdout, which carries JSON-RPC framing under the stdio transport, and
    never at the cost of the start itself — which is what ``_unselected_policy_file``
    already refused for *resolving* a policy. There are four ways for the
    destination to fail a diagnostic and none of them may abort a start: absent
    (CPython sets ``sys.stderr`` to ``None`` when fd 2 is not open at interpreter
    start, as ``serve 2>&-`` arranges), broken, closed, and — the one #269 names —
    open but never read, which blocks instead of raising.

    ``audit``'s sink owns all four, so this function neither resolves the stream nor
    writes to it. One mechanism rather than two: a second writer to the same
    descriptor would carry its own failure mode, and here that mode is a start that
    hangs (ADR 0043).
    """
    for line in lines:
        write_diagnostic(line)


#: What the concise line says. Fixed text with nothing interpolated: the
#: authorization audit record (ADR 0040) already carries the policy, the tool, the
#: effect, the decision, the reason, the connection, and the targets, and this line
#: exists to stop a denial *looking* like a crash, not to restate that record. Since
#: ADR 0051 both go onto the same FIFO sink from the same call, so the record
#: normally precedes this line on stderr — normally, because a full queue drops one
#: of the two and leaves the other. The text names neither, which is why that is a
#: note here rather than a claim in the line itself.
_DENIAL_LINE = (
    "authorization denied; the authorization audit record carries the decision"
)


class _DenialFilter(logging.Filter):
    """Rewrite FastMCP's tool-error record when the error is a policy denial.

    ADR 0046. Two facts about FastMCP make this work, and both are pinned by
    ``fastmcp-slim==3.4.7`` and asserted on captured stderr by
    ``tests/app/test_connection_authorization.py``:

    - ``FastMCP._call_tool`` logs an unhandled handler exception through
      ``fastmcp.server.server.logger`` — imported by object below, so a version
      that moves or renames it fails at import rather than silently rendering
      panels again;
    - a record carrying ``exc_info`` renders as a traceback and one clearing it
      renders as one line. That was true of ``configure_logging``'s two
      ``RichHandler``s, which filter on ``record.exc_info is not None``, and it
      stays true of the single sink-backed handler ADR 0051 puts in their place,
      whose ``logging.Formatter`` appends a traceback only when there is one.
      Clearing ``exc_info`` is how a record asks for one line either way.

    Only a record whose exception *is* a scope error is touched, which is what
    keeps this from being the blanket suppression #267 rejected: a handler bug
    raises something else and keeps its panel.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        error = record.exc_info[1] if record.exc_info else None
        if not isinstance(error, ConnectionScopeError | TargetScopeError):
            return True
        record.exc_info = None
        # Set at format time, so normally still unset here; cleared anyway
        # because a cached rendering would survive the line above.
        record.exc_text = None
        record.msg = _DENIAL_LINE
        record.args = None
        record.levelno = logging.WARNING
        record.levelname = "WARNING"
        return True


def install_denial_log_filter() -> None:
    """Install the filter on the logger FastMCP renders tool errors through.

    Separate from ``create_mcp`` and called beside ``install_audit_sink`` for the
    same reason it is: composing an application must not mutate global logging
    state (ADR 0040). Idempotent, so a process that serves twice adds one filter.

    A logger filter rather than a handler filter: ``configure_logging`` removes
    and re-adds the ``fastmcp`` logger's handlers on every call, so a filter
    attached to a handler is discarded by the next reconfiguration, while a
    filter on the emitting logger is not.
    """
    if not any(isinstance(each, _DenialFilter) for each in _fastmcp_logger.filters):
        _fastmcp_logger.addFilter(_DenialFilter())


#: FastMCP's own logger, and the second of this module's two couplings to that
#: package's logging layout. ``_fastmcp_logger`` is where tool errors are
#: *emitted* — ``fastmcp.server.server``, imported by object so a rename fails at
#: import. This is where they are *handled*: ``configure_logging`` attaches every
#: handler it builds to the ``fastmcp`` root of that namespace, and a child logger
#: reaches them by propagation.
_FASTMCP_LOGGER_NAME: Final = "fastmcp"

#: How a FastMCP record renders once its ``RichHandler``s are gone. Taken from
#: ``configure_logging``'s non-rich branch, which is FastMCP's own answer to
#: rendering these records without ``rich``. Installing a ``Formatter`` at all is
#: the load-bearing part: ``logging.Formatter.format`` is what appends
#: ``exc_info``'s traceback, and without one the sink-backed handler would render
#: the bare message and silently undo ADR 0046's guarantee that a genuine handler
#: bug keeps its traceback.
_FASTMCP_LINE_FORMAT: Final = "%(levelname)s: %(message)s"

#: The third-party loggers the served path binds to ADR 0043's sink (ADR 0051,
#: widened by #330). Each gets its own handler and its own producer-named prefix;
#: ``uvicorn`` and ``uvicorn.access`` additionally get the level and propagation
#: their own ``LOGGING_CONFIG`` would have given them, because the lever this
#: takes -- ``log_config=None`` -- runs no ``dictConfig`` at all. ``fastmcp`` and
#: ``mcp`` stay handlers-only: neither sits inside another bound namespace.
_THIRD_PARTY_LOGGERS: Final = ("fastmcp", "uvicorn", "uvicorn.access", "mcp")

#: The uvicorn namespaces whose level the install pins to INFO. Access records are
#: emitted at INFO, and with no ``dictConfig`` they would inherit root's WARNING --
#: the access log would not move into the sink, it would disappear.
_UVICORN_LEVEL_LOGGERS: Final = ("uvicorn", "uvicorn.access")

#: What ``main_http`` passes FastMCP so the ``uvicorn.Config`` it constructs never
#: runs uvicorn's own ``configure_logging`` ``dictConfig`` (uvicorn 0.52.1 skips it
#: entirely on a null config, ``config.py:384``): the default ``StreamHandler``
#: that would otherwise land on fd 2 *after* the sink install never attaches, and
#: nothing has to re-install after it. Deliberately without ``log_level``: that
#: lever reaches only uvicorn's ``.error``/``.access``/``.asgi`` children and never
#: ``uvicorn`` itself, so levels belong to the install above.
_UVICORN_CONFIG: Final = {"log_config": None}


def install_third_party_stderr_sinks() -> None:
    """Put the bound third-party loggers' stderr output on ADR 0043's queue. ADR 0051.

    ADR 0043 bounded every write *this package* makes to fd 2, on the reasoning
    that a blocked ``write()`` there wedges the server. FastMCP's two
    ``RichHandler``s write to the same descriptor and were not on that queue, so
    the bound was on this package's contribution rather than on the stream. This
    replaces them with one handler feeding the same sink.

    **A handler attached here survives.** ``fastmcp/__init__.py`` calls
    ``configure_logging`` once, at import of ``fastmcp`` and only when
    ``settings.log_enabled``. The only other caller is ``temporary_log_level``,
    which reconfigures nothing when its level is falsy, and neither ``main_stdio``
    nor ``main_http`` passes ``log_level`` to ``.run()``. Verified against
    ``fastmcp-slim==3.4.7``, which this project pins exactly.

    **Every handler goes, not only a recognized ``RichHandler``.** Deciding a
    handler's destination means reading ``rich``'s ``Console.file``, and when
    ``settings.log_enabled`` is false there is no handler to recognize at all.
    Taking the list wholesale is ADR 0051's accepted cost — it also displaces a
    handler an operator attached to any bound logger themselves — and it is what makes
    "no handler on this logger writes to fd 2" something a test asserts rather
    than infers. Removing and re-adding is what makes this idempotent: a second
    call takes out the handler the first one left. A removed handler is not
    ``close()``d: it is no longer reachable through this logger, ``logging.shutdown``
    flushes and closes it at exit anyway, and closing a handler this package did not
    open would be a second liberty on top of removing it.

    **Only the handlers — with one documented exception.** For ``fastmcp`` and
    ``mcp`` the level, ``propagate`` flag, and filters are untouched — including
    ``_DenialFilter``, which sits on the child logger and solves a different
    problem: this handler decides *where* a record goes, that filter decides *what*
    a denial record says. The ``uvicorn`` pair is the exception ADR 0051's
    amendment records: skipping uvicorn's ``dictConfig`` skips its level and
    propagation configuration too, so the install reproduces it — both loggers at
    INFO (access records are INFO; left alone they would inherit root's WARNING and
    the access log would silently vanish) and ``propagate = False`` (with the
    parent-plus-child bindings left propagating, ``callHandlers`` would render
    every access record twice). One thing the wholesale removal takes with it in a
    test process is ``pytest``'s own ``LogCaptureHandler``, so a test that serves
    and then asserts on a bound record through ``caplog`` would pass vacuously;
    nothing does today.

    **The rendering is marked, not just formatted.** ``StreamSafeFormatter`` puts a
    fixed non-JSON prefix on every physical line and escapes the control
    characters, because a rendered exception carries HMC-returned text onto a
    stream whose grammar is one line of JSON per record. See ADR 0051.

    **Called unconditionally**, including when ``settings.log_enabled`` is false
    and the removal loop finds nothing to remove. That case is the reason not to
    skip rather than a reason to: a logger with no handler anywhere above it falls
    through to ``logging.lastResort``, a ``StreamHandler`` on fd 2 that writes
    synchronously and unbounded, which is precisely the writer this exists to
    remove.
    """
    for name in _THIRD_PARTY_LOGGERS:
        logger = logging.getLogger(name)
        for existing in logger.handlers[:]:
            logger.removeHandler(existing)
        handler = sink_handler()
        handler.setFormatter(StreamSafeFormatter(_FASTMCP_LINE_FORMAT, f"{name}: "))
        logger.addHandler(handler)
        if name in _UVICORN_LEVEL_LOGGERS:
            logger.setLevel(logging.INFO)
            logger.propagate = False


def _serve_application(
    enable_arbitrary_command: bool,
    access_policy: AccessPolicy,
    audit_level: int | None = None,
) -> FastMCP:
    """Compose, gate, and diagnose the application about to be served."""
    application = create_mcp(access_policy)
    permits, authorize = _gates(access_policy)

    async def _prepare() -> int:
        await configure_arbitrary_command_tool(
            enable_arbitrary_command,
            application,
            permits=permits,
            authorize=authorize,
        )
        return len(await application.local_provider.list_tools())

    tool_count = asyncio.run(_prepare())
    # Installed here rather than in `create_mcp` or at import: this is where the
    # process has been established as a server, and where `_warn` already writes
    # to stderr for the same reason. Composing an application must not mutate
    # global logging state (ADR 0040).
    # Before the install, so the operator's choice survives its NOTSET-default
    # rule (#270): the level split ADR 0040 offers against record volume is
    # reachable from the documented stdio deployment now too, and ADR 0040's
    # rejected-alternatives note is amended accordingly.
    if audit_level is not None:
        set_audit_level(audit_level)
    install_audit_sink()
    install_third_party_stderr_sinks()
    install_denial_log_filter()
    _warn(_startup_warnings(tool_count, access_policy, enable_arbitrary_command))
    return application


def main_stdio(
    access_policy: AccessPolicy,
    enable_arbitrary_command: bool = False,
    audit_level: int | None = None,
) -> None:
    """Start an MCP server over stdio, bounded by *access_policy*.

    *audit_level*, when given, is applied to the authorization audit logger
    before its stderr sink installs (#270); ``None`` leaves the sink's own
    default in force.
    """
    _serve_application(
        enable_arbitrary_command, access_policy, audit_level=audit_level
    ).run()


def main_http(
    access_policy: AccessPolicy,
    host: str = "127.0.0.1",
    port: int = 8000,
    enable_arbitrary_command: bool = False,
    allow_remote: bool = False,
    audit_level: int | None = None,
) -> None:
    """Start an MCP server over streamable HTTP, bounded by *access_policy*.

    *audit_level* means what it does in :func:`main_stdio`.
    """
    if not allow_remote and not _is_loopback(host):
        raise ValueError(
            f"listen host {host!r} binds beyond loopback, but the streamable HTTP "
            "server has no authentication and exposes every enabled tool "
            "(including user administration). Refusing to start. Explicitly "
            "authorize remote binding and put an authenticated reverse proxy in front."
        )
    _serve_application(
        enable_arbitrary_command, access_policy, audit_level=audit_level
    ).run(
        transport="streamable-http",
        host=host,
        port=port,
        uvicorn_config=_UVICORN_CONFIG,
    )


def _is_loopback(host: str) -> bool:
    """Return true only when every resolved bind address is an IP loopback."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for family, _, _, _, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            return False
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return False
        if not address.is_loopback:
            return False
    return True
