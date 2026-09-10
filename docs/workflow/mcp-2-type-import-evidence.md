# MCP 2 ToolAnnotations evidence

Issue #752, verified 2026-09-09 for the dependency upgrade in #753.
Keep `from mcp.types import ToolAnnotations` in `tool_registry.py`.
With FastMCP 4.0.3 and MCP/mcp-types 2.2.0, it is the same class as
`mcp_types.ToolAnnotations`, defined in `mcp_types._types` and owned by the
`mcp-types` distribution. MCP's namespace remains a supported mirror, as its
[2.2.0 source](https://github.com/modelcontextprotocol/python-sdk/blob/v2.2.0/src/mcp/types/__init__.py)
documents. This confirms the current import; no fallback or import migration is required.

The annotation constructor still accepts wire aliases. Serialized hints remain:

| Effect | readOnlyHint | destructiveHint |
|---|---|---|
| read | true | null |
| mutate | false | null |
| destructive | false | true |
| arbitrary-command | false | true |

`null` means unspecified, not false. FastMCP 4's deprecated camelCase attribute
bridge supports reads only: the old test assignment to `readOnlyHint` raised
`AttributeError: property of 'ToolAnnotations' object has no setter`.
The focused tests now check wire serialization and mutate the common writable
`title` field to verify independent copies. Production annotation construction
and security policy are unchanged.

## Reproduce

From this checkout, with uv and the four Python interpreters available, run in Bash.
The disposable environments do not use the project app extra or rewrite its lock.
The named package versions were resolved from PyPI; these are probe constraints,
not a replacement lock for #753. Other transitive versions can change on re-resolution.

```bash
set -euo pipefail
probe_root=$(mktemp -d)
for minor in 3.11 3.12 3.13 3.14; do
  uv venv --python "$minor" "$probe_root/$minor"
  probe_python="$probe_root/$minor/bin/python"
  uv pip install --python "$probe_python" --link-mode copy \
    'fastmcp-slim[server,client]==4.0.3' 'mcp==2.2.0' 'mcp-types==2.2.0' \
    'asyncssh==2.24.0' 'defusedxml==0.7.1' 'httpx==0.28.1' \
    'pydantic==2.13.5' 'pydantic-settings==2.15.0' 'typing-extensions==4.16.0' \
    'typer==0.27.2' 'pytest==9.1.1' 'pytest-asyncio==1.4.0' \
    'pytest-cov==7.1.0' 'respx==0.23.1'
  uv pip install --python "$probe_python" --link-mode copy --no-deps --editable .
  "$probe_python" - <<'PY'
import sys
from importlib.metadata import packages_distributions, version
from mcp.types import ToolAnnotations
from mcp_types import ToolAnnotations as Standalone
assert ToolAnnotations is Standalone
assert set(packages_distributions()["mcp_types"]) == {"mcp-types"}
print(sys.version.split()[0], ToolAnnotations.__module__)
print({p: version(p) for p in ("fastmcp-slim", "mcp", "mcp-types")})
PY
  "$probe_python" -m pytest --no-cov -q \
    tests/unit/test_tool_registry.py tests/app/test_tool_security.py
  "$probe_python" scripts/smoke_mcp.py
done
printf 'Disposable probe environments: %s\n' "$probe_root"
```

## Observed results and limits

On Linux amd64, Python 3.11.15, 3.12.13, 3.13.14, and 3.14.7 each resolved
the same import/class identity, passed 130 focused tests without warnings, and
completed the MCP handshake with 155 tools. Each command exited zero.
The project-lock focused run also passed 130 tests. A temporary inversion of
the factory's read-only hint produced two assertion failures (unit wire mapping
and registered mutating tool); restoring the source returned the run to green.

These results cover the exact framework versions above, not future releases.
The upgraded full suite and arm64 matrix belong to #753; generated references,
schema-field migration, and audit reconciliation belong to #754.
