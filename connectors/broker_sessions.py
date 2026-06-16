"""Broker login/session helpers for the legacy Exchange runtime.

First pass: the implementations remain on ``Exchange`` in ``connectors.exchange``
to preserve bound ``self`` state and log/DB behavior exactly.
"""

from connectors.exchange import Exchange

BrokerSessionMixin = Exchange

__all__ = ["BrokerSessionMixin"]
