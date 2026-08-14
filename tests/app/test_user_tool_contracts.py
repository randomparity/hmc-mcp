"""Public user-tool documentation contracts."""

from __future__ import annotations

from hmc_mcp import server_users


def test_nullable_user_mutations_document_empty_responses() -> None:
    nullable_mutations = (
        server_users.hmc_create_user,
        server_users.hmc_modify_user,
        server_users.hmc_create_password_policy,
        server_users.hmc_modify_password_policy,
        server_users.hmc_configure_ldap,
    )

    for handler in nullable_mutations:
        assert handler.__doc__ is not None
        normalized_doc = " ".join(handler.__doc__.split())
        assert (
            "None when the HMC returns an empty successful response" in normalized_doc
        )
