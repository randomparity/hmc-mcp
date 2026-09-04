# Domain modules use the common vocabulary and XML imports directly.
# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .common import *
from ..documents_shared import document_envelope, lpar_envelope
from ..xmlutil import escapes_string_arguments

@escapes_string_arguments
def build_logon_request_document(user: str, password: str) -> str:
    """LogonRequest document carrying the configured HMC credentials (PUT).

    The credentials arrive from ``HMCConfig`` rather than from a tool
    argument, which is why they reach this boundary as an explicit builder
    call instead of through a decorator on the client method: an
    argument-boundary decorator on ``HMCClient.logon`` would never see them.
    """
    body = f"""  <Metadata>
    <Atom/>
  </Metadata>
  <UserID kb="CUR" kxe="false">{user}</UserID>
  <Password kb="CUR" kxe="false">{password}</Password>"""
    return document_envelope("LogonRequest", body, WEB_NS)


# UOM UserProfile and ManagementConsole RemoteAccess documents


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
    verify_session_timeout: bool | None = None,
    idle_session_timeout: int | None = None,
    user_inactivity: int | None = None,
    minimum_password_age: int | None = None,
    allow_web_remote_access: bool | None = None,
    allow_ssh_remote_access: bool | None = None,
    remote_user_id: str | None = None,
) -> str:
    """Build a documented UOM ``UserProfile`` create or update document."""
    parts = ["  <Metadata><Atom/></Metadata>"]
    if user_id is not None:
        parts.append(f'  <UserID kb="CUR" kxe="false">{user_id}</UserID>')
    if authentication_type is not None:
        if authentication_type not in AUTHENTICATION_TYPES:
            raise ValueError(
                f"Invalid authentication_type {authentication_type!r}. Must be one of: "
                f"{', '.join(sorted(AUTHENTICATION_TYPES))}"
            )
        parts.append(
            f'  <AuthenticationType kb="CUR" kxe="false">'
            f"{authentication_type}</AuthenticationType>"
        )
    if password is not None:
        parts.append(
            f'  <UserProfilePassword kb="CUR" kxe="false">'
            f"{password}</UserProfilePassword>"
        )
    if description is not None:
        parts.append(
            f'  <UserDescription kb="CUR" kxe="false">{description}</UserDescription>'
        )
    if associated_task_role is not None:
        if associated_task_role:
            parts.append(f'  <AssociatedTaskRole href="{associated_task_role}"/>')
        else:
            parts.append('  <AssociatedTaskRole kb="CUR" kxe="false"/>')
    if associated_resource_roles is not None:
        if associated_resource_roles:
            parts.append("  <AssociatedResourceRoles>")
            parts.extend(
                f'    <ResourceRole href="{role}"/>'
                for role in associated_resource_roles
            )
            parts.append("  </AssociatedResourceRoles>")
        else:
            parts.append('  <AssociatedResourceRoles kb="CUR" kxe="false"/>')
    for name, value in (
        ("PasswordExpiry", password_expiry),
        ("SessionTimeout", session_timeout),
        ("VerifySessionTimeout", verify_session_timeout),
        ("IdleSessionTimeout", idle_session_timeout),
        ("UserInactivity", user_inactivity),
        ("MinimumPasswordAge", minimum_password_age),
        ("AllowWebRemoteAccess", allow_web_remote_access),
        ("AllowSSHRemoteAccess", allow_ssh_remote_access),
        ("RemoteUserID", remote_user_id),
    ):
        if value is not None:
            rendered = str(value).lower() if isinstance(value, bool) else value
            parts.append(f'  <{name} kb="CUR" kxe="false">{rendered}</{name}>')
    return document_envelope("UserProfile", "\n".join(parts), UOM_NS)


REMOTE_ACCESS_FIELDS = frozenset(
    {
        "LdapEnabled",
        "PrimaryLdapUri",
        "SecondaryLdapUri",
        "TLSEncryptionEnabled",
        "UseNonAnonymousBinding",
        "BindDistinguishedName",
        "BindPassword",
        "LoginAttribute",
        "BaseDistinguishedName",
        "SearchScope",
        "AutoManageEnabled",
        "UserPolicyAtrribute",
        "SearchFilter",
        "LdapGroupLogin",
        "LdapGroupMemberAttribute",
        "KerberosAuthenticationEnabled",
        "kerberosRemoteUserId",
        "KerberosEnabled",
        "DefaultRealm",
        "ClockSkew",
        "TicketLifeTime",
        "AuthenticationTimeOut",
        "RealmConfig",
        "KerberosRealm",
        "Hostname",
        "Realm",
    }
)


@escapes_string_arguments
def build_remote_access_document(
    values: dict[str, str | int | bool] | None = None,
    clear_fields: list[str] | None = None,
) -> str:
    """Build a partial documented ``ManagementConsole`` RemoteAccess document."""
    supplied = values or {}
    cleared = clear_fields or []
    unknown = (set(supplied) | set(cleared)) - REMOTE_ACCESS_FIELDS
    if unknown:
        raise ValueError(f"Unknown RemoteAccess fields: {', '.join(sorted(unknown))}")
    conflicts = set(supplied) & set(cleared)
    if conflicts:
        raise ValueError(
            f"RemoteAccess fields both set and cleared: {', '.join(sorted(conflicts))}"
        )
    if not supplied and not cleared:
        raise ValueError("RemoteAccess update must set or clear at least one field")
    parts = ["  <Metadata><Atom/></Metadata>"]
    for name, value in supplied.items():
        rendered = str(value).lower() if isinstance(value, bool) else value
        parts.append(f'  <{name} kb="CUR" kxe="false">{rendered}</{name}>')
    parts.extend(f'  <{name} kb="CUR" kxe="false"/>' for name in cleared)
    return document_envelope("ManagementConsole", "\n".join(parts), UOM_NS)


def merge_remote_access_document(
    current_xml: str,
    values: dict[str, str | int | bool] | None = None,
    clear_fields: list[str] | None = None,
) -> str:
    """Merge explicit RemoteAccess changes into the current console document."""
    # Validate the requested mutation before parsing or changing the current document.
    build_remote_access_document(values, clear_fields)
    root = DET.fromstring(current_xml.encode("utf-8"))
    console = root if root.tag.rsplit("}", 1)[-1] == "ManagementConsole" else None
    if console is None:
        console = root.find(".//{*}ManagementConsole")
    if console is None:
        raise ValueError("RemoteAccess response does not contain ManagementConsole")

    children = {child.tag.rsplit("}", 1)[-1]: child for child in console}
    for name, value in (values or {}).items():
        child = children.get(name)
        if child is None:
            child = ET.SubElement(console, f"{{{UOM_NS}}}{name}")
        child.text = str(value).lower() if isinstance(value, bool) else str(value)
    for name in clear_fields or []:
        child = children.get(name)
        if child is None:
            child = ET.SubElement(console, f"{{{UOM_NS}}}{name}")
        child.clear()
        child.set("kb", "CUR")
        child.set("kxe", "false")
    return ET.tostring(console, encoding="unicode")

