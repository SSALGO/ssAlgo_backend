"""Strategy evaluation helpers for the legacy Exchange runtime.

First pass: the implementations remain on ``Exchange`` in ``connectors.exchange``
to preserve strategy behavior exactly.
"""

from connectors.exchange import Exchange

StrategyEngineMixin = Exchange

__all__ = ["StrategyEngineMixin"]
