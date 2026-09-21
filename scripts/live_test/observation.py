"""Result vocabulary, failure classification and expected outcomes for live tests."""

from __future__ import annotations

import re
import traceback
from dataclasses import dataclass, field

#: Every value an observation's ``cleanup`` disposition may take. This ``not-run``
#: means cleanup did not run; it is unrelated to ADR 0126's deleted ``not-run``
#: observation shape, which ADR 0127 dropped. The result vocabulary itself
#: (``observed``, ``passed``, ``failed``, ``skipped``) is produced by
#: ``_result_for`` and ``record_verified`` in the runner and has no such member.
CLEANUP = frozenset({"not-run", "not-required", "failed", "passed"})

ASSERTION_ID = re.compile(r"\A[a-z][a-z0-9-]{1,62}[a-z0-9]\Z")
SCENARIO_ID = re.compile(r"\Ast\d+-[a-z0-9-]+\Z")
OPERATION_ID = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")
VARIANT_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")

_HTTP_STATUS_RE = re.compile(r"\bHTTP (\d{3})\b")

#: The ``on <targets>`` segment is optional because three of the four
#: ``src/hmc_mcp/authorization/target_scope.py`` templates (``:71``, ``:76``,
#: ``:80``) omit it, and all four are denials.
_DENIAL_RE = re.compile(r" is not permitted (?:on .+ )?by access policy ")


@dataclass(frozen=True)
class CallFailure:
    """One tool call's failure, classified from the exception it raised."""

    exception_type: str
    message: str
    traceback_text: str
    http_status: int | None
    denied: bool


@dataclass(frozen=True)
class Assertion:
    """One named postcondition a live scenario checked, and whether it held."""

    id: str
    holds: bool

    def __post_init__(self) -> None:
        if not ASSERTION_ID.fullmatch(self.id):
            raise ValueError(f"assertion id is not a closed-shape token: {self.id!r}")


@dataclass(frozen=True)
class ExpectedOutcome:
    """A known HMC limitation a scenario declares in advance, and how to spot it."""

    operation: str
    variant: str
    reason: str
    error_codes: frozenset[str] = field(default_factory=frozenset)
    denial: bool = False
    transient: bool = False

    def __post_init__(self) -> None:
        if not OPERATION_ID.fullmatch(self.operation):
            raise ValueError("expected outcome operation must be a domain.action token")
        if not VARIANT_ID.fullmatch(self.variant):
            raise ValueError("expected outcome variant must be a closed slug")
        if not self.error_codes and not self.denial:
            raise ValueError("an expected outcome must name an error code or a denial")

    def matches(self, failure: CallFailure) -> bool:
        """Report whether ``failure`` is the limitation this outcome declares.

        Matching is case-insensitive, as the substring match this replaced was:
        HMC messages render `Not Acceptable` and `Not Running` in title case,
        which a case-sensitive pattern would miss, silently recording a known
        limitation as a real failure. What changed is *where* and *how* the
        match runs — the message only, whole tokens only — not its case rule.
        """
        return any(
            re.search(rf"\b{re.escape(code)}\b", failure.message, re.IGNORECASE)
            for code in self.error_codes
        ) or (self.denial and failure.denied)


@dataclass(frozen=True)
class KnownGap:
    """A previous confirmation skipped dispatch; it is not a fresh observation."""

    outcome: ExpectedOutcome


def classify_failure(exc: BaseException) -> CallFailure:
    """Classify a raised tool failure from its message, never from its traceback.

    The traceback text is carried for a human reader but is not read here: a
    frame quoting an unrelated status would otherwise reclassify the failure.
    """
    message = f"{type(exc).__name__}: {exc}"
    status = _HTTP_STATUS_RE.search(str(exc))
    return CallFailure(
        exception_type=type(exc).__name__,
        message=message,
        traceback_text="".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ),
        http_status=int(status.group(1)) if status else None,
        denied=_DENIAL_RE.search(str(exc)) is not None,
    )
