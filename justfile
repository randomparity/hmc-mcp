# hmc-mcp — canonical commands
#
# These give the workspace a single entry point for "suite green":
#   just verify   # full ad-hoc verification (tests + MCP handshake + CLI)
#   just test     # pytest only
#   just smoke    # MCP handshake / tool count

# run the full pytest suite
test:
    uv run pytest -q

# MCP stdio handshake (lists exposed tools)
smoke:
    uv run python scripts/smoke_mcp.py

# full verification: tests + handshake + CLI groups load
verify: test smoke
    uv run hmc-mcp --help >/dev/null
    uv run hmc-mcp lpars --help >/dev/null
    uv run hmc-mcp storage --help >/dev/null
    uv run hmc-mcp network --help >/dev/null
    uv run hmc-mcp templates --help >/dev/null
    uv run hmc-mcp metrics --help >/dev/null
    @echo "verify: all groups load OK"
