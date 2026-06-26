# Multi-Epic Multi-Session Support

**Date:** 2026-06-26  
**Status:** Approved

## Overview

Extend the NY Open FVG bot to trade multiple epics simultaneously, each assigned to the trading session that best suits its liquidity profile. One shared paper account balance, independent per-epic trade and loss counters.

## Epic-to-Session Mapping

| Epic       | Session      | Timezone             | Opening Range          | Trade Window   |
|------------|-------------|----------------------|------------------------|----------------|
| US100      | NY Open      | America/New_York     | 9:30–9:45 / 9:30–10:00 | 9:45–10:30     |
| NATURALGAS | NY Open      | America/New_York     | 9:30–9:45 / 9:30–10:00 | 9:45–10:30     |
| UK100      | London Open  | Europe/London        | 8:00–8:15 / 8:00–8:30  | 8:15–9:00      |
| GOLD       | London Open  | Europe/London        | 8:00–8:15 / 8:00–8:30  | 8:15–9:00      |
| USDJPY     | Tokyo Open   | Asia/Tokyo           | 9:00–9:15 / 9:00–9:30  | 9:15–10:00     |

## Config Changes

Add to `.env`:
```
CAPITAL_EPICS=US100,UK100,GOLD,USDJPY,NATURALGAS
```

`CAPITAL_EPIC` (singular) kept as fallback so existing deployments without `CAPITAL_EPICS` continue to work.

## Script Changes

### `sync_capital_candles.py`
- Read `CAPITAL_EPICS`, split by comma
- Loop over each epic and sync M1 candles from Capital.com
- Fallback to `CAPITAL_EPIC` if `CAPITAL_EPICS` not set

### `build_m5_candles.py`
- Same loop pattern — build M5 candles for each epic

### `run_auto_paper_once.py`
- Add `SESSION_CONFIG` dict mapping each epic to its timezone and time windows
- Loop over each epic and run the full sweep + FVG scan independently
- Per-epic trade/loss limits using namespaced BotState keys:
  - `stop_today_{EPIC}_{date}` — stopped today flag per epic
  - Trade and loss counts filtered by `symbol` in DB queries
- Shared `PaperAccount` — all trades deduct from the same balance
- Risk per trade remains `RISK_PER_TRADE_PERCENT` (0.5%) applied to shared balance

### `telegram_command_loop.py`
- Status command shows overall balance once, then a per-epic row:
  ```
  🤖 Bot Status

  Balance: $999.93
  Mode: AUTO_PAPER | Paused: False

  US100    | NY Open    | Trades: 0 | Losses: 0
  UK100    | London     | Trades: 0 | Losses: 0
  GOLD     | London     | Trades: 0 | Losses: 0
  USDJPY   | Tokyo      | Trades: 0 | Losses: 0
  NATGAS   | NY Open    | Trades: 0 | Losses: 0
  ```

## Trade Limits

- `MAX_TRADES_PER_DAY` and `MAX_LOSSES_PER_DAY` apply **per epic**
- Up to 5 trades can be open simultaneously (one per epic)
- Max concurrent risk: 5 × 0.5% = 2.5% of balance — acceptable

## No New Files

All changes are in-place edits to existing scripts. No new modules, no new abstractions.
