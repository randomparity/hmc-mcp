"""CLI boundary helpers for loading LPAR assignment input."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from ...operations.lpar.assignments import LparPcieAssignments
from ..output import usage_error


def load_pcie_assignments(path: Path | None) -> LparPcieAssignments:
    """Load and validate the shared PCIe assignment schema from JSON."""
    if path is None:
        return LparPcieAssignments()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return TypeAdapter(LparPcieAssignments).validate_python(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        usage_error(f"Cannot load --pcie-assignments {path}: {error}")
        raise AssertionError("usage_error must raise") from error
