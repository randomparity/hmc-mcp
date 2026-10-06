# Verify user, role and remote-access contracts (V8)

Issue #632 (epic #620, entry V8). Decision record:
[ADR 0202](../../adr/0202-user-profile-roles-are-named.md). ADR 0076 is preserved: no
`web/Hmc*` path returns.

## Problem

Nine operations have no maturity record: `user.{list,get,create,modify,delete}`,
`task_role.list`, `resource_role.list`, `remote_access.{get,configure}`. Their `row_ids` name
REST jobs, CLI commands and MFA/LDAP rows they never issue; `remote_access.*` names the
partition remote-restart job. Round2 ST11 is defective: it declares `REST000E` (the pre-ADR-0076
`HmcUser` refusal) as an expected outcome, so a refusal of the documented resource reads as a
SKIP; it passes `associated_task_role="viewer"`; it creates an HMC-global user on round2's
default path, and preflight never says so. The user-profile builder writes role links and an
element order the documented `UserProfile` shape does not have (ADR 0202).

## Design

1. **Builder (ADR 0202).** `build_hmc_user_document` emits documented-order elements:
   `UserID`, `UserDescription`, `AuthenticationType`, `UserProfilePassword`, `PasswordExpiry`,
   `AssociatedTaskRole`, `AssociatedResourceRoles`, `SessionTimeout`, `VerifySessionTimeout`,
   `IdleSessionTimeout`, `UserInactivity`, `MinimumPasswordAge`, `AllowWebRemoteAccess`,
   `AllowSSHRemoteAccess`, `RemoteUserID`. Roles are text: `<AssociatedTaskRole>name</…>` and
   an `AssociatedResourceRoles` group (with `<Metadata><Atom/></Metadata>`) of
   `<AssociatedResourceRole>name</…>`. Empty string / empty list keep their clearing form. The
   tool docstrings name `TaskRoleName` / `ResourceRoleName` as the value to pass.
2. **Users arm.** `SUBTASK_GROUPS["users"] = [11]`; round2 drops 11; `all` keeps it but ST11
   records one SKIP per step unless `state.group == "users"`. `scripts/live_users.py`
   dispatches it (pattern of `live_profiles.py`). Preflight's `users` verdict names the user it
   creates (`hmcpctl-live-<8 hex>`, viewer task role, web and SSH remote access disabled,
   deleted by UUID in the same run). `LIVE_TEST_TEST_USER_NAME` is retired: the name is minted
   per run, so it cannot collide with or name an existing user.
3. **ST11 steps.** Each read below is `record_verified` with cleanup `not-required`:
   - `hmc_get_console_info` → console UUID (this run's, never a restored artifact), non-promoting.
   - `user.list` (before): `profiles-listed`, `profiles-carry-uuid-and-user-id`,
     `passwords-not-disclosed` (every `UserProfilePassword` empty or absent).
   - `task_role.list`: `task-roles-listed`, `viewer-role-present` (`TaskRoleName` `hmcviewer`).
   - `resource_role.list`: `resource-role-rows-named` or `resource-roles-empty-branch`.
   - `remote_access.get`: `remote-access-group-read` (an `LdapConfiguration` or
     `KerberosConfiguration` key) and `bind-password-not-disclosed` (`BindPassword` empty or
     absent).

   Lifecycle, only when the viewer role resolved and the minted name is not already listed:
   record `artifacts.test_user_name` **before** create, then create (viewer role, description,
   remote access disabled, password minted in the function), list to resolve the UUID, get,
   modify the description, get, clear the description (`""`), get, delete by UUID, list. The
   delete runs whenever a UUID resolved, whatever failed before it. Lifecycle observations are
   recorded after the final list, each with cleanup `passed` when the scratch user is absent
   and every other `(UUID, UserID)` pair equals the before listing, else `failed`:
   - `user.create`: `create-accepted`, `scratch-profile-listed`, `password-not-echoed` (the
     password occurs in no response of the run).
   - `user.get`: `user-id-matches`, `task-role-is-viewer`, `password-not-disclosed`,
     `not-predefined`.
   - `user.modify`: `description-updated`, `description-cleared`, `user-id-unchanged`,
     `profile-uuid-unchanged`, `task-role-unchanged`.
   - `user.delete`: `scratch-profile-absent`, `other-profiles-unchanged`.

   A failed step after create records its row, and the lifecycle observations it fed are
   `failed`; nothing is masked as SKIP. The ST6 round2 listing drops the `REST000E`
   declaration and stays non-promoting.
4. **Recovery.** `live_test_recovery.py` witnesses subtask 11: with `artifacts.test_user_name`
   set it lists users (`hmc_list_users` joins the read-only allowlist) and reports STRANDED
   with `rmhmcusr -u <name>` if the name is present; with no name nothing was created.
5. **Catalog.** `user.*` bind `rest:user-management/userprofile`; `remote_access.*` bind the
   `ldap` and `kerberos` rows; role lists keep theirs. The `userprofile` row becomes
   `supported` (its GET/PUT/POST/DELETE and modifiable fields are all reachable); other rows
   keep their owners. Each operation gets an `implemented` record; observations are copied from
   the emitted file (ADR 0126). Regenerate the projection and `docs/tools/`; CHANGELOG notes
   the role-name change and the retired setting.

## Live gaps

| Case | Prerequisite | State |
|---|---|---|
| `remote_access.configure` | an LDAP/Kerberos server and an approved write window | not run (writes excluded) |
| LDAP/Kerberos `authentication_type`, `remote_user_id` | a directory server | not run |
| password change, policy, MFA | #674, #673 | not run |
| a user with resource roles | a custom resource role (#672) | not run |

## Success

- The nine operations carry the bindings above and a maturity record; `remote_access.configure`
  stays unevidenced with the gap above.
- Catalog observations come from one `users` run at the final `src/` closure, copied with their
  emitted result; a SKIP or non-promoting row is never copied.
- After that run, the HMC user list equals the before listing (UUID and UserID pairs), and
  recovery exits 0 with subtask 11 witnessed.
- No arm other than `users` dispatches `hmc_create_user`, `hmc_modify_user` or `hmc_delete_user`.
- The minted password appears in no argv, log line, results file or observation.
- `just verify` and `uv run --no-sync prek run --all-files` pass.

## Failure model

1. **Actors and deployments:** a local operator running `live_users.py` from the live-test host
   against the designated V10R3 HMC; MCP callers of the user tools; CI, offline.
2. **Invariants and assets:** every existing HMC user (none may change); the operator account;
   LDAP/Kerberos settings (never written); the scratch password; `maturity.json` honesty; the
   user-tool input schemas.
3. **Accepted failure classes:**
   - An interrupted run between create and delete leaves one viewer user with remote access
     disabled; recovery names it and `rmhmcusr -u <name>` removes it.
   - The HMC may reject the documented shape; the observation is `failed` and ADR 0202 is
     superseded with the capture, not worked around.
4. **Covered elsewhere:** role lifecycle (#672), MFA/auth config (#673), password policy (#674),
   CLI user/LDAP rows (#698), test environment isolation (#461).

### Threat model

- **Boundaries:** the scenario mints a credential and sends it to the HMC (added); recovery
  gains one read-only tool (widened).
- **Actors:** the operator (trusted); anyone reading results files, logs or public evidence.
- **Controls:** the password exists only in the scenario's local scope and the tool argument;
  `password-not-echoed` checks responses; results record responses, never arguments; remote
  access is disabled for the scratch user; recovery's allowlist stays read-only.
- **Out of scope:** HMC-side audit logs of the create (owned by the HMC).
