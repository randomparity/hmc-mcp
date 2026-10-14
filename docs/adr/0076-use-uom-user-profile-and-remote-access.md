# 0076: Use UOM user profiles and remote access

## Status

Accepted

## Context

The user-management tools call undocumented `/rest/api/web/HmcUser`,
`HmcLdapServer`, and `HmcPasswordPolicy` resources. Current Power10 and Power11
HMC documentation instead places users and roles below a `ManagementConsole`
resource and represents LDAP and Kerberos settings through its `RemoteAccess`
property group. It documents no password-policy resource.

## Decision

Model user management with the documented child resources
`UserProfile`, `TaskRole`, and `ResourceRole` below a caller-supplied management
console UUID. Identify individual profiles and roles by UUID. Read and update
LDAP and Kerberos settings by getting and posting the management console's
`RemoteAccess` group. Remove the password-policy surface completely.

This replaces the old public contracts; no compatibility route or translation
to the unsupported web resources remains.

## Consequences

- User and remote-access tools require a management console UUID.
- Individual user operations require a user-profile UUID rather than a login
  name; listings expose both identifiers.
- Role inventory becomes available so callers can select the associated role
  links required by `UserProfile`.
- Remote-access updates are partial documents containing only supplied fields;
  clearing a field is explicit through nullable string values represented as
  empty XML elements.
- Password-policy tools, request models, and tests disappear because there is
  no supported target for them.

## Considered & rejected

- **Keep the `/rest/api/web/Hmc*` paths as a fallback.** verified: issue #399
  records zero matching resources in the 2026-08-22 Power10 and Power11 API
  documentation and live `REST000E` responses in issues #99 and #113.
- **Resolve user UUIDs implicitly from login names.** judgment: this hides an
  extra network read and ambiguity inside mutating operations while the UOM
  contract already supplies stable profile UUIDs.
- **Retain password-policy tools until a replacement appears.** verified:
  issue #399's documented-resource inventory identifies only the read-only
  `IsPasswordPolicyEnabled` profile property and no writable policy resource.

## Amendment (2026-10-13, #1538): a read returns `RemoteUserID` without the HMC's newline

The V10R3 HMC serves a Kerberos profile's `RemoteUserID` with a trailing newline that the create
did not send (`tests/fixtures/live/rest-user-profile-kerberos.json`). A user read does not
return it: `element_to_dict` in `src/hmcpctl/xmlutil.py` strips every leaf's text, so
`hmc_get_user` and `hmc_list_users` return the principal with surrounding whitespace removed.
That strip is shared behaviour and has been in place since the parser was written; this
amendment records what it does to `RemoteUserID` and adds no new normalisation. The earlier note
in `tests/fixtures/live/README.md` that hmcpctl returns the newline was wrong.
`tests/security/test_users.py` pins the stripped read against the capture.

The value is not normalised further, and a modify that omits `remote_user_id` does not resend it.
