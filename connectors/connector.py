"""Compatibility facade for the legacy trading connector.

The implementation lives in connectors.exchange.  Importing this module returns
the exchange module object so legacy monkeypatch/import paths such as
``connectors.connector.requests`` and ``connectors.connector.Exchange`` keep
working while the giant implementation is split into smaller modules.
"""

import sys

from connectors import exchange as _exchange

sys.modules[__name__] = _exchange
