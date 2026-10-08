# 0202 — User-profile role associations are role names

## Status

Accepted (2026-10-06), issue #632. Amends one consequence of
[ADR 0076](0076-use-uom-user-profile-and-remote-access.md); its decision is unchanged.

## Context

ADR 0076 expected callers to "select the associated role links required by `UserProfile`", and
`build_hmc_user_document` writes them as links: `<AssociatedTaskRole href="…"/>` and
`<AssociatedResourceRoles><ResourceRole href="…"/></AssociatedResourceRoles>`.

The documented `UserProfile` shape carries neither as a link. Both reference snapshots show
`<AssociatedTaskRole kb="CUD">hmcsuperadmin</AssociatedTaskRole>` and an
`AssociatedResourceRoles` group of `<AssociatedResourceRole kb="CUD">AllSystemResources</…>`
text elements (`docs/refs/hmc-rest-api-p10/user-management/203-userprofile.md`, sample
response; P11 `231-userprofile.md` is identical). The V10R3 M1060 capture in
`tests/fixtures/live/rest-user-profile.json` has the same shape. `kb="CUD"` marks both as
values a caller creates and updates. The element name `ResourceRole` the builder writes inside
the group appears nowhere in either snapshot.

The documented element order also differs from the builder's: `UserDescription` precedes
`AuthenticationType`, and `PasswordExpiry` precedes `AssociatedTaskRole`.

## Decision

`hmc_create_user` and `hmc_modify_user` take `associated_task_role` as a task-role name
(`TaskRoleName` from `hmc_list_task_roles`) and `associated_resource_roles` as resource-role
names (`ResourceRoleName` from `hmc_list_resource_roles`). The builder writes them as the
documented text elements and emits every element in the documented response order. An empty
string or empty list still clears the association. `AuthenticationType` keeps the reference's
spelling at the tool (`Local`, `LDAP`, `Kerberos`) and goes on the wire in lower case, the form
the HMC lists. Effects, target kinds and authorization are unchanged.

The live users arm (issue #632) checks the decision by creating a user with the viewer task
role and reading the association back. Its first run (V10R3 M1060, 2026-10-06) read every
role association as a name and every `AuthenticationType` as lower case, and the HMC refused the
create body, which then carried `Local`, with `REST0001 Failed to unmarshal input payload`.
A second run with the lower-case value was refused with the same code and the schema message
"Value 'CUR' is not facet-valid with respect to enumeration '[COR]'": an element the builder
marks `kb="CUR"` must be `COR`, as the HMC's own listing marks `UserID`. Neither refusal
concerns the role elements, so this decision stands; the create body's `kb` markings are the
open defect (#632 follow-up), and `user.create` carries the failed observation. If the HMC refuses the documented shape, that run's
observation is `failed` and this record is superseded by one that cites the capture.

## Consequences

Issue #1409's 2026-10-08 probe at
`d2b676b37f553af6ff162e44d9f211ff86c2a1d8` on V10R3M1060 accepted both
modifies without a supplied password when the builder sent the empty
`UserProfilePassword` exactly as GET serves it (`ksv="V1_17_0" kb="CUD" kxe="false"`).
Keep the optional password interface; a supplied replacement is sent as before and create
still requires one. This demonstrates accepted writes, not password-preservation testing.

The same run read back description replacement and VerifySessionTimeout replacement, then
returned `HMC User` after `description=""`. The users arm had required an empty readback,
so its `user.modify` observation remains **failed**. The operator approved correcting that
expectation to the exact observed default, retaining the other checks. The REST reference
does not document empty-update normalization; this records observed V10R3 behavior only.
Recovery was clean and initial/final user lists matched. Consolidated live verification of
the corrected harness is pending; the failed observation is not promoted by offline tests.

- A caller that passed an href now sends its URL as a role name, which the HMC rejects or
  stores as the literal text. The tool descriptions and generated tool docs say which value
  to pass.
- `hmc_list_users` output already shows role names, so a caller can copy a user's role to
  another user without a second lookup.

## Considered & rejected

- **Require a password or change to SSH for #1409.** verified: the users ST11 probe on
  V10R3M1060 at `d2b676b37f553af6ff162e44d9f211ff86c2a1d8` accepted the empty element.
- **Accept any description after clearing.** judgment: that would hide an ignored update;
  the corrected check requires the observed `HMC User` default.

- **Keep the href form.** verified: no reference snapshot or capture shows a role link on
  `UserProfile` (`rg -n 'AssociatedTaskRole' docs/refs tests/fixtures/live` on 81902847 finds
  text values only).
- **Accept either a name or an href and translate.** judgment: a second input form with no
  documented target is the compatibility route ADR 0076 ruled out.
- **Decide only after a live create.** judgment: the documented and captured shapes already
  agree; the live run is the check, and a refusal supersedes this record with its capture.
