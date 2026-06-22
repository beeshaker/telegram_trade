import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import requests

from app.config import get_settings

settings = get_settings()

if not settings.telegram_bot_token:
    raise ValueError("TELEGRAM_BOT_TOKEN is missing in .env")

url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/getUpdates"
response = requests.get(url, timeout=10)
print(response.json())
