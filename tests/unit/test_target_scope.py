"""Extraction and per-grant matching for dispatch-time target scope (#223).

These tests exercise the target dimension in isolation: one grant's ``targets``
value at a time, with no policy and no loop. The conjunction across dimensions,
and the rule that a grant is never split across grants, live in
``tests/app/test_target_authorization.py``. See
docs/adr/0039-dispatch-time-target-scope.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

import pytest

from hmc_mcp.audit.records import REASONS
from hmc_mcp.authorization.access_policy import ALL_TARGETS
from hmc_mcp.authorization.target_scope import (
    ABSENT,
    UNREADABLE,
    TargetScopeError,
    _value,
    audit_state,
    denial_reason,
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


def test_the_unboundable_message_does_not_claim_the_tool_has_selectors():
    """The template covers a selector-less tool as well as a composite.

    An earlier wording said "its declared target selectors do not name every
    resource it acts on", which is vacuous for the 19 tools that declare none.
    Reverting to it left the suite green, because every test matched only on
    "all-targets" — so the correctness the rewording exists for had no guard.
    """
    message = _message(LDAP_REMOVE, {"resource": "ldap"})

    assert "all-targets" in message
    assert "declared target selectors" not in message
    assert "no targets table can bound" in message


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


# ---------------------------------------------------------------------------
# Properties a surviving mutant showed were claimed but unproven
# ---------------------------------------------------------------------------


# A *read* tool with two selectors. Kept as its own fixture even though
# `hmc_get_lpar` now declares this exact shape (#259): the fixture pins the
# extraction rule, not any one tool's declaration.
GET_LPAR = ToolSecurity(
    effect="read",
    operation="lpar.list",
    target_kind="lpar",
    targets=(
        TargetSelector("lpar", "lpar_name_or_uuid", True),
        TargetSelector("managed_system", "system_name_or_uuid", False),
    ),
    exhaustive_targets=True,
)


def test_a_read_tool_is_bound_exactly_as_a_destructive_one_is():
    """ADR 0039 widens the dimension past the issue's stated outcome, on purpose.

    Every other fixture in this module is `destructive`, so an effect filter
    inside the authorizer — `extracted = () if effect == "read" else ...` —
    survived the whole suite. It should not: a read against a withheld target is
    a disclosure, and `Grant.targets` is a property of the grant rather than of
    an effect class.
    """
    extracted = selected_targets(
        GET_LPAR, {"lpar_name_or_uuid": "secret-db", "system_name_or_uuid": "sys-a"}
    )
    table = _table(lpar=["db-01"], managed_system=["sys-a"])
    assert targets_permitted(table, GET_LPAR, extracted) is False

    permitted = selected_targets(
        GET_LPAR, {"lpar_name_or_uuid": "db-01", "system_name_or_uuid": "sys-a"}
    )
    assert targets_permitted(table, GET_LPAR, permitted) is True


def test_a_read_tools_omitted_optional_selector_denies_too():
    """"Every partition on every system" is not what a narrow table granted."""
    extracted = selected_targets(
        GET_LPAR, {"lpar_name_or_uuid": "db-01", "system_name_or_uuid": None}
    )
    assert targets_permitted(_table(lpar=["db-01"]), GET_LPAR, extracted) is False


def test_a_value_allowed_for_one_kind_is_not_allowed_for_another():
    """Matching is keyed by kind, and the key is load-bearing.

    Every other table fixture pairs disjoint values with their own kinds, so a
    mutant flattening the table into one set — `frozenset().union(*values())` —
    survived. Under it this call would be permitted: the system named `db-01`
    and the partition named `sys-a` are each *somewhere* in the table, just not
    where the operator put them. That is the selector confusion the spec's
    threat model lists first and epic #218 names as a canonicalization hazard.
    """
    swapped = selected_targets(
        DELETE_LPAR, {"system_name_or_uuid": "db-01", "lpar_name_or_uuid": "sys-a"}
    )
    table = _table(managed_system=["sys-a"], lpar=["db-01"])
    assert targets_permitted(table, DELETE_LPAR, swapped) is False

    # The same two values, each under its own kind, are permitted — so the test
    # above fails for the swap and not because the values are unknown.
    correct = selected_targets(
        DELETE_LPAR, {"system_name_or_uuid": "sys-a", "lpar_name_or_uuid": "db-01"}
    )
    assert targets_permitted(table, DELETE_LPAR, correct) is True


# Spec test -> node id (docs/workflow/specs/2026-08-19-authorization-audit-events-design.md)
#   6a  test_audit_state_maps_each_arm_of_value
#   21  test_each_denial_template_has_exactly_one_reason_code
#   21a test_denial_reason_names_the_condition_that_actually_held

_LPAR = ("lpar", "lpar_name_or_uuid", True)


def test_audit_state_maps_each_arm_of_value():
    """Spec 6a. The seam between `_value`'s result and the record's `state`.

    `_value`'s own arms are covered above; what this pins is the *mapping*, which
    would otherwise be an inline conditional in `dispatch_scope`, one module away
    from the singletons it interprets.
    """
    assert audit_state(_value("db-01")) == "present"
    assert audit_state(_value(3)) == "present"
    assert audit_state(_value(None)) == "absent"
    assert audit_state(_value(True)) == "unreadable"
    assert audit_state(_value(object())) == "unreadable"


def test_each_denial_template_has_exactly_one_reason_code():
    """Spec 21. A code-to-template table, not a re-derivation.

    Asserting that the record's `reason` "agrees with" the raised error would be
    the same function call twice once `target_denial` reads `denial_reason`, so it
    could not fail for any input. This pins the mapping instead.
    """
    cases = {
        "target-selector-unreadable": (
            _security(_LPAR),
            (("lpar", "lpar_name_or_uuid", UNREADABLE),),
            "does not carry a readable",
        ),
        "target-unboundable": (
            _security(_LPAR, exhaustive=False),
            (("lpar", "lpar_name_or_uuid", "db-01"),),
            "no targets table can bound",
        ),
        "target-selector-absent": (
            _security(_LPAR),
            (("lpar", "lpar_name_or_uuid", ABSENT),),
            "which this call did not supply",
        ),
        "target-not-granted": (
            _security(_LPAR),
            (("lpar", "lpar_name_or_uuid", "db-01"),),
            "No grant naming",
        ),
    }
    for code, (security, extracted, fragment) in cases.items():
        assert denial_reason(security, extracted) == code
        assert fragment in str(target_denial("t", "p", security, extracted))
    assert set(cases) | {
        "permitted",
        "configuration-unreadable",
        "connection-not-granted",
    } == REASONS


def test_denial_reason_names_the_condition_that_actually_held():
    """Spec 21a. The two functions check their arms in different orders.

    `targets_permitted` tests UNREADABLE, then `AllTargets`, then
    `exhaustive_targets`, then per-selector; `denial_reason` tests UNREADABLE,
    then `exhaustive_targets`, then ABSENT, then all. Only this pins them together
    for the inputs that actually reach a denial.
    """
    table = MappingProxyType({"lpar": frozenset({"granted-01"})})
    for extracted, security, expected in (
        (
            (("lpar", "lpar_name_or_uuid", UNREADABLE),),
            _security(_LPAR),
            "target-selector-unreadable",
        ),
        (
            (("lpar", "lpar_name_or_uuid", "db-01"),),
            _security(_LPAR, exhaustive=False),
            "target-unboundable",
        ),
        (
            (("lpar", "lpar_name_or_uuid", ABSENT),),
            _security(_LPAR),
            "target-selector-absent",
        ),
        (
            (("lpar", "lpar_name_or_uuid", "db-01"),),
            _security(_LPAR),
            "target-not-granted",
        ),
    ):
        assert targets_permitted(table, security, extracted) is False
        assert denial_reason(security, extracted) == expected


# ---------------------------------------------------------------------------
# #260 — the second extraction rule: a selector read from a caller-supplied
# structured argument, one level below the bound arguments
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ProvisionStorage:
    """As `operations_provision.ProvisionStorage`, trimmed to the identity."""

    vios_uuid: str | None = None


@dataclass(frozen=True)
class _ProvisionAdapters:
    """As `operations_provision.ProvisionAdapters`: an int slot number."""

    vios_partition_id: int = 3


def _nested(*selectors, exhaustive=False):
    """A record whose selectors arrive through a structured argument."""
    targets = tuple(
        TargetSelector(kind, argument, required, container=container)
        for kind, argument, required, container in selectors
    )
    return ToolSecurity(
        effect="mutate",
        operation="provision.lpar",
        target_kind="managed_system",
        targets=targets,
        exhaustive_targets=exhaustive and bool(targets),
    )


PROVISION_NESTED = _nested(
    ("managed_system", "system_name_or_uuid", True, None),
    ("vios", "vios_uuid", True, "storage"),
    ("vios", "vios_partition_id", False, "network"),
)
_SYS = ("managed_system", "system_name_or_uuid", "sys-a")


def test_a_nested_selector_reads_the_caller_supplied_object():
    extracted = selected_targets(
        PROVISION_NESTED,
        {
            "system_name_or_uuid": "sys-a",
            "storage": _ProvisionStorage(vios_uuid="vios-uuid-1"),
            "network": _ProvisionAdapters(vios_partition_id=5),
        },
    )
    assert extracted == (
        _SYS,
        ("vios", "storage.vios_uuid", "vios-uuid-1"),
        ("vios", "network.vios_partition_id", "5"),
    )


def test_a_none_sub_object_is_unreadable_not_absent():
    """Fail closed: a None where the schema requires an object is malformed."""
    extracted = selected_targets(
        PROVISION_NESTED,
        {"system_name_or_uuid": "sys-a", "storage": None, "network": None},
    )
    assert extracted[1] == ("vios", "storage.vios_uuid", UNREADABLE)
    assert extracted[2] == ("vios", "network.vios_partition_id", UNREADABLE)


def test_a_missing_attribute_is_unreadable():
    """An object without the declared field is malformed, not narrow."""
    class Impostor:
        pass

    extracted = selected_targets(
        PROVISION_NESTED,
        {
            "system_name_or_uuid": "sys-a",
            "storage": Impostor(),
            "network": Impostor(),
        },
    )
    assert extracted[1][2] is UNREADABLE
    assert extracted[2][2] is UNREADABLE


def test_a_none_field_value_is_absent():
    """The object is real; the field is an optional selector left unset."""
    extracted = selected_targets(
        PROVISION_NESTED,
        {
            "system_name_or_uuid": "sys-a",
            "storage": _ProvisionStorage(vios_uuid=None),
            "network": _ProvisionAdapters(),
        },
    )
    assert extracted[1] == ("vios", "storage.vios_uuid", ABSENT)


def test_a_missing_container_argument_is_still_malformed():
    """No default was applied, so the call never went through the boundary."""
    with pytest.raises(KeyError):
        selected_targets(PROVISION_NESTED, {"system_name_or_uuid": "sys-a"})


def test_an_unreadable_sub_object_denies_even_under_all_targets():
    extracted = selected_targets(
        PROVISION_NESTED,
        {"system_name_or_uuid": "sys-a", "storage": None, "network": None},
    )
    assert targets_permitted(ALL_TARGETS, PROVISION_NESTED, extracted) is False


def test_an_unreadable_nested_selector_is_reported_by_its_path():
    """The operator must be able to act: 'vios_uuid' alone names four fields."""
    extracted = selected_targets(
        PROVISION_NESTED,
        {"system_name_or_uuid": "sys-a", "storage": None, "network": None},
    )
    message = str(target_denial("t", "p", PROVISION_NESTED, extracted))
    assert "'storage.vios_uuid'" in message
