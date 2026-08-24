"""HMC resource command construction and parsing over the SSH transport.

HMC CLI reference:
    https://www.ibm.com/docs/en/power10/7063-CR1?topic=hmc-commands
"""

from __future__ import annotations

import csv
import io
import re
import shlex
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from .config import HMCConfig
from .documents import LparResources
from .ssh import HMCCLIError, run_hmc_command


@dataclass(frozen=True)
class MemoptLparSelector:
    """Select LPARs by name or ID for an affinity-planning scenario."""

    names: tuple[str, ...] = field(
        default=(), metadata={"description": "LPAR names in the planning scenario."}
    )
    ids: tuple[int, ...] = field(
        default=(), metadata={"description": "LPAR IDs in the planning scenario."}
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "names", tuple(self.names))
        object.__setattr__(self, "ids", tuple(self.ids))
        if not self.names and not self.ids:
            raise ValueError("memopt LPAR selector must not be empty")
        if self.names and self.ids:
            raise ValueError("memopt LPAR selector must contain names or ids, not both")
        if self.names:
            if any(
                not isinstance(name, str)
                or not name.strip()
                or "," in name
                or any(
                    ord(character) < 32 or ord(character) == 127 for character in name
                )
                for name in self.names
            ):
                raise ValueError(
                    "memopt LPAR selector names must be nonblank and contain no "
                    "commas or control characters"
                )
            if len(set(self.names)) != len(self.names):
                raise ValueError(
                    "memopt LPAR selector names must not contain duplicates"
                )
        if self.ids:
            if any(
                not isinstance(lpar_id, int)
                or isinstance(lpar_id, bool)
                or lpar_id <= 0
                for lpar_id in self.ids
            ):
                raise ValueError("memopt LPAR selector ids must be positive integers")
            if len(set(self.ids)) != len(self.ids):
                raise ValueError("memopt LPAR selector ids must not contain duplicates")


# ---------------------------------------------------------------------- #
# HMC CLI -i attribute record grammar (see ADR 0045)
# ---------------------------------------------------------------------- #
# `chsyscfg`/`mksyscfg` take their configuration as one `-i` argument holding
# an attribute record: `name=lpar1,description=web tier`.  Three characters carry
# that record's structure, and the HMC splits the record itself *after* the
# shell has finished with the argument — so `shlex.quote` cannot protect them.

_RECORD_DELIMITERS: dict[str, tuple[str, str]] = {
    ",": ("a comma", "a comma separates one attribute from the next"),
    "=": (
        "an equals sign",
        "an equals sign separates an attribute name from its value",
    ),
    '"': (
        "a double quote",
        "a double quote is the HMC's own escape for a value containing a comma, "
        "so it opens a quoted region that swallows the attributes after it",
    ),
}

# An HMC attribute name, optionally carrying the list append/remove operator
# that `chsyscfg -r prof` uses (`io_slots+=…` / `io_slots-=…`).
_ATTRIBUTE_NAME = re.compile(r"^[a-z_][a-z0-9_]*[+-]?$")

# Characters `set_lpar_description` has always refused in the LPAR name it
# writes a description for.  Neither is record structure — IBM's own escaping
# note shows an unquoted `name=No comma name` — so this rejection is not part
# of the record grammar and is deliberately not extended to the other records.
# It is kept at its historical site, unchanged, because widening or dropping a
# public tool's accepted input is not this module's call to make.  See ADR 0045.
_DESCRIPTION_TARGET_UNSAFE: dict[str, tuple[str, str]] = {
    " ": (
        "a space",
        "a space may make the HMC's internal -i parser tokenise incorrectly",
    ),
    ";": ("a semicolon", "a semicolon may corrupt the HMC CLI -i parser"),
}


def parse_hmc_delimited_rows(
    text: str,
    fields: Sequence[str],
    delimiter: str = ",",
) -> list[dict[str, str]]:
    """Parse strict, header-bearing HMC delimited output into named rows."""
    expected = tuple(fields)
    if not expected or any(not field or field.strip() != field for field in expected):
        raise ValueError(
            "fields must contain non-empty names without surrounding whitespace"
        )
    if len(set(expected)) != len(expected):
        raise ValueError("fields must not contain duplicates")
    if len(delimiter) != 1 or delimiter in {"\r", "\n"}:
        raise ValueError("delimiter must be one non-newline character")

    records = [line for line in text.splitlines() if line.strip()]
    if not records:
        raise ValueError("HMC delimited output is missing its header")
    try:
        parsed = [
            list(csv.reader([line], delimiter=delimiter, strict=True))[0]
            for line in records
        ]
    except csv.Error as error:
        raise ValueError(f"malformed HMC delimited output: {error}") from error
    if tuple(parsed[0]) != expected:
        raise ValueError("HMC delimited header does not match the requested fields")

    rows: list[dict[str, str]] = []
    for number, values in enumerate(parsed[1:], start=2):
        if len(values) != len(expected):
            raise ValueError(
                f"HMC delimited row {number} has {len(values)} columns; expected {len(expected)}"
            )
        rows.append(dict(zip(expected, values, strict=True)))
    return rows


def build_attribute_record(
    pairs: Sequence[tuple[str, object]],
    *,
    quoted: Collection[str] = (),
    surface: str = "-i",
) -> str:
    """Return the ``-i`` attribute record for *pairs*, or raise.

    *quoted* names list-valued attributes whose HMC-side grammar is a
    comma-separated list (ADR 0061).  A marked value containing a comma is
    rendered as the IBM quoted pair ``"name=v1,v2"``; without a comma it
    renders bare, byte-identical to the unmarked form.  Every other record
    delimiter is refused inside a marked value: only the comma's behaviour
    inside a quoted region is live-verified.

    *pairs* is an ordered sequence of ``(attribute, value)``.  Each value is
    rendered with :func:`str` and checked against the record grammar before the
    record is joined, so no caller-supplied value can introduce or terminate an
    attribute the caller was not given an argument for.

    Callers still wrap the result in :func:`shlex.quote`.  The two mechanisms
    protect different layers and neither substitutes for the other:
    ``shlex.quote`` keeps the record a single word for the *remote shell*; this
    function keeps the record's own ``,``, ``=``, and ``"`` structure meaningful
    for the *HMC's* parser, which runs afterwards on the already-unquoted text.

    The rejection message quotes the offending value back to the caller.  Every
    attribute reaching this function today carries a name, an enumerated mode,
    or a number; do not route a credential attribute (``chhmcusr -i
    "name=…,passwd=…"``) through it without redacting the value first.

    Raises:
        HMCCLIError: If *pairs* is empty, repeats an attribute, names a
            malformed attribute, or carries a value containing a character the
            record's parser treats as structure.  The message names the
            attribute, the character, and its effect.
    """
    if not pairs:
        raise HMCCLIError(
            f"cannot build an HMC CLI {surface} attribute record with no "
            "attributes; at least one attribute is required"
        )
    seen: set[str] = set()
    for attribute, _value in pairs:
        if attribute in seen:
            raise HMCCLIError(
                f"HMC CLI {surface} attribute {attribute!r} appears twice in "
                "one record; the HMC's handling of a repeated attribute is "
                "undefined, so the record is refused rather than sent"
            )
        seen.add(attribute)
    quotable = frozenset(quoted)
    parts = []
    for index, (attribute, value) in enumerate(pairs):
        text = _validated_value(
            attribute, value, allow_comma=attribute in quotable, surface=surface
        )
        if attribute in quotable and "," in text:
            if index != len(pairs) - 1:
                # The live probes verified the quoted pair only as the final
                # element of the record (ADR 0061); a non-trailing quoted
                # pair is an unprobed form, so it fails closed like every
                # other unprobed grammar variant.
                raise HMCCLIError(
                    f"HMC CLI {surface} attribute {attribute!r} renders as a "
                    'quoted pair ("name=v1,v2"), which the HMC has only been '
                    "shown to accept as the record's final element; it cannot "
                    f"be followed by {pairs[index + 1][0]!r}. Place "
                    f"{attribute!r} last or split the record."
                )
            parts.append(f'"{attribute}={text}"')
        else:
            parts.append(f"{attribute}={text}")
    return ",".join(parts)


def build_filter(pairs: Sequence[tuple[str, object]]) -> str:
    """Return the ``--filter`` expression for *pairs*, or raise.

    The ``--filter`` grammar is the same ``name=value`` comma-joined record
    grammar (ADR 0061): a delimiter inside a value adds or rewrites a filter
    pair, so a mutation would select a partition the caller did not name.
    Values are validated by the same ``_validated_value`` primitive the
    record builder uses; there is no second delimiter table.

    A comma *inside* one value — IBM's multi-value list form — is refused
    until its encoding is probed; every site here selects a single resolved
    object by name.
    """
    if not pairs:
        raise HMCCLIError(
            "cannot build an HMC CLI --filter expression with no pairs; "
            "at least one name=value pair is required"
        )
    seen: set[str] = set()
    for attribute, _value in pairs:
        if attribute in seen:
            raise HMCCLIError(
                f"HMC CLI --filter attribute {attribute!r} appears twice in "
                "one expression; the HMC's handling of a repeated filter "
                "attribute is undefined, so the expression is refused"
            )
        seen.add(attribute)
    return ",".join(
        f"{attribute}={_validated_value(attribute, value, surface='--filter')}"
        for attribute, value in pairs
    )


def _validated_value(
    attribute: str,
    value: object,
    *,
    allow_comma: bool = False,
    surface: str = "-i",
) -> str:
    """Return *value* as record text, or raise :class:`HMCCLIError`.

    *allow_comma* marks a quotable list attribute (ADR 0061): the comma is
    permitted because the caller renders the pair quoted.  *surface* names
    the command surface in refusal messages so a ``--filter`` or ``-a``
    refusal never blames an ``-i`` record.
    """
    if not _ATTRIBUTE_NAME.match(attribute):
        raise HMCCLIError(
            f"invalid HMC CLI {surface} attribute name {attribute!r}; expected a "
            "lower-case identifier, optionally with a '+' or '-' list operator"
        )
    text = str(value)
    for character, (name, reason) in _RECORD_DELIMITERS.items():
        if character in text and not (allow_comma and character == ","):
            raise HMCCLIError(
                f"HMC CLI {surface} attribute {attribute!r} value {text!r} contains "
                f"{name} ({character!r}); {reason}, so the value would alter "
                f"the record's structure. Remove {name} from the value."
            )
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in text):
        raise HMCCLIError(
            f"HMC CLI {surface} attribute {attribute!r} value {text!r} contains a "
            "control character; the record is one line and the same data "
            "format is read one record per line by the -f file form, so a "
            "newline may terminate the record and a NUL may truncate it. "
            "Remove the control character from the value."
        )
    return text


def validate_lpar_description(description: str) -> None:
    """Raise ``ValueError`` if *description* cannot be written to the HMC.

    The HMC enforces printable ASCII-only partition descriptions (HSCLC63B).
    Control characters (NUL, LF, CR, ESC, …) are also rejected because they
    can corrupt the HMC CLI's CSV-like ``-i`` parser or be silently truncated
    at the C-string layer.  Every character in :data:`_RECORD_DELIMITERS` is
    rejected too, because the ``-i`` record's parser reads them as structure:
    ``description=x,foo=bar`` sets a ``foo`` attribute the caller was never
    given an argument for.  The message names the offending character, so this
    docstring does not restate the table — it has grown once already.

    Called at the MCP tool layer before UUID resolution and again inside
    :func:`set_lpar_description` as a defensive check.  Both call sites are
    intentional: the outer call provides fast rejection without REST
    round-trips; the inner call guards callers that bypass the MCP tool.

    The structural characters come from :data:`_RECORD_DELIMITERS`, the same
    table :func:`build_attribute_record` enforces, so the two layers cannot
    drift.  Only the exception type differs: this is the caller-facing
    validator (``ValueError``); the builder refuses the record itself
    (``HMCCLIError``).
    """
    if not description.isascii() or any(
        ord(c) < 0x20 or ord(c) == 0x7F for c in description
    ):
        raise ValueError(
            "description contains non-ASCII or non-printable characters; "
            "the HMC only accepts printable ASCII partition descriptions (HSCLC63B)"
        )
    for character, (name, reason) in _RECORD_DELIMITERS.items():
        if character in description:
            raise ValueError(
                f"description {description!r} contains {name} ({character!r}); "
                f"{reason} in the HMC CLI -i attribute record, so the text "
                f"would be read as further attributes rather than as the "
                f"description. Remove {name} from the description."
            )


def validate_caller_token(token: str) -> None:
    """Raise ``ValueError`` if *token* cannot be embedded as ``[caller <token>]``.

    The grammar is server-defined (ADR 0064): 1–64 printable ASCII characters,
    forbidding whitespace, control characters, non-ASCII, and the characters
    ``,` ``=```"``[``]`` and ``\\``.  Commas, equals signs, and quotes are the
    HMC CLI ``-i`` record structure (ADR 0045); brackets break the caller
    segment's own framing; the backslash is refused because ADR 0045 records
    its ``-i`` behaviour as unverified.  The empty string is a violation, not
    an omission, and a non-``str`` value is rejected before any character
    check because the second validation site serves callers that bypass MCP
    tool typing.
    """
    if not isinstance(token, str):
        raise ValueError(
            f"caller_token must be a string, got {type(token).__name__}"
        )
    if not token:
        raise ValueError("caller_token must not be empty")
    if len(token) > 64:
        raise ValueError(f"caller_token is {len(token)} characters; maximum is 64")
    if not token.isascii() or any(
        ord(character) < 0x20 or ord(character) == 0x7F for character in token
    ):
        raise ValueError(
            "caller_token contains non-ASCII or non-printable characters; "
            "only printable ASCII is accepted"
        )
    if any(character.isspace() for character in token):
        raise ValueError(
            "caller_token contains whitespace; it must be a single word"
        )
    forbidden = {
        ",": "commas corrupt the HMC CLI -i parser",
        "=": "equals signs corrupt the HMC CLI -i parser",
        '"': "double quotes are the HMC CLI -i record escape",
        "[": "brackets break the [caller <token>] segment format",
        "]": "brackets break the [caller <token>] segment format",
        "\\": "backslash behaviour inside an HMC CLI -i record is unverified "
        "(ADR 0045)",
    }
    for character, reason in forbidden.items():
        if character in token:
            raise ValueError(f"caller_token contains {character!r}; {reason}")


async def stamp_lpar_ownership(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    *,
    agent_id: str | None = None,
    caller_token: str | None = None,
) -> str | None:
    """Write an ownership token, plus an optional caller token, to *lpar_name*.

    Builds ``[hmc-mcp owner:<agent_id> created:<YYYY-MM-DD>]`` and, when
    *caller_token* is given, appends `` [caller <token>]`` (ADR 0064), then
    writes the combined description with :func:`set_lpar_description` over SSH
    in one call.

    Returns the description on success; returns ``None`` (without raising) on
    SSH/network failure or a composed-description grammar failure — a
    best-effort post-create call that must not fail the LPAR creation itself.
    A malformed *caller_token* raises ``ValueError`` before any SSH traffic
    instead of being swallowed, so it can never discard the ownership stamp.

    *agent_id* defaults to ``"hmc-mcp"`` when ``None`` or empty.
    """
    import datetime

    if caller_token is not None:
        validate_caller_token(caller_token)
    effective_id = agent_id if agent_id else "hmc-mcp"
    today = datetime.date.today().isoformat()
    description = f"[hmc-mcp owner:{effective_id} created:{today}]"
    if caller_token is not None:
        description = f"{description} [caller {caller_token}]"
    try:
        # The composed description still gets the HMC's own grammar check
        # here, inside the best-effort boundary, as a defensive last resort:
        # validate_agent_id now refuses every character the description field
        # would reject ('"' and '\\' included; ADR 0065), so a ValueError
        # raised below the pre-flight check has no expected config-driven case
        # left — it still degrades to a skipped stamp rather than failing
        # the owning create after the LPAR already exists.  A malformed
        # caller_token cannot reach this catch: validate_caller_token above
        # the try raises before any SSH traffic (ADR 0064).
        validate_lpar_description(description)
        await set_lpar_description(config, system_name, lpar_name, description)
        return description
    except (HMCCLIError, OSError, ValueError):
        # Transport, network, and composed-description grammar failures are
        # best-effort here: none of these should fail the owning create call.
        return None


async def create_lpar_via_cli(
    config: HMCConfig,
    system_name: str,
    name: str,
    partition_type: str = "AIX/Linux",
    resources: LparResources = LparResources(),
    max_virtual_slots: int | None = None,
    profile_name: str = "default_profile",
) -> str:
    """Create an LPAR via ``mksyscfg`` over SSH.

    Uses the HMC CLI (SSH) instead of the REST API because some HMC firmware
    versions return HTTP 406 for ``PUT ManagedSystem/{uuid}/LogicalPartition``
    regardless of schema-version headers.  This is the same approach used by
    the IBM ansible-power-hmc collection and IBM internal provisioning toolkits.

    When no explicit resource values (memory/proc/vcpu) are provided, the
    ``all_resources=1`` flag is used, which allocates all available system
    resources and skips the need for exact proc/memory configuration.  This is
    the most reliable approach for HMC firmware that enforces strict resource
    accounting.  Pass explicit values to override individual resources.

    Returns the raw ``mksyscfg`` stdout (typically empty on success).
    Raises :class:`HMCCLIError` on non-zero exit.
    """
    _pt = partition_type.lower()
    if "ios" in _pt or "vios" in _pt:
        lpar_env = "vioserver"
    elif "os400" in _pt or "ibmi" in _pt or _pt == "i":
        lpar_env = "os400"
    else:
        lpar_env = "aixlinux"

    config_pairs: list[tuple[str, object]] = [
        ("name", name),
        ("lpar_env", lpar_env),
        ("profile_name", profile_name),
    ]

    # Determine whether any explicit resource values were provided.
    # If none are given, use all_resources=1 (simplest and most compatible).
    explicit_resources = any(
        v is not None
        for v in (
            resources.min_memory,
            resources.desired_memory,
            resources.max_memory,
            resources.min_procs,
            resources.desired_procs,
            resources.max_procs,
            resources.min_vcpus,
            resources.desired_vcpus,
            resources.max_vcpus,
        )
    )

    if explicit_resources:
        # mksyscfg requires min/desired/max for all three resource axes when
        # any explicit value is given; fall back to safe defaults for omitted
        # fields so the command does not fail with a missing-attribute error.
        _min_mem = resources.min_memory or 256
        _des_mem = resources.desired_memory or 4096
        _max_mem = resources.max_memory or max(_des_mem, 8192)
        _min_pu = resources.min_procs or 0.1
        _des_pu = resources.desired_procs or 0.1
        _max_pu = resources.max_procs or max(_des_pu, 2.0)
        _min_vp = resources.min_vcpus or 1
        _des_vp = resources.desired_vcpus or 1
        _max_vp = resources.max_vcpus or max(_des_vp, 2)

        config_pairs += [
            ("min_mem", _min_mem),
            ("desired_mem", _des_mem),
            ("max_mem", _max_mem),
            ("proc_mode", "shared"),
            ("sharing_mode", "uncap"),
            ("min_proc_units", _min_pu),
            ("desired_proc_units", _des_pu),
            ("max_proc_units", _max_pu),
            ("min_procs", _min_vp),
            ("desired_procs", _des_vp),
            ("max_procs", _max_vp),
        ]
        if max_virtual_slots is not None:
            config_pairs.append(("max_virtual_slots", max_virtual_slots))
    else:
        config_pairs.append(("all_resources", 1))

    # Two guards at two layers, neither substituting for the other:
    # build_attribute_record keeps the record's own ',' and '=' delimiters
    # meaningful to the HMC's parser, which splits the record itself; shlex.quote
    # keeps the whole record one word for the remote shell, which runs first and
    # strips the quotes before the HMC ever sees the text.
    config_str = build_attribute_record(config_pairs)
    cmd = f"mksyscfg -r lpar -m {shlex.quote(system_name)} -i {shlex.quote(config_str)}"
    return await run_hmc_command(config, cmd)


# ---------------------------------------------------------------------- #
# UUID -> CLI-name lookup (SSH fallback for the REST-based resolvers)
# ---------------------------------------------------------------------- #


async def _ssh_system_name(config: HMCConfig, system_uuid: str) -> str:
    """Look up a managed-system UUID's CLI SystemName over SSH.

    Runs ``lssyscfg -r sys -F UUID,SystemName`` and returns the row whose
    UUID column matches. Used as the fallback by the REST-based system-name
    resolver in :mod:`hmc_mcp._app` when the REST API is unreachable.

    Raises:
        HMCCLIError: If no row matches *system_uuid* in the command output.
    """
    raw = await run_hmc_command(config, "lssyscfg -r sys -F UUID,SystemName")
    return _match_uuid_name(raw, system_uuid, "system")


async def _ssh_lpar_name(
    config: HMCConfig,
    lpar_uuid: str,
    system_name: str | None = None,
) -> str:
    """Look up an LPAR UUID's CLI PartitionName over SSH.

    Runs ``lssyscfg -r lpar [-m <system_name>] -F UUID,PartitionName``, scoped
    to *system_name* when given and across all managed systems otherwise. Used
    as the fallback by the REST-based LPAR-name resolver in :mod:`hmc_mcp._app`
    when the REST API is unreachable.

    Raises:
        HMCCLIError: If no row matches *lpar_uuid* in the command output.
    """
    cmd = "lssyscfg -r lpar"
    if system_name:
        cmd += f" -m {shlex.quote(system_name)}"
    cmd += " -F UUID,PartitionName"
    raw = await run_hmc_command(config, cmd)
    return _match_uuid_name(raw, lpar_uuid, "LPAR")


def _match_uuid_name(raw: str, uuid: str, what: str) -> str:
    """Return the name on the ``UUID,<name>`` line matching *uuid*.

    Non-matching lines are skipped; a matching line with an empty name column
    (malformed row) is not returned.
    """
    for line in raw.splitlines():
        row_uuid, _, name = line.partition(",")
        if row_uuid.strip() == uuid:
            name = name.strip()
            if name:
                return name
    raise HMCCLIError(
        f"Could not resolve {what} UUID {uuid!r} to a CLI name over SSH. "
        "No matching row in the lssyscfg UUID,name output."
    )


def _parse_lshwres_output(text: str) -> list[dict[str, Any]]:
    """Parse ``lshwres`` key=value output into a list of dicts.

    Each non-empty line is expected to be a comma-separated sequence of
    ``key=value`` pairs (the default ``lshwres`` output format).  Values that
    are absent (empty string) are included as empty strings so callers can
    distinguish missing from absent fields.
    """
    results = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        row: dict[str, Any] = {}
        last_key: str | None = None
        for pair in line.split(","):
            if "=" in pair:
                key, _, value = pair.partition("=")
                last_key = key.strip()
                row[last_key] = value.strip()
            elif last_key is not None:
                # bare token — comma is part of the previous value (e.g. LPAR name lists)
                row[last_key] = row[last_key] + "," + pair.strip()
            else:
                # bare token with no prior key — store as-is
                row[pair.strip()] = ""
        if row:
            results.append(row)
    return results


# PCI class codes used by lshwres -r io --rsubtype slot.
_IO_SLOT_PCI_CLASS = {
    "eth": "0200",
    "sas": "0104",
    "san": "0C04",
    "nvme": "0108",
}
PciClass = Literal["all", "eth", "sas", "san", "nvme"]
_VALID_PCI_CLASSES = frozenset(get_args(PciClass))


async def list_io_slots(
    config: HMCConfig,
    system_name: str,
    pci_class: PciClass = "all",
) -> list[dict[str, Any]]:
    """List physical I/O slots on *system_name* via SSH.

    Runs ``lshwres -r io --rsubtype slot -m <system_name>`` and optionally
    filters by PCI class using ``grep pci_class=<code>``.

    pci_class may be one of:
      - ``"all"``   — return every slot (default, no filter)
      - ``"eth"``   — Ethernet adapters (PCI class 0200)
      - ``"sas"``   — SAS/SCSI adapters (PCI class 0104)
      - ``"san"``   — Fibre Channel / SAN adapters (PCI class 0C04)
      - ``"nvme"``  — NVMe adapters (PCI class 0108)

    Returns a list of dicts parsed from the key=value HMC output rows, with
    fields such as ``drc_name``, ``pci_class``, ``feature_codes``, and
    ``lpar_name`` (empty string when the slot is unassigned).

    Raises:
        ValueError: If *pci_class* is not one of the recognised values.
    """
    if pci_class not in _VALID_PCI_CLASSES:
        valid = ", ".join(sorted(_VALID_PCI_CLASSES))
        raise ValueError(f"Invalid pci_class {pci_class!r}. Must be one of: {valid}")
    cmd = f"lshwres -r io --rsubtype slot -m {shlex.quote(system_name)}"
    if pci_class != "all":
        pci_code = _IO_SLOT_PCI_CLASS[pci_class]
        cmd += f" | grep pci_class={shlex.quote(pci_code)}"
    output = await run_hmc_command(config, cmd)
    return _parse_lshwres_output(output)


async def list_dedicated_pcie_slot_rows(
    config: HMCConfig,
    system_name: str,
) -> list[dict[str, str]]:
    """Read the exact dedicated-slot projection admitted by ADR 0053."""
    fields = ("drc_index", "description", "lpar_name")
    projection = ",".join(fields)
    command = (
        f"lshwres -r io --rsubtype slot -m {shlex.quote(system_name)} "
        f"-F {projection} --header"
    )
    output = await run_hmc_command(config, command)
    return parse_hmc_delimited_rows(output, fields)


def _parse_admitted_rows(output: str, fields: tuple[str, ...]) -> list[dict[str, str]]:
    if output.strip() == "No results were found.":
        return []
    return parse_hmc_delimited_rows(output, fields)


async def list_sriov_adapter_rows(
    config: HMCConfig, system_name: str
) -> list[dict[str, str]]:
    fields = (
        "adapter_id",
        "slot_id",
        "config_state",
        "functional_state",
        "phys_loc",
        "phys_ports",
        "logical_ports",
        "adapter_max_logical_ports",
        "sriov_status",
    )
    command = f"lshwres -r sriov --rsubtype adapter -m {shlex.quote(system_name)} -F {','.join(fields)} --header"
    return _parse_admitted_rows(await run_hmc_command(config, command), fields)


async def read_sriov_environment(
    config: HMCConfig, system_name: str
) -> tuple[str, str]:
    """Return the exact HMC release and managed-system model admission inputs."""
    version = (await run_hmc_command(config, "lshmc -V")).strip()
    model = (
        await run_hmc_command(
            config,
            f"lssyscfg -r sys -m {shlex.quote(system_name)} -F type_model",
        )
    ).strip()
    return version, model


async def list_sriov_physical_port_rows(
    config: HMCConfig, system_name: str, adapter_id: str
) -> list[dict[str, str]]:
    fields = (
        "adapter_id",
        "phys_port_id",
        "phys_port_type",
        "phys_port_loc",
        "state",
        "config_logical_ports",
        "phys_port_max_logical_ports",
        "curr_eth_logical_ports",
    )
    command = f"lshwres -r sriov --rsubtype physport -m {shlex.quote(system_name)} --level roce --filter {shlex.quote(build_filter([('adapter_ids', adapter_id)]))} -F {','.join(fields)} --header"
    return _parse_admitted_rows(await run_hmc_command(config, command), fields)


_SRIOV_LOGICAL_FIELDS = (
    "config_id",
    "lpar_name",
    "lpar_id",
    "lpar_state",
    "adapter_id",
    "logical_port_id",
    "logical_port_type",
    "phys_port_id",
    "functional_state",
    "capacity",
    "max_capacity",
)


async def list_sriov_configured_logical_port_rows(
    config: HMCConfig, system_name: str, adapter_id: str
) -> list[dict[str, str]]:
    command = f"lshwres -r sriov --rsubtype logport -m {shlex.quote(system_name)} --level eth --filter {shlex.quote(build_filter([('adapter_ids', adapter_id)]))} -F {','.join(_SRIOV_LOGICAL_FIELDS)} --header"
    return _parse_admitted_rows(
        await run_hmc_command(config, command), _SRIOV_LOGICAL_FIELDS
    )


async def list_sriov_unconfigured_logical_port_rows(
    config: HMCConfig, system_name: str
) -> list[dict[str, str]]:
    command = f"lshwres -r sriov --rsubtype logport -m {shlex.quote(system_name)}"
    return [
        dict(row)
        for row in _parse_lshwres_output(await run_hmc_command(config, command))
        if row.get("logical_port_type") == "unconfigured"
    ]


async def read_sriov_lpar_state(
    config: HMCConfig, system_name: str, lpar_name: str
) -> dict[str, str]:
    fields = ("name", "lpar_id", "state", "rmc_state")
    command = f"lssyscfg -r lpar -m {shlex.quote(system_name)} --filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))} -F {','.join(fields)} --header"
    rows = _parse_admitted_rows(await run_hmc_command(config, command), fields)
    if len(rows) != 1:
        raise HMCCLIError(
            f"Expected one LPAR state row for {lpar_name!r}; got {len(rows)}"
        )
    return rows[0]


async def read_sriov_profile_ports(
    config: HMCConfig, system_name: str, lpar_name: str, profile_name: str
) -> dict[str, str]:
    fields = ("name", "sriov_eth_logical_ports")
    filters = build_filter(
        [("lpar_names", lpar_name), ("profile_names", profile_name)]
    )
    command = f"lssyscfg -r prof -m {shlex.quote(system_name)} --filter {shlex.quote(filters)} -F {','.join(fields)} --header"
    rows = _parse_admitted_rows(await run_hmc_command(config, command), fields)
    if len(rows) != 1:
        raise HMCCLIError(
            f"Expected one SR-IOV profile row for {profile_name!r}; got {len(rows)}"
        )
    return rows[0]


async def assign_sriov_logical_port_dynamic(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    adapter_id: str,
    physical_port_id: str,
    logical_port_id: str,
    capacity: str,
) -> str:
    record = build_attribute_record(
        [
            ("adapter_id", adapter_id),
            ("phys_port_id", physical_port_id),
            ("logical_port_id", logical_port_id),
            ("logical_port_type", "eth"),
            ("capacity", capacity),
        ]
    )
    command = f"chhwres -r sriov --rsubtype logport -m {shlex.quote(system_name)} -o a -p {shlex.quote(lpar_name)} -a {shlex.quote(record)}"
    return await run_hmc_command(config, command)


async def unassign_sriov_logical_port_profile(
    config: HMCConfig, system_name: str, lpar_name: str, profile_name: str
) -> str:
    record = build_attribute_record(
        [
            ("name", profile_name),
            ("lpar_name", lpar_name),
            ("sriov_eth_logical_ports", "none"),
        ]
    )
    command = f"chsyscfg -r prof -m {shlex.quote(system_name)} -i {shlex.quote(record)}"
    return await run_hmc_command(config, command)


async def list_fc_ports(
    config: HMCConfig,
    system_name: str,
    lpar_name: str | None = None,
) -> list[dict[str, str]]:
    """List Virtual Fibre Channel (NPIV) adapters via SSH.

    Runs ``lshwres -r virtualio --rsubtype fc --level lpar -m <system_name>``
    and parses the CSV output rows (lpar_name, slot_num, wwpns, ...).  Pass
    *lpar_name* to restrict results to a single partition.
    """
    cmd = (
        f"lshwres -r virtualio --rsubtype fc --level lpar -m {shlex.quote(system_name)}"
    )
    if lpar_name:
        cmd += f" --filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))}"
    raw = await run_hmc_command(config, cmd)
    if not raw.strip():
        return []
    reader = csv.DictReader(io.StringIO(raw.strip()))
    return [dict(row) for row in reader]


async def list_sea_adapters(
    config: HMCConfig,
    system_name: str,
    lpar_name: str | None = None,
) -> list[dict[str, str]]:
    """List Shared Ethernet Adapter (SEA) virtual Ethernet ports via SSH.

    Runs ``lshwres -r virtualio --rsubtype eth --level lpar -m <system_name>
    -F lpar_name,port_vlan_id,vswitch,state,trunk_priority`` and returns one
    dict with those five fields per port.  Pass *lpar_name* to restrict
    results to a single partition.
    """
    fields = "lpar_name,port_vlan_id,vswitch,state,trunk_priority"
    cmd = (
        f"lshwres -r virtualio --rsubtype eth --level lpar -m {shlex.quote(system_name)}"
        f" -F {fields}"
    )
    if lpar_name:
        cmd += f" --filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))}"
    raw = await run_hmc_command(config, cmd)
    if not raw.strip():
        return []
    keys = fields.split(",")
    result: list[dict[str, str]] = []
    for line in raw.strip().splitlines():
        values = line.split(",", len(keys) - 1)
        result.append(dict(zip(keys, values)))
    return result


async def list_vnics(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
) -> list[dict[str, Any]]:
    """List vNICs (SR-IOV-backed Virtual NICs) on an LPAR via SSH.

    Runs ``lshwres -r virtualio --rsubtype vnic --level lpar -m <system_name>
    --filter lpar_names=<lpar_name>`` and returns one dict per vNIC parsed
    from the key=value rows, with fields such as ``vnic_id``, ``capacity``,
    ``vswitch_name``, ``port_vlan_id``, and ``backing_devices``.
    """
    cmd = (
        f"lshwres -r virtualio --rsubtype vnic --level lpar -m {shlex.quote(system_name)}"
        f" --filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))}"
    )
    raw = await run_hmc_command(config, cmd)
    if not raw.strip():
        return []
    return _parse_lshwres_output(raw)


_VNIC_FIELDS = (
    "lpar_name",
    "lpar_id",
    "slot_num",
    "desired_mode",
    "curr_mode",
    "auto_priority_failover",
    "port_vlan_id",
    "pvid_priority",
    "allowed_vlan_ids",
    "mac_addr",
    "allowed_os_mac_addrs",
    "backing_devices",
    "backing_device_states",
)
_VNIC_BACKING_FIELDS = (
    "lpar_name",
    "lpar_id",
    "type",
    "adapter_id",
    "physical_port_id",
    "logical_port_id",
    "capacity",
    "desired_capacity",
    "max_capacity",
    "desired_max_capacity",
    "failover_priority",
    "is_active",
    "status",
)
_VIOS_IDENTITY_FIELDS = ("name", "lpar_id", "lpar_env")


async def list_vnic_rows(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
) -> list[dict[str, str]]:
    """Return strict, version-admitted vNIC rows for one partition."""
    fields = ",".join(_VNIC_FIELDS)
    command = (
        "lshwres -r virtualio --rsubtype vnic --level lpar"
        f" -m {shlex.quote(system_name)}"
        f" --filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))}"
        f" -F {fields} --header"
    )
    output = await run_hmc_command(config, command)
    return parse_hmc_delimited_rows(output, _VNIC_FIELDS)


async def list_vnic_backing_rows(
    config: HMCConfig,
    system_name: str,
) -> list[dict[str, str]]:
    """Return strict, system-wide vNIC backing-device rows."""
    fields = ",".join(_VNIC_BACKING_FIELDS)
    command = (
        "lshwres -r virtualio --rsubtype vnicbkdev"
        f" -m {shlex.quote(system_name)} -F {fields} --header"
    )
    output = await run_hmc_command(config, command)
    if output.strip() == "No results were found.":
        return []
    return parse_hmc_delimited_rows(output, _VNIC_BACKING_FIELDS)


async def read_vios_identity(
    config: HMCConfig,
    system_name: str,
    vios_name: str,
) -> dict[str, str]:
    """Return the unique strict identity row for a named VIOS candidate."""
    fields = ",".join(_VIOS_IDENTITY_FIELDS)
    command = (
        f"lssyscfg -r lpar -m {shlex.quote(system_name)}"
        f" --filter {shlex.quote(build_filter([('lpar_names', vios_name)]))}"
        f" -F {fields} --header"
    )
    rows = parse_hmc_delimited_rows(
        await run_hmc_command(config, command), _VIOS_IDENTITY_FIELDS
    )
    if len(rows) != 1:
        raise ValueError(
            f"VIOS identity read for {vios_name!r} returned {len(rows)} rows; expected 1"
        )
    return rows[0]


async def add_vnic_backing(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    backing_device: str,
    port_vlan_id: int,
) -> str:
    """Add one vNIC via ``chhwres -r virtualio --rsubtype vnic -o a``.

    *backing_device* is a ``/``-delimited SR-IOV device spec, or a
    comma-separated list of them; a value carrying a comma renders as the
    IBM quoted pair ``"backing_devices=dev1,dev2"`` so the list survives the
    record grammar (ADR 0061).  Any other record delimiter in the value is
    refused before the command is built.
    """
    payload = build_attribute_record(
        [("port_vlan_id", port_vlan_id), ("backing_devices", backing_device)],
        quoted=("backing_devices",),
        # Not spelled `chhwres -a ...`: a plain string opening with the
        # command name would itself trip the recurrence guard's -a scan.
        surface="`chhwres -a`",
    )
    command = (
        "chhwres -r virtualio --rsubtype vnic -o a"
        f" -m {shlex.quote(system_name)} -p {shlex.quote(lpar_name)}"
        f" -a {shlex.quote(payload)}"
    )
    return await run_hmc_command(config, command)


async def remove_vnic_slot(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    slot_num: str,
) -> str:
    """Remove one vNIC by its admitted partition-local slot identity."""
    command = (
        "chhwres -r virtualio --rsubtype vnic -o r"
        f" -m {shlex.quote(system_name)} -p {shlex.quote(lpar_name)}"
        f" -s {shlex.quote(slot_num)}"
    )
    return await run_hmc_command(config, command)


async def list_lpar_memopt_scores(
    config: HMCConfig,
    system_name: str,
    lpar_name: str | None = None,
) -> list[dict[str, Any]]:
    """List current memory-optimization scores for a system's LPARs via SSH."""
    command = f"lsmemopt -m {shlex.quote(system_name)} -r lpar -o currscore"
    if lpar_name is not None:
        if not lpar_name.strip():
            raise ValueError("lpar_name must not be empty")
        lpar_filter = build_filter([("lpar_names", lpar_name)])
        command += f" --filter {shlex.quote(lpar_filter)}"
    output = await run_hmc_command(config, command)
    if not output.strip():
        return []
    rows = _parse_lshwres_output(output)
    required = {"lpar_name", "lpar_id", "curr_lpar_score"}
    for index, row in enumerate(rows, start=1):
        missing = sorted(required - row.keys())
        if missing:
            raise HMCCLIError(
                f"lsmemopt row {index} is missing required fields: {', '.join(missing)}"
            )
    if lpar_name is not None:
        if len(rows) > 1:
            raise HMCCLIError(
                f"lsmemopt filtered query for LPAR {lpar_name!r} returned "
                f"{len(rows)} rows; expected at most 1"
            )
        if rows and rows[0]["lpar_name"] != lpar_name:
            raise HMCCLIError(
                f"lsmemopt reported LPAR {rows[0]['lpar_name']!r}; "
                f"expected {lpar_name!r}"
            )
    return rows


async def get_lpar_memopt_score(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
) -> dict[str, Any]:
    """Return one LPAR's current memory-optimization score via SSH.

    Raises:
        ValueError: If *lpar_name* is empty.
        HMCCLIError: If the HMC reports no score row for the partition.
    """
    if not lpar_name.strip():
        raise ValueError("lpar_name must not be empty")
    rows = await list_lpar_memopt_scores(config, system_name, lpar_name)
    if not rows:
        raise HMCCLIError(
            f"lsmemopt query for LPAR {lpar_name!r} on system {system_name!r} "
            "returned 0 rows; expected exactly 1"
        )
    return rows[0]


def _memopt_selector_options(
    prioritized: MemoptLparSelector | None,
    excluded: MemoptLparSelector | None,
) -> str:
    """Validate a planning scenario and render its fixed selector options."""
    if prioritized is not None and excluded is not None:
        prioritized_uses_names = bool(prioritized.names)
        excluded_uses_names = bool(excluded.names)
        if prioritized_uses_names != excluded_uses_names:
            raise ValueError(
                "prioritized and excluded selectors must use the same representation"
            )
        prioritized_values = prioritized.names or prioritized.ids
        excluded_values = excluded.names or excluded.ids
        if set(prioritized_values) & set(excluded_values):
            raise ValueError("prioritized and excluded selectors must not overlap")

    options: list[str] = []
    for selector, name_flag, id_flag in (
        (prioritized, "-p", "--id"),
        (excluded, "-x", "--xid"),
    ):
        if selector is None:
            continue
        flag = name_flag if selector.names else id_flag
        values = selector.names or selector.ids
        rendered = shlex.quote(",".join(str(value) for value in values))
        options.append(f" {flag} {rendered}")
    return "".join(options)


def _validated_memopt_rows(
    output: str,
    required: Collection[str],
) -> list[dict[str, object]]:
    """Parse affinity-score rows and require every scope-specific field."""
    rows: list[dict[str, object]] = _parse_lshwres_output(output)
    for index, row in enumerate(rows, start=1):
        missing = sorted(set(required) - row.keys())
        if missing:
            raise HMCCLIError(
                f"lsmemopt row {index} is missing required fields: {', '.join(missing)}"
            )
        empty = sorted(field for field in required if row[field] == "")
        if empty:
            raise HMCCLIError(
                f"lsmemopt row {index} has empty required fields: {', '.join(empty)}"
            )
    return rows


async def get_system_memopt_score(
    config: HMCConfig, system_name: str
) -> dict[str, object]:
    """Return the current system memory-affinity score via SSH."""
    command = f"lsmemopt -m {shlex.quote(system_name)} -r sys -o currscore"
    output = await run_hmc_command(config, command)
    rows = _parse_lshwres_output(output)
    if len(rows) != 1:
        raise HMCCLIError(
            f"lsmemopt system query returned {len(rows)} rows; expected exactly 1"
        )
    return _validated_memopt_rows(output, {"curr_sys_score"})[0]


async def plan_lpar_memopt_scores(
    config: HMCConfig,
    system_name: str,
    prioritized: MemoptLparSelector | None = None,
    excluded: MemoptLparSelector | None = None,
) -> list[dict[str, object]]:
    """Return predicted LPAR memory-affinity scores via SSH."""
    command = f"lsmemopt -m {shlex.quote(system_name)} -r lpar -o calcscore"
    command += _memopt_selector_options(prioritized, excluded)
    rows = _validated_memopt_rows(
        await run_hmc_command(config, command),
        {"lpar_name", "lpar_id", "curr_lpar_score", "predicted_lpar_score"},
    )
    for row in rows:
        row["prediction_guaranteed"] = False
    return rows


async def plan_system_memopt_score(
    config: HMCConfig,
    system_name: str,
    prioritized: MemoptLparSelector | None = None,
    excluded: MemoptLparSelector | None = None,
) -> dict[str, object]:
    """Return a predicted system memory-affinity score via SSH."""
    command = f"lsmemopt -m {shlex.quote(system_name)} -r sys -o calcscore"
    command += _memopt_selector_options(prioritized, excluded)
    output = await run_hmc_command(config, command)
    rows = _parse_lshwres_output(output)
    if len(rows) != 1:
        raise HMCCLIError(
            f"lsmemopt system query returned {len(rows)} rows; expected exactly 1"
        )
    result = _validated_memopt_rows(output, {"curr_sys_score", "predicted_sys_score"})[
        0
    ]
    result["prediction_guaranteed"] = False
    return result


async def list_memory_pools(
    config: HMCConfig,
    system_name: str,
) -> list[dict[str, Any]]:
    """List shared memory pools on *system_name* via SSH.

    Runs ``lshwres -r mempool -m <system_name>`` and returns one dict per
    pool parsed from the key=value rows, with fields such as ``pool_name``,
    ``size``, ``lpar_names``, and ``curr_lpar_names`` (comma-separated).
    """
    output = await run_hmc_command(
        config, f"lshwres -r mempool -m {shlex.quote(system_name)}"
    )
    return _parse_lshwres_output(output)


async def remove_memory_pool(
    config: HMCConfig,
    system_name: str,
    pool_name: str,
) -> str:
    """Remove a shared memory pool from *system_name* via SSH.

    Before issuing the remove command, fetches the current pool list and
    checks that *pool_name* exists and that no LPARs are still assigned to
    it.  If a pool with that name is missing, or any LPARs are still
    assigned, the command is **not** executed and an ``HMCCLIError``
    describing the problem is raised instead.

    Runs ``chhwres -r mempool -m <system_name> -o r -a <pool_name>`` on
    the HMC via SSH when the pool exists and no LPARs are assigned.

    Returns the HMC CLI output (immediate delete — no job to poll).

    Raises:
        HMCCLIError: If *pool_name* has LPARs still assigned to it, or if
            no pool with that name exists on *system_name*.
    """
    # The `-a` value here is a bare pool name, not an attribute record
    # (ADR 0061); validate it against the same delimiter table before the
    # round trip so a bad name fails locally.
    _validated_value("pool_name", pool_name, surface="chhwres -a value")

    # Safety check: list pools and look for LPAR assignments.
    pools = await list_memory_pools(config, system_name)

    found = False
    for pool in pools:
        if pool.get("pool_name") == pool_name:
            found = True
            # curr_lpar_names may be a comma-separated string or empty.
            assigned = pool.get("curr_lpar_names", "").strip()
            if assigned:
                lpar_list = [lp.strip() for lp in assigned.split(",") if lp.strip()]
                raise HMCCLIError(
                    f"Cannot remove memory pool '{pool_name}' on "
                    f"'{system_name}' — the following LPARs are still "
                    f"assigned to it: {', '.join(lpar_list)}. Reassign or "
                    "remove them from the pool before retrying."
                )
            break
    if not found:
        raise HMCCLIError(
            f"Cannot remove memory pool '{pool_name}' on '{system_name}' — "
            f"no pool with that name exists in the current pool list. "
            f"Use hmc_list_memory_pools to see the available pools."
        )

    cmd = f"chhwres -r mempool -m {shlex.quote(system_name)} -o r -a {shlex.quote(pool_name)}"
    return await run_hmc_command(config, cmd)


# ---------------------------------------------------------------------- #
# LPAR description and MSP (lssyscfg / chsyscfg — no REST equivalent)
# ---------------------------------------------------------------------- #


async def get_lpar_description(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
) -> str:
    """Get the description field of *lpar_name* on *system_name* via SSH.

    Runs ``lssyscfg -r lpar -m <system_name> --filter lpar_names=<lpar_name>
    -F description`` and returns the raw output (the description string, or an
    empty line if none is set). It is the same text shown in the HMC GUI
    Partitions tab.

    The description *is* exposed via the HMC REST API — an earlier revision of
    this docstring claimed otherwise. The #374 live-REST survey found it
    inlined in the bulk list feed ``GET
    /rest/api/uom/ManagedSystem/<uuid>/LogicalPartition`` (and in
    per-partition detail), byte-for-byte identical to this CLI output, present
    since REST schema version V1_2_0, with an empty description signaled by
    element absence rather than an empty element. Bulk ownership reads use
    that feed (``hmc_mcp.operations_lpar.list_lpar_ownership``); this SSH read
    stays for the CLI-name-keyed write flows that share this module's
    transport.
    """
    cmd = (
        f"lssyscfg -r lpar -m {shlex.quote(system_name)} "
        f"--filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))} -F description"
    )
    return await run_hmc_command(config, cmd)


async def set_lpar_description(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    description: str,
) -> str:
    """Set the description field of *lpar_name* via SSH.

    Runs ``chsyscfg -r lpar -m <system_name>
    -i "name=<lpar_name>,description=<description>"`` and returns the raw
    command output.

    Raises ``ValueError`` if *description* is not printable ASCII or carries a
    character the record treats as structure; see
    :func:`validate_lpar_description` for the constraint and error code.

    Raises :class:`HMCCLIError` if *lpar_name* contains a character that would
    corrupt the ``chsyscfg -i`` attribute record; see
    :func:`build_attribute_record`, which enforces the record grammar for both
    fields so the guard cannot be present at one and absent at its neighbour.
    A space or a semicolon in *lpar_name* is refused too — a restriction this
    function has always carried and that ADR 0045 deliberately kept here rather
    than extending to the other records, where it would refuse HMC-legal names.
    """
    validate_lpar_description(description)
    for character, (name, reason) in _DESCRIPTION_TARGET_UNSAFE.items():
        if character in lpar_name:
            raise HMCCLIError(
                f"LPAR name {lpar_name!r} contains {name} ({character!r}); "
                f"cannot safely write description via chsyscfg -i ({reason})"
            )
    record = build_attribute_record([("name", lpar_name), ("description", description)])
    cmd = f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i {shlex.quote(record)}"
    return await run_hmc_command(config, cmd)


async def get_lpar_msp(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
) -> bool:
    """Get the MSP (Migratable Service Partition) flag of *lpar_name* via SSH.

    Runs ``lssyscfg -r lpar -m <system_name> --filter lpar_names=<lpar_name>
    -F msp`` and returns ``True`` when the flag is ``1``, ``False`` when ``0``.
    """
    cmd = (
        f"lssyscfg -r lpar -m {shlex.quote(system_name)} "
        f"--filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))} -F msp"
    )
    raw = await run_hmc_command(config, cmd)
    value = raw.strip()
    if value == "1":
        return True
    if value == "0":
        return False
    raise HMCCLIError(
        f"Unexpected MSP value {value!r} for LPAR {lpar_name!r} "
        f"on system {system_name!r}; expected '0' or '1'"
    )


async def set_lpar_msp(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    enabled: bool,
) -> str:
    """Set the MSP (Migratable Service Partition) flag of *lpar_name* via SSH.

    Checks that *lpar_name* is a VIOS partition (``lpar_env=vioserver``) before
    issuing the command.  The HMC rejects ``msp=...`` for AIX/Linux partitions
    with a confusing generic error; this guard surfaces a clear diagnostic
    before the SSH round-trip.

    Note: the ``lpar_env`` probe and the ``chsyscfg`` write are two separate
    SSH connections (each ``run_hmc_command`` call opens its own connection).
    The guard is not atomic with the write; the HMC itself enforces the
    VIOS-only invariant and returns an error if the race were to occur.

    Runs ``chsyscfg -r lpar -m <system_name> -i "name=<lpar_name>,msp=<0|1>"``
    and returns the raw command output.

    Raises:
        HMCCLIError: If the partition is not found on the system, or if its
            ``lpar_env`` is not ``vioserver``.
    """
    env_cmd = (
        f"lssyscfg -r lpar -m {shlex.quote(system_name)} "
        f"--filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))} -F lpar_env"
    )
    lpar_env = (await run_hmc_command(config, env_cmd)).strip()
    if not lpar_env:
        raise HMCCLIError(
            f"Cannot set MSP on '{lpar_name}': lssyscfg returned no output — "
            f"partition not found on system '{system_name}'. "
            "Check the partition name with hmc_list_lpars."
        )
    if lpar_env != "vioserver":
        raise HMCCLIError(
            f"Cannot set MSP on '{lpar_name}': the msp attribute is only valid "
            f"for a VIOS partition (lpar_env=vioserver), but '{lpar_name}' has "
            f"lpar_env='{lpar_env}'. Use hmc_list_vios to confirm the partition type."
        )
    value = "1" if enabled else "0"
    record = build_attribute_record([("name", lpar_name), ("msp", value)])
    cmd = f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i {shlex.quote(record)}"
    return await run_hmc_command(config, cmd)


# ---------------------------------------------------------------------- #
# Processor compatibility (lssyscfg / chsyscfg)
# ---------------------------------------------------------------------- #


async def get_proc_compat_modes(
    config: HMCConfig,
    system_name: str,
) -> list[str]:
    """List processor compatibility modes supported by *system_name* via SSH.

    Runs ``lssyscfg -r sys -m <system_name> -F lpar_proc_compat_modes`` and
    returns the comma-separated modes as a list of stripped strings.
    """
    cmd = f"lssyscfg -r sys -m {shlex.quote(system_name)} -F lpar_proc_compat_modes"
    raw = await run_hmc_command(config, cmd)
    if not raw.strip():
        return []
    return [mode.strip() for mode in raw.strip().split(",") if mode.strip()]


async def get_lpar_proc_compat(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
) -> dict[str, str]:
    """Get the current and desired processor compatibility modes of an LPAR.

    Runs ``lssyscfg -r lpar -m <system_name> --filter lpar_names=<lpar_name>
    -F desired_lpar_proc_compat_mode,curr_lpar_proc_compat_mode`` and returns a
    dict with keys ``"desired"`` and ``"curr"``.

    Note: ``pend_lpar_proc_compat_mode`` is not a valid HMC CLI attribute;
    ``desired_lpar_proc_compat_mode`` is the correct field name.
    """
    cmd = (
        f"lssyscfg -r lpar -m {shlex.quote(system_name)} "
        f"--filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))} "
        "-F desired_lpar_proc_compat_mode,curr_lpar_proc_compat_mode"
    )
    raw = await run_hmc_command(config, cmd)
    if not raw.strip():
        return {"desired": "", "curr": ""}
    parts = raw.strip().split(",")
    desired = parts[0].strip() if len(parts) > 0 else ""
    curr = parts[1].strip() if len(parts) > 1 else ""
    return {"desired": desired, "curr": curr}


async def set_lpar_proc_compat(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    mode: str,
) -> str:
    """Set the processor compatibility mode of *lpar_name* via SSH.

    Runs ``chsyscfg -r lpar -m <system_name>
    -i "name=<lpar_name>,lpar_proc_compat_mode=<mode>"`` and returns the raw
    command output.

    Raises:
        HMCCLIError: If *lpar_name* or *mode* contains a character the ``-i``
            record's parser treats as structure.
    """
    record = build_attribute_record(
        [("name", lpar_name), ("lpar_proc_compat_mode", mode)]
    )
    cmd = f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i {shlex.quote(record)}"
    return await run_hmc_command(config, cmd)


# ---------------------------------------------------------------------- #
# SR-IOV adapter mode and vNICs (chhwres)
# ---------------------------------------------------------------------- #

SriovMode = Literal["sriov", "dedicated"]
_VALID_SRIOV_MODES = frozenset(get_args(SriovMode))


def validate_sriov_mode(mode: SriovMode) -> SriovMode:
    """Return *mode* if it is a recognised SR-IOV adapter mode, else raise.

    Shared by :func:`set_sriov_adapter_mode` and the CLI pre-confirmation
    guard so the valid-mode set is defined once.

    Raises:
        ValueError: If *mode* is not one of ``"sriov"`` or ``"dedicated"``.
    """
    if mode not in _VALID_SRIOV_MODES:
        raise ValueError(
            f"Invalid mode {mode!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_SRIOV_MODES))}"
        )
    return mode


# ---------------------------------------------------------------------- #
# LPAR profile backup/restore/sync and I/O slot assignment (bkprofdata /
# rstprofdata / chsyscfg — no REST equivalent)
# ---------------------------------------------------------------------- #


async def backup_lpar_profiles(
    config: HMCConfig,
    system_name: str,
    file_path: str,
    *,
    force: bool = False,
) -> str:
    """Backup all LPAR profiles on *system_name* to *file_path* via SSH.

    Runs ``bkprofdata -m <system_name> -f <file_path>`` and returns the raw
    command output. *file_path* is on the HMC filesystem, not the local
    machine; the backup file is created at that path on the HMC host.

    When *force* is ``True``, ``--force`` is appended to the command so that
    an existing file at *file_path* is overwritten instead of raising an error.
    """
    cmd = f"bkprofdata -m {shlex.quote(system_name)} -f {shlex.quote(file_path)}"
    if force:
        cmd += " --force"  # literal flag — not a user value, no quoting needed
    return await run_hmc_command(config, cmd)


async def restore_lpar_profiles(
    config: HMCConfig,
    system_name: str,
    file_path: str,
) -> str:
    """Restore LPAR profiles from *file_path* on *system_name* via SSH.

    Runs ``rstprofdata -m <system_name> -f <file_path>`` and returns the raw
    command output. *file_path* must already exist on the HMC filesystem.
    Restoring overwrites the current LPAR profile configuration.
    """
    # NOTE: no empty file_path guard here; see backup_lpar_profiles for the
    # guard pattern. A blank path produces an opaque HMC error rather than a
    # clear ValueError — tracked as a follow-on improvement.
    cmd = f"rstprofdata -m {shlex.quote(system_name)} -f {shlex.quote(file_path)}"
    return await run_hmc_command(config, cmd)


async def sync_lpar_profile(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
) -> str:
    """Sync *lpar_name*'s running configuration back to its current profile.

    Runs ``chsyscfg -r lpar -m <system_name>
    -i "name=<lpar_name>,sync_curr_profile=1"`` and returns the raw command
    output. This saves the LPAR's current running configuration to its
    current named profile, overwriting the previous profile definition.

    Raises:
        HMCCLIError: If *lpar_name* contains a character the ``-i`` record's
            parser treats as structure.
    """
    record = build_attribute_record([("name", lpar_name), ("sync_curr_profile", 1)])
    cmd = f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i {shlex.quote(record)}"
    return await run_hmc_command(config, cmd)


async def assign_profile_io_slot(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    profile_name: str,
    drc_index: str,
) -> str:
    """Add a physical I/O slot DRC index to *profile_name* without force.

    Raises:
        HMCCLIError: If *profile_name*, *drc_index*, or *lpar_name* contains a
            character the ``-i`` record's parser treats as structure.  The
            ``//0`` suffix is record-safe, so validating the whole ``io_slots``
            value covers *drc_index*.
    """
    return await _change_profile_io_slot(
        config, system_name, lpar_name, profile_name, drc_index, add=True
    )


async def unassign_profile_io_slot(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    profile_name: str,
    drc_index: str,
) -> str:
    """Remove a physical I/O slot DRC index from a profile without force."""
    return await _change_profile_io_slot(
        config, system_name, lpar_name, profile_name, drc_index, add=False
    )


async def _change_profile_io_slot(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    profile_name: str,
    drc_index: str,
    *,
    add: bool,
) -> str:
    operator = "io_slots+" if add else "io_slots-"
    record = build_attribute_record(
        [
            ("name", profile_name),
            (operator, f"{drc_index}//0"),
            ("lpar_name", lpar_name),
        ]
    )
    command = f"chsyscfg -r prof -m {shlex.quote(system_name)} -i {shlex.quote(record)}"
    return await run_hmc_command(config, command)


# ---------------------------------------------------------------------- #
# NIM install via the HMC CLI ``installios`` command (ADR 0070)
# ---------------------------------------------------------------------- #

_IPV4_OCTET = r"(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
_IPV4_PATTERN = re.compile(rf"^{_IPV4_OCTET}(\.{_IPV4_OCTET}){{3}}$")
_MAC_ADDRESS_PATTERN = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")
_VLAN_MIN, _VLAN_MAX = 0, 4094
_LOG_SLUG_PATTERN = re.compile(r"[^A-Za-z0-9._-]")
_INSTALLIOS_LOG_TEMPLATE = "/tmp/hmc-mcp-installios-{slug}.log"
INSTALLIOS_PID_PREFIX = "HMC_MCP_INSTALLIOS_PID="


def validate_ipv4_address(value: str) -> str:
    """Return *value* when it is a dotted-quad IPv4 address, else raise.

    Each octet must be 0-255 without a sign or surrounding whitespace; this is
    the grammar every installios network flag accepts (ADR 0070).
    """
    if not isinstance(value, str) or not _IPV4_PATTERN.match(value):
        raise ValueError(f"{value!r} is not a valid IPv4 address")
    return value


def validate_ipv4_subnet_mask(value: str) -> str:
    """Return *value* when it is a contiguous dotted-quad subnet mask.

    A valid mask is one run of set bits followed by one run of clear bits
    (``255.255.0.0``, not ``255.0.255.0``); installios hands the value straight
    to the client's network configuration, where a discontiguous mask can only
    fail late, mid-install.
    """
    validate_ipv4_address(value)
    bits = "".join(f"{int(octet):08b}" for octet in value.split("."))
    if "01" in bits:
        raise ValueError(
            f"{value!r} is not a contiguous subnet mask "
            "(set bits must precede clear bits)"
        )
    return value


def validate_vlan_id(value: str) -> str:
    """Return *value* when it names an installios VLAN tag, else raise.

    The man page admits 0 (untagged) through 4094 for ``-V`` (ADR 0070).
    """
    if (
        not isinstance(value, str)
        or not value.isdigit()
        or not _VLAN_MIN <= int(value) <= _VLAN_MAX
    ):
        raise ValueError(
            f"{value!r} is not a valid VLAN tag identifier "
            f"(expected an integer from {_VLAN_MIN} to {_VLAN_MAX})"
        )
    return value


def validate_mac_address(value: str) -> str:
    """Return *value* when it is a colon-separated MAC address, else raise."""
    if not isinstance(value, str) or not _MAC_ADDRESS_PATTERN.match(value):
        raise ValueError(
            f"{value!r} is not a valid MAC address "
            "(expected six colon-separated hex octets, e.g. f2:d4:60:00:d0:03)"
        )
    return value


def validate_install_source(value: str) -> str:
    """Return *value* when it fits the ``installios -d`` source grammar.

    Three shapes are admitted by the man page: a device path (``/dev/cdrom``,
    or an ``lsmediadev`` USB device name), an absolute path on the HMC to a
    ``backupios`` nim_resources tarball or VIOS ISO, or ``server:/path`` for an
    NFS-served backup. Values are shlex-quoted into the command, so validation
    is defense in depth rather than the injection boundary — what it buys is a
    fast, named rejection for values that could never be a source (control
    characters, flag-like leading dashes) instead of a confusing remote parse.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("install_source must be a non-empty string")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError(
            "install_source contains control characters; only printable text "
            "is accepted"
        )
    if value.startswith("-"):
        raise ValueError(
            f"install_source {value!r} starts with '-'; it would be parsed as "
            "an installios flag, not a source path"
        )
    host, sep, remote_path = value.partition(":")
    if sep and ("/" in host or not host.strip()):
        raise ValueError(
            f"install_source {value!r} looks like an NFS location but its "
            "server part is not a hostname"
        )
    return value


def validate_hmc_name(value: str, field: str) -> str:
    """Return *value* when it can name an HMC object, else raise.

    HMC object names are free-form on the console side, so the only hard rule
    here is printable, non-empty text: anything else cannot be a name, and
    everything legitimate survives ``shlex.quote`` intact.
    """
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError(f"{field} must be non-empty printable text")
    return value


def build_installios_command(
    *,
    install_source: str,
    client_ip: str,
    subnet_mask: str,
    gateway: str,
    system_name: str,
    partition_name: str,
    profile_name: str,
    vlan_id: str = "0",
    mac_address: str | None = None,
) -> tuple[str, str]:
    """Compose the detached ``installios`` invocation for one partition.

    Returns ``(command, log_path)``. The invocation runs under ``nohup`` with
    stdin closed and output redirected to a per-partition log, then echoes the
    backgrounded PID tagged with :data:`INSTALLIOS_PID_PREFIX` — submit-and-
    detach semantics, so the SSH exec returns immediately and the NIM install
    continues after the connection closes (ADR 0070).

    Every interpolated value is validated here as well as at the tool layer:
    this function is the injection boundary, and it does not trust its callers.
    """
    validate_install_source(install_source)
    validate_ipv4_address(client_ip)
    validate_ipv4_subnet_mask(subnet_mask)
    validate_ipv4_address(gateway)
    validate_hmc_name(system_name, "system_name")
    validate_hmc_name(partition_name, "partition_name")
    validate_hmc_name(profile_name, "profile_name")
    validate_vlan_id(vlan_id)

    flags = [
        "-d", shlex.quote(install_source),
        "-i", shlex.quote(client_ip),
        "-S", shlex.quote(subnet_mask),
        "-g", shlex.quote(gateway),
        "-s", shlex.quote(system_name),
        "-p", shlex.quote(partition_name),
        "-r", shlex.quote(profile_name),
        "-V", vlan_id,
    ]
    if mac_address is not None:
        flags += ["-m", shlex.quote(validate_mac_address(mac_address))]

    slug = _LOG_SLUG_PATTERN.sub("_", partition_name)
    log_path = _INSTALLIOS_LOG_TEMPLATE.format(slug=slug)
    installios = "installios " + " ".join(flags)
    command = (
        f"nohup {installios} </dev/null >{shlex.quote(log_path)} 2>&1 "
        f"& echo {INSTALLIOS_PID_PREFIX}$!"
    )
    return command, log_path


def parse_installios_pid(raw_output: str) -> int:
    """Extract the PID echoed by :func:`build_installios_command`'s command.

    Raises:
        HMCCLIError: If the submission output carries no PID tag, which means
            the shell never got far enough to background the command.
    """
    prefix_len = len(INSTALLIOS_PID_PREFIX)
    for line in raw_output.splitlines():
        candidate = line.strip()
        if candidate.startswith(INSTALLIOS_PID_PREFIX):
            digits = candidate[prefix_len:].strip()
            if digits.isdigit():
                return int(digits)
    raise HMCCLIError(
        "installios submission produced no PID; the HMC shell did not report "
        f"a backgrounded process. Output: {raw_output.strip()[:200]!r}"
    )


async def run_installios(config: HMCConfig, command: str) -> int:
    """Submit a composed ``installios`` command over SSH, return its PID.

    Only the submission is bounded by ``config.ssh_timeout``; the install
    itself runs detached on the HMC and outlives the SSH session (ADR 0070).
    Failures surface as :class:`HMCCLIError` per the transport's conventions.
    """
    return parse_installios_pid(await run_hmc_command(config, command))
