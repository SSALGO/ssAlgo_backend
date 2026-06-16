"""Order execution helpers for the legacy Exchange runtime.

First pass: the implementations remain on ``Exchange`` in ``connectors.exchange``
to preserve live broker routing, slicing, and audit behavior exactly.
"""

from connectors.exchange import Exchange

OrderExecutionMixin = Exchange

__all__ = ["OrderExecutionMixin"]
