"""Compatibility imports for historical callers; no loader runtime is restored.

The formal desktop imports backend.scanning.legacy_loader_rules directly so this
retired package can stay excluded from delivery.
"""
from backend.scanning.legacy_loader_rules import *  # noqa: F401,F403
from backend.scanning.legacy_loader_rules import _result, _version  # noqa: F401
