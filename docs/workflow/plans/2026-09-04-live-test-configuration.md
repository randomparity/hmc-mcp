# Configure live-test environment values

Implement issue #609 by loading a strict `LIVE_TEST_*` configuration into the
existing run context. The runner owns parsing and validation; scenario modules
only consume context values. The standard library and the runner's existing
`.env` loader are sufficient.

## Global Constraints

- Keep `HMC_*` connection precedence and semantics unchanged; local `.env` is
  authoritative for `LIVE_TEST_*` even when conflicting values are exported.
- Do not add dependencies.
- Missing or malformed `LIVE_TEST_*` values must fail before MCP client creation.
- `.env.example` uses only fictional, mutually consistent sample values.

Expected implementation size: 280–400 changed lines (M) — one context parser,
scenario substitutions, example documentation, and focused tests.

## Task 1: Define and validate live-test configuration

Files: modify `scripts/live_test_runner.py`; modify `tests/test_live_runner.py`.

Interfaces: add `LiveTestContext.from_environment() -> LiveTestContext` and a
shared pre-bootstrap entry that calls it before the CLI runs HMC bootstrap or a
direct caller enters `main()`. Make `RunState` require the resulting context.
The command-line wrapper and direct `main()` caller pass that context onward.
The context reader uses a
side-effect-free local-file reader that returns only `LIVE_TEST_*` entries,
never reads or mutates `os.environ`, and raises one actionable `ValueError`
listing all missing, invalid, or duplicate entries with their source lines.

Verification:

- Contract: complete fictional configuration becomes typed context values.
  Mode: focused-test. Add `test_live_context_reads_complete_environment`; it is
  red because no parser exists, then green with
  `uv run --no-sync pytest tests/test_live_runner.py -q`.
- Contract: absent and malformed values prevent client construction.
  Mode: focused-test. Add a parametrized validation test and a runner-entry
  test; they are red because defaults permit execution, then green with the
  same command. Cover direct `main()` and command-line paths, successful TOML
  connection bootstrap, conflicting ambient exports, a missing local file, and
  duplicate keys with their line numbers.

Steps:

1. Add a separate `LIVE_TEST_*` file reader and required-key mapping with
   conversion functions. It retains line numbers and rejects duplicate keys.
   Add a shared pre-bootstrap entry used by the CLI and direct `main()`; do not
   reuse `_load_dotenv`.
2. Make `LiveTestContext` store all configured scenario inputs, including
   SR-IOV, provisioning, and virtual-media inputs, while retaining mutable
   discovered state. Define the complete specification inventory as the parser's
   single required-key list.
3. Replace default construction with the shared required validated context
   before connection bootstrap, MCP construction, and `Client` entry.
4. Add focused tests using fictional mapping values and prove no client is
   instantiated when validation fails or when a selected subtask is requested.
   Prove a conflicting exported `LIVE_TEST_*` value cannot override `.env`.

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
  configured values, with separate bind and advertised hosts.
  Mode: focused-test. Add a focused virtual-media configuration test; it is red
  while constants are used, then green with the same command.

Steps:

1. Remove every source item in the specification inventory and
   environment-specific prose from the scenario modules.
2. Substitute the corresponding typed context values in every tool call,
   command, server setup, allow-list update, and safety guard.
3. Update focused tests to build a complete fictional context and assert the
   selected SR-IOV, provisioning, primary/alternate media, bind, advertised URL,
   allow-list, and protection values reach the relevant calls.

Acceptance: scenario modules do not retain environment-specific identifiers or
read environment variables directly.

## Task 3: Publish the safe example contract

Files: add `.env.example`; modify `tests/test_live_runner.py`.

Interfaces: `.env.example` contains every required `LIVE_TEST_*` key once,
with comments describing format and use.

Verification:

- Contract: the example key set equals the specification inventory and parser's
  required key set and does not contain retired source identifiers.
  Mode: focused-test. Add an example-contract test; it is red before the file
  exists, then green with `uv run --no-sync pytest tests/test_live_runner.py -q`.

Steps:

1. Add a commented `.env.example` with the specification's exact fictional
   sample map, including `0.0.0.0`, `iso.example.test`, and the ISO path.
2. Add the contract test so future configuration fields cannot omit example
   documentation or substitute an unreviewed sample value.

Acceptance: copying the file gives an operator every key to replace and no
value reveals the previous environment.

## Final verification

Run `just test`, `just smoke`, `just static`, and `uv run --no-sync prek run
--all-files`. Run `just verify` before delivery. The live hardware suite is not
run locally because its purpose is to configure another operator's environment.
