# Multi-Strategy Paper Trading + Per-Strategy Dashboard Visibility

**Date:** 2026-07-14
**Status:** Approved

## Overview

Add two new independent strategies alongside the existing NY-session liquidity-sweep + FVG bot, without changing its behavior, and make per-strategy performance visible in the Streamlit dashboard:

1. **PDH/PDL sweep + FVG** — same sweep/FVG mechanism as today, but the reference level is the previous calendar day's high/low instead of the opening range, scanned across the whole trading day.
2. **Extra ICT session windows** — the existing opening-range sweep+FVG mechanism, applied to additional session times (London Close, NY PM) on top of the existing AM windows. Tracked under the *same* strategy code as the existing strategy (mechanism is identical).
3. **VWAP mean-reversion fade** — a new detector that fades price back toward session VWAP when it deviates beyond a band, with the target set at VWAP itself rather than a fixed R-multiple.

Portfolio-wide guardrail: total open risk across **all** strategies and epics combined is hard-capped at 3% of balance.

## Strategy identities

Three strategy codes, stored explicitly so trades/signals can be grouped by them:

| Code | Meaning |
|---|---|
| `SWEEP_FVG_OPENING_RANGE` | Existing strategy, unchanged. Also covers the new London Close / NY PM windows. |
| `SWEEP_FVG_PDH_PDL` | New — previous calendar day high/low sweep + FVG. |
| `VWAP_MEAN_REVERSION` | New — VWAP deviation-band fade. |

## Data model changes

### `EpicConfig` (`app/models.py`)

- Add `strategy: Mapped[str] = mapped_column(String(50), index=True)`. Backfill existing 5 rows to `SWEEP_FVG_OPENING_RANGE`.
- Add `params: Mapped[dict | None] = mapped_column(JSON, nullable=True)` — strategy-specific extras (currently only used by `VWAP_MEAN_REVERSION`: `{"deviation_threshold": 1.5}`).
- Make `range_short_start`, `range_short_end`, `range_long_start`, `range_long_end` nullable — `SWEEP_FVG_PDH_PDL` and `VWAP_MEAN_REVERSION` rows only populate `trade_start`/`trade_end` (their active scan window); they leave the range columns `NULL`.
- Replace the column-level `unique=True` on `epic` with a composite `UniqueConstraint("epic", "strategy", "session_name", name="uq_epic_strategy_session")`, so the same epic can have multiple strategy rows (and even multiple session windows of the same strategy, e.g. AM + NY PM both under `SWEEP_FVG_OPENING_RANGE` on US100).

### `Signal` (`app/models.py`)

- Add `strategy: Mapped[str] = mapped_column(String(50), index=True)`. Backfill the 1 existing row to `SWEEP_FVG_OPENING_RANGE`.

### Migration

No Alembic in this repo; SQLite can't alter unique constraints or column nullability in place. Add `scripts/migrate_multi_strategy.py`:
- Rebuild `epic_configs` (create new table with the updated schema, copy rows across with `strategy='SWEEP_FVG_OPENING_RANGE'`, drop old, rename) — mirrors the idempotent-seeding style already used in `app/epics.py::ensure_seeded`.
- `ALTER TABLE signals ADD COLUMN strategy VARCHAR(50)` + backfill existing rows to `SWEEP_FVG_OPENING_RANGE` (simple add-column, no constraint change needed).
- Safe to run multiple times (checks current schema before acting), same spirit as `create_tables.py`.

## New strategy logic

### PDH/PDL sweep + FVG

- New helper, e.g. `app/strategy/previous_day_range.py::calculate_previous_day_range(candles, session_date, timezone)` — returns `{high, low, candles}` for the full previous calendar day (00:00–23:59 in the epic's local timezone). Distinct from the existing "overnight" range (18:00 prev day → 09:30) already used by `SWEEP_FVG_OPENING_RANGE`.
- Reuses `detect_sweep` and `detect_fvg_at` **unchanged** — the previous-day high/low is simply passed in place of the opening-range high/low.
- Reuses `RiskManager.build_trade_plan` unchanged (fixed 2:1 RR, same as the existing strategy).
- Config rows: all 5 current epics (US100, NATURALGAS, UK100, GOLD, USDJPY), `trade_start=00:05`, `trade_end=23:55` in each epic's existing local timezone — i.e., scans essentially all day.

### Extra ICT session windows

- No new detector code — new `EpicConfig` rows under `SWEEP_FVG_OPENING_RANGE` with new `session_name`/time windows:
  - **NY PM** on US100, NATURALGAS: `range_short 14:00–14:15`, `range_long 14:00–14:30`, `trade_start 14:15`, `trade_end 15:00` (America/New_York).
  - **London Close** on UK100, GOLD: `range_short 15:00–15:15`, `range_long 15:00–15:30`, `trade_start 15:15`, `trade_end 16:00` (Europe/London).
- These are ordinary additive rows in the existing dashboard editor — no code path changes beyond what §"Orchestration" already covers.

### VWAP mean-reversion fade

- New module `app/strategy/vwap.py`:
  - `calculate_vwap_bands(candles, deviation_threshold) -> dict` — computes session-anchored VWAP (cumulative typical-price × volume ÷ cumulative volume from the session's `trade_start`) and a rolling stddev of price from VWAP, returning `{"high": vwap + k·stddev, "low": vwap - k·stddev, "vwap": vwap}` in the same shape as `opening_range.calculate_range`, so it plugs directly into the existing `detect_sweep`.
- Reuses `detect_sweep` unchanged, fed the VWAP band instead of the opening range, and `detect_fvg_at` unchanged for entry confirmation.
- New `RiskManager.build_trade_plan_with_target(symbol, direction, entry_price, stop_loss, take_profit)` — same shape as `build_trade_plan` but takes an explicit take-profit (the VWAP price) instead of deriving it from `min_risk_reward`; still computes `risk_reward` for record-keeping and the existing `is_valid()` check.
- Config rows on all 5 epics, `params={"deviation_threshold": 1.5}`, active window during each session's quiet mid-session lull:
  - US100, NATURALGAS: `trade_start=11:00`, `trade_end=13:30` (America/New_York).
  - UK100, GOLD: `trade_start=10:00`, `trade_end=12:00` (Europe/London).
  - USDJPY: `trade_start=11:00`, `trade_end=13:00` (Asia/Tokyo) — covers the Tokyo lunch lull.

## Portfolio risk ceiling (≤3% combined, hard block)

- New `app/paper/auto_paper.py::total_open_risk_percent(db, account) -> float` — sums `risk_amount` across all `PENDING`/`ACTIVE` `PaperTrade` rows (any epic, any strategy), divided by current balance.
- Before any strategy creates a trade: `total_open_risk_percent(...) + new_trade_risk_percent > 3.0` → skip trade creation, log it, no Telegram alert spam (a single debug log line is enough — this is expected to trigger occasionally by design).
- Applies uniformly regardless of which strategy/epic proposes the trade.

## Per-(epic, strategy) scoping (consequence of the config model change)

Since each strategy now has its own `max_trades_per_day`/`max_losses_per_day` on its own `EpicConfig` row, the daily-limit and open-trade checks must be scoped per (epic, strategy), not just epic — otherwise a PDH/PDL trade on US100 could be blocked by an unrelated open opening-range trade on the same epic, and per-strategy daily limits would be meaningless.

`app/paper/auto_paper.py` functions gain an **optional** `strategy` filter (default `None` = "all strategies for this epic", preserving today's behavior for dashboard-wide views):
- `get_open_trades(db, symbol=None, strategy=None)`
- `trades_today_count(db, symbol=None, strategy=None)`
- `losses_today_count(db, symbol=None, strategy=None)`
- `stop_today_key(epic=None, strategy=None)`, `is_stopped_today(db, epic=None, strategy=None)`, `stop_trading_today(db, epic=None, strategy=None)`

When `strategy` is provided, these filter via a join from `PaperTrade.signal_id` → `Signal.strategy`. The orchestrator (below) always passes both `epic` and `strategy` explicitly; the dashboard keeps calling them epic-only (or with no args) for its existing "everything for this epic" displays, so no dashboard call sites break.

## Orchestration

`scripts/run_auto_paper_once.py` currently loops over `list_enabled_epics(db)` (one row = one epic = one strategy). It changes to loop over **all enabled `EpicConfig` rows** (now one row = one epic+strategy+session combination) and dispatch based on `cfg.strategy`:

- `SWEEP_FVG_OPENING_RANGE` → existing code path, unchanged, now also naturally covers the new NY PM / London Close rows.
- `SWEEP_FVG_PDH_PDL` → previous-day-range pipeline described above.
- `VWAP_MEAN_REVERSION` → VWAP-band pipeline described above.

Each pipeline tags the created `Signal.strategy = cfg.strategy`, checks the portfolio risk ceiling before creating the paper trade, and uses the per-(epic,strategy) scoped checks from the section above instead of the current epic-only checks. Same single polling loop, same script, no new infrastructure.

## Dashboard changes (`dashboard.py`)

- **Epic & Session Management** editor: add a **Strategy** column (`SelectboxColumn`, options = the 3 strategy codes) and a **Params** column (JSON as text, parsed/validated on save). The table now naturally shows multiple rows per epic.
- New **Strategy Performance** section (new expander/subheader): for each strategy code, trade count, win rate, total P&L, sum R and avg R, and current open risk contribution — computed via a query joining `PaperTrade` → `Signal` grouped by `Signal.strategy` (not limited to the existing 500-row display cap, since these are aggregate stats).
- Add a **Strategy** column to the existing `trade_rows`/`signal_rows` tables (sourced via the `Signal.strategy` join for trades).
- A portfolio risk gauge near the balance display: current total open risk % vs. the 3% ceiling (from `total_open_risk_percent`).

## Validation before going live

No backtest engine exists in this repo, and building one is out of scope here. Each new `EpicConfig` row ships with `enabled=False`. Two new dry-run scripts (following the existing single-purpose script convention — `scripts/check_candles.py`, `scripts/demo_strategy_detection.py`, etc.):
- `scripts/dry_run_pdh_pdl.py`
- `scripts/dry_run_vwap.py`

Each loads real recently-stored candles for the configured epics from the DB and prints any detected signals without writing to the database, so the strategy can be sanity-checked before being flipped to `enabled=True` via the dashboard.

## Testing

- Unit tests for `calculate_previous_day_range`, `calculate_vwap_bands` (+ confirming it feeds correctly into the existing `detect_sweep`), `RiskManager.build_trade_plan_with_target`, and `total_open_risk_percent`.
- Unit tests for the updated `auto_paper.py` counter functions confirming: (a) omitting `strategy` preserves today's exact behavior, (b) passing `strategy` correctly isolates counts/limits per (epic, strategy).
- The full existing test suite (8 tests as of the last dashboard change) must continue passing unchanged, since `SWEEP_FVG_OPENING_RANGE` behavior is untouched.

## No new infrastructure

No new services, no new polling loops, no backtest framework, no Alembic adoption. Everything above is additive to the existing FastAPI/SQLAlchemy/Streamlit/Capital.com architecture.
