"""Structural checks for explicit, human-readable task handoffs."""
from .core import HandoffError, load_handoff, validate_handoff

__version__ = '0.1.1rc1'
__all__ = ['HandoffError', 'load_handoff', 'validate_handoff']
