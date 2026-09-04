"""Compatibility alias for the affinity SSH operations package."""

import sys

from .affinity import ssh as _implementation

sys.modules[__name__] = _implementation
