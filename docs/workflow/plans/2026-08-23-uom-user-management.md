# UOM user-management implementation plan

## Files

- `src/hmc_mcp/documents.py`: replace legacy builders and models with UOM
  `UserProfile` and `ManagementConsole` RemoteAccess builders.
- `src/hmc_mcp/client_users.py`: replace web paths with nested UOM resources.
- `src/hmc_mcp/server_users.py`: expose the replacement tool contracts and
  remove password-policy/legacy LDAP tools.
- `src/hmc_mcp/server.py`, `src/hmc_mcp/xmlutil.py`: update exports and stale
  surface descriptions.
- `tests/security/test_users.py`, `tests/security/test_ldap.py`: replace
  endpoint, parsing, error, and tool behavior coverage.
- `tests/security/test_password_policy.py`: remove the phantom-feature tests.
- `tests/app/test_user_tool_contracts.py`, `tests/app/test_tool_security.py`,
  `tests/unit/test_documents.py`, `tests/unit/test_xml_escaping.py`, and
  request-path tests: update public contract, registry, builders, and safety
  expectations.

## Tasks

1. Write failing builder tests for `UserProfile` and `RemoteAccess`, including
   escaping, invalid enums, conflicting clears, and empty updates. Replace the
   builders and remove password-policy models; run the focused tests.
2. Write failing client tests for nested user/role resources and grouped
   remote-access reads/writes. Implement exact UOM paths, verbs, resource
   types, response parsing, authentication filtering, and errors; run focused
   tests.
3. Write failing MCP contract/registry tests for the replacement tools and
   absent phantom tools. Replace server functions and exports, then update
   security metadata expectations; run focused tests and smoke.
4. Sweep all legacy resource names and password-policy references. Update
   direct safety and escaping tests without weakening their asserted boundary.
5. Run `just test`, `just smoke`, and `just verify`; inspect the diff and commit
   each logical correction separately.
