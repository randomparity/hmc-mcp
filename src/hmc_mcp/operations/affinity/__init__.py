"""Public LPAR affinity operations grouped by assessment and SSH workflows."""

from . import rest as rest
from . import ssh as ssh

AffinityAssessmentInput = rest.AffinityAssessmentInput
AffinityAssessmentResult = rest.AffinityAssessmentResult
AffinityClassification = rest.AffinityClassification
AffinityEvidence = rest.AffinityEvidence
CapturedPolicyState = rest.CapturedPolicyState
LparAffinityAssessmentOutcome = rest.LparAffinityAssessmentOutcome
PolicyState = rest.PolicyState
PostActivationAffinityAssessment = rest.PostActivationAffinityAssessment
ProvisionAffinityAssessment = rest.ProvisionAffinityAssessment
affinity_not_measured = rest.affinity_not_measured
assess_affinity = rest.assess_affinity
assess_post_activation_affinity = rest.assess_post_activation_affinity
classify_affinity_outcome = rest.classify_affinity_outcome
validate_affinity_request = rest.validate_affinity_request

MemoptLparSelector = ssh.MemoptLparSelector
MemoptResourceGroupSelector = ssh.MemoptResourceGroupSelector
MinimumAffinityPolicyResult = ssh.MinimumAffinityPolicyResult
ResourceGroupAffinityResult = ssh.ResourceGroupAffinityResult
get_lpar_memopt_score = ssh.get_lpar_memopt_score
get_minimum_affinity_policy = ssh.get_minimum_affinity_policy
get_system_memopt_score = ssh.get_system_memopt_score
list_lpar_memopt_scores = ssh.list_lpar_memopt_scores
list_resource_group_memopt_scores = ssh.list_resource_group_memopt_scores
plan_lpar_memopt_scores = ssh.plan_lpar_memopt_scores
plan_resource_group_memopt_scores = ssh.plan_resource_group_memopt_scores
plan_system_memopt_score = ssh.plan_system_memopt_score
set_minimum_affinity_policy = ssh.set_minimum_affinity_policy
