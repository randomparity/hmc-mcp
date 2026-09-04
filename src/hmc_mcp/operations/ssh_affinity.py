"""Compatibility exports for the pre-package SSH affinity import path."""

from .affinity.ssh import (
    MinimumAffinityPolicyResult,
    ResourceGroupAffinityResult,
    get_lpar_memopt_score,
    get_minimum_affinity_policy,
    get_system_memopt_score,
    list_lpar_memopt_scores,
    list_resource_group_memopt_scores,
    plan_lpar_memopt_scores,
    plan_resource_group_memopt_scores,
    plan_system_memopt_score,
    set_minimum_affinity_policy,
)

__all__ = [
    "MinimumAffinityPolicyResult",
    "ResourceGroupAffinityResult",
    "get_lpar_memopt_score",
    "get_minimum_affinity_policy",
    "get_system_memopt_score",
    "list_lpar_memopt_scores",
    "list_resource_group_memopt_scores",
    "plan_lpar_memopt_scores",
    "plan_resource_group_memopt_scores",
    "plan_system_memopt_score",
    "set_minimum_affinity_policy",
]
