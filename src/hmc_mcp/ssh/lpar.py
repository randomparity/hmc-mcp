"""LPAR creation, ownership, validation, and SSH name resolution."""

from __future__ import annotations

import shlex

from ..config import HMCConfig
from ..documents import LparResources
from .transport import HMCCLIError, run_hmc_command
from .commands import build_attribute_record
from .description_validation import validate_lpar_description
from .profiles import set_lpar_description


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
        raise ValueError(f"caller_token must be a string, got {type(token).__name__}")
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
        raise ValueError("caller_token contains whitespace; it must be a single word")
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
