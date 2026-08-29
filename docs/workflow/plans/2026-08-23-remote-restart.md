# RemoteRestart correction plan

1. Add focused builder and job-error tests that fail for the borrowed parameters, missing operation,
   conditional validation, UUID/name routing, and `detailedStatus` handling.
2. Implement the dedicated builder and error-detail extraction in `jobs.py`.
3. Add operation/source/target forwarding through `client_lpm.py` and `operations/lpm.py`, retaining
   existing normalized result types.
4. Expose the explicit contract through `server_tools/lpm.py` and `cli_commands/lpars.py`; update their tests.
5. Run focused tests, smoke verification, and the full `just verify` guardrail.
