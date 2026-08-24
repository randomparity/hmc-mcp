# UOM user-management design

Issue: [#399](https://github.com/randomparity/hmc-mcp/issues/399)  
Decision: [ADR 0076](../../adr/0076-use-uom-user-profile-and-remote-access.md)

## Outcome

Replace the unsupported web-resource implementation of HMC user, role, LDAP,
and password-policy tools with the documented UOM `ManagementConsole` child
resources and `RemoteAccess` property group. Remove password-policy tools,
models, builders, and exports because no documented target exists.

## Public contracts

- `hmc_list_users(console_uuid, authentication_type="all")` lists
  `ManagementConsole/{console_uuid}/UserProfile` and optionally filters the
  parsed resources by `AuthenticationType` (`Local`, `LDAP`, or `Kerberos`).
- `hmc_get_user`, `hmc_modify_user`, and `hmc_delete_user` take both
  `console_uuid` and `user_profile_uuid`. Creation takes `console_uuid` and a
  `UserProfile` document containing `UserProfilePassword`, description,
  authentication type, role links, and timeout/access settings supplied by the
  caller.
- `hmc_list_task_roles(console_uuid)` and
  `hmc_list_resource_roles(console_uuid)` expose the documented child feeds.
- `hmc_get_remote_access(console_uuid)` gets
  `ManagementConsole/{uuid}?group=RemoteAccess`.
- `hmc_configure_remote_access(console_uuid, ...)` posts a partial
  `ManagementConsole` document to that grouped path. It covers the documented
  LDAP and Kerberos fields from issue #399. Optional values are omitted;
  explicit clearing uses a separate `clear_fields` closed vocabulary and
  emits empty elements. Read-only fields cannot be submitted.
- The prior password-policy and LDAP `?Remove=` tools are removed, not
  deprecated. No request may contain `HmcUser`, `HmcLdapServer`, or
  `HmcPasswordPolicy`.

All UUID path segments pass through the client's existing request-path safety
boundary. Invalid enum values and invalid `clear_fields` values fail before an
HTTP call. Empty successful responses retain the existing `None`/empty-list
convention; HTTP and parse failures retain the shared `HMCError` behavior.

## Components and data flow

`documents.py` builds typed UOM `UserProfile` and `ManagementConsole`
documents. `client_users.py` owns the exact child/group paths, HTTP verbs, and
feed parsing. `server_users.py` exposes the MCP contracts and delegates XML
construction and transport. The existing tool registry and security metadata
remain the registration boundary.

Create uses `PUT` on the child collection; modify and grouped remote-access
updates use `POST`; delete uses `DELETE`. Reads use `GET`. Every request uses
the UOM media type with the documented resource type.

## Error and edge behavior

- Reject unknown authentication filters and clear-field names before I/O.
- An empty child feed returns an empty list; an empty individual/group response
  returns `None`.
- A remote-access update with neither values nor fields to clear is rejected.
- A field cannot be both assigned and cleared in one request.
- Secrets are XML-escaped by the existing recursive argument-boundary
  decorator and are never included in error messages.
- UUID/path traversal attempts are rejected by the shared transport guard.

## Threat model

The MCP caller is an authenticated local operator but its tool arguments are
untrusted. New boundaries are the console/profile UUID path segments and the
LDAP/Kerberos values written to HMC. Existing path-segment rejection prevents
dot-segment traversal; closed vocabularies bound filter and clear-field names;
the XML escaping decorator encodes all caller strings. HMC authorization and
session handling remain owned by `HMCClient`.

Bind passwords and user passwords cross from the MCP caller through process
memory and XML into the authenticated HMC session. They are not logged or
returned. This change does not manage certificates/keytab upload, invent
authorization beyond HMC enforcement, or validate whether a directory URI is
reachable; those are outside scope.

## Test plan

- Builder tests assert UOM roots, documented element names, escaping, omitted
  values, explicit clears, invalid fields, and empty-update rejection.
- Client tests assert every method, nested path, verb, media type, response
  shape, filtering behavior, empty response, malformed XML, HTTP failure, and
  path traversal rejection.
- Tool-contract and security tests assert the new signatures and inventory,
  and prove password-policy/legacy LDAP tools are absent.
- Repository guardrails run through `just test`, `just smoke`, and
  `just verify` before delivery.
