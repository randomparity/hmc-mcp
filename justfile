# hmc-mcp — canonical commands
#
# These give the workspace a single entry point for "suite green":
#   just verify   # full ad-hoc verification (tests + MCP handshake + CLI)
#   just test             # quiet suite summary
#   just test-verbose     # pytest diagnostics and missing-lines coverage
#   just smoke    # MCP handshake / tool count

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

# local and hosted static-analysis gate
static: lint typecheck secrets workflow-security env-vars nicknames

# run the full pytest suite with one semantic summary
test:
    uv run --no-sync python scripts/run_tests.py

# run the full pytest suite with native diagnostics
test-verbose:
    uv run --no-sync pytest -q --cov-report=term-missing

# MCP stdio handshake (lists exposed tools)
smoke:
    uv run --no-sync python scripts/smoke_mcp.py

# construct a fresh wheel and source distribution
build:
    uv build --clear --wheel --sdist --out-dir dist .

# validate the retained distributions without rebuilding them
verify-artifacts:
    uv run --no-sync python tests/validate_release_artifacts.py dist .

# full verification: tests + handshake + CLI groups load
verify: static test smoke build verify-artifacts
    uv run --no-sync hmc-mcp --help >/dev/null
    uv run --no-sync hmc-mcp lpars --help >/dev/null
    uv run --no-sync hmc-mcp storage --help >/dev/null
    uv run --no-sync hmc-mcp network --help >/dev/null
    uv run --no-sync hmc-mcp templates --help >/dev/null
    uv run --no-sync hmc-mcp metrics --help >/dev/null
    @echo "verify: all groups load OK"
