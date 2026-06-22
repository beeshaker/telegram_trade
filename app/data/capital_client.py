import os
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()


class CapitalClient:
    def __init__(self):
        self.base_url = os.getenv(
            "CAPITAL_BASE_URL",
            "https://demo-api-capital.backend-capital.com/api/v1",
        )
        self.identifier = os.getenv("CAPITAL_IDENTIFIER")
        self.api_key = os.getenv("CAPITAL_API_KEY")
        self.api_password = os.getenv("CAPITAL_API_PASSWORD")

        self.cst = None
        self.security_token = None

        if not self.identifier:
            raise ValueError("CAPITAL_IDENTIFIER is missing in .env")

        if not self.api_key:
            raise ValueError("CAPITAL_API_KEY is missing in .env")

        if not self.api_password:
            raise ValueError("CAPITAL_API_PASSWORD is missing in .env")

    def create_session(self) -> None:
        url = f"{self.base_url}/session"

        headers = {
            "X-CAP-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }

        payload = {
            "identifier": self.identifier,
            "password": self.api_password,
            "encryptedPassword": False,
        }

        response = requests.post(url, headers=headers, json=payload, timeout=20)

        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Capital.com session failed: {response.status_code} {response.text}"
            )

        self.cst = response.headers.get("CST")
        self.security_token = response.headers.get("X-SECURITY-TOKEN")

        if not self.cst or not self.security_token:
            raise RuntimeError("Capital.com session tokens were not returned.")

    def auth_headers(self) -> dict[str, str]:
        if not self.cst or not self.security_token:
            self.create_session()

        return {
            "CST": self.cst,
            "X-SECURITY-TOKEN": self.security_token,
            "Content-Type": "application/json",
        }

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        url = f"{self.base_url}{path}"

        response = requests.get(
            url,
            headers=self.auth_headers(),
            params=params,
            timeout=20,
        )

        if response.status_code >= 400:
            raise RuntimeError(
                f"GET {path} failed: {response.status_code} {response.text}"
            )

        return response.json()

    def get_accounts(self) -> dict:
        return self.get("/accounts")

    def search_markets(self, search_term: str) -> dict:
        return self.get("/markets", params={"searchTerm": search_term})

    def get_prices(self, epic: str, resolution: str = "MINUTE", max_count: int = 10) -> dict:
        return self.get(
            f"/prices/{epic}",
            params={
                "resolution": resolution,
                "max": max_count,
            },
        )