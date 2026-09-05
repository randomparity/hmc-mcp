# HMC V11 REST-port fallback implementation plan

## Goal

Make an omitted HMC REST port prefer 443 and retry 12443 once after a
transport-only logon failure, while preserving explicit port authority.

## Architecture

`HMCConfig` keeps existing constructor/environment/TOML precedence and uses
Pydantic field provenance to distinguish omission from an explicit value.
`HMCClient` owns the bounded fallback because it owns session establishment and
HTTP-client cleanup. ADR 0059 and the linked design spec define the contract.

## Tech stack

Python 3.11–3.14, Pydantic Settings, httpx async transport, pytest/respx, Ruff,
ty, and the repository `just` recipes.

## Global constraints

- `port` remains configurable by TOML profile and `HMC_PORT`; omission selects
  443, while any explicit port is authoritative and disables fallback.
- Fallback is exactly one retry to literal port 12443 and only follows an
  `HMCTransportError` during logon before a token or HTTP response is obtained.
- The failed client closes before the legacy client opens. TLS verification,
  timeout, host, credentials, and headers do not change.
- HTTP errors, parse errors, explicit-port errors, and failures after logon do
  not trigger fallback.
- Error messages contain neither credentials, response bodies, nor session
  tokens.
- Quick-property behavior and unrelated transport/configuration behavior remain
  outside scope.
- CI targets `amd64` and `arm64` on Python 3.11–3.14; implementation is
  architecture-neutral.
- Guardrails are `just test`, `just smoke`, and `just verify`; CI individually
  runs `just verify` and `UV_NO_SYNC=1 uv run prek run --all-files`.

## File map

- `src/hmc_mcp/config.py`: change the default port only; retain existing
  precedence and `base_url` contract.
- `src/hmc_mcp/client/__init__.py`: record fallback eligibility and implement the bounded
  logon retry, cleanup/error behavior, and private active REST base.
- `src/hmc_mcp/client/client_contracts.py` and `src/hmc_mcp/client/client_network.py`: declare
  the private active-base attribute required by typed mixin boundaries.
- `src/hmc_mcp/client/client_pcm.py`, `src/hmc_mcp/client/client_storage.py`, and
  `src/hmc_mcp/client/client_network.py`: use the active base at the five existing sites
  that expand relative links or generate absolute REST relationship links.
- `tests/unit/test_config.py`: prove default and explicit TOML/environment port
  provenance.
- `tests/unit/test_client.py`: prove retry gates, cleanup, success, and bounded
  failure messages.
- `tests/conftest.py` and every test-local `https://hmc.test:12443` route:
  classify the route as implicit-default behavior to move to 443 or deliberate
  legacy behavior whose config must pass `port=12443`.
- `README.md`, `docs/environment-variables.md`, and
  `tests/fixtures/config.example.toml`: document the operator contract.

## Task 1: Configuration default and provenance

### Interfaces

- Consumes `HMCConfig.port`, `HMCConfig.model_fields_set`, and
  `load_profile(profile, config_path)`.
- Produces the invariant used by Task 2: `port == 443` and `"port" not in
  model_fields_set` means fallback-eligible omission; every constructor,
  environment, or TOML port includes `"port"`.

### Steps

1. Add tests in `tests/unit/test_config.py` asserting a bare
   `HMCConfig(_env_file=None)` has port 443 with `port` absent from
   `model_fields_set`; constructor port 443, `HMC_PORT=12443`, and TOML
   `port = 12443` each include `port` and retain their values.
2. Run
   `uv run --no-sync pytest -q --no-cov tests/unit/test_config.py`; expect the
   new default assertion to fail with 12443 before implementation.
3. Change `HMCConfig.port` in `src/hmc_mcp/config.py` to
   `Field(default=443, description="HMC REST API port")`.
4. Run the same focused command; expect all configuration tests to pass.
5. Commit with `fix: prefer the HMC V11 REST port` after Task 2 completes the
   independently usable connection behavior.

### Acceptance

Every supported configuration source still overrides the default, and omission
is distinguishable without a new public setting.

## Task 2: Bounded logon fallback and test-fixture migration

### Interfaces

- Consumes Task 1's field-set invariant and existing
  `HMCClient.logon() -> str`, `HMCClient._http: httpx.AsyncClient`, and
  `HMCTransportError`.
- Preserves `logon() -> str`, `is_logged_on`, context-manager cleanup, request
  headers, timeout, and TLS settings for all callers.
- Produces `_rest_base_url: str` for existing mixins; it matches the current HTTP
  client's destination and is not part of the supported API.

### Steps

1. Inventory the complete test surface with `rg -n '12443' tests`. Classify
   every occurrence as an implicit-default destination/assertion to move to
   443, deliberate legacy behavior that must construct an explicit 12443
   config/profile, or unrelated literal data. This includes alternate hosts in
   `tests/app/test_profile_routing.py` and the default assertion in
   `tests/unit/test_server_tools/hosts.py`; do not limit the sweep to `hmc.test`.
2. Move all implicit-default routes and assertions needed by
   `tests/unit/test_client.py`, `tests/unit/test_config.py`, and their
   `tests/conftest.py` fixtures to 443. Retain 12443 only with explicit port
   provenance in the same test or fixture.
3. Add async tests in `tests/unit/test_client.py` using `respx` or a mocked
   `httpx.AsyncClient` to assert: implicit 443 transport failure closes the
   first client and retries 12443; the retry can succeed and stores the token;
   explicit 443 and 12443 never retry; HTTP 401 never retries; a failed retry
   preserves the existing sanitized transport error without including the
   configured password.
   Also make the failed client's `aclose()` raise and, separately, suspend it
   while cancelling a direct `logon()` task; each case must propagate its
   exception/cancellation with zero 12443 client constructions or requests.
4. Run
   `uv run --no-sync pytest -q --no-cov tests/unit/test_client.py`; expect the
   fallback tests to fail because `logon()` currently makes one attempt.
5. In `HMCClient.__init__`, snapshot fallback eligibility from
   `config.port == 443 and "port" not in config.model_fields_set` and retain the
   HTTP construction inputs needed to reproduce the client exactly.
6. Refactor only the repeated HTTP-client construction into a private method
   with signature `_new_http_client(self, port: int) -> httpx.AsyncClient`.
   It must use the same host normalization as `HMCConfig.base_url`, the existing
   `verify_ssl`, `timeout`, and `X-Audit-Memento` values.
7. Split the existing exchange into private
   `async def _logon_once(self, body: str) -> str`, preserving status, parsing,
   token, and header behavior. Keep XML creation before the retry boundary.
8. Initialize `_rest_base_url` from the created HTTP client's normalized base.
   In `logon()`, catch only `HMCTransportError` from the first `_logon_once`.
   When ineligible, re-raise. When eligible, disable further fallback, close the
   first client with the existing ordinary `aclose()` call, and only after that
   call returns replace it with `_new_http_client(12443)`. Then update
   `_rest_base_url` from that client, and call `_logon_once` once. Let any second
   `HMCTransportError` retain the existing error behavior.
9. Add `_rest_base_url: str` to `PcmClient`, `StorageClient`, and the local
    network-client protocol. Replace only the five `self.config.base_url` sites
    in `client_pcm.py`, `client_storage.py`, and `client_network.py` with it.
10. Add focused assertions for all five active-base sites: PCM relative-link
    expansion in `tests/unit/test_client.py`; `get_lpar_link`, volume-group URL
    expansion, and optical-mapping relationship generation in
    `tests/unit/test_client_domain_mixins.py` and the directly owning storage
    suites; and the virtual-switch relationship in
    `tests/unit/test_client_domain_mixins.py`. Prove each targets 12443 after
    fallback, and prove an absolute HMC-supplied PCM link remains unchanged.
11. Run
    `rg -n 'self\.config\.base_url' src/hmc_mcp/client/client_pcm.py src/hmc_mcp/client/client_storage.py src/hmc_mcp/client/client_network.py`;
    expect no matches, structurally guarding all five migrations.
12. Run the focused client and affected mixin/storage tests; expect all to pass.
13. Run
   `uv run --no-sync pytest -q --no-cov tests/unit/test_config.py tests/unit/test_client.py`;
   expect all focused tests to pass.
14. Complete the repository-wide classification from step 1: migrate every
   remaining implicit-default route/assertion to 443, make every intended
   legacy case explicit, and rerun `rg -n '12443' tests`. Record the
   classification of every remaining match in the commit review; none may rely
   on omission.
15. Run `just test`; expect the configured suite and exact coverage gate to
   pass.
16. Commit Tasks 1–2 together as `fix: prefer the HMC V11 REST port` because the
   new default without fallback is not the approved independently usable slice.

### Acceptance

Fallback happens once and only for implicit-port logon transport failure; all
other failures and explicit ports preserve existing behavior, cleanup owns the
active client, and every locally constructed REST URL follows its active base.

## Task 3: Operator documentation

### Interfaces

- Consumes the Task 2 behavior.
- Produces public guidance for `HMC_PORT` and TOML `port` after Task 2 has made
  all test routes explicit about implicit-default versus legacy behavior.

### Steps

1. Update `README.md`, `docs/environment-variables.md`, and
   `tests/fixtures/config.example.toml` to show 443 as the default and explain:
   implicit transport-only fallback, explicit-port authority, the additional
   failed-attempt latency on old HMCs, and the unreachable-session residual.
2. Run `just test`; expect the configured test and exact coverage gate to pass.
3. Run `just smoke`; expect the MCP handshake and tool-count check to pass.
4. Commit as `docs: explain HMC REST port fallback`.

### Acceptance

No user-facing source claims 12443 is the implicit default, and the operational
cost and override behavior are explicit.

## Task 4: Final verification

### Interfaces

- Consumes all prior tasks and produces the branch ready for review.

### Steps

1. Run `git status --short --untracked-files=all`; expect only intended tracked
   modifications and no untracked artifacts before staging.
2. Run `just verify` bare; expect all static, test, smoke, build, artifact, and
   CLI checks to pass with zero warnings.
3. Run `UV_NO_SYNC=1 uv run prek run --all-files`; expect every hook to pass.
4. Re-read `git diff main...HEAD` for naming, complexity, credential leakage,
   and conformance to ADR 0059. Commit any evidence-backed correction separately.

### Acceptance

The branch is clean, all repository and CI guardrails pass, and the diff stays
within the frozen surface.
