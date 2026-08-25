# hmc-mcp — canonical commands
#
# These give the workspace a single entry point for "suite green":
#   just verify   # full ad-hoc verification (tests + MCP handshake + CLI)
#   just test             # quiet suite summary
#   just test-verbose     # pytest diagnostics and missing-lines coverage
#   just smoke             # MCP handshake / tool count
#   just smoke-verbose     # MCP handshake / exposed tool names
#   just tool-docs         # regenerate docs/tools/ from the registry
#   just tool-docs-check   # fail when docs/tools/ has fallen behind the registry

# synchronize locked dependencies and install repository hooks
setup:
    uv sync --locked --extra app --link-mode copy
    uv run --no-sync prek install

# Python lint
lint:
    uv run --no-sync ruff check .

# type-check the explicit clean production-module boundary
typecheck:
    uv run --no-sync ty check

# scan every tracked file against the reviewed fixture baseline
secrets:
    git ls-files -z | xargs -0 uv run --no-sync detect-secrets-hook \
        --baseline .secrets.baseline --no-verify --

# audit GitHub Actions without depending on mutable remote state
workflow-security:
    uv run --no-sync zizmor -qq --no-online-audits .github/workflows/

# verify every HMC_* env var in HMCConfig is documented
env-vars:
    uv run --no-sync python scripts/check_env_vars.py

# verify the committed config fixture has a well-formed nicknames table
nicknames:
    uv run --no-sync python scripts/check_nicknames.py

# regenerate docs/tools/ from the MCP tool registry
tool-docs:
    uv run --no-sync python scripts/gen_tool_reference.py

# verify the committed docs/tools/ still matches the registry
tool-docs-check:
    uv run --no-sync python scripts/gen_tool_reference.py --check

# verify every decision record in docs/adr/ carries a unique number
adr-numbering:
    uv run --no-sync python scripts/check_adr_numbering.py

# local and hosted static-analysis gate
static: lint typecheck secrets workflow-security env-vars nicknames tool-docs-check adr-numbering

# run the full pytest suite with one semantic summary
test:
    uv run --no-sync python scripts/run_tests.py

# run the full pytest suite with native diagnostics
test-verbose:
    uv run --no-sync pytest -q --cov-report=term-missing

# MCP stdio handshake (reports exposed tool count)
smoke:
    uv run --no-sync python scripts/smoke_mcp.py

# MCP stdio handshake with the full exposed tool registry
smoke-verbose:
    uv run --no-sync python scripts/smoke_mcp.py --verbose

# construct a fresh wheel and source distribution
build:
    uv build --clear --wheel --sdist --out-dir dist .

# validate the retained distributions without rebuilding them
verify-artifacts:
    uv run --no-sync python tests/validate_release_artifacts.py dist .

# full verification: tests + handshake + CLI groups load
# The root help goes through the installed console script, so the entry point is
# covered; the group helps are derived from the Typer app rather than listed here.
verify: static test smoke build verify-artifacts
    uv run --no-sync hmc-mcp --help >/dev/null
    uv run --no-sync python scripts/smoke_cli_groups.py
    @echo "verify: all groups load OK"
