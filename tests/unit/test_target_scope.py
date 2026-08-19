"""Extraction and per-grant matching for dispatch-time target scope (#223).

These tests exercise the target dimension in isolation: one grant's ``targets``
value at a time, with no policy and no loop. The conjunction across dimensions,
and the rule that a grant is never split across grants, live in
``tests/app/test_target_authorization.py``. See
docs/adr/0039-dispatch-time-target-scope.md.
"""

from __future__ import annotations

from types import MappingProxyType

import pytest

from hmc_mcp.access_policy import ALL_TARGETS
from hmc_mcp.target_scope import (
    ABSENT,
    UNREADABLE,
    TargetScopeError,
    selected_targets,
    target_denial,
    targets_permitted,
)
from hmc_mcp.tool_registry import TargetSelector, ToolSecurity


def _security(*selectors, exhaustive=True):
    """A record declaring *selectors*, as ``tool()`` would have built it."""
    targets = tuple(
        TargetSelector(kind, argument, required) for kind, argument, required in selectors
    )
    return ToolSecurity(
        effect="destructive",
        operation="lpar.delete",
        target_kind="lpar",
        targets=targets,
        exhaustive_targets=exhaustive and bool(targets),
    )


DELETE_LPAR = _security(
    ("managed_system", "system_name_or_uuid", True),
    ("lpar", "lpar_name_or_uuid", True),
)
POWER_OFF_LPAR = _security(
    ("lpar", "lpar_name_or_uuid", True),
    ("managed_system", "system_name_or_uuid", False),
)
ADD_VFC = _security(
    ("lpar", "lpar_name_or_uuid", True),
    ("vios", "vios_partition_id", True),
)
LDAP_REMOVE = _security()
PROVISION = _security(("managed_system", "system_name_or_uuid", True), exhaustive=False)


def _table(**kinds):
    return MappingProxyType({kind: frozenset(values) for kind, values in kinds.items()})


# ---------------------------------------------------------------------------
# R4 — extraction is total, and reads only the declared selectors
# ---------------------------------------------------------------------------


def test_strings_pass_through_in_declaration_order():
    extracted = selected_targets(
        DELETE_LPAR, {"system_name_or_uuid": "sys-a", "lpar_name_or_uuid": "db-01"}
    )
    assert extracted == (
        ("managed_system", "system_name_or_uuid", "sys-a"),
        ("lpar", "lpar_name_or_uuid", "db-01"),
    )


def test_an_int_selector_renders_rather_than_denying():
    """`vios_partition_id` is the surface's one non-string selector.

    The arm is load-bearing, not convenient: UNREADABLE denies even under
    `all-targets`, so without it #225's legacy-equivalent policy would stop
    covering three live `mutate` tools. It does not make the *comparison*
    unambiguous — a `vios` allowlist holds partition IDs and VIOS names in one
    set — which is why ADR 0039 refuses `vios_partition_id` as a bounding
    identity outright rather than trusting the rendering.
    """
    extracted = selected_targets(
        ADD_VFC, {"lpar_name_or_uuid": "db-01", "vios_partition_id": 3}
    )
    assert extracted[1] == ("vios", "vios_partition_id", "3")


def test_a_bool_is_unreadable_rather_than_an_int():
    """`bool` is an `int` subclass, so the arms must be ordered.

    Without the explicit refusal, `True` would render `"True"` into a comparison
    against resource names, and a policy allowlisting a partition literally
    named `True` would match a boolean.
    """
    extracted = selected_targets(
        ADD_VFC, {"lpar_name_or_uuid": "db-01", "vios_partition_id": True}
    )
    assert extracted[1] == ("vios", "vios_partition_id", UNREADABLE)


def test_an_omitted_optional_selector_is_absent_not_unreadable():
    extracted = selected_targets(
        POWER_OFF_LPAR, {"lpar_name_or_uuid": "db-01", "system_name_or_uuid": None}
    )
    assert extracted[1] == ("managed_system", "system_name_or_uuid", ABSENT)


@pytest.mark.parametrize("value", [1.5, ["db-01"], {"lpar": "db-01"}, object(), b"db-01"])
def test_every_other_type_is_unreadable_uninspected(value):
    extracted = selected_targets(
        POWER_OFF_LPAR,
        {"lpar_name_or_uuid": value, "system_name_or_uuid": "sys-a"},
    )
    assert extracted[0][2] is UNREADABLE


def test_the_empty_string_is_a_string_not_an_omission():
    """`""` denies under any table by construction, and is deliberately not ABSENT.

    `access_policy._check_entries` already rejects an empty allowlist entry, so no
    table can contain it. Note the asymmetry with `selected_connection`, where
    `""` *is* the default connection because `load_profile` treats it that way.
    """
    extracted = selected_targets(
        POWER_OFF_LPAR, {"lpar_name_or_uuid": "", "system_name_or_uuid": "sys-a"}
    )
    assert extracted[0] == ("lpar", "lpar_name_or_uuid", "")
    table = _table(lpar=["db-01"], managed_system=["sys-a"])
    assert targets_permitted(table, POWER_OFF_LPAR, extracted) is False
    assert targets_permitted(ALL_TARGETS, POWER_OFF_LPAR, extracted) is True


def test_a_selector_less_tool_extracts_nothing():
    assert selected_targets(LDAP_REMOVE, {"resource": "ldap", "profile": None}) == ()


def test_extraction_reads_no_argument_it_was_not_told_about():
    """Only declared selectors. `name` on a create tool is deliberately not one."""
    extracted = selected_targets(
        DELETE_LPAR,
        {
            "system_name_or_uuid": "sys-a",
            "lpar_name_or_uuid": "db-01",
            "name": "new-lpar",
            "profile": "lab",
            "dry_run": True,
        },
    )
    assert [entry[1] for entry in extracted] == [
        "system_name_or_uuid",
        "lpar_name_or_uuid",
    ]


def test_a_missing_bound_argument_is_a_malformed_call():
    """Indexed, not `.get`: an absent key must not read as an omitted argument."""
    with pytest.raises(KeyError):
        selected_targets(DELETE_LPAR, {"system_name_or_uuid": "sys-a"})


# ---------------------------------------------------------------------------
# R5 — a targets table covers a call only when everything matches
# ---------------------------------------------------------------------------


def test_a_table_naming_every_selector_permits():
    extracted = selected_targets(
        DELETE_LPAR, {"system_name_or_uuid": "sys-a", "lpar_name_or_uuid": "db-01"}
    )
    table = _table(managed_system=["sys-a"], lpar=["db-01"])
    assert targets_permitted(table, DELETE_LPAR, extracted) is True


def test_one_matching_selector_is_not_enough():
    """Every declared selector, not the first one that happens to match."""
    extracted = selected_targets(
        DELETE_LPAR, {"system_name_or_uuid": "sys-b", "lpar_name_or_uuid": "db-01"}
    )
    table = _table(managed_system=["sys-a"], lpar=["db-01"])
    assert targets_permitted(table, DELETE_LPAR, extracted) is False


def test_a_table_omitting_a_declared_kind_denies():
    extracted = selected_targets(
        DELETE_LPAR, {"system_name_or_uuid": "sys-a", "lpar_name_or_uuid": "db-01"}
    )
    assert targets_permitted(_table(lpar=["db-01"]), DELETE_LPAR, extracted) is False


@pytest.mark.parametrize(
    "value",
    [
        "db-01 ",
        " db-01",
        "DB-01",
        "db-1",
        "db-01\n",
        "5c4bf7a8-1e2b-4c3d-9e8f-0a1b2c3d4e5f",
    ],
)
def test_matching_is_exact_with_no_normalization(value):
    """No strip, no case fold, and no name-to-UUID resolution.

    The UUID case is the one an operator will meet: a policy written in names
    does not cover a call written in UUIDs. ADR 0039 keeps it that way because
    resolving would put an outbound HMC call inside the decision whose whole
    purpose is to precede outbound calls.
    """
    extracted = selected_targets(
        DELETE_LPAR, {"system_name_or_uuid": "sys-a", "lpar_name_or_uuid": value}
    )
    table = _table(managed_system=["sys-a"], lpar=["db-01"])
    assert targets_permitted(table, DELETE_LPAR, extracted) is False


def test_an_omitted_optional_selector_denies_under_a_table():
    """The carry-forward: an unpinned system means 'whichever system has one'.

    `hmc_power_off_lpar` is the live instance — `destructive`, with the only
    optional `managed_system` selector on the surface.
    """
    extracted = selected_targets(
        POWER_OFF_LPAR, {"lpar_name_or_uuid": "db-01", "system_name_or_uuid": None}
    )
    table = _table(lpar=["db-01"], managed_system=["sys-a"])
    assert targets_permitted(table, POWER_OFF_LPAR, extracted) is False


def test_supplying_the_optional_selector_permits():
    extracted = selected_targets(
        POWER_OFF_LPAR, {"lpar_name_or_uuid": "db-01", "system_name_or_uuid": "sys-a"}
    )
    table = _table(lpar=["db-01"], managed_system=["sys-a"])
    assert targets_permitted(table, POWER_OFF_LPAR, extracted) is True


def test_a_table_never_covers_a_selector_less_tool():
    """The fail-open ADR 0039 names and refuses.

    Reading (i) would let `effects = ["destructive"], targets = {lpar = [...]}`
    delete the console's LDAP configuration.
    """
    extracted = selected_targets(LDAP_REMOVE, {"resource": "ldap"})
    assert extracted == ()
    assert targets_permitted(_table(lpar=["scratch-01"]), LDAP_REMOVE, extracted) is False


def test_a_table_never_covers_a_composite_whose_selectors_do_not_bound_it():
    extracted = selected_targets(PROVISION, {"system_name_or_uuid": "sys-a"})
    table = _table(managed_system=["sys-a"])
    assert targets_permitted(table, PROVISION, extracted) is False


def test_an_unreadable_selector_denies_under_a_table():
    extracted = selected_targets(
        ADD_VFC, {"lpar_name_or_uuid": "db-01", "vios_partition_id": 1.5}
    )
    table = _table(lpar=["db-01"], vios=["3"])
    assert targets_permitted(table, ADD_VFC, extracted) is False


# ---------------------------------------------------------------------------
# R6 — all-targets widens everything except unreadability
# ---------------------------------------------------------------------------


def test_all_targets_permits_any_value():
    extracted = selected_targets(
        DELETE_LPAR, {"system_name_or_uuid": "anything", "lpar_name_or_uuid": "at-all"}
    )
    assert targets_permitted(ALL_TARGETS, DELETE_LPAR, extracted) is True


def test_all_targets_permits_an_omitted_optional_selector():
    """It must: #225's legacy-equivalent policy is all-targets throughout, and
    every call omitting an optional argument is part of the exposure it copies.
    """
    extracted = selected_targets(
        POWER_OFF_LPAR, {"lpar_name_or_uuid": "db-01", "system_name_or_uuid": None}
    )
    assert targets_permitted(ALL_TARGETS, POWER_OFF_LPAR, extracted) is True


def test_all_targets_permits_a_selector_less_tool():
    extracted = selected_targets(LDAP_REMOVE, {"resource": "ldap"})
    assert targets_permitted(ALL_TARGETS, LDAP_REMOVE, extracted) is True


def test_all_targets_permits_a_non_exhaustive_composite():
    extracted = selected_targets(PROVISION, {"system_name_or_uuid": "sys-a"})
    assert targets_permitted(ALL_TARGETS, PROVISION, extracted) is True


def test_all_targets_still_denies_an_unreadable_selector():
    """The sentinel says 'any target of the kinds you declare', not 'any type'."""
    extracted = selected_targets(
        ADD_VFC, {"lpar_name_or_uuid": "db-01", "vios_partition_id": object()}
    )
    assert targets_permitted(ALL_TARGETS, ADD_VFC, extracted) is False


# ---------------------------------------------------------------------------
# R16 — the denial names the blocked constraint and nothing else
# ---------------------------------------------------------------------------


def _message(security, arguments) -> str:
    extracted = selected_targets(security, arguments)
    error = target_denial("hmc_delete_lpar", "lab-only", security, extracted)
    assert isinstance(error, TargetScopeError)
    return str(error)


def test_a_denied_value_is_echoed_back_to_the_caller():
    """The caller already holds its own value, so echoing it discloses nothing.

    ADR 0038 applied the same test to the connection token, and refused to
    render the *normalized* value for exactly the reason this one is safe.
    """
    message = _message(
        DELETE_LPAR, {"system_name_or_uuid": "sys-b", "lpar_name_or_uuid": "db-01"}
    )
    assert "hmc_delete_lpar" in message
    assert "'lab-only'" in message
    assert "managed_system='sys-b'" in message
    assert "lpar='db-01'" in message


def test_a_denial_never_names_the_allowlist():
    message = _message(
        DELETE_LPAR, {"system_name_or_uuid": "sys-b", "lpar_name_or_uuid": "db-01"}
    )
    assert "sys-a" not in message
    assert "scratch-01" not in message


def test_an_omitted_selector_is_told_which_argument_to_supply():
    message = _message(
        POWER_OFF_LPAR, {"lpar_name_or_uuid": "db-01", "system_name_or_uuid": None}
    )
    assert "system_name_or_uuid" in message
    assert "all-targets" in message


def test_an_unboundable_tool_is_told_to_use_the_sentinel():
    message = _message(PROVISION, {"system_name_or_uuid": "sys-a"})
    assert "all-targets" in message
    assert "sys-a" not in message


def test_an_unreadable_selector_does_not_echo_the_value():
    """An arbitrary object's repr is not the caller's own token in any useful
    sense, and could carry anything. The argument name is enough to act on.
    """

    class Sneaky:
        def __repr__(self) -> str:
            return "password=hunter2"

    message = _message(
        POWER_OFF_LPAR,
        {"lpar_name_or_uuid": Sneaky(), "system_name_or_uuid": "sys-a"},
    )
    assert "hunter2" not in message
    assert "lpar_name_or_uuid" in message


def test_a_control_character_in_a_value_cannot_forge_a_line():
    message = _message(
        DELETE_LPAR,
        {"system_name_or_uuid": "sys-a\nGRANTED: everything", "lpar_name_or_uuid": "x"},
    )
    assert "\n" not in message


def test_the_unreadable_arm_wins_over_the_others():
    """Ordering: a malformed call is reported as malformed, not as unmatched."""
    message = _message(
        POWER_OFF_LPAR,
        {"lpar_name_or_uuid": object(), "system_name_or_uuid": None},
    )
    assert "readable" in message


def test_the_sentinels_name_themselves():
    """They appear in assertion output and debugger frames, never in a message.

    `_DENIED` is the only template that renders a value, and both sentinels are
    handled by earlier arms, so neither can reach it — which is why this is a
    legibility contract rather than a disclosure one.
    """
    assert repr(ABSENT) == "ABSENT"
    assert repr(UNREADABLE) == "UNREADABLE"
    assert ABSENT is not UNREADABLE
