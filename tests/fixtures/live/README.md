# Captured HMC fixtures

Each file here is one request and the HMC's answer, taken for issue #1161:
REST answers and the refcode listing from captures recorded with
`scripts/live_test/capture.py`, and two CLI listings (`cli-io-slots.json`,
`cli-lpar-uuid-name.json`) from raw read-only command output. The captures were
taken on an HMC at V10R3 managing POWER9 hardware; `docs/api-patterns.md`
records the patterns they show. CLI captures whose `capture` begins `2026-09-30-ro/` come
from the read-only sweep for #1202, on the same HMC and system; a nonzero-exit one is
raised in tests with `live_process_error(name)`. Captures whose `capture` begins
`2026-09-30-v11r2-p9/`, `2026-09-30-v11r2-p11-9824/` or `2026-09-30-v11r2-p11-9242/` come
from the same sweep on HMCs at V11R2 SP1120 managing a POWER9 9009-42A, a POWER11 9824-42A
and a POWER11 9242-21B.

## Format

Every file is a JSON object with these keys:

- `capture`: the source record, as `<capture file>#<line>` or a named raw
  output. The raw captures stay private to the operator, so this field is the
  citation, not a link.
- A REST capture has `method`, `path`, `status`, `content_type` and `body`.
- A CLI capture has `command`, `exit_status`, `stdout` and `stderr`.

Tests read a file with `live_fixture(name)` from `tests/conftest.py`, or with
`live_response(name)`, which also returns the captured path and an
`httpx.Response` for a respx route.

## Tokenization

Identifiers are replaced consistently across files, so a value that appears in
two captures has the same token in both:

- UUIDs become `NNNNNNNN-abcd-4ef0-8abc-NNNNNNNNNNNN`, keeping the case the HMC
  printed. LPAR UUIDs are upper case, as V10R3 prints them.
- The managed system is `sys-R1`, and partition names keep their suffix
  (`sys-R1-lp3`, `sys-R1-vios1`).
- The HMC host is `hmc.test:443` (the port in an echoed `Host` header is the
  one the capture used), and the client IP address is `192.0.2.1`.
- Serial numbers, session tokens and cookies are `<REDACTED-SERIAL>`,
  `<REDACTED-SESSION>` and `<REDACTED-COOKIE>`. Inside an XML body the token is escaped
  (`&lt;REDACTED-SESSION&gt;`), so the body stays well-formed XML and parses
  as the HMC's original did.

Nothing else in a body is changed. A new or changed HMC-shaped fixture cites the
capture it came from.
