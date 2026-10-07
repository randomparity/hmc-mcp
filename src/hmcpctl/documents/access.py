from __future__ import annotations

from typing import Literal, get_args
from xml.etree import ElementTree as ET  # nosec B405

from defusedxml import ElementTree as DET

from ..xmlutil import WEB_NS, escapes_string_arguments
from .common import UOM_NS, document_envelope

AuthenticationType = Literal["Local", "LDAP", "Kerberos"]
AUTHENTICATION_TYPES = frozenset(get_args(AuthenticationType))


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


# Every UserProfile element carries the facets the V10R3 capture lists: UserID is
# create-only (COR) and the rest modifiable (CUD), and each names its schema level.
# A create missing ksv, or carrying any other kb, fails REST0001 (#1375).
_USER_ID_ATTRS = 'ksv="V1_17_0" kb="COR" kxe="false"'
_MODIFIABLE_ATTRS = 'ksv="V1_17_0" kb="CUD" kxe="false"'


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
    """Build a documented UOM ``UserProfile`` create or update document.

    Children follow the documented response order, and roles are names rather
    than links (ADR 0202): ``203-userprofile.md`` in the reference and the V10R3
    capture carry ``AssociatedTaskRole`` and ``AssociatedResourceRole`` as text.
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
    ):
        if value is not None:
            parts.append(f"  <{name} {_MODIFIABLE_ATTRS}>{value}</{name}>")
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
        ("RemoteUserID", remote_user_id),
    ):
        if value is not None:
            rendered = str(value).lower() if isinstance(value, bool) else value
            parts.append(f"  <{name} {_MODIFIABLE_ATTRS}>{rendered}</{name}>")
    return document_envelope("UserProfile", "\n".join(parts), UOM_NS)


_LDAP_FIELDS = (
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
)
_KERBEROS_FIELDS = (
    "KerberosEnabled",
    "DefaultRealm",
    "ClockSkew",
    "TicketLifeTime",
    "AuthenticationTimeOut",
)
# Each scalar RemoteAccess field lives in one documented container, never directly
# under ManagementConsole (199-ldap.md:98-118 and 198-kerberos.md:103-124 in the
# reference; the V10R3 console feed has the same shape). The KDC list
# (`RealmConfig/KerberosRealm/{HostName,Realm}`) is not a scalar and is not settable.
_REMOTE_ACCESS_CONTAINERS = {
    **dict.fromkeys(_LDAP_FIELDS, "LdapConfiguration"),
    **dict.fromkeys(_KERBEROS_FIELDS, "KerberosConfiguration"),
}
REMOTE_ACCESS_FIELDS = frozenset(_REMOTE_ACCESS_CONTAINERS)


def _render_remote_access_value(value: str | int | bool) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def _validate_remote_access_update(
    supplied: dict[str, str | int | bool], cleared: list[str]
) -> None:
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


@escapes_string_arguments
def build_remote_access_document(
    values: dict[str, str | int | bool] | None = None,
    clear_fields: list[str] | None = None,
) -> str:
    """Build a partial documented ``ManagementConsole`` RemoteAccess document."""
    supplied = values or {}
    cleared = clear_fields or []
    _validate_remote_access_update(supplied, cleared)
    parts = ["  <Metadata><Atom/></Metadata>"]
    for container in ("LdapConfiguration", "KerberosConfiguration"):
        lines = [
            f'    <{name} kb="CUR" kxe="false">'
            f"{_render_remote_access_value(value)}</{name}>"
            for name, value in supplied.items()
            if _REMOTE_ACCESS_CONTAINERS[name] == container
        ] + [
            f'    <{name} kb="CUR" kxe="false"/>'
            for name in cleared
            if _REMOTE_ACCESS_CONTAINERS[name] == container
        ]
        if lines:
            parts.append(f'  <{container} schemaVersion="V1_0">')
            parts.extend(lines)
            parts.append(f"  </{container}>")
    return document_envelope("ManagementConsole", "\n".join(parts), UOM_NS)


def _remote_access_field(console: ET.Element, name: str) -> ET.Element:
    """Return field *name* inside its container, adding the field if absent."""
    container_name = _REMOTE_ACCESS_CONTAINERS[name]
    container = console.find(f"{{*}}{container_name}")
    if container is None:
        raise ValueError(
            f"RemoteAccess response has no {container_name}, so {name} cannot be "
            "changed; this HMC level does not offer RemoteAccess configuration"
        )
    child = container.find(f"{{*}}{name}")
    if child is None:
        child = ET.SubElement(container, f"{{{UOM_NS}}}{name}")
    return child


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

    for name, value in (values or {}).items():
        _remote_access_field(console, name).text = _render_remote_access_value(value)
    for name in clear_fields or []:
        child = _remote_access_field(console, name)
        child.text = None
        child.set("kb", "CUR")
        child.set("kxe", "false")
    return ET.tostring(console, encoding="unicode")
