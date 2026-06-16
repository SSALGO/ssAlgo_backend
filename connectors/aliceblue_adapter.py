"""AliceBlue compatibility adapter entrypoint.

The adapter is re-exported from ``connectors.exchange`` in this first pass so
existing monkeypatch paths and runtime globals keep identical behavior.
"""

from connectors.exchange import AliceBlueTradeHubAdapter

__all__ = ["AliceBlueTradeHubAdapter"]
