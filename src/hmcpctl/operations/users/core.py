"""Presentation-neutral HMC user and remote-access operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Annotated, Any

from pydantic import BeforeValidator, Field

from hmcpctl.client.core import HMCClient

from ...documents import (
    AUTHENTICATION_TYPES,
    AuthenticationType,
    build_hmc_user_document,
)
from ...errors import HMCError
from ...xmlutil import leaf_text


def _require_timeout_minutes(value: object) -> object:
    """Refuse a VerifySessionTimeout that is not a whole number of minutes.

    The reference sends ``15`` and documents minutes with no upper bound; the V10R3
    capture reads ``0``. ``bool`` subclasses ``int``, so it is refused by name.
    """
    if isinstance(value, bool) or (isinstance(value, int) and value < 0):
        raise ValueError(
            f"verify_session_timeout {value!r} must be a non-negative whole "
            "number of minutes"
        )
    return value


#: The tool parameter type. FastMCP validates arguments in lax mode, overriding even a
#: field-level ``strict``, so JSON ``true`` would become 1 without the validator.
TimeoutMinutes = Annotated[int, Field(ge=0), BeforeValidator(_require_timeout_minutes)]


@dataclass(frozen=True)
class CreateUserRequest:
    """Fields required to create an HMC user profile."""

    user_id: str
    password: str
    authentication_type: AuthenticationType
    description: str | None = None
    associated_task_role: str | None = None
    associated_resource_roles: list[str] | None = None
    password_expiry: int | None = None
    session_timeout: int | None = None
    verify_session_timeout: int | None = None
    idle_session_timeout: int | None = None
    user_inactivity: int | None = None
    minimum_password_age: int | None = None
    allow_web_remote_access: bool | None = None
    allow_ssh_remote_access: bool | None = None
    remote_user_id: str | None = None

    def __post_init__(self) -> None:
        _require_timeout_minutes(self.verify_session_timeout)


@dataclass(frozen=True)
class ModifyUserPatch:
    """User-profile fields to replace; omitted values remain unchanged."""

    password: str | None = None
    description: str | None = None
    authentication_type: AuthenticationType | None = None
    associated_task_role: str | None = None
    associated_resource_roles: list[str] | None = None
    password_expiry: int | None = None
    session_timeout: int | None = None
    verify_session_timeout: int | None = None
    idle_session_timeout: int | None = None
    user_inactivity: int | None = None
    minimum_password_age: int | None = None
    allow_web_remote_access: bool | None = None
    allow_ssh_remote_access: bool | None = None
    remote_user_id: str | None = None

    def __post_init__(self) -> None:
        _require_timeout_minutes(self.verify_session_timeout)


async def create_user(
    hmc: HMCClient,
    console_uuid: str,
    request: CreateUserRequest,
) -> dict[str, Any] | None:
    """Create an HMC user profile."""
    document = build_hmc_user_document(**asdict(request))
    return await hmc.create_hmc_user(console_uuid, document)


# The HMC lists the authentication type in lower case; the builder takes the tool's.
_AUTHENTICATION_SPELLING = {kind.lower(): kind for kind in AUTHENTICATION_TYPES}


def _profile_text(
    resource: dict[str, Any], name: str, user_profile_uuid: str, remedy: str
) -> str:
    """Return a profile element a modify must resend, or refuse before any POST."""
    value = leaf_text(resource.get(name))
    if not isinstance(value, str) or not value:
        raise HMCError(
            f"User profile {user_profile_uuid!r} returned no {name}, which the HMC "
            f"requires to modify it; {remedy}"
        )
    return value


async def modify_user(
    hmc: HMCClient,
    console_uuid: str,
    user_profile_uuid: str,
    patch: ModifyUserPatch,
) -> dict[str, Any] | None:
    """Apply the supplied fields to an HMC user profile.

    V10R3 refuses a modify that omits the read-only ``UserID`` or the
    ``AuthenticationType`` with REST0344 (#1381), so both are read from the profile
    and sent unchanged unless the patch replaces the authentication type. It also
    refuses one without ``UserProfilePassword`` (#1409); with no replacement
    password the builder sends that element empty, as the profile GET serves it.
    """
    profile = await hmc.get_hmc_user(console_uuid, user_profile_uuid)
    resource = (profile or {}).get("Resource") or {}
    user_id = _profile_text(
        resource, "UserID", user_profile_uuid, "confirm the UUID with hmc_list_users"
    )
    fields = asdict(patch)
    if fields["authentication_type"] is None:
        current = _profile_text(
            resource,
            "AuthenticationType",
            user_profile_uuid,
            "passing authentication_type sets how the user authenticates",
        )
        fields["authentication_type"] = _AUTHENTICATION_SPELLING.get(current.lower())
        if fields["authentication_type"] is None:
            raise HMCError(
                f"User profile {user_profile_uuid!r} returned AuthenticationType "
                f"{current!r}, which this tool cannot resend (it knows "
                f"{', '.join(sorted(AUTHENTICATION_TYPES))}); passing "
                "authentication_type would change how the user authenticates"
            )
    document = build_hmc_user_document(user_id=user_id, **fields)
    return await hmc.modify_hmc_user(console_uuid, user_profile_uuid, document)
