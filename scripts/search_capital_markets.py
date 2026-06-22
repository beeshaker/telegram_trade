import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.data.capital_client import CapitalClient


def print_markets(search_term: str, response: dict):
    markets = response.get("markets", [])

    print("\n" + "=" * 80)
    print(f"Search term: {search_term}")
    print(f"Results: {len(markets)}")
    print("=" * 80)

    for market in markets[:20]:
        print("EPIC:", market.get("epic"))
        print("Name:", market.get("instrumentName"))
        print("Type:", market.get("instrumentType"))
        print("Status:", market.get("marketStatus"))
        print("Bid:", market.get("bid"))
        print("Offer:", market.get("offer"))
        print("Currency:", market.get("currency"))
        print("-" * 40)


def main():
    client = CapitalClient()
    client.create_session()

    search_terms = [
        "US Tech 100",
        "Nasdaq",
        "NASDAQ",
        "US100",
        "NAS100",
        "USTEC",
        "Wall Street",
        "US 30",
        "Gold",
        "XAUUSD",
    ]

    for term in search_terms:
        try:
            response = client.search_markets(term)
            print_markets(term, response)
        except Exception as e:
            print(f"Search failed for {term}: {e}")


if __name__ == "__main__":
    main()