"""Contract master file helpers for the legacy Exchange runtime."""

from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = BACKEND_ROOT / "data" / "contracts"


def contract_file_path(filename):
    """Return the preferred contract path, falling back to the legacy root path."""
    data_path = CONTRACTS_DIR / filename
    if data_path.exists():
        return data_path
    return BACKEND_ROOT / filename


class ContractsMixin:
    """Reserved mixin boundary for future contract-method extraction."""

    pass


__all__ = ["BACKEND_ROOT", "CONTRACTS_DIR", "ContractsMixin", "contract_file_path"]
