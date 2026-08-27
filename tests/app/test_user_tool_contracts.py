"""Public user-tool documentation contracts."""

from __future__ import annotations

from hmc_mcp.server_tools import users as server_users


def test_nullable_user_mutations_document_empty_responses() -> None:
    nullable_mutations = (
        server_users.hmc_create_user,
        server_users.hmc_modify_user,
        server_users.hmc_configure_remote_access,
    )

    for handler in nullable_mutations:
        assert handler.__doc__ is not None
        normalized_doc = " ".join(handler.__doc__.split())
        assert "None" in normalized_doc or "partial" in normalized_doc
