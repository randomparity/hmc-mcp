# hmc-mcp — canonical commands
#
# These give the workspace a single entry point for "suite green":
#   just verify   # full ad-hoc verification (tests + MCP handshake + CLI)
#   just test     # pytest only
#   just smoke    # MCP handshake / tool count

# synchronize locked dependencies and install repository hooks
setup:
    uv sync --locked
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

# local and hosted static-analysis gate
static: lint typecheck secrets workflow-security env-vars

# run the full pytest suite
test:
    uv run --no-sync pytest -q

# MCP stdio handshake (lists exposed tools)
smoke:
    uv run --no-sync python scripts/smoke_mcp.py

# full verification: tests + handshake + CLI groups load
verify: static test smoke
    uv run --no-sync hmc-mcp --help >/dev/null
    uv run --no-sync hmc-mcp lpars --help >/dev/null
    uv run --no-sync hmc-mcp storage --help >/dev/null
    uv run --no-sync hmc-mcp network --help >/dev/null
    uv run --no-sync hmc-mcp templates --help >/dev/null
    uv run --no-sync hmc-mcp metrics --help >/dev/null
    @echo "verify: all groups load OK"
