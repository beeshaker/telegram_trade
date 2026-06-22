import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.data.capital_client import CapitalClient

load_dotenv()


def main():
    epic = os.getenv("CAPITAL_EPIC")

    if not epic:
        raise ValueError("CAPITAL_EPIC is missing in .env")

    client = CapitalClient()
    client.create_session()

    response = client.get_prices(epic=epic, resolution="MINUTE", max_count=10)

    print("Price response:")
    print(response)


if __name__ == "__main__":
    main()