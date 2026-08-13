# Spec: Multi-Agent LPAR Ownership — Advisory Protocol

**Issue:** #132
**ADR:** [ADR 0011](../../adr/0011-multi-agent-lpar-ownership.md)
**Status:** Accepted

---

## Outcome

Prevent AI agents sharing one hmc-mcp server from accidentally destroying each
other's LPARs. Every mutation is attributable to a specific agent in the HMC
audit log. This spec covers Phase 0 (per-agent attribution) and Phase 1
(advisory ownership token). No middleware enforcement; cooperative protocol only.

---

## Completion Criteria

1. **`HMC_AGENT_ID` env var** is added to `HMCConfig` (optional `str | None`,
   default `None`). An `effective_audit_memento` property returns
   `f"hmc-mcp/{self.agent_id}"` when `agent_id` is set and non-empty, or the
   existing `audit_memento` value otherwise. The `X-Audit-Memento` header in
   `client.py` is updated to use `effective_audit_memento`.

2. **`stamp_lpar_ownership`** async helper in `ssh.py`:
   - Signature: `async def stamp_lpar_ownership(config, system_name, lpar_name, *, agent_id: str | None = None) -> str | None`
   - Builds token `[hmc-mcp owner:<agent_id or "hmc-mcp"> created:<YYYY-MM-DD>]`
   - Calls `set_lpar_description` with that token.
   - Returns the token on success; returns `None` and does **not** raise on
     `HMCCLIError` or `asyncssh` errors (best-effort).
   - Uses `datetime.date.today().isoformat()` for the date; no timezone conversion.

3. **`hmc_create_lpar`** (in `server_power.py`) stamps the ownership token
   immediately after a successful REST create *or* CLI fallback create. The tool
   return value gains an optional `"ownership_stamped"` key:
   - `True` when the stamp succeeded.
   - `False` when stamping failed (tool still returns the created LPAR data;
     a `"warnings"` key lists the stamp failure reason).

4. **`hmc_provision_lpar`** (in `server_provision.py`) stamps after the "create"
   step succeeds (LPAR UUID is known). Stamp is best-effort: failure appends to
   `warnings` and sets `ownership_stamped: False` in the result. Does not prevent
   subsequent provisioning steps.

5. **`hmc_deploy_partition_template`** (in `server_templates.py`) stamps after
   the deploy job completes. Because this tool submits an async job, stamping is
   only attempted when `wait=True` and the job reaches a terminal `COMPLETED`
   state. When `wait=False`, no stamping is attempted and a note is added to the
   result. The tool return value gains `"ownership_stamped"` key.

6. **Advisory docstrings** on destructive / rename-capable tools:
   - `hmc_delete_lpar`: "Before deleting, read the LPAR description with
     `hmc_get_lpar_description`. If it contains `[hmc-mcp owner:…]`, verify the
     owner matches the current agent (`HMC_AGENT_ID`) before proceeding."
   - `hmc_modify_lpar` (rename path): Same advisory when `name` parameter is
     supplied.
   - `hmc_set_lpar_description`: "If the current description contains
     `[hmc-mcp owner:…]`, compare the owner to the current agent. If they differ,
     stop and ask the operator before overwriting."

7. **Server instructions** (`_app.py`) gain a "Multi-agent ownership" section
   after the existing composite-tool guidance, summarising:
   - The token format.
   - What to do on create (token is stamped automatically).
   - What to do before destructive/rename/description operations.

8. **`scripts/check_env_vars.py` / env-var docs** updated to include
   `HMC_AGENT_ID` so the `just verify` env-var guard passes.

9. **Tests:**
   - `tests/unit/test_ownership.py`: unit tests for `stamp_lpar_ownership`
     (success, SSH error swallowed, token format).
   - `tests/app/test_capabilities.py` or a new test: verifies
     `HMC_AGENT_ID` round-trips correctly through `HMCConfig` and that
     `effective_audit_memento` returns the right values.
   - `tests/app/test_server_tools.py` or a new test: verifies
     `hmc_create_lpar` result contains `ownership_stamped` key.

---

## Threat Model

This change is security-adjacent (attribution), but its threat surface is narrow.

### Boundary inventory

| Boundary | What enters | From whom | Control |
|---|---|---|---|
| `HMC_AGENT_ID` env var read | Agent-operator-supplied string | Process owner | `validate_lpar_description` already rejects non-printable ASCII; same validator applied to `agent_id` before use in the token |
| Token written to LPAR description via SSH `chsyscfg` | Token string | This server | `validate_lpar_description` validates the full token before sending |
| `X-Audit-Memento` header value | `effective_audit_memento` string | This server | Derived from controlled fields; pydantic validates string type |

### Actor model

Local stdio deployment: the only actor is the human operator who configures
`HMC_AGENT_ID` and authorises the MCP session. No remote untrusted actors can
inject `agent_id` at runtime.

### Controls per boundary

- `agent_id` is validated as printable ASCII (length ≤ 64 characters) before
  being embedded in the token. Colons and spaces are allowed; commas and `=` are
  not (they would corrupt the HMC CLI `-i` parser). The validator rejects invalid
  values with a clear error at config construction time.
- The full token string is validated by `validate_lpar_description` before the
  SSH command runs. This is the existing defense layer; no new bypass is added.
- `X-Audit-Memento` is an HTTP header whose value is the pydantic-validated
  `effective_audit_memento` string. No injection vector exists.

### Explicitly out of scope

- Cross-agent impersonation (agent B sets `HMC_AGENT_ID=alice`): not addressable
  by advisory protocol; requires Phase D (per-HMC-user isolation).
- `hmc_run_command` bypass: explicitly deferred to Phase B.

---

## Notes and Follow-ups

- **REST `Description` discrepancy** (`server_composite.py:44` reads
  `res.get("Description")`; `ssh.py:521` says description is not exposed via
  REST): `hmc_lpar_summary` already returns the description via REST when the HMC
  includes it. An agent reading the description for ownership checks should
  prefer `hmc_lpar_summary` (one REST call) over `hmc_get_lpar_description` (one
  SSH call) when the summary is being fetched anyway. This PR does not change the
  REST path; the advisory docstrings recommend `hmc_get_lpar_description` for
  isolated checks since it is authoritative.
- Phase B (middleware) and Phase D (per-agent HMC users) remain as follow-up
  issues, to be filed once the advisory layer is deployed and validated.
