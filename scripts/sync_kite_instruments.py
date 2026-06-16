from app.core.database import get_database
from app.domain.brokers.kite_instruments import sync_kite_instruments


def main():
    result = sync_kite_instruments(get_database())
    print(f"Synced Kite instruments: {result['count']}")


if __name__ == "__main__":
    main()
