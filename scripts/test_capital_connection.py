import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

BASE_URL = os.getenv("CAPITAL_BASE_URL", "https://demo-api-capital.backend-capital.com/api/v1")
IDENTIFIER = os.getenv("CAPITAL_IDENTIFIER")
API_KEY = os.getenv("CAPITAL_API_KEY")
API_PASSWORD = os.getenv("CAPITAL_API_PASSWORD")


def fail(message):
    print(f"ERROR: {message}")
    sys.exit(1)


def main():
    if not IDENTIFIER:
        fail("CAPITAL_IDENTIFIER is missing in .env")

    if not API_KEY:
        fail("CAPITAL_API_KEY is missing in .env")

    if not API_PASSWORD:
        fail("CAPITAL_API_PASSWORD is missing in .env")

    print("Testing Capital.com API connection...")
    print("Base URL:", BASE_URL)

    # 1. Test public API connectivity
    ping_url = f"{BASE_URL}/ping"
    ping_response = requests.get(ping_url, timeout=10)

    print("\nPing status:", ping_response.status_code)
    print("Ping response:", ping_response.text)

    # 2. Start session
    session_url = f"{BASE_URL}/session"

    headers = {
        "X-CAP-API-KEY": API_KEY,
        "Content-Type": "application/json",
    }

    payload = {
        "identifier": IDENTIFIER,
        "password": API_PASSWORD,
        "encryptedPassword": False,
    }

    response = requests.post(session_url, headers=headers, json=payload, timeout=20)

    print("\nSession status:", response.status_code)

    if response.status_code not in (200, 201):
        print("Session response:", response.text)
        fail("Could not create Capital.com session. Check email, API key, and API password.")

    cst = response.headers.get("CST")
    security_token = response.headers.get("X-SECURITY-TOKEN")

    if not cst or not security_token:
        print("Response headers:", dict(response.headers))
        fail("CST or X-SECURITY-TOKEN missing from response headers.")

    print("Session created successfully.")
    print("CST received:", bool(cst))
    print("X-SECURITY-TOKEN received:", bool(security_token))

    # 3. Get account details
    auth_headers = {
        "CST": cst,
        "X-SECURITY-TOKEN": security_token,
    }

    accounts_url = f"{BASE_URL}/accounts"
    accounts_response = requests.get(accounts_url, headers=auth_headers, timeout=20)

    print("\nAccounts status:", accounts_response.status_code)
    print("Accounts response:")
    print(accounts_response.text)

    print("\nCapital.com API connection test complete.")


if __name__ == "__main__":
    main()