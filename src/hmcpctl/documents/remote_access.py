"""ManagementConsole RemoteAccess (LDAP and Kerberos) request documents."""

from __future__ import annotations

from xml.etree import ElementTree as ET  # nosec B405

from defusedxml import ElementTree as DET

from ..xmlutil import escapes_string_arguments
from .common import UOM_NS, document_envelope

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
