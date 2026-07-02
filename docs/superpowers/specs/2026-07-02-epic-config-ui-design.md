# Epic & Session Management via Streamlit UI

**Date:** 2026-07-02
**Status:** Approved
**Builds on:** [2026-06-26-multi-epic-design.md](2026-06-26-multi-epic-design.md)

## Overview

The 2026-06-26 design made the bot trade multiple epics in one loop pass, but epic-to-session
mapping lives in a hardcoded `SESSION_CONFIG` dict in `run_auto_paper_once.py` and the epic list
lives in the `CAPITAL_EPICS` env var — both require editing code/`.env` and restarting scripts.

This design moves that config into the database and exposes full CRUD for it from the Streamlit
dashboard, so epics, timezones, session windows, and per-epic risk limits are all managed from
the UI with no code changes or restarts required (background scripts pick up changes on their
next 60s loop iteration).

"Trading epics simultaneously" is already satisfied by the prior design (each loop iteration
processes every enabled epic in sequence, ~seconds apart, with independent trade/loss counters).
This design's job is making the per-epic *configuration* — which epics are active and what
timezone/session window each uses — a UI-managed resource instead of a code-managed one.

## Data Model

New table in `app/models.py`:

```python
class EpicConfig(Base):
    __tablename__ = "epic_configs"

    id: Mapped[int]
    epic: Mapped[str]                      # unique, e.g. "US100"
    enabled: Mapped[bool]                  # default True
    timezone: Mapped[str]                  # IANA tz, e.g. "America/New_York"
    session_name: Mapped[str]              # e.g. "NY Open"
    range_short_start: Mapped[time]
    range_short_end: Mapped[time]
    range_long_start: Mapped[time]
    range_long_end: Mapped[time]
    trade_start: Mapped[time]
    trade_end: Mapped[time]
    risk_per_trade_percent: Mapped[float | None]   # override; falls back to global .env
    max_trades_per_day: Mapped[int | None]          # override; falls back to global .env
    max_losses_per_day: Mapped[int | None]          # override; falls back to global .env
    created_at, updated_at
```

`epic` is unique. Existing `PaperTrade`/`Signal`/`Candle` rows key off the raw `symbol` string, so
no FK is needed — deleting or disabling an `EpicConfig` row doesn't touch trade history.

## Shared Module: `app/epics.py`

- `CURATED_TIMEZONES` — curated dropdown list: `America/New_York`, `America/Chicago`,
  `Europe/London`, `Europe/Frankfurt`, `Asia/Tokyo`, `Asia/Singapore`, `Australia/Sydney`, `UTC`.
- `ensure_seeded(db)` — creates the table if missing (`Base.metadata.create_all`) and, **only if
  the table is empty**, inserts the five epics from the 2026-06-26 design (US100, NATURALGAS,
  UK100, GOLD, USDJPY) with their existing timezone/session/window values, `enabled` set per the
  current `CAPITAL_EPICS` list. One-time, idempotent, called lazily from every entry point so no
  manual migration step is needed.
- `list_enabled_epics(db)` / `list_all_epics(db)` — return `EpicConfig` rows, calling
  `ensure_seeded` first.
- `get_epic_config(db, epic)`, `upsert_epic_config(db, epic, **fields)`,
  `delete_epic_config(db, epic)`.
- `effective_risk(cfg, settings) -> (risk_percent, max_trades, max_losses)` — resolves per-epic
  overrides, falling back to `settings.risk_per_trade_percent` / `MAX_TRADES_PER_DAY` /
  `MAX_LOSSES_PER_DAY` when the override is `None`.

## Backend Script Changes

All four scripts drop their local `parse_epics()`/hardcoded dict and read from `app.epics`:

- **`run_auto_paper_once.py`** — loop over `list_enabled_epics(db)`; use `cfg.timezone`,
  `cfg.session_name`, `cfg.range_short_*`, `cfg.range_long_*`, `cfg.trade_start/end` in place of
  the old `SESSION_CONFIG` lookup; call `effective_risk(cfg, settings)` per epic instead of
  computing `risk_percent`/`max_trades_per_day`/`max_losses_per_day` once globally.
- **`sync_capital_candles.py`**, **`build_m5_candles.py`** — replace `parse_epics()` with
  `[c.epic for c in list_enabled_epics(db)]`.
- **`telegram_command_loop.py`** — replace `parse_epics()` and the hardcoded `EPIC_DISPLAY`/
  `SESSION_DISPLAY` dicts in `status_text()` with `cfg.session_name` from the DB; default symbol
  becomes the first enabled epic instead of `CAPITAL_EPIC`.

`CAPITAL_EPICS`/`CAPITAL_EPIC` in `.env` are no longer read by these scripts after this change.
They're left in `.env` untouched (harmless) rather than removed.

`check_ny_session_status.py` is a standalone manual diagnostic tool, out of scope — left as-is.

## Dashboard Changes (`dashboard.py`)

**New "⚙️ Epic & Session Management" expander**, placed near the top of the page:

1. **Editable table** (`st.data_editor`, `num_rows="fixed"`, `epic` column disabled/read-only)
   listing all epics (enabled and disabled) with columns: Enabled (checkbox), Timezone
   (`SelectboxColumn` from `CURATED_TIMEZONES`), Session name, Range short start/end, Range long
   start/end, Trade start/end (all `TimeColumn`), Risk % override, Max trades override, Max
   losses override (`NumberColumn`, nullable — blank means "use global default"). A **Save
   changes** button diffs the edited dataframe against the loaded rows and calls
   `upsert_epic_config` for each changed row.
2. **Add new epic form** (`st.form`, below the table) — epic code text input, timezone dropdown,
   session name, range/trade time inputs, optional risk overrides, enabled checkbox. Submitting
   calls `upsert_epic_config` and reruns.
3. **Remove epic control** — multiselect of epic codes + a "Confirm removal" checkbox (matching
   the existing reset-account confirm pattern) + delete button calling `delete_epic_config`.

**Sidebar:** "Symbol / EPIC" free-text input becomes a `st.selectbox` populated from
`list_enabled_epics(db)`, defaulting to the first entry. If no epics are enabled, show a warning
and skip the rest of the page's data-dependent sections.

**Session levels panel:** `get_levels()` currently hardcodes NY hours (9:30 opening range, 18:00
overnight start, fixed `America/New_York` tz) regardless of which symbol is selected. It's
generalized to take the selected epic's `EpicConfig` and use `cfg.timezone`, `cfg.range_short_*`,
`cfg.range_long_*` for its window calculations (overnight start stays "18:00 previous day in the
epic's own tz" to mirror `run_auto_paper_once.py`). The expander title becomes dynamic, e.g.
"London session levels" for UK100/GOLD, "Tokyo session levels" for USDJPY.

No changes to the open-trades/trade-history/signal-history tables — they already show all
symbols at once, satisfying "manage all epics" visibility without additional filtering.

## Testing

Light unit tests for `app/epics.py` in `tests/test_epics.py`, using an in-memory SQLite DB
(same `Base`/model pattern as production, separate engine):
- `ensure_seeded` is idempotent — calling it twice doesn't duplicate rows.
- `effective_risk` falls back to global settings when overrides are `None`, and returns the
  override when set.
- `upsert_epic_config` creates on first call, updates in place on second call for the same epic.

## Out of Scope

- True parallel/concurrent execution (threads/async) across epics — sequential looping within a
  60s cycle is already fast enough for this bot's timeframe and is unchanged by this design.
- Filtering the trade history / signal history tables by epic in the UI.
- Editing global risk settings (`RISK_PER_TRADE_PERCENT` etc.) from the UI — only per-epic
  overrides are added; global defaults remain `.env`-managed.
