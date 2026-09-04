# Configure live-test environment values

Implement issue #609 by loading a strict `LIVE_TEST_*` configuration into the
existing run context. The runner owns parsing and validation; scenario modules
only consume context values. The standard library and the runner's existing
`.env` loader are sufficient.

## Global Constraints

- Keep `HMC_*` connection precedence and semantics unchanged.
- Do not add dependencies.
- Missing or malformed `LIVE_TEST_*` values must fail before MCP client creation.
- `.env.example` uses only fictional, mutually consistent sample values.

Expected implementation size: 180–280 changed lines (M) — one context parser,
scenario substitutions, example documentation, and focused tests.

## Task 1: Define and validate live-test configuration

Files: modify `scripts/live_test_runner.py`; modify `tests/test_live_runner.py`.

Interfaces: add `LiveTestContext.from_environment() -> LiveTestContext`, called
after `.env` loading and before `RunState` or `Client` creation. It reads the
complete `LIVE_TEST_*` key set and raises one actionable `ValueError` listing
all missing or invalid entries.

Verification:

- Contract: complete fictional configuration becomes typed context values.
  Mode: focused-test. Add `test_live_context_reads_complete_environment`; it is
  red because no parser exists, then green with
  `uv run --no-sync pytest tests/test_live_runner.py -q`.
- Contract: absent and malformed values prevent client construction.
  Mode: focused-test. Add a parametrized validation test and a runner-entry
  test; they are red because defaults permit execution, then green with the
  same command.

Steps:

1. Add a single mapping of required key names and conversion functions beside
   `_load_dotenv`; load values after the existing connection bootstrap.
2. Make `LiveTestContext` store all configured scenario inputs, including
   SR-IOV and virtual-media inputs, while retaining mutable discovered state.
3. Replace default construction in the runner entry path with validated
   construction before `Client` is entered.
4. Add focused tests using fictional mapping values and prove no client is
   instantiated when validation fails.

Acceptance: every required key has validation and error text; no current
environment identifier remains a default in `LiveTestContext`.

## Task 2: Route every scenario through the context

Files: modify `scripts/live_test/pcie.py`, `scripts/live_test/provisioning.py`,
`scripts/live_test/vmedia.py`, and direct tests in `tests/test_live_runner.py`.

Interfaces: scenario functions continue to accept `RunState`; their resource,
media, host, and protection inputs come from `state.context`.

Verification:

- Contract: SR-IOV tool calls use configured adapter, port, capacity, and profile.
  Mode: focused-test. Extend the SR-IOV test with fictional context values;
  it is red while module constants are used, then green with
  `uv run --no-sync pytest tests/test_live_runner.py -q`.
- Contract: virtual-media serving, allow-list, media names, and protection use
  configured values.
  Mode: focused-test. Add a focused virtual-media configuration test; it is red
  while constants are used, then green with the same command.

Steps:

1. Remove environment-specific constants and source-specific prose from the
   scenario modules.
2. Substitute the corresponding typed context values in every tool call,
   command, server setup, allow-list update, and safety guard.
3. Update focused tests to build a complete fictional context and assert the
   selected values reach the relevant calls.

Acceptance: scenario modules do not retain environment-specific identifiers or
read environment variables directly.

## Task 3: Publish the safe example contract

Files: add `.env.example`; modify `tests/test_live_runner.py`.

Interfaces: `.env.example` contains every required `LIVE_TEST_*` key once,
with comments describing format and use.

Verification:

- Contract: the example key set equals the parser's required key set and does
  not contain retired source identifiers.
  Mode: focused-test. Add an example-contract test; it is red before the file
  exists, then green with `uv run --no-sync pytest tests/test_live_runner.py -q`.

Steps:

1. Add a commented `.env.example` with fictional values that use the
   `example-lt-609` prefix.
2. Add the contract test so future configuration fields cannot omit their
   example documentation.

Acceptance: copying the file gives an operator every key to replace and no
value reveals the previous environment.

## Final verification

Run `just test`, `just smoke`, `just static`, and `uv run --no-sync prek run
--all-files`. Run `just verify` before delivery. The live hardware suite is not
run locally because its purpose is to configure another operator's environment.
