"""``hmc_inventory``: the primary logical inventory tool (ADR 0196).

Each part of the inventory is authorized as the specialist tool it delegates to,
through the served ``dispatch_authorizer``, so a handler needs the application's
``permits`` and ``authorize`` gates; that is why it is built by a factory rather
than a ``tool_module`` decorator, as ``gateway`` is.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from fastmcp import FastMCP

from ..._app import with_client
from ...authorization.connection_scope import ConnectionScopeError
from ...authorization.target_scope import TargetScopeError
from ...operations.inventory.logical import (
    Admit,
    InventoryPage,
    check_inputs,
    read_inventory,
)
from ...operations.logical.store import connection_label
from ...operations.partition_state import PartitionState
from ...tool_registry import (
    Authorize,
    ToolSecurity,
    annotations_for,
    authorized,
    validate_security,
)

INVENTORY_TOOL_NAME = "hmc_inventory"
# "console" and not exhaustive, as hmc_operation_status: a list of selectors has no
# selector shape, so the delegated tools carry the target bound (ADR 0196 Decision 4).
INVENTORY_SECURITY = ToolSecurity(
    effect="read", operation="inventory.logical", target_kind="console"
)


def _admitter(
    tool_security: Mapping[str, ToolSecurity],
    permits: Callable[[str], bool],
    authorize: Authorize,
    profile: str | None,
) -> Admit:
    def admit(name: str, system: str | None) -> str | None:
        if not permits(name):
            return f"{name} is not permitted by this server's access policy"
        security = tool_security[name]
        arguments = {target.argument: system for target in security.targets}
        if security.connection_argument is not None:
            arguments[security.connection_argument] = profile
        try:
            authorize(name, security, arguments)
        except (TargetScopeError, ConnectionScopeError) as exc:
            return str(exc)
        return None

    return admit


def inventory_handler(
    tool_security: Mapping[str, ToolSecurity],
    permits: Callable[[str], bool],
    authorize: Authorize,
) -> Callable[..., InventoryPage]:
    """The unwrapped ``hmc_inventory`` handler bound to the served gates."""

    def hmc_inventory(
        systems: list[str] | None = None,
        lpar_state: PartitionState | None = None,
        owner: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        profile: str | None = None,
    ) -> InventoryPage:
        """List one connection's managed systems and partitions, with capacity and owner.

        Each part is authorized as the tool it delegates to: enumerating systems as
        ``hmc_list_systems``, partitions as ``hmc_list_lpars`` and owners as
        ``hmc_list_lpar_ownership`` for each system, and capacity as
        ``hmc_capacity_report``, which only an ``all-targets`` grant admits. A part the
        policy denies, or the HMC fails to return, is reported in ``sources`` as
        ``denied`` or ``unavailable`` and contributes no data; a missing figure is
        ``null``, never zero. If the HMC stops answering altogether, the call can fail as a
        tool error instead. Without ``hmc_list_systems`` authority nothing is read: pass
        ``systems`` selectors, each authorized separately. Ids are
        ``<connection>/<system uuid>[/<partition uuid>]``. Pages follow live state, so a
        partition created or deleted between pages can be missed or repeated.

        Args:
            systems: 1 to 16 managed-system names or UUIDs; omit to enumerate.
            lpar_state: Only partitions in this state.
            owner: Only partitions whose hmcpctl ownership stamp names this agent id.
            limit: Most partitions on one page, 1 to 200.
            cursor: ``next_cursor`` from the previous page, unchanged.
            profile: HMC connection profile.
        """
        # ADR 0094: a client may send an unset optional string as "".
        owner = (owner or "").strip() or None
        cursor = (cursor or "").strip() or None
        check_inputs(systems, owner, limit, cursor)
        admit = _admitter(tool_security, permits, authorize, profile)
        connection = connection_label(profile, tool=INVENTORY_TOOL_NAME)
        return with_client(
            lambda hmc: read_inventory(
                hmc,
                connection=connection,
                admit=admit,
                systems=systems,
                lpar_state=lpar_state,
                owner=owner,
                limit=limit,
                cursor=cursor,
            ),
            profile=profile,
        )

    return hmc_inventory


def register_inventory_tool(
    mcp: FastMCP,
    tool_security: Mapping[str, ToolSecurity],
    *,
    permits: Callable[[str], bool],
    authorize: Authorize,
) -> None:
    """Register ``hmc_inventory`` on *mcp* when the ceiling admits it."""
    if not permits(INVENTORY_TOOL_NAME):
        return
    handler = inventory_handler(tool_security, permits, authorize)
    validate_security(INVENTORY_SECURITY, handler)
    mcp.tool(
        authorized(INVENTORY_TOOL_NAME, INVENTORY_SECURITY, handler, authorize),
        annotations=annotations_for(INVENTORY_SECURITY.effect),
    )
