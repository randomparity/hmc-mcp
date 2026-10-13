# ADR 0211: Document builders are imported from their owning module

## Status

Accepted (2026-10-13)

## Context

ADR 0122 kept a package facade: `hmcpctl/documents/__init__.py` imported every public name
from its owning module. ADR 0127 derives an operation's live-verification staleness from its
handler's import closure, and that walk adds every package `__init__.py` on an import's
resolution path. Any import of a `hmcpctl.documents` submodule therefore read the facade, and the
facade imported every builder module, so every builder was in every closure.

The logon builder sat in `documents/access.py` beside the `UserProfile` builder, so that module
was in the closure of 159 of 163 operations. #1491 changed only the `UserProfile` document and
turned all seventeen current verifications stale (#1495).

## Decision

`hmcpctl/documents/__init__.py` holds only its docstring. Every caller imports a builder, model,
or vocabulary from the submodule that owns it. The logon builder lives in `documents/logon.py`
and the RemoteAccess builders in `documents/remote_access.py`; `documents/access.py` keeps the
`UserProfile` builder.

The operator chose this on 2026-10-13 over keeping the facade (#1495). Measured with
`closure_paths`, `documents/access.py` was in the closure of 159 of 163 operations before the
change and is in 9 after: the `remote_access.*`, `resource_role.list`, `task_role.list`, and
`user.*` operations.

This supersedes ADR 0122's package-facade sentence and its consequence that package-level
consumers keep their names. Its ownership rule stands: each name lives in the domain module that
owns its builder, and `documents.common` holds only `UOM_NS` and `document_envelope`.

## Consequences

A change to the `UserProfile` builder re-stales only the operations whose closure imports
`documents/access.py`. Consumers of `hmcpctl.documents.<name>` must import from the submodule;
`hmcpctl.documents` is outside the ADR 0118 facade, so no compatibility re-export is kept.
A test holds the initializer to its docstring.

The builder modules that `client/*.py` mixins import stay in every closure, because `HMCClient`
composes every mixin.

## Considered & rejected

- **Keep the facade and move only the logon builder out of the package.** verified: on
  2026-10-13, with the logon builder moved to `client/`, `lpar.get`'s closure still held
  `documents/__init__.py` and `access.py`, because `client_users.py`, `client_adapters.py`,
  `client_storage.py`, and `client_network.py` are in every closure and import through the
  package.
- **Teach the closure walk to skip package initializers.** judgment: an initializer runs on
  import, so skipping it would hide real changes from the staleness check.
