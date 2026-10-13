# Narrow the logon builder's import closure

Issue #1495. ADR 0127 derives an operation's verification staleness from its handler module's
import closure (`closure_paths` in `scripts/check_capability_inventory.py`). Every operation
logs on, so whatever module holds the logon builder sits in every closure. Today that module is
`documents/access.py`, which also holds the `UserProfile` builder: editing that builder for one
operation re-staled seventeen.

## Changes

1. **Logon builder.** `build_logon_request_document` moves verbatim to `documents/logon.py`.
   `client/core.py` is its only caller.
2. **Remote-access builders.** `build_remote_access_document`,
   `merge_remote_access_document`, and their private helpers and field tables move verbatim to
   `documents/remote_access.py`. `client/client_users.py` imports `merge_remote_access_document`,
   and `HMCClient` composes `UsersMixin`, so the module holding it is in every closure too; left
   in `access.py`, it would keep the `UserProfile` builder there. `access.py` keeps the
   `UserProfile` builder, `AuthenticationType`, `AUTHENTICATION_TYPES`, and
   `DEFAULT_USER_DESCRIPTION`.
3. **Package re-export.** `documents/__init__.py` keeps its docstring and imports nothing. The
   closure walk adds every package `__init__.py` on a dotted name's resolution path, so any
   import of `hmcpctl.documents.<x>` reads the package initializer; a re-exporting initializer
   would pull every builder module into every closure. Callers in `src/`, `scripts/`, and
   `tests/` import from the owning submodule. ADR 0118 keeps `hmcpctl.documents` outside the
   stable facade, so no compatibility re-export is added. Tests that reach builders as
   `documents.<name>` reach them through the owning submodule;
   `test_document_facade_exports_domain_owned_objects`, which asserts the re-export, is deleted.
4. **Escaping harness (ADR 0042).** `tests/unit/test_xml_escaping.py` finds document builders by
   reflecting over `hmcpctl.documents`. It enumerates every `hmcpctl.documents` submodule instead,
   so the moved builders stay covered, and its discovery test keeps requiring
   `build_logon_request_document`.
5. **Regenerated records.** `src/hmcpctl/_operation_maturity.json` and `docs/tools/` are
   regenerated, a CHANGELOG `[Unreleased]` entry is added, and the PR states what moved: the
   closure change re-stales operations once.

## Proof

- A test calls `closure_paths` on `lpar.get`'s handler module
  (`hmcpctl.server_tools.systems.core`) and asserts `documents/access.py` is absent and
  `documents/logon.py` present.
- A test pins the exact output of the moved builders, captured before the move.

## Failure model

A missed caller raises `ImportError` or `AttributeError` at import or call time; `ty`, the test
suite, and `just smoke` import every module. A builder whose output changed fails the pinned
output test.

## Out of scope

XML content changes, the `hmcpctl.api` facade, and live HMC contact (operator exclusions).
