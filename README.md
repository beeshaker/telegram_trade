# NY Open Liquidity Sweep + FVG Bot

This is the initial project skeleton for a NAS100 NY Open Liquidity Sweep + FVG alert and paper-trading bot.

Version 1 includes:

- FastAPI backend
- SQLAlchemy database models
- SQLite quick-start support
- PostgreSQL Docker support
- Telegram alert module
- Health endpoint
- Test Telegram endpoint
- Initial FVG detector
- Initial sweep detector
- Initial risk manager
- Initial paper trade model

Live trading is disabled by default.

---

## 1. Local Setup

```bash
cd ny_open_fvg_bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/create_tables.py
uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/health
```

---

## 2. Telegram Setup

Create a bot using Telegram BotFather.

Update `.env`:

```env
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

To get the chat ID:

1. Open your bot in Telegram.
2. Send `/start`.
3. Run:

```bash
python scripts/get_telegram_chat_id.py
```

Then test:

```bash
python scripts/test_telegram_alert.py
```

Or call the API:

```bash
curl -X POST http://127.0.0.1:8000/alerts/test
```

---

## 3. Docker Setup

```bash
cp .env.example .env
# edit .env and add Telegram values if needed

docker compose up --build
```

Open:

```text
http://127.0.0.1:8000/health
```

---

## 4. Current API Endpoints

```text
GET  /
GET  /health
POST /alerts/test
GET  /signals
GET  /paper-trades
```

---

## 5. Strategy Rules in Version 1

Recommended first testing configuration:

```text
Symbol: NAS100
Mode: PAPER
Entry timeframe: 5-minute FVG
Data requirement: 1-minute and 5-minute candles
Session timezone: America/New_York
Previous session: 18:00 previous day to 09:29 current day
NY 15-minute range: 09:30 to 09:45
NY 30-minute range: 09:30 to 10:00
Trade window: 09:45 to 11:30
```

---

## 6. Important Safety Note

Live trading is intentionally blocked unless all are true:

```env
TRADING_MODE=LIVE
LIVE_TRADING_CONFIRM=true
MT5_ENABLED=true
```

Do not enable live trading before backtesting and paper testing.

---

## 7. Next Build Steps

1. Add candle ingestion from MT5 or CSV.
2. Store 1-minute candles.
3. Build 5-minute candles.
4. Calculate overnight high/low.
5. Calculate NY opening range.
6. Detect sweep.
7. Detect FVG.
8. Save signal.
9. Send Telegram alert.
10. Create paper trade.
