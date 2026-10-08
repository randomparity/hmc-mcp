# User modify: empty password and observed description reset

## Authority and outcome

Issue #1409's scope `q1409-0428c054` and the operator's 2026-10-08
“Include the harness correction” decision authorize this bounded correction.
The completed probe accepted the empty password element. Its failed observation
stays failed; the next live verification belongs to the authorized consolidated round.

## Design

Keep the optional password interface. The existing document builder emits an empty
`UserProfilePassword` with `ksv="V1_17_0" kb="CUD" kxe="false"` when no replacement
is supplied; a supplied password retains its existing encoding. Create requires a password.
The builder remains the XML owner; no caller migration or compatibility path is needed.

The users arm remains the verification owner. Its second modify sends `description=""`.
At commit `d2b676b37f553af6ff162e44d9f211ff86c2a1d8`, V10R3M1060 returned
`UserDescription` text `HMC User`. Replace the empty-readback expectation with that exact
value, retaining the `description-cleared` assertion ID to mean the supplied description
was removed. Do not admit arbitrary text, an unchanged description, or a missing value.
The external-service fake models this response; all other lifecycle assertions remain.
This is an observed V10R3 expectation, not a claim about untested HMC releases.

Record the decision and its limitations in [ADR 0202](../../adr/0202-user-profile-roles-are-named.md)
and CHANGELOG. Do not replace capability observations with synthetic passing evidence.

## Success

- Existing empty-password, supplied-password and required-create contracts retain their tests.
- The users-arm fake returns the captured default on reset; the lifecycle observation passes.
- Missing, empty, unrelated and unchanged descriptions fail `description-cleared` while cleanup
  still deletes the scratch user. Other modify and security assertions continue to run.
- Local guardrails pass. No new HMC contact occurs; consolidated live proof remains pending.

## Global constraints

Python 3.11–3.14; CI targets amd64 and arm64. Add no dependencies or public interface.
Use existing authorization, XML escaping and secret scrubbing. No standalone live rerun.
Retain current failed evidence. RemoteUserID facets remain owned by #1414;
password-required and SSH alternatives require an operator decision and are untriggered.

## Failure model

- Actors and deployments: authorized MCP/CLI callers; local users-arm operators; offline CI.
- Invariants and assets: passwords remain undisclosed; supplied password encoding is preserved;
  strict description/timeout/identity/remote-access readback and scratch cleanup remain enforced.
- Accepted failure classes: an untested release returning a different reset value fails the
  observation rather than being promoted; consolidated live proof is explicitly pending.
- Covered elsewhere: RemoteUserID facets (#1414); existing authorization and cleanup guards.

## Threat model

- Boundary inventory: existing caller-to-XML and HMC-to-verification boundaries; none added.
- Actors: authenticated callers supply strings; HMC responses are external data; operators
  authorize the isolated scratch-user lifecycle.
- Controls: existing string escaping, required create password, authorization, password
  scrubbing, strict readback predicates, UUID-bound deletion and baseline comparison.
- Out of scope: new authentication surfaces and remote account changes; existing owners and
  guards remain responsible. No live password-preservation proof was performed.

## Validation

Run users-arm tests with the fake changed first: lifecycle fails on the observed reset.
Then change the one predicate and verify positive and negative readbacks plus cleanup.
Run affected document/operation/security tests, full `just verify` and pinned prek hooks.
Keep the exact prior probe SHA and failed result in delivery notes; a local fake is not live proof.
