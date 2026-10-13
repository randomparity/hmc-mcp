"""UOM UserProfile request documents."""

from __future__ import annotations

from typing import Literal, get_args

from ..xmlutil import escapes_string_arguments
from .common import UOM_NS, document_envelope

AuthenticationType = Literal["Local", "LDAP", "Kerberos"]
AUTHENTICATION_TYPES = frozenset(get_args(AuthenticationType))


# Every UserProfile element carries the facets the V10R3 capture lists: UserID is
# create-only (COR) and the rest modifiable (CUD), and each names its schema level.
# A create missing ksv, or carrying any other kb, fails REST0001 (#1375).
_USER_ID_ATTRS = 'ksv="V1_17_0" kb="COR" kxe="false"'
_MODIFIABLE_ATTRS = 'ksv="V1_17_0" kb="CUD" kxe="false"'
#: What a create sends when the caller gives no description: V10R3 refuses a create
#: without UserDescription (REST0344, #1414), and `mkhmcusr` defaults it the same way.
DEFAULT_USER_DESCRIPTION = "HMC User"


@escapes_string_arguments
def build_hmc_user_document(
    user_id: str | None = None,
    authentication_type: AuthenticationType | None = None,
    password: str | None = None,
    description: str | None = None,
    associated_task_role: str | None = None,
    associated_resource_roles: list[str] | None = None,
    password_expiry: int | None = None,
    session_timeout: int | None = None,
    verify_session_timeout: int | None = None,
    idle_session_timeout: int | None = None,
    user_inactivity: int | None = None,
    minimum_password_age: int | None = None,
    allow_web_remote_access: bool | None = None,
    allow_ssh_remote_access: bool | None = None,
    remote_user_id: str | None = None,
) -> str:
    """Build a documented UOM ``UserProfile`` create or update document.

    Children follow the V10R3 schema's ``UserProfile`` sequence, which the
    captured profiles also serve, and roles are names rather than links (ADR 0202):
    ``203-userprofile.md`` in the reference and the V10R3 capture carry
    ``AssociatedTaskRole`` and ``AssociatedResourceRole`` as text.
    """
    if authentication_type is not None and authentication_type not in (
        AUTHENTICATION_TYPES
    ):
        raise ValueError(
            f"Invalid authentication_type {authentication_type!r}. Must be one of: "
            f"{', '.join(sorted(AUTHENTICATION_TYPES))}"
        )
    parts = ["  <Metadata><Atom/></Metadata>"]
    if user_id is not None:
        parts.append(f"  <UserID {_USER_ID_ATTRS}>{user_id}</UserID>")
    for name, value in (
        ("UserDescription", description),
        # The tool keeps the reference's spelling; the HMC lists and accepts only
        # lower case (a V10R3 create carrying `Local` failed REST0001, #632).
        (
            "AuthenticationType",
            None if authentication_type is None else authentication_type.lower(),
        ),
        ("UserProfilePassword", password),
        ("PasswordExpiry", password_expiry),
        # The V10R3 schema places it here, before AssociatedTaskRole; sent last,
        # a create failed REST0001 (#1414).
        ("RemoteUserID", remote_user_id),
    ):
        if value is not None:
            parts.append(f"  <{name} {_MODIFIABLE_ATTRS}>{value}</{name}>")
        elif name == "UserProfilePassword":
            # V10R3 refuses a modify without this element (REST0344, #1409) and
            # never returns the value, so an unchanged password is sent empty,
            # as the GET serves it.
            parts.append(f"  <{name} {_MODIFIABLE_ATTRS}/>")
    if associated_task_role is not None:
        if associated_task_role:
            parts.append(
                f"  <AssociatedTaskRole {_MODIFIABLE_ATTRS}>"
                f"{associated_task_role}</AssociatedTaskRole>"
            )
        else:
            parts.append(f"  <AssociatedTaskRole {_MODIFIABLE_ATTRS}/>")
    if associated_resource_roles is not None:
        if associated_resource_roles:
            roles = "".join(
                f"<AssociatedResourceRole {_MODIFIABLE_ATTRS}>{role}"
                "</AssociatedResourceRole>"
                for role in associated_resource_roles
            )
            parts.append(
                f'  <AssociatedResourceRoles {_MODIFIABLE_ATTRS} schemaVersion="V1_0">'
                f"<Metadata><Atom/></Metadata>{roles}</AssociatedResourceRoles>"
            )
        else:
            parts.append(f"  <AssociatedResourceRoles {_MODIFIABLE_ATTRS}/>")
    for name, value in (
        ("SessionTimeout", session_timeout),
        ("VerifySessionTimeout", verify_session_timeout),
        ("IdleSessionTimeout", idle_session_timeout),
        ("UserInactivity", user_inactivity),
        ("MinimumPasswordAge", minimum_password_age),
        ("AllowWebRemoteAccess", allow_web_remote_access),
        ("AllowSSHRemoteAccess", allow_ssh_remote_access),
    ):
        if value is not None:
            rendered = str(value).lower() if isinstance(value, bool) else value
            parts.append(f"  <{name} {_MODIFIABLE_ATTRS}>{rendered}</{name}>")
    return document_envelope("UserProfile", "\n".join(parts), UOM_NS)
