# HMC capture harness and verified API patterns

Issue #1161, narrowed to two of its Expected bullets: the reusable capture harness and
`docs/api-patterns.md`. The redacted fixture corpus, divergence filing, and any change under
`src/hmcpctl/` are out of scope (release freeze).

## Problem

Live probes for #1161 each carried a one-off spy that monkeypatched the production seams and
wrote JSONL. Nothing tracked makes that reusable, redacts consistently, or keeps the output out
of git. The 47 verified patterns and four #879-window observations live only in issue comments,
where code and tests cannot cite them.

## Design

### Capture harness — `scripts/live_test/capture.py`

A library module, not an entry point: probe scripts import it, so it sits beside the other
`scripts/live_test/` library code and is tested by a behaviour-grouped
`tests/scripts/test_capture.py`.

Interface:

```python
@contextlib.contextmanager
def capture(path: Path) -> Iterator[Capture]
```

`Capture.step(name)` labels subsequent records, so a probe can mark its phases.

- On entry, before patching anything, it refuses (`ValueError`, naming the path and the fix)
  a destination for which `git -C <destination's directory> check-ignore -q` exits 1 — the
  rule `scripts/live_test_runner.py:1541` applies: exit 0 (ignored) and any other status (no
  repository, or outside one) are safe. Running git from the destination's own directory
  checks the repository the file actually lands in, whatever the caller's working directory.
- It patches `hmcpctl.client.core.HMCClient._request` and
  `asyncssh.SSHClientConnection.run` on the class, so every caller — including modules that
  imported `run_hmc_command` by name — is observed. On exit both originals are restored,
  exception or not.
- Each record is one JSON line appended to `path` and flushed: `kind` (`rest`|`ssh`), `step`,
  `t` (epoch seconds).
  - `rest`: `method`, `path`, `accept`, `content_type` (from the per-call headers), `status`,
    `response_headers`, `body`, `request_body`; on an exception instead of a response,
    `exception` (`Type: message`) and the exception is re-raised.
  - `ssh`: `command`, `exit_status`, `stdout`, `stderr`; an `asyncssh.ProcessError` is
    recorded from its fields and re-raised.
- Redaction, applied before anything is written:
  - request and response headers named `X-API-Session`, `Cookie`, `Set-Cookie`, or
    `Authorization` (case-insensitive) are dropped;
  - for a path containing `Logon`, request body, response body, and exception text are
    replaced by `"<redacted: logon>"`;
  - any request body, response body, exception text, SSH command, stdout, or stderr containing
    `password` (case-insensitive) is replaced by `"<redacted: password>"`.
- `.gitignore` gains `*.capture.jsonl` and `hmc-captures/`, so a destination inside the
  repository has an ignored place to land.

### `docs/api-patterns.md`

Opens with a plain-prose note (no generation banner) that every pattern is from V10R3 on
POWER9 and says nothing about other releases or families. Sections: envelope structure;
identifiers and their stability; link forms; media types per endpoint; job lifecycle;
error-code families and bodies; schema and enumerations; CLI output forms. Each pattern row:
ID (P1–P47 from the issue comments; `N1`–`N4` for the #879-window observations), the
observation, capture commit (`2281afd2` for P1–P27, `90c97b5f` for N1–N4, "not stated" for
P28–P47, whose comments record no commit), conforming or divergent code paths with
`path:line` citations verified against the branch base, and the fixing PR/issue where one
exists. A pattern whose comment is ambiguous says so. It is linked from `docs/index.md`
(Reference list). A closing section states the rule that new HMC-shaped fixtures should cite
a capture, as #1161 proposes, marked unenforced.

## Failure model

1. **Actors and deployments**
   - An operator on the operator host running a probe script against a designated HMC, under
     `docs/live-testing.md` discipline.
   - CI and developers running the unit tests; they never reach an HMC.
2. **Invariants and assets at stake**
   - HMC session tokens and passwords must not reach a capture file.
   - Raw captures (hostnames, serials, UUIDs) must not be committable from the repository.
   - Production behaviour: the patch must return exactly what the original returned or raised.
   - The doc's citations are claims; a wrong one misleads later fixes.
3. **Accepted failure classes**
   - Non-secret identifiers (hostnames, UUIDs, serials) are recorded verbatim: the capture is
     private by design and git-ignored; tokenizing is the fixture corpus's job (excluded).
   - A secret whose text contains neither `password` nor a session/cookie header name and
     does not travel on a `Logon` path is not recognized; no HMC operation the harness
     observes today carries one.
   - Interactive console sessions (`create_process`, used by the console tools) are not
     `run` calls and are not recorded; #1161's windows did not capture console either.
   - Concurrent `capture()` contexts in one process are unsupported (the patch is global);
     a nested entry raises `RuntimeError`.
   - Line citations drift as `main` moves; the doc states the commit they were verified at.
4. **Covered elsewhere**
   - Publishing redacted fixtures: #1161's fixture-corpus bullet.
   - Live-run authorization and cleanup: `docs/live-testing.md` and its scripts.

### Threat model

- **Boundaries added:** process memory → capture file (the harness writes HMC responses and
  CLI output to disk). **Widened:** none.
- **Actors:** the local operator (trusted, owns the file); a later reader of the repository
  or a public issue (untrusted — must never receive a capture).
- **Controls:** header/body/command redaction above (tested per field); git-ignore refusal
  before any patching; the file is created with mode 0600.
- **Out of scope:** a compromised operator host; secrets in formats the redaction rule does
  not match (accepted, class 3).

## Validation

- Harness: `tests/scripts/test_capture.py` with a fake `httpx` response and fake SSH
  results — one test per recorded field set, per redaction rule, for the ignore refusal,
  nesting refusal, exception pass-through, and restore-on-exit. No network.
- Doc: `task-test-not-applicable` — human-read prose with no executable consumer; every
  `path:line` is checked by reading it at the base commit during authoring and review.
- Guardrails: `just verify`, `uv run --no-sync prek run --all-files`.
