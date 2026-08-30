"""Docstring contracts for tools returning a submission ``JobOutcome``."""

from hmc_mcp.server_tools.lpm import (
    hmc_migrate_abort_lpar,
    hmc_migrate_lpar,
    hmc_migrate_recover_lpar,
    hmc_migrate_validate_lpar,
    hmc_remote_restart_lpar,
)


SUBMISSION_TOOLS = (
    hmc_migrate_lpar,
    hmc_migrate_validate_lpar,
    hmc_migrate_abort_lpar,
    hmc_migrate_recover_lpar,
    hmc_remote_restart_lpar,
)


def test_submission_job_outcome_docstrings_explain_persisted_fields() -> None:
    for tool in SUBMISSION_TOOLS:
        doc = " ".join((tool.__doc__ or "").split()).lower()
        assert "submission" in doc, tool.__name__
        assert "found" in doc, tool.__name__
        assert "job_href" in doc, tool.__name__
        assert "synthetic" in doc, tool.__name__
        assert "pollable" in doc, tool.__name__
