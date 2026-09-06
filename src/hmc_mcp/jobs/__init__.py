"""Job request builders and polling result types.

The implementation lives in :mod:`hmc_mcp.jobs.core`; this package boundary
collects the names consumed by client, operation, and presentation modules.
"""

# ruff: noqa: F401 -- intentional internal aggregation for existing consumers

from .core import (
    DEFAULT_JOB_POLL_INTERVAL,
    DEFAULT_JOB_TIMEOUT_SECONDS,
    FAILED_JOB_STATUSES,
    SUCCESSFUL_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    JobOutcome,
    JobWaitClient,
    job_identifier,
    job_outcome,
    validate_wait_timing,
    vios_stdout,
    wait_for_submitted_job,
)
from .requests import (
    DEVICE_TYPES,
    LU_TYPES,
    REMOTE_RESTART_OPERATIONS,
    DeviceType,
    LuType,
    RemoteRestartOperation,
    build_job_request,
    create_logical_unit_job,
    delete_logical_unit_job,
    deploy_partition_template_job,
    migrate_abort_lpar_job,
    migrate_lpar_job,
    migrate_recover_lpar_job,
    migrate_validate_lpar_job,
    power_off_lpar_job,
    power_off_system_job,
    power_off_vios_job,
    power_on_lpar_job,
    power_on_system_job,
    power_on_vios_job,
    remote_restart_lpar_job,
    validate_logical_unit_types,
)
