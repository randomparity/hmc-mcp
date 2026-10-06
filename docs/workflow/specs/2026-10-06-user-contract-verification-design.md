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
   per run, so it cannot collide with or name an existing user. `from_env_file` ignores the
   retired key (one printed line) so existing `.env` files still load. The `test_user_uuid`
   artifact is replaced by `test_user_name` and dropped when an old document is restored.
   Field reads use the parser's shapes, through helpers shared with recovery: a profile's UUID
   is the entry's `UUID`, its UserID is `leaf_text` of `Resource.UserID`, and a field is
   *empty* when it is absent, `""`, or a mapping with no `text` key.
3. **ST11 steps.** Each read below is `record_verified` with cleanup `not-required`; the
   tool label of each observation is the tool name alone, so the eight ids are distinct:
   - `hmc_get_console_info` → console UUID (this run's, never a restored artifact), non-promoting.
   - `user.list` (before): `profiles-listed`, `profiles-carry-uuid-and-user-id`,
     `passwords-not-disclosed` (every `UserProfilePassword` empty or absent).
   - `task_role.list`: `task-roles-listed`, `viewer-role-present` (`TaskRoleName` `hmcviewer`).
   - `resource_role.list`: `resource-role-rows-named` or `resource-roles-empty-branch`.
   - `remote_access.get`: `remote-access-group-read` (an `LdapConfiguration` or
     `KerberosConfiguration` key) and `bind-password-not-disclosed` (`BindPassword` empty or
     absent).

   Lifecycle, only when the before listing PASSed, the viewer role resolved, and no listed
   UserID starts with `hmcpctl-live-` (residue: each step SKIPs naming recovery): record
   `artifacts.test_user_name` **before** create, then create (viewer role, description,
   remote access disabled, password minted in the function), list to resolve the UUID
   *whatever create returned*, get, modify the description, get, clear the description
   (`""`), get, delete by UUID, list. The UUID is a local value resolved by the minted name
   from this run's listings — the post-create one, else the final one, then delete. The
   delete runs whenever a UUID resolved, whatever failed before it. Lifecycle observations are
   recorded after the final list, each with cleanup `passed` when no `hmcpctl-live-` user
   remains and no before-listed UUID vanished or changed UserID, else `failed`:
   - `user.create`: `create-accepted`, `scratch-profile-listed`, `password-not-echoed` (the
     password occurs in no response of the run).
   - `user.get`: `user-id-matches`, `task-role-is-viewer`, `password-not-disclosed`,
     `not-predefined`, `remote-access-disabled`.
   - `user.modify`: `description-updated`, `description-cleared`, `user-id-unchanged`,
     `profile-uuid-unchanged`, `task-role-unchanged`, `remote-access-unchanged`.
   - `user.delete`: `scratch-profile-absent`, `pre-existing-profiles-unchanged`.

   A failed step after create records its row, and the lifecycle observations it fed are
   `failed`; nothing is masked as SKIP. Before ST11 records anything, it replaces the minted
   password in the data (a refused PUT may echo its body). The ST6 round2 listing drops the
   `REST000E` declaration and stays non-promoting.

   *Target constraints* (criterion 3) means two things here: the scenario only addresses the
   scratch user, by the UUID this run resolved for the minted name; and each `user.*` tool's
   ADR 0039 target selector (`console_uuid` for list, `user_id` for create,
   `user_profile_uuid` for get/modify/delete) is pinned by an offline test in
   `tests/app/test_user_tool_contracts.py`.
4. **Recovery.** `live_test_recovery.py` witnesses subtask 11 by the prefix, not the document:
   when 11 was dispatched it reads the console UUID (`hmc_get_console_info`), lists users
   (both join the read-only allowlist), and reports STRANDED with `rmhmcusr -u <name>` for
   every UserID starting `hmcpctl-live-` — so a hard-killed run or a later run's document
   still finds it. `main()`'s check gate and `_run_checks` take this input. A document with a
   non-SKIP ST11 `hmc_create_user` row but no `test_user_name` (written before this change) is
   unreadable: exit 2.
5. **Catalog.** `user.*` bind `rest:user-management/userprofile`; `remote_access.*` bind the
   `ldap` and `kerberos` rows; role lists keep theirs. The `userprofile` row's owner moves from
   #637 (partition profiles) to #698, which owns the remaining coverage obligations
   (`IsPredefinedUser`, directory authentication); other rows keep their owners. Each operation gets an `implemented` record; observations are copied from
   the emitted file (ADR 0126). Regenerate the projection and `docs/tools/`; CHANGELOG notes
   the role-name change and the retired setting. `hmcpctl.documents` is in every handler's
   import closure, so the builder edit reports every other recorded observation (39 at this
   design) as stale under ADR 0127 until its own arm re-runs.

## Live gaps

| Case | Prerequisite | State |
|---|---|---|
| `remote_access.configure` | an LDAP/Kerberos server and an approved write window | not run (writes excluded) |
| LDAP/Kerberos `authentication_type`, `remote_user_id` | a directory server | not run |
| password change, policy, MFA | #674, #673 | not run |
| a user with resource roles | a custom resource role (#672) | not run |
| `user.get`, `user.modify`, `user.delete` live | a create body the HMC accepts: V10R3 refused it with REST0001, "Value 'CUR' is not facet-valid with respect to enumeration '[COR]'" (the listing marks `UserID` `kb="COR"`) | not reached; follow-up |
| `verify_session_timeout` as documented minutes | the tool types it `bool` and sends `true`/`false`; the integer fix changes the shared request models outside this surface | catalogued `partial`, missing `verify-session-timeout-minutes` |

## Success

- The nine operations carry the bindings above and a maturity record; `remote_access.configure`
  stays unevidenced with the gap above.
- Catalog observations come from one `users` run at the final `src/` closure, copied with their
  emitted result; a SKIP or non-promoting row is never copied.
- After that run, no `hmcpctl-live-` user exists, every before-listed UUID keeps its UserID,
  and recovery exits 0 with subtask 11 witnessed.
- No arm other than `users` dispatches `hmc_create_user`, `hmc_modify_user` or `hmc_delete_user`.
- The minted password appears in no argv, and in no printed line, results-file row or
  observation that ST11 writes.
- `just verify` and `uv run --no-sync prek run --all-files` pass.

## Failure model

1. **Actors and deployments:** a local operator running `live_users.py` from the live-test host
   against the designated V10R3 HMC; MCP callers of the user tools; CI, offline. Another
   administrator may add users during a run; the cleanup check tolerates additions.
2. **Invariants and assets:** every existing HMC user (none may change); the operator account;
   LDAP/Kerberos settings (never written); the scratch password; `maturity.json` honesty; the
   user-tool input schemas.
3. **Accepted failure classes:**
   - An interrupted run between create and delete leaves one viewer user with remote access
     disabled; recovery's prefix scan names it and `rmhmcusr -u <name>` removes it.
   - The HMC may reject the documented shape; the observation is `failed` and ADR 0202 is
     superseded with the capture, not worked around.
4. **Covered elsewhere:** role lifecycle (#672), MFA/auth config (#673), password policy (#674),
   CLI user/LDAP rows (#698), test environment isolation (#461).

### Threat model

- **Boundaries:** the scenario mints a credential and sends it to the HMC (added); recovery
  gains two read-only tools, `hmc_get_console_info` and `hmc_list_users` (widened).
- **Actors:** the operator (trusted); anyone reading results files, logs or public evidence.
- **Controls:** the password exists only in the scenario's local scope and the tool argument;
  `password-not-echoed` checks responses; results record responses, never arguments; remote
  access is disabled for the scratch user; recovery's allowlist stays read-only.
- **Out of scope:** HMC-side audit logs of the create (owned by the HMC). Residual: if the HMC
  refuses the create and echoes its body, the in-process FastMCP server logs the tool error,
  password included, to the run's stderr before ST11 can scrub it; the runbook says to keep the
  users arm's terminal output private.
