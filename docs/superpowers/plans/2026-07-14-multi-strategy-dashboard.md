# Multi-Strategy Paper Trading + Per-Strategy Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new independent paper-trading strategies (PDH/PDL sweep+FVG, VWAP mean-reversion) alongside the existing NY-session sweep+FVG bot, plus extra ICT session windows on the existing strategy, a portfolio-wide 3% open-risk ceiling, and per-strategy performance visibility in the Streamlit dashboard.

**Architecture:** Extend the existing `EpicConfig` table so one row represents one (epic, strategy, session) combination instead of one row per epic. Tag every `Signal` with a `strategy` code. Reuse the existing `detect_sweep`/`detect_fvg_at`/`RiskManager` building blocks for all three strategies — only the reference price band fed into them differs per strategy. `scripts/run_auto_paper_once.py` becomes a per-config-row dispatcher over three strategy-handler functions with identical signatures.

**Tech Stack:** Python, FastAPI, SQLAlchemy (SQLite), Streamlit, pytest.

**Reference spec:** `docs/superpowers/specs/2026-07-14-multi-strategy-dashboard-design.md`

## Global Constraints

- No Alembic in this repo — schema changes to the live SQLite DB use hand-written, idempotent migration SQL, not `Base.metadata.create_all` (which only creates missing tables, never alters existing ones).
- Every new `EpicConfig` row for the two new strategies ships with `enabled=False` — nothing new goes live without a manual flip in the dashboard.
- Total open risk across ALL strategies and epics combined must never exceed `settings.max_portfolio_risk_percent` (default 3.0) — enforced as a hard block before any trade is created, by any strategy.
- The existing `SWEEP_FVG_OPENING_RANGE` strategy's behavior must not change — every task touching shared code must keep the existing test suite green.
- Run `python3 -m pytest -q` after every task; it must show only new tests added and zero regressions.

---

## Task 1: EpicConfig + Signal model changes

**Files:**
- Modify: `app/models.py:141-159` (EpicConfig), `app/models.py:44-52` (Signal, add one field)
- Test: `tests/test_models.py` (new)

**Interfaces:**
- Produces: `EpicConfig.strategy` (str, default `"SWEEP_FVG_OPENING_RANGE"`), `EpicConfig.params` (dict | None), nullable `range_short_start/end`/`range_long_start/end`, unique constraint on `(epic, strategy, session_name)`. `Signal.strategy` (str, default `"SWEEP_FVG_OPENING_RANGE"`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_models.py`:

```python
from datetime import time as dtime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import EpicConfig


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_config(**overrides):
    fields = dict(
        epic="US100",
        strategy="SWEEP_FVG_OPENING_RANGE",
        enabled=True,
        timezone="America/New_York",
        session_name="NY Open",
        range_short_start=dtime(9, 30),
        range_short_end=dtime(9, 45),
        range_long_start=dtime(9, 30),
        range_long_end=dtime(10, 0),
        trade_start=dtime(9, 45),
        trade_end=dtime(10, 30),
    )
    fields.update(overrides)
    return EpicConfig(**fields)


def test_same_epic_different_strategy_is_allowed(db_session):
    db_session.add(_make_config(strategy="SWEEP_FVG_OPENING_RANGE"))
    db_session.add(_make_config(strategy="SWEEP_FVG_PDH_PDL", session_name="PDH/PDL All Day"))
    db_session.commit()

    count = db_session.query(EpicConfig).filter(EpicConfig.epic == "US100").count()
    assert count == 2


def test_same_epic_strategy_session_is_rejected(db_session):
    db_session.add(_make_config())
    db_session.commit()

    db_session.add(_make_config())
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_pdh_pdl_row_can_omit_range_columns(db_session):
    cfg = _make_config(
        strategy="SWEEP_FVG_PDH_PDL",
        session_name="PDH/PDL All Day",
        range_short_start=None,
        range_short_end=None,
        range_long_start=None,
        range_long_end=None,
    )
    db_session.add(cfg)
    db_session.commit()

    saved = db_session.query(EpicConfig).filter(EpicConfig.epic == "US100").one()
    assert saved.range_short_start is None


def test_epic_config_default_strategy_is_opening_range(db_session):
    cfg = EpicConfig(
        epic="TESTDEFAULT",
        enabled=True,
        timezone="UTC",
        session_name="Test",
        range_short_start=dtime(9, 30),
        range_short_end=dtime(9, 45),
        range_long_start=dtime(9, 30),
        range_long_end=dtime(10, 0),
        trade_start=dtime(9, 45),
        trade_end=dtime(10, 30),
    )
    db_session.add(cfg)
    db_session.commit()
    db_session.refresh(cfg)
    assert cfg.strategy == "SWEEP_FVG_OPENING_RANGE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_models.py -v`
Expected: FAIL — `TypeError: 'strategy' is an invalid keyword argument for EpicConfig` (column doesn't exist yet).

- [ ] **Step 3: Modify `app/models.py`**

Replace the `EpicConfig` class (currently lines 141-159) with:

```python
class EpicConfig(Base):
    __tablename__ = "epic_configs"
    __table_args__ = (UniqueConstraint("epic", "strategy", "session_name", name="uq_epic_strategy_session"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    epic: Mapped[str] = mapped_column(String(50), index=True)
    strategy: Mapped[str] = mapped_column(String(50), index=True, default="SWEEP_FVG_OPENING_RANGE")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timezone: Mapped[str] = mapped_column(String(50))
    session_name: Mapped[str] = mapped_column(String(100))
    range_short_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    range_short_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    range_long_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    range_long_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    trade_start: Mapped[time] = mapped_column(Time)
    trade_end: Mapped[time] = mapped_column(Time)
    risk_per_trade_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    max_trades_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_losses_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

In the `Signal` class (currently starting at line 44), add the `strategy` field right after `symbol` (line 48):

```python
    symbol: Mapped[str] = mapped_column(String(50), index=True)
    strategy: Mapped[str] = mapped_column(String(50), index=True, default="SWEEP_FVG_OPENING_RANGE")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_models.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run full suite to confirm no regressions**

Run: `python3 -m pytest -q`
Expected: All existing tests still pass (the `strategy` default means existing `EpicConfig(...)` calls elsewhere in the codebase that don't pass `strategy` still work).

- [ ] **Step 6: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat: support multiple strategies per epic in EpicConfig, tag Signal with strategy"
```

---

## Task 2: Strategy constants + epics.py CRUD updated for (epic, strategy, session_name) identity

**Files:**
- Modify: `app/epics.py` (whole file)
- Modify: `tests/test_epics.py` (update existing calls)

**Interfaces:**
- Consumes: `EpicConfig.strategy`, `EpicConfig.params` (Task 1)
- Produces: `STRATEGY_SWEEP_FVG_OPENING_RANGE`, `STRATEGY_SWEEP_FVG_PDH_PDL`, `STRATEGY_VWAP_MEAN_REVERSION`, `ALL_STRATEGIES` (list). `get_epic_config(db, epic, strategy, session_name)`, `upsert_epic_config(db, epic, strategy, session_name, **fields)`, `delete_epic_config(db, epic, strategy, session_name)` — all now keyed on the full (epic, strategy, session_name) triple, matching the Task 1 unique constraint. `list_all_epics`/`list_enabled_epics`/`effective_risk` signatures unchanged.

- [ ] **Step 1: Write the failing test**

In `tests/test_epics.py`, replace `test_ensure_seeded_is_idempotent` and `test_upsert_epic_config_creates_then_updates` with:

```python
def test_ensure_seeded_is_idempotent(db_session):
    ensure_seeded(db_session)
    count_after_first = db_session.query(EpicConfig).count()
    assert count_after_first == 19

    ensure_seeded(db_session)
    count_after_second = db_session.query(EpicConfig).count()
    assert count_after_second == count_after_first


def test_ensure_seeded_new_strategies_default_disabled(db_session, monkeypatch):
    monkeypatch.setenv("CAPITAL_EPICS", "US100,UK100,GOLD,USDJPY,NATURALGAS")
    ensure_seeded(db_session)

    pdh_pdl_rows = db_session.query(EpicConfig).filter(EpicConfig.strategy == "SWEEP_FVG_PDH_PDL").all()
    assert len(pdh_pdl_rows) == 5
    assert all(not row.enabled for row in pdh_pdl_rows)

    vwap_rows = db_session.query(EpicConfig).filter(EpicConfig.strategy == "VWAP_MEAN_REVERSION").all()
    assert len(vwap_rows) == 5
    assert all(not row.enabled for row in vwap_rows)


def test_upsert_epic_config_creates_then_updates(db_session):
    created = upsert_epic_config(
        db_session,
        epic="TESTEPIC",
        strategy="SWEEP_FVG_OPENING_RANGE",
        session_name="Test Session",
        enabled=True,
        timezone="UTC",
        range_short_start=dtime(9, 0),
        range_short_end=dtime(9, 15),
        range_long_start=dtime(9, 0),
        range_long_end=dtime(9, 30),
        trade_start=dtime(9, 15),
        trade_end=dtime(10, 0),
        risk_per_trade_percent=None,
        max_trades_per_day=None,
        max_losses_per_day=None,
        params=None,
    )

    assert created.id is not None
    assert created.session_name == "Test Session"

    updated = upsert_epic_config(
        db_session,
        epic="TESTEPIC",
        strategy="SWEEP_FVG_OPENING_RANGE",
        session_name="Test Session",
        enabled=False,
        timezone="UTC",
        range_short_start=dtime(9, 0),
        range_short_end=dtime(9, 15),
        range_long_start=dtime(9, 0),
        range_long_end=dtime(9, 30),
        trade_start=dtime(9, 15),
        trade_end=dtime(10, 0),
        risk_per_trade_percent=1.0,
        max_trades_per_day=5,
        max_losses_per_day=2,
        params=None,
    )

    assert updated.id == created.id
    assert updated.enabled is False
    assert float(updated.risk_per_trade_percent) == 1.0

    count = db_session.query(EpicConfig).filter(EpicConfig.epic == "TESTEPIC").count()
    assert count == 1


def test_upsert_epic_config_same_epic_strategy_different_session_creates_new_row(db_session):
    upsert_epic_config(
        db_session, epic="US100", strategy="SWEEP_FVG_OPENING_RANGE", session_name="NY Open",
        enabled=True, timezone="America/New_York",
        range_short_start=dtime(9, 30), range_short_end=dtime(9, 45),
        range_long_start=dtime(9, 30), range_long_end=dtime(10, 0),
        trade_start=dtime(9, 45), trade_end=dtime(10, 30),
        risk_per_trade_percent=None, max_trades_per_day=None, max_losses_per_day=None, params=None,
    )
    upsert_epic_config(
        db_session, epic="US100", strategy="SWEEP_FVG_OPENING_RANGE", session_name="NY PM",
        enabled=True, timezone="America/New_York",
        range_short_start=dtime(14, 0), range_short_end=dtime(14, 15),
        range_long_start=dtime(14, 0), range_long_end=dtime(14, 30),
        trade_start=dtime(14, 15), trade_end=dtime(15, 0),
        risk_per_trade_percent=None, max_trades_per_day=None, max_losses_per_day=None, params=None,
    )

    count = db_session.query(EpicConfig).filter(EpicConfig.epic == "US100").count()
    assert count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_epics.py -v`
Expected: FAIL — `TypeError: upsert_epic_config() missing 1 required positional argument: 'strategy'` (and seed count mismatch).

- [ ] **Step 3: Rewrite `app/epics.py`**

Replace the entire file with:

```python
from __future__ import annotations

import os
from datetime import time

from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base
from app.models import EpicConfig

CURATED_TIMEZONES = [
    "America/New_York",
    "America/Chicago",
    "Europe/London",
    "Europe/Frankfurt",
    "Asia/Tokyo",
    "Asia/Singapore",
    "Australia/Sydney",
    "UTC",
]

STRATEGY_SWEEP_FVG_OPENING_RANGE = "SWEEP_FVG_OPENING_RANGE"
STRATEGY_SWEEP_FVG_PDH_PDL = "SWEEP_FVG_PDH_PDL"
STRATEGY_VWAP_MEAN_REVERSION = "VWAP_MEAN_REVERSION"

ALL_STRATEGIES = [
    STRATEGY_SWEEP_FVG_OPENING_RANGE,
    STRATEGY_SWEEP_FVG_PDH_PDL,
    STRATEGY_VWAP_MEAN_REVERSION,
]

# Original strategy: enabled state driven by CAPITAL_EPICS, as before.
_SEED_EPICS = [
    {
        "epic": "US100",
        "strategy": STRATEGY_SWEEP_FVG_OPENING_RANGE,
        "timezone": "America/New_York",
        "session_name": "NY Open",
        "range_short_start": time(9, 30),
        "range_short_end": time(9, 45),
        "range_long_start": time(9, 30),
        "range_long_end": time(10, 0),
        "trade_start": time(9, 45),
        "trade_end": time(10, 30),
    },
    {
        "epic": "NATURALGAS",
        "strategy": STRATEGY_SWEEP_FVG_OPENING_RANGE,
        "timezone": "America/New_York",
        "session_name": "NY Open",
        "range_short_start": time(9, 30),
        "range_short_end": time(9, 45),
        "range_long_start": time(9, 30),
        "range_long_end": time(10, 0),
        "trade_start": time(9, 45),
        "trade_end": time(10, 30),
    },
    {
        "epic": "UK100",
        "strategy": STRATEGY_SWEEP_FVG_OPENING_RANGE,
        "timezone": "Europe/London",
        "session_name": "London",
        "range_short_start": time(8, 0),
        "range_short_end": time(8, 15),
        "range_long_start": time(8, 0),
        "range_long_end": time(8, 30),
        "trade_start": time(8, 15),
        "trade_end": time(9, 0),
    },
    {
        "epic": "GOLD",
        "strategy": STRATEGY_SWEEP_FVG_OPENING_RANGE,
        "timezone": "Europe/London",
        "session_name": "London",
        "range_short_start": time(8, 0),
        "range_short_end": time(8, 15),
        "range_long_start": time(8, 0),
        "range_long_end": time(8, 30),
        "trade_start": time(8, 15),
        "trade_end": time(9, 0),
    },
    {
        "epic": "USDJPY",
        "strategy": STRATEGY_SWEEP_FVG_OPENING_RANGE,
        "timezone": "Asia/Tokyo",
        "session_name": "Tokyo",
        "range_short_start": time(9, 0),
        "range_short_end": time(9, 15),
        "range_long_start": time(9, 0),
        "range_long_end": time(9, 30),
        "trade_start": time(9, 15),
        "trade_end": time(10, 0),
    },
]

# New strategies: always seeded disabled, regardless of CAPITAL_EPICS.
_SEED_EPICS_ADDITIONAL = [
    # Extra ICT session windows on the existing strategy
    {
        "epic": "US100", "strategy": STRATEGY_SWEEP_FVG_OPENING_RANGE, "timezone": "America/New_York",
        "session_name": "NY PM", "range_short_start": time(14, 0), "range_short_end": time(14, 15),
        "range_long_start": time(14, 0), "range_long_end": time(14, 30),
        "trade_start": time(14, 15), "trade_end": time(15, 0),
    },
    {
        "epic": "NATURALGAS", "strategy": STRATEGY_SWEEP_FVG_OPENING_RANGE, "timezone": "America/New_York",
        "session_name": "NY PM", "range_short_start": time(14, 0), "range_short_end": time(14, 15),
        "range_long_start": time(14, 0), "range_long_end": time(14, 30),
        "trade_start": time(14, 15), "trade_end": time(15, 0),
    },
    {
        "epic": "UK100", "strategy": STRATEGY_SWEEP_FVG_OPENING_RANGE, "timezone": "Europe/London",
        "session_name": "London Close", "range_short_start": time(15, 0), "range_short_end": time(15, 15),
        "range_long_start": time(15, 0), "range_long_end": time(15, 30),
        "trade_start": time(15, 15), "trade_end": time(16, 0),
    },
    {
        "epic": "GOLD", "strategy": STRATEGY_SWEEP_FVG_OPENING_RANGE, "timezone": "Europe/London",
        "session_name": "London Close", "range_short_start": time(15, 0), "range_short_end": time(15, 15),
        "range_long_start": time(15, 0), "range_long_end": time(15, 30),
        "trade_start": time(15, 15), "trade_end": time(16, 0),
    },
    # PDH/PDL sweep + FVG, all 5 epics, scans nearly all day
    {
        "epic": "US100", "strategy": STRATEGY_SWEEP_FVG_PDH_PDL, "timezone": "America/New_York",
        "session_name": "PDH/PDL All Day", "range_short_start": None, "range_short_end": None,
        "range_long_start": None, "range_long_end": None,
        "trade_start": time(0, 5), "trade_end": time(23, 55),
    },
    {
        "epic": "NATURALGAS", "strategy": STRATEGY_SWEEP_FVG_PDH_PDL, "timezone": "America/New_York",
        "session_name": "PDH/PDL All Day", "range_short_start": None, "range_short_end": None,
        "range_long_start": None, "range_long_end": None,
        "trade_start": time(0, 5), "trade_end": time(23, 55),
    },
    {
        "epic": "UK100", "strategy": STRATEGY_SWEEP_FVG_PDH_PDL, "timezone": "Europe/London",
        "session_name": "PDH/PDL All Day", "range_short_start": None, "range_short_end": None,
        "range_long_start": None, "range_long_end": None,
        "trade_start": time(0, 5), "trade_end": time(23, 55),
    },
    {
        "epic": "GOLD", "strategy": STRATEGY_SWEEP_FVG_PDH_PDL, "timezone": "Europe/London",
        "session_name": "PDH/PDL All Day", "range_short_start": None, "range_short_end": None,
        "range_long_start": None, "range_long_end": None,
        "trade_start": time(0, 5), "trade_end": time(23, 55),
    },
    {
        "epic": "USDJPY", "strategy": STRATEGY_SWEEP_FVG_PDH_PDL, "timezone": "Asia/Tokyo",
        "session_name": "PDH/PDL All Day", "range_short_start": None, "range_short_end": None,
        "range_long_start": None, "range_long_end": None,
        "trade_start": time(0, 5), "trade_end": time(23, 55),
    },
    # VWAP mean-reversion, all 5 epics, quiet mid-session windows
    {
        "epic": "US100", "strategy": STRATEGY_VWAP_MEAN_REVERSION, "timezone": "America/New_York",
        "session_name": "NY Midday VWAP", "range_short_start": None, "range_short_end": None,
        "range_long_start": None, "range_long_end": None,
        "trade_start": time(11, 0), "trade_end": time(13, 30),
        "params": {"deviation_threshold": 1.5},
    },
    {
        "epic": "NATURALGAS", "strategy": STRATEGY_VWAP_MEAN_REVERSION, "timezone": "America/New_York",
        "session_name": "NY Midday VWAP", "range_short_start": None, "range_short_end": None,
        "range_long_start": None, "range_long_end": None,
        "trade_start": time(11, 0), "trade_end": time(13, 30),
        "params": {"deviation_threshold": 1.5},
    },
    {
        "epic": "UK100", "strategy": STRATEGY_VWAP_MEAN_REVERSION, "timezone": "Europe/London",
        "session_name": "London Midday VWAP", "range_short_start": None, "range_short_end": None,
        "range_long_start": None, "range_long_end": None,
        "trade_start": time(10, 0), "trade_end": time(12, 0),
        "params": {"deviation_threshold": 1.5},
    },
    {
        "epic": "GOLD", "strategy": STRATEGY_VWAP_MEAN_REVERSION, "timezone": "Europe/London",
        "session_name": "London Midday VWAP", "range_short_start": None, "range_short_end": None,
        "range_long_start": None, "range_long_end": None,
        "trade_start": time(10, 0), "trade_end": time(12, 0),
        "params": {"deviation_threshold": 1.5},
    },
    {
        "epic": "USDJPY", "strategy": STRATEGY_VWAP_MEAN_REVERSION, "timezone": "Asia/Tokyo",
        "session_name": "Tokyo Lunch VWAP", "range_short_start": None, "range_short_end": None,
        "range_long_start": None, "range_long_end": None,
        "trade_start": time(11, 0), "trade_end": time(13, 0),
        "params": {"deviation_threshold": 1.5},
    },
]


def _parse_capital_epics() -> list[str]:
    multi = os.getenv("CAPITAL_EPICS", "")
    if multi:
        return [e.strip() for e in multi.split(",") if e.strip()]
    single = os.getenv("CAPITAL_EPIC")
    return [single] if single else []


def ensure_seeded(db: Session) -> None:
    Base.metadata.create_all(bind=db.get_bind())

    if db.query(EpicConfig).count() > 0:
        return

    enabled_epics = set(_parse_capital_epics())
    for seed in _SEED_EPICS:
        db.add(EpicConfig(enabled=seed["epic"] in enabled_epics, **seed))
    for seed in _SEED_EPICS_ADDITIONAL:
        db.add(EpicConfig(enabled=False, **seed))
    db.commit()


def list_all_epics(db: Session) -> list[EpicConfig]:
    ensure_seeded(db)
    return db.query(EpicConfig).order_by(EpicConfig.epic.asc(), EpicConfig.strategy.asc()).all()


def list_enabled_epics(db: Session) -> list[EpicConfig]:
    ensure_seeded(db)
    return (
        db.query(EpicConfig)
        .filter(EpicConfig.enabled.is_(True))
        .order_by(EpicConfig.epic.asc(), EpicConfig.strategy.asc())
        .all()
    )


def get_epic_config(db: Session, epic: str, strategy: str, session_name: str) -> EpicConfig | None:
    Base.metadata.create_all(bind=db.get_bind())
    return (
        db.query(EpicConfig)
        .filter(
            EpicConfig.epic == epic,
            EpicConfig.strategy == strategy,
            EpicConfig.session_name == session_name,
        )
        .first()
    )


def upsert_epic_config(db: Session, epic: str, strategy: str, session_name: str, **fields) -> EpicConfig:
    cfg = get_epic_config(db, epic, strategy, session_name)
    if cfg is None:
        cfg = EpicConfig(epic=epic, strategy=strategy, session_name=session_name, **fields)
        db.add(cfg)
    else:
        for key, value in fields.items():
            setattr(cfg, key, value)

    db.commit()
    db.refresh(cfg)
    return cfg


def delete_epic_config(db: Session, epic: str, strategy: str, session_name: str) -> bool:
    cfg = get_epic_config(db, epic, strategy, session_name)
    if cfg is None:
        return False
    db.delete(cfg)
    db.commit()
    return True


def effective_risk(cfg: EpicConfig, settings: Settings) -> tuple[float, int, int]:
    risk_percent = (
        float(cfg.risk_per_trade_percent)
        if cfg.risk_per_trade_percent is not None
        else settings.risk_per_trade_percent
    )
    max_trades = (
        cfg.max_trades_per_day if cfg.max_trades_per_day is not None else settings.max_trades_per_day
    )
    max_losses = (
        cfg.max_losses_per_day if cfg.max_losses_per_day is not None else settings.max_losses_per_day
    )
    return risk_percent, max_trades, max_losses
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_epics.py -v`
Expected: PASS (all tests including the 2 new ones)

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest -q`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/epics.py tests/test_epics.py
git commit -m "feat: add strategy constants and (epic, strategy, session_name)-keyed epic config CRUD"
```

---

## Task 3: Migration script for the live SQLite database

**Files:**
- Create: `scripts/migrate_multi_strategy.py`
- Test: `tests/test_migrate_multi_strategy.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (works at raw-SQL level against the pre-Task-1 schema shape)
- Produces: `migrate_epic_configs(conn, inspector)`, `migrate_signals(conn, inspector)`, `main()` — run once against `trading_bot.db` to bring the live database in line with the Task 1 model.

- [ ] **Step 1: Write the failing test**

Create `tests/test_migrate_multi_strategy.py`:

```python
from sqlalchemy import create_engine, inspect, text

from scripts.migrate_multi_strategy import migrate_epic_configs, migrate_signals


def _build_old_schema(engine):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE epic_configs (
                    id INTEGER PRIMARY KEY,
                    epic VARCHAR(50) NOT NULL UNIQUE,
                    enabled BOOLEAN NOT NULL,
                    timezone VARCHAR(50) NOT NULL,
                    session_name VARCHAR(100) NOT NULL,
                    range_short_start TIME NOT NULL,
                    range_short_end TIME NOT NULL,
                    range_long_start TIME NOT NULL,
                    range_long_end TIME NOT NULL,
                    trade_start TIME NOT NULL,
                    trade_end TIME NOT NULL,
                    risk_per_trade_percent NUMERIC(5, 2),
                    max_trades_per_day INTEGER,
                    max_losses_per_day INTEGER,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO epic_configs (
                    id, epic, enabled, timezone, session_name,
                    range_short_start, range_short_end, range_long_start, range_long_end,
                    trade_start, trade_end, created_at, updated_at
                ) VALUES (
                    1, 'US100', 1, 'America/New_York', 'NY Open',
                    '09:30:00', '09:45:00', '09:30:00', '10:00:00',
                    '09:45:00', '10:30:00', '2026-01-01 00:00:00', '2026-01-01 00:00:00'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE signals (
                    id INTEGER PRIMARY KEY,
                    symbol VARCHAR(50) NOT NULL,
                    signal_time DATETIME NOT NULL,
                    direction VARCHAR(10) NOT NULL,
                    setup_type VARCHAR(100) NOT NULL,
                    status VARCHAR(50) NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO signals (id, symbol, signal_time, direction, setup_type, status, created_at)
                VALUES (1, 'US100', '2026-01-01 09:50:00', 'BUY',
                        'NY Open Sweep + FVG AUTO_PAPER (15-min opening range)', 'DETECTED',
                        '2026-01-01 09:50:00')
                """
            )
        )


def test_migrate_epic_configs_preserves_data_and_backfills_strategy():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    _build_old_schema(engine)

    with engine.begin() as conn:
        migrate_epic_configs(conn, inspect(conn))

    with engine.begin() as conn:
        rows = conn.execute(text("SELECT epic, strategy, session_name FROM epic_configs")).fetchall()
        assert rows == [("US100", "SWEEP_FVG_OPENING_RANGE", "NY Open")]


def test_migrate_epic_configs_is_idempotent():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    _build_old_schema(engine)

    with engine.begin() as conn:
        migrate_epic_configs(conn, inspect(conn))
    with engine.begin() as conn:
        migrate_epic_configs(conn, inspect(conn))  # must no-op, not raise
        count = conn.execute(text("SELECT COUNT(*) FROM epic_configs")).fetchone()[0]
        assert count == 1


def test_migrate_signals_backfills_strategy():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    _build_old_schema(engine)

    with engine.begin() as conn:
        migrate_signals(conn, inspect(conn))

    with engine.begin() as conn:
        row = conn.execute(text("SELECT strategy FROM signals WHERE id = 1")).fetchone()
        assert row[0] == "SWEEP_FVG_OPENING_RANGE"


def test_migrate_signals_is_idempotent():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    _build_old_schema(engine)

    with engine.begin() as conn:
        migrate_signals(conn, inspect(conn))
    with engine.begin() as conn:
        migrate_signals(conn, inspect(conn))  # must no-op, not raise
        row = conn.execute(text("SELECT strategy FROM signals WHERE id = 1")).fetchone()
        assert row[0] == "SWEEP_FVG_OPENING_RANGE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_migrate_multi_strategy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.migrate_multi_strategy'`

- [ ] **Step 3: Create `scripts/migrate_multi_strategy.py`**

```python
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import inspect, text

from app.db import engine


def _column_names(inspector, table_name) -> set[str]:
    return {col["name"] for col in inspector.get_columns(table_name)}


def migrate_epic_configs(conn, inspector) -> None:
    columns = _column_names(inspector, "epic_configs")
    if "strategy" in columns:
        print("epic_configs already migrated. Skipping.")
        return

    print("Migrating epic_configs to support multiple strategies per epic...")
    conn.execute(text("ALTER TABLE epic_configs RENAME TO epic_configs_old"))
    conn.execute(
        text(
            """
            CREATE TABLE epic_configs (
                id INTEGER PRIMARY KEY,
                epic VARCHAR(50) NOT NULL,
                strategy VARCHAR(50) NOT NULL DEFAULT 'SWEEP_FVG_OPENING_RANGE',
                enabled BOOLEAN NOT NULL,
                timezone VARCHAR(50) NOT NULL,
                session_name VARCHAR(100) NOT NULL,
                range_short_start TIME,
                range_short_end TIME,
                range_long_start TIME,
                range_long_end TIME,
                trade_start TIME NOT NULL,
                trade_end TIME NOT NULL,
                risk_per_trade_percent NUMERIC(5, 2),
                max_trades_per_day INTEGER,
                max_losses_per_day INTEGER,
                params JSON,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                CONSTRAINT uq_epic_strategy_session UNIQUE (epic, strategy, session_name)
            )
            """
        )
    )
    conn.execute(
        text(
            """
            INSERT INTO epic_configs (
                id, epic, strategy, enabled, timezone, session_name,
                range_short_start, range_short_end, range_long_start, range_long_end,
                trade_start, trade_end, risk_per_trade_percent, max_trades_per_day,
                max_losses_per_day, created_at, updated_at
            )
            SELECT
                id, epic, 'SWEEP_FVG_OPENING_RANGE', enabled, timezone, session_name,
                range_short_start, range_short_end, range_long_start, range_long_end,
                trade_start, trade_end, risk_per_trade_percent, max_trades_per_day,
                max_losses_per_day, created_at, updated_at
            FROM epic_configs_old
            """
        )
    )
    conn.execute(text("DROP TABLE epic_configs_old"))
    print("epic_configs migrated.")


def migrate_signals(conn, inspector) -> None:
    columns = _column_names(inspector, "signals")
    if "strategy" in columns:
        print("signals already migrated. Skipping.")
        return

    print("Adding strategy column to signals...")
    conn.execute(
        text("ALTER TABLE signals ADD COLUMN strategy VARCHAR(50) NOT NULL DEFAULT 'SWEEP_FVG_OPENING_RANGE'")
    )
    print("signals migrated.")


def main() -> None:
    with engine.begin() as conn:
        inspector = inspect(conn)
        migrate_epic_configs(conn, inspector)
        migrate_signals(conn, inspector)
    print("Migration complete.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_migrate_multi_strategy.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the migration against the real dev database**

Run: `python3 scripts/migrate_multi_strategy.py`
Expected output: `Migrating epic_configs to support multiple strategies per epic...` / `epic_configs migrated.` / `Adding strategy column to signals...` / `signals migrated.` / `Migration complete.`

Then verify:

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('trading_bot.db')
cur = conn.cursor()
cur.execute('PRAGMA table_info(epic_configs)')
print([r[1] for r in cur.fetchall()])
cur.execute('SELECT epic, strategy, session_name FROM epic_configs')
print(cur.fetchall())
cur.execute('SELECT strategy FROM signals')
print(cur.fetchall())
"
```

Expected: `epic_configs` columns include `strategy` and `params`; the existing row shows `strategy='SWEEP_FVG_OPENING_RANGE'`; the existing signal shows `strategy='SWEEP_FVG_OPENING_RANGE'`.

- [ ] **Step 6: Run migration again to confirm idempotency against the real file**

Run: `python3 scripts/migrate_multi_strategy.py`
Expected: `epic_configs already migrated. Skipping.` / `signals already migrated. Skipping.` / `Migration complete.` — no errors, no duplicate rows.

- [ ] **Step 7: Run full test suite**

Run: `python3 -m pytest -q`
Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add scripts/migrate_multi_strategy.py tests/test_migrate_multi_strategy.py
git commit -m "feat: add idempotent migration for multi-strategy epic_configs and signals schema"
```

---

## Task 4: Previous-day range window helper (for PDH/PDL)

**Files:**
- Create: `app/strategy/previous_day_range.py`
- Test: `tests/test_previous_day_range.py`

**Interfaces:**
- Produces: `previous_day_range_window(session_date, timezone: str) -> tuple[datetime, datetime]` — returns UTC-naive `(start, end)` bounds for the previous full calendar day (00:00-23:59:59) in the given timezone. Feed directly into the existing `get_range(db, symbol, "M1", start, end)` helper in `scripts/run_auto_paper_once.py` (same pattern already used there for the overnight/opening-range windows).

- [ ] **Step 1: Write the failing test**

Create `tests/test_previous_day_range.py`:

```python
from datetime import date, datetime

from app.strategy.previous_day_range import previous_day_range_window


def test_previous_day_range_window_returns_utc_naive_bounds_for_ny():
    session_date = date(2026, 6, 2)  # Tuesday

    start_utc, end_utc = previous_day_range_window(session_date, "America/New_York")

    # 2026-06-01 00:00 America/New_York == 2026-06-01 04:00 UTC (EDT, UTC-4)
    assert start_utc == datetime(2026, 6, 1, 4, 0)
    # 2026-06-02 00:00 America/New_York == 2026-06-02 04:00 UTC
    assert end_utc == datetime(2026, 6, 2, 4, 0)


def test_previous_day_range_window_spans_exactly_24_hours():
    session_date = date(2026, 1, 15)

    start_utc, end_utc = previous_day_range_window(session_date, "Europe/London")

    assert (end_utc - start_utc).total_seconds() == 24 * 3600


def test_previous_day_range_window_returns_naive_datetimes():
    start_utc, end_utc = previous_day_range_window(date(2026, 6, 2), "Asia/Tokyo")

    assert start_utc.tzinfo is None
    assert end_utc.tzinfo is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_previous_day_range.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.strategy.previous_day_range'`

- [ ] **Step 3: Create `app/strategy/previous_day_range.py`**

```python
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")


def previous_day_range_window(session_date, timezone: str) -> tuple[datetime, datetime]:
    """UTC-naive (start, end) bounds for the previous full calendar day in `timezone`.

    Distinct from the existing "overnight" window (18:00 prev day -> 09:30) used by
    the opening-range strategy: this covers the full 00:00-23:59:59 prior day, for
    the PDH/PDL sweep+FVG strategy.
    """
    tz = ZoneInfo(timezone)
    start_local = datetime.combine(session_date - timedelta(days=1), time(0, 0), tzinfo=tz)
    end_local = datetime.combine(session_date, time(0, 0), tzinfo=tz)
    return (
        start_local.astimezone(UTC).replace(tzinfo=None),
        end_local.astimezone(UTC).replace(tzinfo=None),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_previous_day_range.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/strategy/previous_day_range.py tests/test_previous_day_range.py
git commit -m "feat: add previous-calendar-day range window helper for PDH/PDL strategy"
```

---

## Task 5: VWAP band calculator (for VWAP mean-reversion)

**Files:**
- Create: `app/strategy/vwap.py`
- Test: `tests/test_vwap.py`

**Interfaces:**
- Produces: `calculate_vwap_bands(candles: list[dict], deviation_threshold: float, min_lookback: int = 5) -> list[dict | None]` — one entry per input candle (same length as `candles`, index-aligned), `None` until `min_lookback` candles have accumulated, then `{"candle_time", "vwap", "stddev", "high", "low"}` where `high`/`low` are in the exact shape `detect_sweep` expects (`opening_range_high`/`opening_range_low`-equivalent).

- [ ] **Step 1: Write the failing test**

Create `tests/test_vwap.py`:

```python
import pytest

from app.strategy.vwap import calculate_vwap_bands


def _candle(h, l, c, v=100):
    return {"high": h, "low": l, "close": c, "volume": v}


def test_calculate_vwap_bands_returns_none_before_min_lookback():
    candles = [_candle(101, 99, 100) for _ in range(4)]
    bands = calculate_vwap_bands(candles, deviation_threshold=1.5)

    assert bands == [None, None, None, None]


def test_calculate_vwap_bands_produces_band_once_lookback_satisfied():
    candles = [_candle(101, 99, 100) for _ in range(5)]
    bands = calculate_vwap_bands(candles, deviation_threshold=1.5)

    assert bands[0] is None
    assert bands[4] is not None
    assert bands[4]["vwap"] == pytest.approx(100.0)
    assert bands[4]["high"] >= bands[4]["vwap"]
    assert bands[4]["low"] <= bands[4]["vwap"]


def test_calculate_vwap_bands_widens_with_more_deviation():
    candles = [_candle(105, 95, 100 + i) for i in range(6)]
    narrow = calculate_vwap_bands(candles, deviation_threshold=1.0)
    wide = calculate_vwap_bands(candles, deviation_threshold=3.0)

    assert (wide[5]["high"] - wide[5]["low"]) > (narrow[5]["high"] - narrow[5]["low"])


def test_calculate_vwap_bands_falls_back_to_equal_weight_when_volume_missing():
    candles = [_candle(101, 99, 100, v=0) for _ in range(5)]
    bands = calculate_vwap_bands(candles, deviation_threshold=1.5)

    assert bands[4]["vwap"] == pytest.approx(100.0)


def test_calculate_vwap_bands_same_length_as_input():
    candles = [_candle(101, 99, 100) for _ in range(8)]
    bands = calculate_vwap_bands(candles, deviation_threshold=1.5)

    assert len(bands) == len(candles)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_vwap.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.strategy.vwap'`

- [ ] **Step 3: Create `app/strategy/vwap.py`**

```python
def calculate_vwap_bands(candles: list[dict], deviation_threshold: float, min_lookback: int = 5) -> list[dict | None]:
    """Session-anchored VWAP with rolling deviation bands, one entry per candle.

    `candles` must be in chronological order starting from the session anchor
    (e.g. the strategy's trade_start). VWAP and stddev are cumulative from
    candles[0] through the current candle. Returns None for the first
    `min_lookback - 1` candles, since a band computed from too few candles
    is not a meaningful reference level.
    """
    bands: list[dict | None] = []
    cum_pv = 0.0
    cum_vol = 0.0
    typical_prices: list[float] = []

    for i, candle in enumerate(candles):
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        # Some CFD instruments report zero/missing volume; fall back to an
        # equal-weighted average so VWAP degrades gracefully instead of
        # dividing by zero.
        volume = float(candle.get("volume") or 0) or 1.0
        typical_price = (high + low + close) / 3.0

        cum_pv += typical_price * volume
        cum_vol += volume
        vwap = cum_pv / cum_vol
        typical_prices.append(typical_price)

        if i + 1 < min_lookback:
            bands.append(None)
            continue

        mean = sum(typical_prices) / len(typical_prices)
        variance = sum((p - mean) ** 2 for p in typical_prices) / len(typical_prices)
        stddev = variance ** 0.5

        bands.append(
            {
                "candle_time": candle.get("candle_time") or candle.get("time"),
                "vwap": vwap,
                "stddev": stddev,
                "high": vwap + deviation_threshold * stddev,
                "low": vwap - deviation_threshold * stddev,
            }
        )

    return bands
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_vwap.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/strategy/vwap.py tests/test_vwap.py
git commit -m "feat: add session-anchored VWAP band calculator for mean-reversion strategy"
```

---

## Task 6: RiskManager target-based exit for VWAP fades

**Files:**
- Modify: `app/risk/risk_manager.py`
- Test: `tests/test_risk_manager.py` (new)

**Interfaces:**
- Consumes: `TradePlan` (existing dataclass, unchanged)
- Produces: `RiskManager.build_trade_plan_with_target(symbol, direction, entry_price, stop_loss, take_profit) -> TradePlan` — same shape as the existing `build_trade_plan`, but takes an explicit take-profit (the VWAP price) instead of deriving it from `min_risk_reward`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_risk_manager.py`:

```python
import pytest

from app.risk.risk_manager import RiskManager


def test_build_trade_plan_with_target_buy():
    plan = RiskManager().build_trade_plan_with_target(
        "US100", "BUY", entry_price=100.0, stop_loss=98.0, take_profit=103.0
    )

    assert plan.entry_price == 100.0
    assert plan.stop_loss == 98.0
    assert plan.take_profit == 103.0
    assert plan.risk_reward == pytest.approx(1.5)


def test_build_trade_plan_with_target_sell():
    plan = RiskManager().build_trade_plan_with_target(
        "US100", "SELL", entry_price=100.0, stop_loss=102.0, take_profit=97.0
    )

    assert plan.risk_reward == pytest.approx(1.5)


def test_build_trade_plan_with_target_rejects_non_positive_risk():
    with pytest.raises(ValueError):
        RiskManager().build_trade_plan_with_target(
            "US100", "BUY", entry_price=100.0, stop_loss=100.0, take_profit=103.0
        )


def test_build_trade_plan_with_target_rejects_non_positive_reward():
    with pytest.raises(ValueError):
        RiskManager().build_trade_plan_with_target(
            "US100", "BUY", entry_price=100.0, stop_loss=98.0, take_profit=100.0
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_risk_manager.py -v`
Expected: FAIL — `AttributeError: 'RiskManager' object has no attribute 'build_trade_plan_with_target'`

- [ ] **Step 3: Add the method to `app/risk/risk_manager.py`**

Add this method to the `RiskManager` class, after the existing `build_trade_plan` method (currently ends at line 46):

```python
    def build_trade_plan_with_target(
        self, symbol: str, direction: Literal["BUY", "SELL"], entry_price: float, stop_loss: float, take_profit: float
    ) -> TradePlan:
        if direction == "BUY":
            risk = entry_price - stop_loss
            reward = take_profit - entry_price
        else:
            risk = stop_loss - entry_price
            reward = entry_price - take_profit

        if risk <= 0:
            raise ValueError("Invalid trade plan: risk must be greater than zero.")
        if reward <= 0:
            raise ValueError("Invalid trade plan: reward must be greater than zero.")

        return TradePlan(
            symbol=symbol,
            direction=direction,
            entry_price=round(entry_price, 5),
            stop_loss=round(stop_loss, 5),
            take_profit=round(take_profit, 5),
            risk_reward=round(reward / risk, 4),
            risk_percent=self.settings.risk_per_trade_percent,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_risk_manager.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest -q`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/risk/risk_manager.py tests/test_risk_manager.py
git commit -m "feat: add target-based trade plan for VWAP mean-reversion exits"
```

---

## Task 7: Portfolio-wide open-risk ceiling

**Files:**
- Modify: `app/config.py` (add setting), `.env`, `.env.example`
- Modify: `app/paper/auto_paper.py` (add function + import)
- Test: `tests/test_auto_paper.py` (new)

**Interfaces:**
- Produces: `settings.max_portfolio_risk_percent` (float, default 3.0). `total_open_risk_percent(db, account) -> float` — sums `risk_amount` across all `PENDING`/`ACTIVE` trades (any epic, any strategy) as a percentage of current balance.

- [ ] **Step 1: Write the failing test**

Create `tests/test_auto_paper.py`:

```python
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import PaperAccount, PaperTrade
from app.paper.auto_paper import total_open_risk_percent


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_account(db, balance=1000):
    account = PaperAccount(name="default", currency="USD", starting_balance=balance, balance=balance, equity=balance)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def test_total_open_risk_percent_sums_pending_and_active_only(db_session):
    account = _make_account(db_session, balance=1000)
    db_session.add_all(
        [
            PaperTrade(symbol="US100", direction="BUY", status="PENDING", risk_amount=Decimal("10")),
            PaperTrade(symbol="GOLD", direction="SELL", status="ACTIVE", risk_amount=Decimal("15")),
            PaperTrade(symbol="UK100", direction="BUY", status="CLOSED", risk_amount=Decimal("100")),
        ]
    )
    db_session.commit()

    result = total_open_risk_percent(db_session, account)

    assert result == pytest.approx(2.5)  # (10 + 15) / 1000 * 100


def test_total_open_risk_percent_is_zero_with_no_open_trades(db_session):
    account = _make_account(db_session, balance=1000)
    result = total_open_risk_percent(db_session, account)
    assert result == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_auto_paper.py -v`
Expected: FAIL — `ImportError: cannot import name 'total_open_risk_percent' from 'app.paper.auto_paper'`

- [ ] **Step 3: Add the setting to `app/config.py`**

In the `Settings` class, right after the existing `min_risk_reward` line (line 27):

```python
    min_risk_reward: float = Field(default=2.0, ge=0.1)
    max_portfolio_risk_percent: float = Field(default=3.0, ge=0.1, le=100)
```

Add to `.env` (after `MIN_RISK_REWARD=2.0`) and `.env.example` (same spot):

```
MAX_PORTFOLIO_RISK_PERCENT=3.0
```

- [ ] **Step 4: Add `total_open_risk_percent` to `app/paper/auto_paper.py`**

Add `from sqlalchemy import func` to the imports at the top of the file (alongside the existing `from sqlalchemy.orm import Session`).

Add this function anywhere after `ensure_paper_account` (e.g. right after it):

```python
def total_open_risk_percent(db: Session, account: PaperAccount) -> float:
    open_risk = (
        db.query(func.coalesce(func.sum(PaperTrade.risk_amount), 0))
        .filter(PaperTrade.status.in_(["PENDING", "ACTIVE"]))
        .scalar()
    )
    balance = float(account.balance)
    if balance <= 0:
        return 0.0
    return float(open_risk) / balance * 100
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_auto_paper.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run full suite**

Run: `python3 -m pytest -q`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/config.py .env .env.example app/paper/auto_paper.py tests/test_auto_paper.py
git commit -m "feat: add portfolio-wide open-risk ceiling calculation"
```

---

## Task 8: Per-(epic, strategy) scoping for trade/loss counters

**Files:**
- Modify: `app/paper/auto_paper.py`
- Modify: `tests/test_auto_paper.py`

**Interfaces:**
- Consumes: `Signal.strategy` (Task 1)
- Produces: `get_open_trades(db, symbol=None, strategy=None)`, `trades_today_count(db, symbol=None, strategy=None)`, `losses_today_count(db, symbol=None, strategy=None)`, `stop_today_key(epic=None, strategy=None)`, `is_stopped_today(db, epic=None, strategy=None)`, `stop_trading_today(db, epic=None, strategy=None)` — all backward compatible: omitting `strategy` preserves today's exact epic-only (or global) behavior.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auto_paper.py`:

```python
from datetime import datetime

from app.models import Signal


def _make_signal(db, symbol, strategy):
    signal = Signal(
        symbol=symbol,
        signal_time=datetime.utcnow(),
        direction="BUY",
        setup_type="test",
        strategy=strategy,
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)
    return signal


def test_get_open_trades_scoped_by_strategy_does_not_see_other_strategy(db_session):
    from app.paper.auto_paper import get_open_trades

    sig_a = _make_signal(db_session, "US100", "SWEEP_FVG_OPENING_RANGE")
    sig_b = _make_signal(db_session, "US100", "SWEEP_FVG_PDH_PDL")
    db_session.add_all(
        [
            PaperTrade(signal_id=sig_a.id, symbol="US100", direction="BUY", status="ACTIVE"),
            PaperTrade(signal_id=sig_b.id, symbol="US100", direction="BUY", status="PENDING"),
        ]
    )
    db_session.commit()

    opening_range_trades = get_open_trades(db_session, "US100", "SWEEP_FVG_OPENING_RANGE")
    pdh_pdl_trades = get_open_trades(db_session, "US100", "SWEEP_FVG_PDH_PDL")
    all_trades_for_epic = get_open_trades(db_session, "US100")

    assert len(opening_range_trades) == 1
    assert len(pdh_pdl_trades) == 1
    assert len(all_trades_for_epic) == 2


def test_trades_today_count_scoped_by_strategy(db_session):
    from app.paper.auto_paper import trades_today_count

    sig_a = _make_signal(db_session, "US100", "SWEEP_FVG_OPENING_RANGE")
    sig_b = _make_signal(db_session, "US100", "VWAP_MEAN_REVERSION")
    db_session.add_all(
        [
            PaperTrade(signal_id=sig_a.id, symbol="US100", direction="BUY", status="CLOSED"),
            PaperTrade(signal_id=sig_b.id, symbol="US100", direction="SELL", status="CLOSED"),
        ]
    )
    db_session.commit()

    assert trades_today_count(db_session, "US100", "SWEEP_FVG_OPENING_RANGE") == 1
    assert trades_today_count(db_session, "US100", "VWAP_MEAN_REVERSION") == 1
    assert trades_today_count(db_session, "US100") == 2


def test_losses_today_count_scoped_by_strategy(db_session):
    from app.paper.auto_paper import losses_today_count

    sig_a = _make_signal(db_session, "US100", "SWEEP_FVG_OPENING_RANGE")
    sig_b = _make_signal(db_session, "US100", "VWAP_MEAN_REVERSION")
    db_session.add_all(
        [
            PaperTrade(signal_id=sig_a.id, symbol="US100", direction="BUY", status="CLOSED", result="LOSS"),
            PaperTrade(signal_id=sig_b.id, symbol="US100", direction="SELL", status="CLOSED", result="WIN"),
        ]
    )
    db_session.commit()

    assert losses_today_count(db_session, "US100", "SWEEP_FVG_OPENING_RANGE") == 1
    assert losses_today_count(db_session, "US100", "VWAP_MEAN_REVERSION") == 0


def test_stop_trading_today_scoped_by_strategy_only_cancels_that_strategy(db_session):
    from app.paper.auto_paper import is_stopped_today, stop_trading_today

    sig_a = _make_signal(db_session, "US100", "SWEEP_FVG_OPENING_RANGE")
    sig_b = _make_signal(db_session, "US100", "VWAP_MEAN_REVERSION")
    trade_a = PaperTrade(signal_id=sig_a.id, symbol="US100", direction="BUY", status="PENDING")
    trade_b = PaperTrade(signal_id=sig_b.id, symbol="US100", direction="SELL", status="PENDING")
    db_session.add_all([trade_a, trade_b])
    db_session.commit()

    stop_trading_today(db_session, "US100", "SWEEP_FVG_OPENING_RANGE")

    db_session.refresh(trade_a)
    db_session.refresh(trade_b)
    assert trade_a.status == "CANCELLED"
    assert trade_b.status == "PENDING"
    assert is_stopped_today(db_session, "US100", "SWEEP_FVG_OPENING_RANGE") is True
    assert is_stopped_today(db_session, "US100", "VWAP_MEAN_REVERSION") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_auto_paper.py -v`
Expected: FAIL — `TypeError: get_open_trades() takes from 1 to 2 positional arguments but 3 were given`

- [ ] **Step 3: Update `app/paper/auto_paper.py`**

Add `Signal` to the existing `from app.models import BotState, Candle, PaperAccount, PaperTrade, Signal` import (already imported — no change needed there).

Replace `stop_today_key`, `is_stopped_today`, `stop_trading_today` (currently lines 90-112) with:

```python
def stop_today_key(epic: str | None = None, strategy: str | None = None) -> str:
    parts = ["stop_today"]
    if epic is not None:
        parts.append(epic)
    if strategy is not None:
        parts.append(strategy)
    parts.append(today_ny().isoformat())
    return "_".join(parts)


def is_stopped_today(db: Session, epic: str | None = None, strategy: str | None = None) -> bool:
    return get_state(db, stop_today_key(epic, strategy), "false").lower() == "true"


def _pending_trades_query(db: Session, epic: str | None = None, strategy: str | None = None):
    q = db.query(PaperTrade).filter(PaperTrade.status == "PENDING")
    if epic is not None:
        q = q.filter(PaperTrade.symbol == epic)
    if strategy is not None:
        q = q.join(Signal, PaperTrade.signal_id == Signal.id).filter(Signal.strategy == strategy)
    return q


def stop_trading_today(db: Session, epic: str | None = None, strategy: str | None = None) -> None:
    set_state(db, stop_today_key(epic, strategy), "true")
    if epic is None and strategy is None:
        cancel_pending_trades(db)
    else:
        trades = _pending_trades_query(db, epic, strategy).all()
        for trade in trades:
            trade.status = "CANCELLED"
            trade.result = "CANCELLED"
            trade.updated_at = utc_now()
        db.commit()
```

Replace `get_open_trades` (currently lines 134-138) with:

```python
def get_open_trades(db: Session, symbol: str | None = None, strategy: str | None = None) -> list[PaperTrade]:
    q = db.query(PaperTrade).filter(PaperTrade.status.in_(["PENDING", "ACTIVE"]))
    if symbol is not None:
        q = q.filter(PaperTrade.symbol == symbol)
    if strategy is not None:
        q = q.join(Signal, PaperTrade.signal_id == Signal.id).filter(Signal.strategy == strategy)
    return q.order_by(PaperTrade.created_at.asc()).all()
```

Replace `trades_today_count` and `losses_today_count` (currently lines 279-294) with:

```python
def trades_today_count(db: Session, symbol: str | None = None, strategy: str | None = None) -> int:
    start_ny = datetime.combine(today_ny(), datetime.min.time(), tzinfo=NY)
    start_utc = start_ny.astimezone(UTC).replace(tzinfo=None)
    q = db.query(PaperTrade).filter(PaperTrade.created_at >= start_utc)
    if symbol is not None:
        q = q.filter(PaperTrade.symbol == symbol)
    if strategy is not None:
        q = q.join(Signal, PaperTrade.signal_id == Signal.id).filter(Signal.strategy == strategy)
    return q.count()


def losses_today_count(db: Session, symbol: str | None = None, strategy: str | None = None) -> int:
    start_ny = datetime.combine(today_ny(), datetime.min.time(), tzinfo=NY)
    start_utc = start_ny.astimezone(UTC).replace(tzinfo=None)
    q = db.query(PaperTrade).filter(PaperTrade.created_at >= start_utc, PaperTrade.result == "LOSS")
    if symbol is not None:
        q = q.filter(PaperTrade.symbol == symbol)
    if strategy is not None:
        q = q.join(Signal, PaperTrade.signal_id == Signal.id).filter(Signal.strategy == strategy)
    return q.count()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_auto_paper.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest -q`
Expected: All tests pass — the dashboard's existing calls like `get_open_trades(db, epic)` and `stop_trading_today(db)` still work unchanged since `strategy` defaults to `None`.

- [ ] **Step 6: Commit**

```bash
git add app/paper/auto_paper.py tests/test_auto_paper.py
git commit -m "feat: scope trade/loss counters and stop-today state per (epic, strategy)"
```

---

## Task 9: Extract `run_opening_range_strategy` and add strategy dispatch to the orchestrator

**Files:**
- Modify: `scripts/run_auto_paper_once.py` (whole file rewrite)
- Test: `tests/test_run_auto_paper_once.py` (new)

**Interfaces:**
- Consumes: `effective_risk`, `list_enabled_epics` (Task 2), `total_open_risk_percent` (Task 7), `get_open_trades`/`trades_today_count`/`losses_today_count`/`is_stopped_today`/`stop_trading_today` with `strategy` param (Task 8)
- Produces: `run_opening_range_strategy(db, cfg, settings, sweep_buffer, stop_buffer, account, latest_candle, latest_local, session_date)`, `portfolio_risk_available(db, account, settings, risk_percent) -> bool`, `STRATEGY_HANDLERS` dict (one entry so far). This is a **behavior-preserving refactor** — with only `SWEEP_FVG_OPENING_RANGE` configs enabled, the bot must do exactly what it does today.

This task first captures the current behavior with a new regression test (since none existed for this script), then refactors.

- [ ] **Step 1: Write the regression test against current behavior**

Create `tests/test_run_auto_paper_once.py`:

```python
from datetime import date, datetime, time as dtime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.models import Candle, EpicConfig, PaperAccount, PaperTrade, Signal

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _insert_candle(db, symbol, timeframe, local_dt, h, l, c, v=100):
    db.add(
        Candle(
            symbol=symbol,
            timeframe=timeframe,
            candle_time=local_dt.astimezone(UTC).replace(tzinfo=None),
            open=c,
            high=h,
            low=l,
            close=c,
            volume=v,
        )
    )


def test_run_opening_range_strategy_creates_signal_and_trade(db_session):
    from scripts.run_auto_paper_once import run_opening_range_strategy

    session_date = date(2026, 6, 1)

    _insert_candle(db_session, "US100", "M1", datetime.combine(session_date, dtime(9, 0), tzinfo=NY), 101, 99, 100)
    _insert_candle(db_session, "US100", "M1", datetime.combine(session_date, dtime(9, 30), tzinfo=NY), 105, 95, 102)

    m5_specs = [
        (dtime(9, 45), 104, 99, 103),
        (dtime(9, 50), 106, 102, 104),
        (dtime(9, 55), 107, 101, 103),
        (dtime(10, 0), 108, 102, 104),
        (dtime(10, 5), 109, 105, 106),
        (dtime(10, 10), 107, 98, 99),
        (dtime(10, 15), 100, 95, 96),
    ]
    last_candle_time = None
    for t, h, l, c in m5_specs:
        local_dt = datetime.combine(session_date, t, tzinfo=NY)
        _insert_candle(db_session, "US100", "M5", local_dt, h, l, c)
        last_candle_time = local_dt.astimezone(UTC).replace(tzinfo=None)
    db_session.commit()

    account = PaperAccount(name="default", currency="USD", starting_balance=1000, balance=1000, equity=1000)
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    cfg = EpicConfig(
        epic="US100",
        strategy="SWEEP_FVG_OPENING_RANGE",
        enabled=True,
        timezone="America/New_York",
        session_name="NY Open",
        range_short_start=dtime(9, 30),
        range_short_end=dtime(9, 45),
        range_long_start=dtime(9, 30),
        range_long_end=dtime(11, 0),  # widened so this test's 10:15 "now" stays on the short-range branch
        trade_start=dtime(9, 45),
        trade_end=dtime(16, 0),
    )

    settings = Settings(risk_per_trade_percent=0.5, max_trades_per_day=1, max_losses_per_day=2, min_risk_reward=2.0)
    latest_candle = SimpleNamespace(candle_time=last_candle_time)
    latest_local = datetime.combine(session_date, dtime(10, 15), tzinfo=NY)

    run_opening_range_strategy(
        db_session, cfg, settings, sweep_buffer=0.0, stop_buffer=0.5,
        account=account, latest_candle=latest_candle, latest_local=latest_local, session_date=session_date,
    )

    signal = db_session.query(Signal).one()
    assert signal.strategy == "SWEEP_FVG_OPENING_RANGE"
    assert signal.symbol == "US100"
    assert signal.direction == "SELL"
    assert float(signal.entry_price) == pytest.approx(102.5)
    assert float(signal.stop_loss) == pytest.approx(106.5)
    assert float(signal.take_profit) == pytest.approx(94.5)
    assert float(signal.risk_reward) == pytest.approx(2.0)

    trade = db_session.query(PaperTrade).one()
    assert trade.signal_id == signal.id


def test_run_opening_range_strategy_does_nothing_outside_trade_window(db_session):
    from scripts.run_auto_paper_once import run_opening_range_strategy

    session_date = date(2026, 6, 1)
    account = PaperAccount(name="default", currency="USD", starting_balance=1000, balance=1000, equity=1000)
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    cfg = EpicConfig(
        epic="US100", strategy="SWEEP_FVG_OPENING_RANGE", enabled=True, timezone="America/New_York",
        session_name="NY Open", range_short_start=dtime(9, 30), range_short_end=dtime(9, 45),
        range_long_start=dtime(9, 30), range_long_end=dtime(10, 0),
        trade_start=dtime(9, 45), trade_end=dtime(10, 30),
    )
    settings = Settings()
    latest_candle = SimpleNamespace(candle_time=datetime(2026, 6, 1, 20, 0))
    latest_local = datetime.combine(session_date, dtime(20, 0), tzinfo=NY)  # after trade_end

    run_opening_range_strategy(
        db_session, cfg, settings, sweep_buffer=0.0, stop_buffer=0.5,
        account=account, latest_candle=latest_candle, latest_local=latest_local, session_date=session_date,
    )

    assert db_session.query(Signal).count() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_run_auto_paper_once.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_opening_range_strategy' from 'scripts.run_auto_paper_once'` (the function doesn't exist yet; current script only has `main()`).

- [ ] **Step 3: Rewrite `scripts/run_auto_paper_once.py`**

Replace the entire file with:

```python
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.alerts.telegram_alerts import TelegramAlert, format_signal_alert
from app.config import get_settings
from app.db import SessionLocal
from app.epics import STRATEGY_SWEEP_FVG_OPENING_RANGE, effective_risk, list_enabled_epics
from app.models import Candle, Signal
from app.paper.auto_paper import (
    create_trade_from_signal,
    ensure_paper_account,
    get_latest_candle,
    get_latest_price,
    get_open_trades,
    is_paused,
    is_stopped_today,
    losses_today_count,
    monitor_trades,
    stop_trading_today,
    total_open_risk_percent,
    trades_today_count,
)
from app.risk.risk_manager import RiskManager
from app.strategy.fvg_detector import detect_fvg_at
from app.strategy.sweep_detector import detect_sweep

load_dotenv()

UTC = ZoneInfo("UTC")


def send(msg: str):
    TelegramAlert().send_message(msg)


def local_to_utc_naive(dt):
    return dt.astimezone(UTC).replace(tzinfo=None)


def get_range(db, symbol, timeframe, start_utc, end_utc):
    candles = (
        db.query(Candle)
        .filter(
            Candle.symbol == symbol,
            Candle.timeframe == timeframe,
            Candle.candle_time >= start_utc,
            Candle.candle_time < end_utc,
        )
        .order_by(Candle.candle_time.asc())
        .all()
    )
    if not candles:
        return None
    return {
        "count": len(candles),
        "high": max(float(c.high) for c in candles),
        "low": min(float(c.low) for c in candles),
        "first": candles[0].candle_time,
        "last": candles[-1].candle_time,
    }


def get_candles(db, symbol, timeframe, start_utc, end_utc):
    rows = (
        db.query(Candle)
        .filter(
            Candle.symbol == symbol,
            Candle.timeframe == timeframe,
            Candle.candle_time >= start_utc,
            Candle.candle_time <= end_utc,
        )
        .order_by(Candle.candle_time.asc())
        .all()
    )
    return [
        {
            "candle_time": c.candle_time,
            "time": c.candle_time,
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": float(c.volume or 0),
        }
        for c in rows
    ]


def notify_trade_created(signal, trade, account, risk_percent):
    msg = format_signal_alert(
        {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "setup_type": signal.setup_type,
            "entry_price": float(signal.entry_price),
            "stop_loss": float(signal.stop_loss),
            "take_profit": float(signal.take_profit),
            "risk_percent": risk_percent,
            "session_high": float(signal.session_high) if signal.session_high is not None else None,
            "session_low": float(signal.session_low) if signal.session_low is not None else None,
            "opening_range_high": float(signal.opening_range_high),
            "opening_range_low": float(signal.opening_range_low),
            "sweep_level": signal.sweep_level,
            "fvg_low": float(signal.fvg_low),
            "fvg_high": float(signal.fvg_high),
            "mode": "AUTO_PAPER",
        }
    )
    msg += f"\n\n<b>Paper trade:</b> Created automatically"
    msg += f"\n<b>Status:</b> PENDING ENTRY"
    msg += f"\n<b>Risk amount:</b> ${float(trade.risk_amount):.2f}"
    msg += f"\n<b>Paper balance:</b> ${float(account.balance):.2f}"
    send(msg)


def notify_trade_event(event):
    trade = event["trade"]
    if event["event"] == "entry_triggered":
        send(
            f"✅ <b>Paper Entry Triggered</b>\n\n"
            f"<b>Symbol:</b> {trade.symbol}\n"
            f"<b>Direction:</b> {trade.direction}\n"
            f"<b>Entry:</b> {float(trade.entry_price):.2f}\n"
            f"<b>SL:</b> {float(trade.stop_loss):.2f}\n"
            f"<b>TP:</b> {float(trade.take_profit):.2f}"
        )
    elif event["event"] == "closed":
        emoji = "🎯" if event["result"] == "WIN" else "❌"
        send(
            f"{emoji} <b>Paper Trade Closed</b>\n\n"
            f"<b>Symbol:</b> {trade.symbol}\n"
            f"<b>Direction:</b> {trade.direction}\n"
            f"<b>Result:</b> {event['result']}\n"
            f"<b>R Multiple:</b> {event['r_multiple']:.2f}R\n"
            f"<b>P/L:</b> ${event['pnl']:.2f}\n"
            f"<b>Old Balance:</b> ${event['old_balance']:.2f}\n"
            f"<b>New Balance:</b> ${event['new_balance']:.2f}"
        )


def portfolio_risk_available(db, account, settings, risk_percent) -> bool:
    current = total_open_risk_percent(db, account)
    return (current + risk_percent) <= settings.max_portfolio_risk_percent


def run_opening_range_strategy(db, cfg, settings, sweep_buffer, stop_buffer, account, latest_candle, latest_local, session_date):
    epic = cfg.epic
    TZ = ZoneInfo(cfg.timezone)
    trade_start = cfg.trade_start
    trade_end = cfg.trade_end
    range_short_start, range_short_end = cfg.range_short_start, cfg.range_short_end
    range_long_start, range_long_end = cfg.range_long_start, cfg.range_long_end

    if latest_local.time() < trade_start:
        print(f"[{epic}/{cfg.strategy}] Before {trade_start}. Strategy not active yet.")
        return
    if latest_local.time() > trade_end:
        print(f"[{epic}/{cfg.strategy}] After {trade_end}. No new trades.")
        return

    overnight_start_local = datetime.combine(session_date - timedelta(days=1), time(18, 0), tzinfo=TZ)
    overnight_end_local = datetime.combine(session_date, range_short_start, tzinfo=TZ)
    overnight = get_range(db, epic, "M1", local_to_utc_naive(overnight_start_local), local_to_utc_naive(overnight_end_local))

    if latest_local.time() >= range_long_end:
        range_start_local = datetime.combine(session_date, range_long_start, tzinfo=TZ)
        range_end_local = datetime.combine(session_date, range_long_end, tzinfo=TZ)
        range_name = "30-min opening range"
    else:
        range_start_local = datetime.combine(session_date, range_short_start, tzinfo=TZ)
        range_end_local = datetime.combine(session_date, range_short_end, tzinfo=TZ)
        range_name = "15-min opening range"

    opening_range = get_range(db, epic, "M1", local_to_utc_naive(range_start_local), local_to_utc_naive(range_end_local))
    if not opening_range:
        print(f"[{epic}/{cfg.strategy}] Opening range not ready.")
        return

    scan_start_utc = local_to_utc_naive(range_end_local)
    scan_end_utc = latest_candle.candle_time
    candles = get_candles(db, epic, "M5", scan_start_utc, scan_end_utc)

    if len(candles) < 5:
        print(f"[{epic}/{cfg.strategy}] Not enough M5 candles to scan.")
        return

    found = None
    for i, candle in enumerate(candles):
        sweep = detect_sweep(candle, opening_range["high"], opening_range["low"], buffer=sweep_buffer)
        if not sweep:
            continue
        for j in range(max(i + 2, 2), len(candles)):
            fvg = detect_fvg_at(candles, j)
            if fvg and fvg.direction == sweep.direction:
                found = (sweep, fvg, candles[j])
                break
        if found:
            break

    if not found:
        print(f"[{epic}/{cfg.strategy}] No sweep + FVG setup found.")
        return

    sweep, fvg, fvg_candle = found
    risk_percent, _, _ = effective_risk(cfg, settings)
    plan = RiskManager().build_trade_plan(epic, sweep.direction, fvg.midpoint, sweep.sweep_price, buffer=stop_buffer)

    if not portfolio_risk_available(db, account, settings, risk_percent):
        print(f"[{epic}/{cfg.strategy}] Portfolio risk ceiling reached. Skipping trade.")
        return

    setup_type = f"{cfg.session_name} Sweep + FVG AUTO_PAPER ({range_name})"
    existing_signal = (
        db.query(Signal)
        .filter(
            Signal.symbol == epic,
            Signal.strategy == cfg.strategy,
            Signal.direction == sweep.direction,
            Signal.signal_time == fvg_candle["candle_time"],
            Signal.setup_type == setup_type,
        )
        .first()
    )
    if existing_signal:
        print(f"[{epic}/{cfg.strategy}] Signal already exists. No duplicate.")
        return

    signal = Signal(
        symbol=epic,
        strategy=cfg.strategy,
        signal_time=fvg_candle["candle_time"],
        direction=sweep.direction,
        setup_type=setup_type,
        status="DETECTED",
        session_high=overnight["high"] if overnight else None,
        session_low=overnight["low"] if overnight else None,
        opening_range_high=opening_range["high"],
        opening_range_low=opening_range["low"],
        sweep_level=sweep.sweep_level,
        sweep_price=sweep.sweep_price,
        fvg_low=fvg.fvg_low,
        fvg_high=fvg.fvg_high,
        entry_price=plan.entry_price,
        stop_loss=plan.stop_loss,
        take_profit=plan.take_profit,
        risk_reward=plan.risk_reward,
        mode="AUTO_PAPER",
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)

    trade = create_trade_from_signal(db, signal, risk_percent)
    account = ensure_paper_account(db)
    notify_trade_created(signal, trade, account, risk_percent)
    print(f"[{epic}/{cfg.strategy}] Auto paper trade created.")


STRATEGY_HANDLERS = {
    STRATEGY_SWEEP_FVG_OPENING_RANGE: run_opening_range_strategy,
}


def main():
    settings = get_settings()
    sweep_buffer = float(os.getenv("SWEEP_BUFFER_POINTS", "0"))
    stop_buffer = float(os.getenv("STOP_BUFFER_POINTS", "2"))

    db = SessionLocal()

    try:
        account = ensure_paper_account(db)
        epic_configs = list_enabled_epics(db)

        for cfg in epic_configs:
            epic = cfg.epic
            strategy = cfg.strategy
            risk_percent, max_trades_per_day, max_losses_per_day = effective_risk(cfg, settings)

            TZ = ZoneInfo(cfg.timezone)

            latest_candle = get_latest_candle(db, epic)
            latest_price = get_latest_price(db, epic)

            if not latest_candle or latest_price is None:
                print(f"[{epic}/{strategy}] No latest candle/price. Skipping.")
                continue

            events = monitor_trades(db, epic, latest_price)
            for event in events:
                notify_trade_event(event)

            latest_utc = latest_candle.candle_time.replace(tzinfo=UTC)
            latest_local = latest_utc.astimezone(TZ)
            session_date = latest_local.date()

            print(f"[{epic}/{strategy}] Latest local time: {latest_local.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            print(f"[{epic}/{strategy}] Paper balance: {float(account.balance)}")

            if is_paused(db):
                print(f"[{epic}/{strategy}] Trading paused. Monitoring only.")
                continue

            if is_stopped_today(db) or is_stopped_today(db, epic, strategy):
                print(f"[{epic}/{strategy}] Stopped for today. Monitoring only.")
                continue

            if get_open_trades(db, epic, strategy):
                print(f"[{epic}/{strategy}] Open/pending trade exists. No new trade.")
                continue

            if trades_today_count(db, epic, strategy) >= max_trades_per_day:
                print(f"[{epic}/{strategy}] Max trades per day reached.")
                continue

            if losses_today_count(db, epic, strategy) >= max_losses_per_day:
                print(f"[{epic}/{strategy}] Max losses per day reached.")
                stop_trading_today(db, epic, strategy)
                send(f"🛑 <b>Daily Risk Limit Hit ({epic} / {strategy})</b>\n\nNo more AUTO_PAPER trades today for {epic} on {strategy}.")
                continue

            handler = STRATEGY_HANDLERS.get(strategy)
            if handler is None:
                print(f"[{epic}/{strategy}] Unknown strategy. Skipping.")
                continue

            handler(db, cfg, settings, sweep_buffer, stop_buffer, account, latest_candle, latest_local, session_date)

    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_run_auto_paper_once.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest -q`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/run_auto_paper_once.py tests/test_run_auto_paper_once.py
git commit -m "refactor: extract run_opening_range_strategy and add per-config strategy dispatch"
```

---

## Task 10: PDH/PDL strategy handler

**Files:**
- Modify: `scripts/run_auto_paper_once.py`
- Modify: `tests/test_run_auto_paper_once.py`

**Interfaces:**
- Consumes: `previous_day_range_window` (Task 4), `STRATEGY_SWEEP_FVG_PDH_PDL` (Task 2)
- Produces: `run_pdh_pdl_strategy(db, cfg, settings, sweep_buffer, stop_buffer, account, latest_candle, latest_local, session_date)` — same signature as `run_opening_range_strategy`, wired into `STRATEGY_HANDLERS`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_run_auto_paper_once.py`:

```python
def test_run_pdh_pdl_strategy_creates_signal_and_trade(db_session):
    from scripts.run_auto_paper_once import run_pdh_pdl_strategy

    previous_day = date(2026, 6, 1)
    session_date = date(2026, 6, 2)

    _insert_candle(db_session, "US100", "M1", datetime.combine(previous_day, dtime(12, 0), tzinfo=NY), 105, 95, 100)

    m5_specs = [
        (dtime(9, 45), 104, 99, 103),
        (dtime(9, 50), 106, 102, 104),
        (dtime(9, 55), 107, 101, 103),
        (dtime(10, 0), 108, 102, 104),
        (dtime(10, 5), 109, 105, 106),
        (dtime(10, 10), 107, 98, 99),
        (dtime(10, 15), 100, 95, 96),
    ]
    last_candle_time = None
    for t, h, l, c in m5_specs:
        local_dt = datetime.combine(session_date, t, tzinfo=NY)
        _insert_candle(db_session, "US100", "M5", local_dt, h, l, c)
        last_candle_time = local_dt.astimezone(UTC).replace(tzinfo=None)
    db_session.commit()

    account = PaperAccount(name="default", currency="USD", starting_balance=1000, balance=1000, equity=1000)
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    cfg = EpicConfig(
        epic="US100",
        strategy="SWEEP_FVG_PDH_PDL",
        enabled=True,
        timezone="America/New_York",
        session_name="PDH/PDL All Day",
        trade_start=dtime(0, 5),
        trade_end=dtime(23, 55),
    )

    settings = Settings(risk_per_trade_percent=0.5, max_trades_per_day=1, max_losses_per_day=2, min_risk_reward=2.0)
    latest_candle = SimpleNamespace(candle_time=last_candle_time)
    latest_local = datetime.combine(session_date, dtime(10, 15), tzinfo=NY)

    run_pdh_pdl_strategy(
        db_session, cfg, settings, sweep_buffer=0.0, stop_buffer=0.5,
        account=account, latest_candle=latest_candle, latest_local=latest_local, session_date=session_date,
    )

    signal = db_session.query(Signal).one()
    assert signal.strategy == "SWEEP_FVG_PDH_PDL"
    assert signal.direction == "SELL"
    assert float(signal.opening_range_high) == pytest.approx(105.0)
    assert float(signal.opening_range_low) == pytest.approx(95.0)
    assert float(signal.entry_price) == pytest.approx(102.5)
    assert float(signal.stop_loss) == pytest.approx(106.5)
    assert float(signal.take_profit) == pytest.approx(94.5)

    trade = db_session.query(PaperTrade).one()
    assert trade.signal_id == signal.id


def test_run_pdh_pdl_strategy_does_nothing_when_previous_day_range_missing(db_session):
    from scripts.run_auto_paper_once import run_pdh_pdl_strategy

    session_date = date(2026, 6, 2)
    account = PaperAccount(name="default", currency="USD", starting_balance=1000, balance=1000, equity=1000)
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    cfg = EpicConfig(
        epic="US100", strategy="SWEEP_FVG_PDH_PDL", enabled=True, timezone="America/New_York",
        session_name="PDH/PDL All Day", trade_start=dtime(0, 5), trade_end=dtime(23, 55),
    )
    settings = Settings()
    latest_candle = SimpleNamespace(candle_time=datetime(2026, 6, 2, 14, 15))
    latest_local = datetime.combine(session_date, dtime(10, 15), tzinfo=NY)

    run_pdh_pdl_strategy(
        db_session, cfg, settings, sweep_buffer=0.0, stop_buffer=0.5,
        account=account, latest_candle=latest_candle, latest_local=latest_local, session_date=session_date,
    )

    assert db_session.query(Signal).count() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_run_auto_paper_once.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_pdh_pdl_strategy'`

- [ ] **Step 3: Add to `scripts/run_auto_paper_once.py`**

Add to the imports:

```python
from app.epics import STRATEGY_SWEEP_FVG_OPENING_RANGE, STRATEGY_SWEEP_FVG_PDH_PDL, effective_risk, list_enabled_epics
from app.strategy.previous_day_range import previous_day_range_window
```

Add this function right after `run_opening_range_strategy` and before `STRATEGY_HANDLERS`:

```python
def run_pdh_pdl_strategy(db, cfg, settings, sweep_buffer, stop_buffer, account, latest_candle, latest_local, session_date):
    epic = cfg.epic
    TZ = ZoneInfo(cfg.timezone)
    trade_start = cfg.trade_start
    trade_end = cfg.trade_end

    if latest_local.time() < trade_start:
        print(f"[{epic}/{cfg.strategy}] Before {trade_start}. Strategy not active yet.")
        return
    if latest_local.time() > trade_end:
        print(f"[{epic}/{cfg.strategy}] After {trade_end}. No new trades.")
        return

    prev_start_utc, prev_end_utc = previous_day_range_window(session_date, cfg.timezone)
    previous_day = get_range(db, epic, "M1", prev_start_utc, prev_end_utc)
    if not previous_day:
        print(f"[{epic}/{cfg.strategy}] Previous day range not ready.")
        return

    scan_start_local = datetime.combine(session_date, trade_start, tzinfo=TZ)
    scan_start_utc = local_to_utc_naive(scan_start_local)
    scan_end_utc = latest_candle.candle_time
    candles = get_candles(db, epic, "M5", scan_start_utc, scan_end_utc)

    if len(candles) < 5:
        print(f"[{epic}/{cfg.strategy}] Not enough M5 candles to scan.")
        return

    found = None
    for i, candle in enumerate(candles):
        sweep = detect_sweep(candle, previous_day["high"], previous_day["low"], buffer=sweep_buffer)
        if not sweep:
            continue
        for j in range(max(i + 2, 2), len(candles)):
            fvg = detect_fvg_at(candles, j)
            if fvg and fvg.direction == sweep.direction:
                found = (sweep, fvg, candles[j])
                break
        if found:
            break

    if not found:
        print(f"[{epic}/{cfg.strategy}] No sweep + FVG setup found.")
        return

    sweep, fvg, fvg_candle = found
    risk_percent, _, _ = effective_risk(cfg, settings)
    plan = RiskManager().build_trade_plan(epic, sweep.direction, fvg.midpoint, sweep.sweep_price, buffer=stop_buffer)

    if not portfolio_risk_available(db, account, settings, risk_percent):
        print(f"[{epic}/{cfg.strategy}] Portfolio risk ceiling reached. Skipping trade.")
        return

    setup_type = f"{cfg.session_name} Sweep + FVG AUTO_PAPER (Previous Day Range)"
    existing_signal = (
        db.query(Signal)
        .filter(
            Signal.symbol == epic,
            Signal.strategy == cfg.strategy,
            Signal.direction == sweep.direction,
            Signal.signal_time == fvg_candle["candle_time"],
            Signal.setup_type == setup_type,
        )
        .first()
    )
    if existing_signal:
        print(f"[{epic}/{cfg.strategy}] Signal already exists. No duplicate.")
        return

    signal = Signal(
        symbol=epic,
        strategy=cfg.strategy,
        signal_time=fvg_candle["candle_time"],
        direction=sweep.direction,
        setup_type=setup_type,
        status="DETECTED",
        # Reusing opening_range_high/low to store the previous day's high/low
        # (the reference level actually swept by this strategy).
        opening_range_high=previous_day["high"],
        opening_range_low=previous_day["low"],
        sweep_level=sweep.sweep_level,
        sweep_price=sweep.sweep_price,
        fvg_low=fvg.fvg_low,
        fvg_high=fvg.fvg_high,
        entry_price=plan.entry_price,
        stop_loss=plan.stop_loss,
        take_profit=plan.take_profit,
        risk_reward=plan.risk_reward,
        mode="AUTO_PAPER",
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)

    trade = create_trade_from_signal(db, signal, risk_percent)
    account = ensure_paper_account(db)
    notify_trade_created(signal, trade, account, risk_percent)
    print(f"[{epic}/{cfg.strategy}] Auto paper trade created.")
```

Update `STRATEGY_HANDLERS`:

```python
STRATEGY_HANDLERS = {
    STRATEGY_SWEEP_FVG_OPENING_RANGE: run_opening_range_strategy,
    STRATEGY_SWEEP_FVG_PDH_PDL: run_pdh_pdl_strategy,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_run_auto_paper_once.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest -q`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/run_auto_paper_once.py tests/test_run_auto_paper_once.py
git commit -m "feat: add PDH/PDL sweep+FVG strategy handler"
```

---

## Task 11: VWAP mean-reversion strategy handler

**Files:**
- Modify: `scripts/run_auto_paper_once.py`
- Modify: `tests/test_run_auto_paper_once.py`

**Interfaces:**
- Consumes: `calculate_vwap_bands` (Task 5), `RiskManager.build_trade_plan_with_target` (Task 6), `STRATEGY_VWAP_MEAN_REVERSION` (Task 2)
- Produces: `run_vwap_strategy(db, cfg, settings, sweep_buffer, stop_buffer, account, latest_candle, latest_local, session_date)`, wired into `STRATEGY_HANDLERS`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_run_auto_paper_once.py`:

```python
def test_run_vwap_strategy_creates_signal_and_trade_on_fade(db_session):
    from scripts.run_auto_paper_once import run_vwap_strategy

    session_date = date(2026, 6, 1)

    m5_specs = [
        (dtime(9, 45), 100, 100, 100),
        (dtime(9, 50), 100, 100, 100),
        (dtime(9, 55), 100, 100, 100),
        (dtime(10, 0), 100, 100, 100),
        (dtime(10, 5), 110, 101, 100.5),
        (dtime(10, 10), 101, 99, 100),
        (dtime(10, 15), 100.9, 97, 98.5),
    ]
    last_candle_time = None
    for t, h, l, c in m5_specs:
        local_dt = datetime.combine(session_date, t, tzinfo=NY)
        _insert_candle(db_session, "US100", "M5", local_dt, h, l, c, v=1)
        last_candle_time = local_dt.astimezone(UTC).replace(tzinfo=None)
    db_session.commit()

    account = PaperAccount(name="default", currency="USD", starting_balance=1000, balance=1000, equity=1000)
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    cfg = EpicConfig(
        epic="US100",
        strategy="VWAP_MEAN_REVERSION",
        enabled=True,
        timezone="America/New_York",
        session_name="NY Midday VWAP",
        trade_start=dtime(9, 45),
        trade_end=dtime(13, 30),
        params={"deviation_threshold": 1.5},
    )

    settings = Settings(risk_per_trade_percent=0.5, max_trades_per_day=1, max_losses_per_day=2, min_risk_reward=2.0)
    latest_candle = SimpleNamespace(candle_time=last_candle_time)
    latest_local = datetime.combine(session_date, dtime(10, 15), tzinfo=NY)

    run_vwap_strategy(
        db_session, cfg, settings, sweep_buffer=0.0, stop_buffer=0.5,
        account=account, latest_candle=latest_candle, latest_local=latest_local, session_date=session_date,
    )

    signal = db_session.query(Signal).one()
    assert signal.strategy == "VWAP_MEAN_REVERSION"
    assert signal.direction == "SELL"
    assert float(signal.entry_price) == pytest.approx(100.95, abs=0.01)
    assert float(signal.stop_loss) == pytest.approx(110.5, abs=0.01)
    assert float(signal.take_profit) == pytest.approx(100.77, abs=0.01)
    assert float(signal.take_profit) < float(signal.entry_price)

    trade = db_session.query(PaperTrade).one()
    assert trade.signal_id == signal.id


def test_run_vwap_strategy_does_nothing_with_too_few_candles(db_session):
    from scripts.run_auto_paper_once import run_vwap_strategy

    session_date = date(2026, 6, 1)
    for t in [dtime(9, 45), dtime(9, 50), dtime(9, 55)]:
        local_dt = datetime.combine(session_date, t, tzinfo=NY)
        _insert_candle(db_session, "US100", "M5", local_dt, 100, 100, 100, v=1)
    db_session.commit()

    account = PaperAccount(name="default", currency="USD", starting_balance=1000, balance=1000, equity=1000)
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    cfg = EpicConfig(
        epic="US100", strategy="VWAP_MEAN_REVERSION", enabled=True, timezone="America/New_York",
        session_name="NY Midday VWAP", trade_start=dtime(9, 45), trade_end=dtime(13, 30),
        params={"deviation_threshold": 1.5},
    )
    settings = Settings()
    latest_candle = SimpleNamespace(candle_time=datetime(2026, 6, 1, 13, 55))
    latest_local = datetime.combine(session_date, dtime(9, 55), tzinfo=NY)

    run_vwap_strategy(
        db_session, cfg, settings, sweep_buffer=0.0, stop_buffer=0.5,
        account=account, latest_candle=latest_candle, latest_local=latest_local, session_date=session_date,
    )

    assert db_session.query(Signal).count() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_run_auto_paper_once.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_vwap_strategy'`

- [ ] **Step 3: Add to `scripts/run_auto_paper_once.py`**

Add to the imports:

```python
from app.epics import (
    STRATEGY_SWEEP_FVG_OPENING_RANGE,
    STRATEGY_SWEEP_FVG_PDH_PDL,
    STRATEGY_VWAP_MEAN_REVERSION,
    effective_risk,
    list_enabled_epics,
)
from app.strategy.vwap import calculate_vwap_bands
```

Add this function right after `run_pdh_pdl_strategy` and before `STRATEGY_HANDLERS`:

```python
def run_vwap_strategy(db, cfg, settings, sweep_buffer, stop_buffer, account, latest_candle, latest_local, session_date):
    epic = cfg.epic
    TZ = ZoneInfo(cfg.timezone)
    trade_start = cfg.trade_start
    trade_end = cfg.trade_end

    if latest_local.time() < trade_start:
        print(f"[{epic}/{cfg.strategy}] Before {trade_start}. Strategy not active yet.")
        return
    if latest_local.time() > trade_end:
        print(f"[{epic}/{cfg.strategy}] After {trade_end}. No new trades.")
        return

    scan_start_local = datetime.combine(session_date, trade_start, tzinfo=TZ)
    scan_start_utc = local_to_utc_naive(scan_start_local)
    scan_end_utc = latest_candle.candle_time
    candles = get_candles(db, epic, "M5", scan_start_utc, scan_end_utc)

    if len(candles) < 5:
        print(f"[{epic}/{cfg.strategy}] Not enough M5 candles to scan.")
        return

    deviation_threshold = (cfg.params or {}).get("deviation_threshold", 1.5)
    bands = calculate_vwap_bands(candles, deviation_threshold)

    found = None
    for i, candle in enumerate(candles):
        band = bands[i]
        if band is None:
            continue
        sweep = detect_sweep(candle, band["high"], band["low"], buffer=sweep_buffer)
        if not sweep:
            continue
        for j in range(max(i + 2, 2), len(candles)):
            fvg = detect_fvg_at(candles, j)
            if fvg and fvg.direction == sweep.direction:
                found = (sweep, fvg, candles[j], band)
                break
        if found:
            break

    if not found:
        print(f"[{epic}/{cfg.strategy}] No VWAP fade setup found.")
        return

    sweep, fvg, fvg_candle, band = found
    risk_percent, _, _ = effective_risk(cfg, settings)

    entry_price = fvg.midpoint
    target_price = band["vwap"]
    stop_loss = sweep.sweep_price + stop_buffer if sweep.direction == "SELL" else sweep.sweep_price - stop_buffer

    try:
        plan = RiskManager().build_trade_plan_with_target(epic, sweep.direction, entry_price, stop_loss, target_price)
    except ValueError as exc:
        print(f"[{epic}/{cfg.strategy}] Invalid VWAP trade plan ({exc}). Skipping.")
        return

    if not portfolio_risk_available(db, account, settings, risk_percent):
        print(f"[{epic}/{cfg.strategy}] Portfolio risk ceiling reached. Skipping trade.")
        return

    setup_type = f"{cfg.session_name} VWAP Fade AUTO_PAPER"
    existing_signal = (
        db.query(Signal)
        .filter(
            Signal.symbol == epic,
            Signal.strategy == cfg.strategy,
            Signal.direction == sweep.direction,
            Signal.signal_time == fvg_candle["candle_time"],
            Signal.setup_type == setup_type,
        )
        .first()
    )
    if existing_signal:
        print(f"[{epic}/{cfg.strategy}] Signal already exists. No duplicate.")
        return

    signal = Signal(
        symbol=epic,
        strategy=cfg.strategy,
        signal_time=fvg_candle["candle_time"],
        direction=sweep.direction,
        setup_type=setup_type,
        status="DETECTED",
        # Reusing opening_range_high/low to store the VWAP band in effect at signal time.
        opening_range_high=band["high"],
        opening_range_low=band["low"],
        sweep_level=sweep.sweep_level,
        sweep_price=sweep.sweep_price,
        fvg_low=fvg.fvg_low,
        fvg_high=fvg.fvg_high,
        entry_price=plan.entry_price,
        stop_loss=plan.stop_loss,
        take_profit=plan.take_profit,
        risk_reward=plan.risk_reward,
        mode="AUTO_PAPER",
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)

    trade = create_trade_from_signal(db, signal, risk_percent)
    account = ensure_paper_account(db)
    notify_trade_created(signal, trade, account, risk_percent)
    print(f"[{epic}/{cfg.strategy}] Auto paper trade created.")
```

Update `STRATEGY_HANDLERS`:

```python
STRATEGY_HANDLERS = {
    STRATEGY_SWEEP_FVG_OPENING_RANGE: run_opening_range_strategy,
    STRATEGY_SWEEP_FVG_PDH_PDL: run_pdh_pdl_strategy,
    STRATEGY_VWAP_MEAN_REVERSION: run_vwap_strategy,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_run_auto_paper_once.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest -q`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/run_auto_paper_once.py tests/test_run_auto_paper_once.py
git commit -m "feat: add VWAP mean-reversion strategy handler"
```

---

## Task 12: Dashboard — Epic & Session Management editor supports multiple strategies per epic

**Files:**
- Modify: `dashboard.py:1` (add `import json`), `dashboard.py:26` (import), `dashboard.py:259-279` (`epic_config_rows`), `dashboard.py:381-517` (the whole "Epic & Session Management" expander block)

**Interfaces:**
- Consumes: `ALL_STRATEGIES`, `upsert_epic_config(db, epic, strategy, session_name, **fields)`, `delete_epic_config(db, epic, strategy, session_name)` (Task 2)
- Produces: editor grid now has Strategy/Params columns; row identity is (Epic, Strategy, Session name), all three locked (`disabled=True`) in the bulk editor — changing identity only happens via the Add/Remove controls, mirroring how Epic was already locked.

- [ ] **Step 1: Add `import json` and update the `app.epics` import**

At the top of `dashboard.py`, change line 1 from:

```python
import sys
```

to:

```python
import json
import sys
```

Change line 26 from:

```python
from app.epics import CURATED_TIMEZONES, delete_epic_config, list_all_epics, list_enabled_epics, upsert_epic_config
```

to:

```python
from app.epics import ALL_STRATEGIES, CURATED_TIMEZONES, delete_epic_config, list_all_epics, list_enabled_epics, upsert_epic_config
```

- [ ] **Step 2: Replace `epic_config_rows` (currently lines 259-279)**

```python
def epic_config_rows(configs):
    rows = []
    for cfg in configs:
        rows.append(
            {
                "Epic": cfg.epic,
                "Strategy": cfg.strategy,
                "Session name": cfg.session_name,
                "Enabled": cfg.enabled,
                "Timezone": cfg.timezone,
                "Range short start": cfg.range_short_start,
                "Range short end": cfg.range_short_end,
                "Range long start": cfg.range_long_start,
                "Range long end": cfg.range_long_end,
                "Trade start": cfg.trade_start,
                "Trade end": cfg.trade_end,
                "Risk % override": float(cfg.risk_per_trade_percent) if cfg.risk_per_trade_percent is not None else None,
                "Max trades override": cfg.max_trades_per_day,
                "Max losses override": cfg.max_losses_per_day,
                "Params": json.dumps(cfg.params) if cfg.params else "",
            }
        )
    return pd.DataFrame(rows)
```

- [ ] **Step 3: Replace the whole "Epic & Session Management" block (currently lines 381-517)**

```python
with st.expander("⚙️ Epic & Session Management", expanded=False):
    db = SessionLocal()
    try:
        all_configs = list_all_epics(db)
    finally:
        db.close()

    st.markdown("#### Configured epics")
    st.caption("Epic, Strategy, and Session name identify a row and can't be edited here — use Add/Remove below to change them.")
    original_df = epic_config_rows(all_configs)
    edited_df = st.data_editor(
        original_df,
        num_rows="fixed",
        hide_index=True,
        use_container_width=True,
        key="epic_config_editor",
        column_config={
            "Epic": st.column_config.TextColumn("Epic", disabled=True),
            "Strategy": st.column_config.TextColumn("Strategy", disabled=True),
            "Session name": st.column_config.TextColumn("Session name", disabled=True),
            "Enabled": st.column_config.CheckboxColumn("Enabled"),
            "Timezone": st.column_config.SelectboxColumn("Timezone", options=CURATED_TIMEZONES),
            "Range short start": st.column_config.TimeColumn("Range short start"),
            "Range short end": st.column_config.TimeColumn("Range short end"),
            "Range long start": st.column_config.TimeColumn("Range long start"),
            "Range long end": st.column_config.TimeColumn("Range long end"),
            "Trade start": st.column_config.TimeColumn("Trade start"),
            "Trade end": st.column_config.TimeColumn("Trade end"),
            "Risk % override": st.column_config.NumberColumn("Risk % override", min_value=0.01, max_value=10.0, step=0.01),
            "Max trades override": st.column_config.NumberColumn("Max trades override", min_value=1, step=1),
            "Max losses override": st.column_config.NumberColumn("Max losses override", min_value=1, step=1),
            "Params": st.column_config.TextColumn("Params (JSON)"),
        },
    )

    if st.button("💾 Save changes", key="save_epic_configs"):
        db = SessionLocal()
        errors = []
        try:
            changed = 0
            for i in range(len(original_df)):
                before = original_df.iloc[i]
                after = edited_df.iloc[i]
                if before.equals(after):
                    continue
                try:
                    params = json.loads(after["Params"]) if str(after["Params"]).strip() else None
                except json.JSONDecodeError:
                    errors.append(f"{after['Epic']} / {after['Strategy']} / {after['Session name']}: invalid Params JSON, skipped.")
                    continue
                upsert_epic_config(
                    db,
                    epic=after["Epic"],
                    strategy=after["Strategy"],
                    session_name=after["Session name"],
                    enabled=bool(after["Enabled"]),
                    timezone=after["Timezone"],
                    range_short_start=after["Range short start"] if pd.notna(after["Range short start"]) else None,
                    range_short_end=after["Range short end"] if pd.notna(after["Range short end"]) else None,
                    range_long_start=after["Range long start"] if pd.notna(after["Range long start"]) else None,
                    range_long_end=after["Range long end"] if pd.notna(after["Range long end"]) else None,
                    trade_start=after["Trade start"],
                    trade_end=after["Trade end"],
                    risk_per_trade_percent=after["Risk % override"] if pd.notna(after["Risk % override"]) else None,
                    max_trades_per_day=int(after["Max trades override"]) if pd.notna(after["Max trades override"]) else None,
                    max_losses_per_day=int(after["Max losses override"]) if pd.notna(after["Max losses override"]) else None,
                    params=params,
                )
                changed += 1
        finally:
            db.close()
        for err in errors:
            st.warning(err)
        st.success(f"Saved {changed} epic config change(s).")
        st.cache_data.clear()
        st.rerun()

    st.markdown("#### Add new epic config")
    st.caption("Range fields are only used by SWEEP_FVG_OPENING_RANGE — they're ignored (stored blank) for other strategies.")
    with st.form("add_epic_form", clear_on_submit=True):
        add_cols = st.columns(4)
        new_epic = add_cols[0].text_input("Epic code")
        new_strategy = add_cols[1].selectbox("Strategy", ALL_STRATEGIES)
        new_timezone = add_cols[2].selectbox("Timezone", CURATED_TIMEZONES)
        new_session_name = add_cols[3].text_input("Session name", value="NY Open")

        range_cols = st.columns(4)
        new_range_short_start = range_cols[0].time_input("Range short start", value=dtime(9, 30))
        new_range_short_end = range_cols[1].time_input("Range short end", value=dtime(9, 45))
        new_range_long_start = range_cols[2].time_input("Range long start", value=dtime(9, 30))
        new_range_long_end = range_cols[3].time_input("Range long end", value=dtime(10, 0))

        trade_cols = st.columns(2)
        new_trade_start = trade_cols[0].time_input("Trade start", value=dtime(9, 45))
        new_trade_end = trade_cols[1].time_input("Trade end", value=dtime(10, 30))

        override_cols = st.columns(4)
        new_risk_override_text = override_cols[0].text_input("Risk % override (blank = global default)", value="")
        new_max_trades_override_text = override_cols[1].text_input("Max trades override (blank = global default)", value="")
        new_max_losses_override_text = override_cols[2].text_input("Max losses override (blank = global default)", value="")
        new_params_text = override_cols[3].text_input("Params JSON (blank = none)", value="")

        new_enabled = st.checkbox("Enabled", value=False)

        if st.form_submit_button("➕ Add epic config"):
            if not new_epic.strip():
                st.warning("Epic code is required.")
            else:
                try:
                    risk_override = float(new_risk_override_text) if new_risk_override_text.strip() else None
                    max_trades_override = int(new_max_trades_override_text) if new_max_trades_override_text.strip() else None
                    max_losses_override = int(new_max_losses_override_text) if new_max_losses_override_text.strip() else None
                    params = json.loads(new_params_text) if new_params_text.strip() else None
                except (ValueError, json.JSONDecodeError):
                    st.warning("Overrides must be numbers and Params must be valid JSON (or left blank).")
                else:
                    range_fields_apply = new_strategy == "SWEEP_FVG_OPENING_RANGE"
                    db = SessionLocal()
                    try:
                        upsert_epic_config(
                            db,
                            epic=new_epic.strip(),
                            strategy=new_strategy,
                            session_name=new_session_name,
                            enabled=new_enabled,
                            timezone=new_timezone,
                            range_short_start=new_range_short_start if range_fields_apply else None,
                            range_short_end=new_range_short_end if range_fields_apply else None,
                            range_long_start=new_range_long_start if range_fields_apply else None,
                            range_long_end=new_range_long_end if range_fields_apply else None,
                            trade_start=new_trade_start,
                            trade_end=new_trade_end,
                            risk_per_trade_percent=risk_override,
                            max_trades_per_day=max_trades_override,
                            max_losses_per_day=max_losses_override,
                            params=params,
                        )
                    finally:
                        db.close()
                    st.success(f"Added {new_epic.strip()} / {new_strategy} / {new_session_name}.")
                    st.cache_data.clear()
                    st.rerun()

    st.markdown("#### Remove epic config")
    config_labels = {f"{cfg.epic} | {cfg.strategy} | {cfg.session_name}": cfg for cfg in all_configs}
    remove_labels = st.multiselect("Configs to remove", list(config_labels.keys()), key="remove_epic_select")
    confirm_remove = st.checkbox("Confirm removal", key="confirm_epic_removal")
    if st.button("🗑️ Delete selected configs", disabled=not (remove_labels and confirm_remove)):
        db = SessionLocal()
        try:
            for label in remove_labels:
                cfg = config_labels[label]
                delete_epic_config(db, cfg.epic, cfg.strategy, cfg.session_name)
        finally:
            db.close()
        st.success(f"Removed {len(remove_labels)} config(s).")
        st.cache_data.clear()
        st.rerun()
```

- [ ] **Step 4: Syntax check**

Run: `python3 -m py_compile dashboard.py`
Expected: no output, exit code 0.

- [ ] **Step 5: Headless boot check**

Run:

```bash
(streamlit run dashboard.py --server.headless true --server.port 8599 > /tmp/dashboard_boot.log 2>&1 &) && \
  sleep 6 && \
  curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8599 && \
  pkill -f "streamlit run dashboard.py" && \
  cat /tmp/dashboard_boot.log
```

Expected: HTTP status `200`, no tracebacks in the log.

- [ ] **Step 6: Run full pytest suite**

Run: `python3 -m pytest -q`
Expected: All tests pass (dashboard.py isn't covered by pytest, but this confirms nothing else broke).

- [ ] **Step 7: Commit**

```bash
git add dashboard.py
git commit -m "feat: dashboard epic config editor supports multiple strategies per epic"
```

---

## Task 13: Dashboard — Strategy Performance section

**Files:**
- Modify: `dashboard.py` (add function after `epic_config_rows`, add render call after the title/caption)

**Interfaces:**
- Consumes: `ALL_STRATEGIES` (Task 2, imported in Task 12), `Signal.strategy` (Task 1)
- Produces: `strategy_performance_rows() -> pd.DataFrame` with one row per strategy in `ALL_STRATEGIES` (always all 3, even with zero trades).

- [ ] **Step 1: Add `strategy_performance_rows()` to `dashboard.py`**

Add this function right after `epic_config_rows` (from Task 12) and before `price_chart_df`:

```python
def strategy_performance_rows():
    db = SessionLocal()
    try:
        closed = (
            db.query(
                Signal.strategy.label("strategy"),
                func.count(PaperTrade.id).label("trades"),
                func.sum(func.coalesce(PaperTrade.pnl_amount, 0)).label("pnl"),
                func.avg(PaperTrade.r_multiple).label("avg_r"),
            )
            .join(Signal, PaperTrade.signal_id == Signal.id)
            .filter(PaperTrade.status == "CLOSED")
            .group_by(Signal.strategy)
            .all()
        )
        closed_map = {row.strategy: row for row in closed}

        wins = dict(
            db.query(Signal.strategy, func.count(PaperTrade.id))
            .join(Signal, PaperTrade.signal_id == Signal.id)
            .filter(PaperTrade.status == "CLOSED", PaperTrade.result == "WIN")
            .group_by(Signal.strategy)
            .all()
        )
        open_risk = dict(
            db.query(Signal.strategy, func.sum(func.coalesce(PaperTrade.risk_amount, 0)))
            .join(Signal, PaperTrade.signal_id == Signal.id)
            .filter(PaperTrade.status.in_(["PENDING", "ACTIVE"]))
            .group_by(Signal.strategy)
            .all()
        )

        rows = []
        for strategy in ALL_STRATEGIES:
            row = closed_map.get(strategy)
            trades = row.trades if row else 0
            pnl = float(row.pnl) if row and row.pnl is not None else 0.0
            avg_r = float(row.avg_r) if row and row.avg_r is not None else None
            win_count = wins.get(strategy, 0)
            rows.append(
                {
                    "Strategy": strategy,
                    "Closed trades": trades,
                    "Win rate %": round(100 * win_count / trades, 1) if trades else 0.0,
                    "Total P/L": pnl,
                    "Avg R": round(avg_r, 2) if avg_r is not None else None,
                    "Open risk $": float(open_risk.get(strategy, 0)),
                }
            )
        return pd.DataFrame(rows)
    finally:
        db.close()
```

- [ ] **Step 2: Render it near the top of the page**

Change:

```python
st.title("📈 NY Open FVG Bot Dashboard")
st.caption("US100 AUTO_PAPER monitoring, selectable charts, history, levels, and paper balance.")

with st.expander("⚙️ Epic & Session Management", expanded=False):
```

to:

```python
st.title("📈 NY Open FVG Bot Dashboard")
st.caption("US100 AUTO_PAPER monitoring, selectable charts, history, levels, and paper balance.")

st.subheader("Strategy performance")
st.dataframe(strategy_performance_rows(), hide_index=True, use_container_width=True)

with st.expander("⚙️ Epic & Session Management", expanded=False):
```

- [ ] **Step 3: Syntax + boot check**

Run: `python3 -m py_compile dashboard.py`
Expected: no output.

Run the same headless boot check as Task 12 Step 5.
Expected: HTTP `200`, no tracebacks — with an empty database this should show all 3 strategies with zero trades, not error.

- [ ] **Step 4: Run full pytest suite**

Run: `python3 -m pytest -q`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add dashboard.py
git commit -m "feat: add strategy performance section to dashboard"
```

---

## Task 14: Dashboard — Strategy column on trade/signal tables + portfolio risk gauge

**Files:**
- Modify: `dashboard.py` (imports, `load_dashboard_data`, `trade_rows`, `signal_rows`, metrics row)

**Interfaces:**
- Consumes: `total_open_risk_percent` (Task 7), `get_settings().max_portfolio_risk_percent` (Task 7)
- Produces: `trade_rows`/`signal_rows` gain a `"Strategy"` column; `load_dashboard_data` return dict gains `"open_risk_percent"`.

- [ ] **Step 1: Update imports**

Add `from sqlalchemy.orm import joinedload` alongside the existing `from sqlalchemy import func` import.

Add `from app.config import get_settings` and add `total_open_risk_percent` to the existing `from app.paper.auto_paper import (...)` import block.

- [ ] **Step 2: Eagerly load `Signal` on trade queries in `load_dashboard_data`**

Change the `open_trades` query from:

```python
        open_trades = (
            db.query(PaperTrade)
            .filter(PaperTrade.status.in_(["PENDING", "ACTIVE"]))
            .order_by(PaperTrade.created_at.asc())
            .all()
        )
```

to:

```python
        open_trades = (
            db.query(PaperTrade)
            .options(joinedload(PaperTrade.signal))
            .filter(PaperTrade.status.in_(["PENDING", "ACTIVE"]))
            .order_by(PaperTrade.created_at.asc())
            .all()
        )
```

Change the `all_trades` query from:

```python
        all_trades = (
            db.query(PaperTrade)
            .order_by(PaperTrade.created_at.desc())
            .limit(500)
            .all()
        )
```

to:

```python
        all_trades = (
            db.query(PaperTrade)
            .options(joinedload(PaperTrade.signal))
            .order_by(PaperTrade.created_at.desc())
            .limit(500)
            .all()
        )
```

This matters because `load_dashboard_data` is `@st.cache_data`-decorated and closes its DB session before returning — without eager loading, accessing `t.signal` later (after the session is closed) would raise `DetachedInstanceError`.

Add `"open_risk_percent": total_open_risk_percent(db, account) if account else 0.0,` to the dict returned by `load_dashboard_data` (next to the other `"...today"` keys).

- [ ] **Step 3: Add Strategy column to `trade_rows` and `signal_rows`**

In `trade_rows`, add to the per-row dict (after `"Symbol": t.symbol,`):

```python
                "Strategy": t.signal.strategy if t.signal else None,
```

In `signal_rows`, add to the per-row dict (after `"Symbol": s.symbol,`):

```python
                "Strategy": s.strategy,
```

- [ ] **Step 4: Add the portfolio risk gauge to the metrics row**

Change:

```python
col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("Paper balance", fmt_money(account.balance if account else 0))
col2.metric("Latest price", fmt_num(latest_price))
col3.metric("Open trades", len(data["open_trades"]))
col4.metric("Trades today", data["trades_today"])
col5.metric("Wins / Losses", f"{data['wins_today']} / {data['losses_today']}")
col6.metric("P/L today", fmt_money(data["pnl_today"]))
```

to:

```python
col1, col2, col3, col4, col5, col6, col7 = st.columns(7)
col1.metric("Paper balance", fmt_money(account.balance if account else 0))
col2.metric("Latest price", fmt_num(latest_price))
col3.metric("Open trades", len(data["open_trades"]))
col4.metric("Trades today", data["trades_today"])
col5.metric("Wins / Losses", f"{data['wins_today']} / {data['losses_today']}")
col6.metric("P/L today", fmt_money(data["pnl_today"]))
col7.metric("Open risk", f"{data['open_risk_percent']:.2f}% / {get_settings().max_portfolio_risk_percent:.1f}%")
```

- [ ] **Step 5: Syntax + boot check**

Run: `python3 -m py_compile dashboard.py`
Expected: no output.

Run the same headless boot check as Task 12 Step 5.
Expected: HTTP `200`, no `DetachedInstanceError` traceback in the log (this is exactly the bug this task avoids).

- [ ] **Step 6: Run full pytest suite**

Run: `python3 -m pytest -q`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add dashboard.py
git commit -m "feat: show strategy per trade/signal and add portfolio open-risk gauge"
```

---

## Task 15: Dry-run validation script for PDH/PDL

**Files:**
- Create: `scripts/dry_run_pdh_pdl.py`

**Interfaces:**
- Consumes: `get_range`, `get_candles`, `local_to_utc_naive` (from `scripts/run_auto_paper_once.py`), `previous_day_range_window` (Task 4)
- Produces: a standalone script, run manually, that prints what `SWEEP_FVG_PDH_PDL` would signal right now for every configured epic — no DB writes.

- [ ] **Step 1: Create `scripts/dry_run_pdh_pdl.py`**

```python
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import SessionLocal
from app.epics import STRATEGY_SWEEP_FVG_PDH_PDL, list_all_epics
from app.strategy.fvg_detector import detect_fvg_at
from app.strategy.previous_day_range import previous_day_range_window
from app.strategy.sweep_detector import detect_sweep
from scripts.run_auto_paper_once import get_candles, get_range, local_to_utc_naive


def main():
    db = SessionLocal()
    try:
        configs = [cfg for cfg in list_all_epics(db) if cfg.strategy == STRATEGY_SWEEP_FVG_PDH_PDL]
        if not configs:
            print("No SWEEP_FVG_PDH_PDL config rows found. Run scripts/migrate_multi_strategy.py first.")
            return

        for cfg in configs:
            TZ = ZoneInfo(cfg.timezone)
            now_local = datetime.now(tz=TZ)
            session_date = now_local.date()

            prev_start_utc, prev_end_utc = previous_day_range_window(session_date, cfg.timezone)
            previous_day = get_range(db, cfg.epic, "M1", prev_start_utc, prev_end_utc)
            if not previous_day:
                print(f"[{cfg.epic}] No previous-day M1 candles found. Skipping.")
                continue

            scan_start_local = datetime.combine(session_date, cfg.trade_start, tzinfo=TZ)
            scan_start_utc = local_to_utc_naive(scan_start_local)
            scan_end_utc = local_to_utc_naive(now_local)
            candles = get_candles(db, cfg.epic, "M5", scan_start_utc, scan_end_utc)

            print(f"[{cfg.epic}] Previous day range: high={previous_day['high']}, low={previous_day['low']}")
            print(f"[{cfg.epic}] Scanning {len(candles)} M5 candles from {scan_start_utc} to {scan_end_utc}")

            if len(candles) < 5:
                print(f"[{cfg.epic}] Not enough M5 candles to scan.")
                continue

            found = None
            for i, candle in enumerate(candles):
                sweep = detect_sweep(candle, previous_day["high"], previous_day["low"])
                if not sweep:
                    continue
                for j in range(max(i + 2, 2), len(candles)):
                    fvg = detect_fvg_at(candles, j)
                    if fvg and fvg.direction == sweep.direction:
                        found = (sweep, fvg)
                        break
                if found:
                    break

            if found:
                sweep, fvg = found
                print(f"[{cfg.epic}] Would signal: {sweep.direction} sweep at {sweep.sweep_price}, FVG midpoint {fvg.midpoint}")
            else:
                print(f"[{cfg.epic}] No sweep + FVG setup found right now.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against the real dev database**

Run: `python3 scripts/dry_run_pdh_pdl.py`
Expected: For each `SWEEP_FVG_PDH_PDL` config row (5 of them, seeded disabled by Task 3's migration), prints the previous-day range and either a detected setup or "No sweep + FVG setup found right now." No exceptions.

- [ ] **Step 3: Commit**

```bash
git add scripts/dry_run_pdh_pdl.py
git commit -m "feat: add PDH/PDL dry-run validation script"
```

---

## Task 16: Dry-run validation script for VWAP

**Files:**
- Create: `scripts/dry_run_vwap.py`

**Interfaces:**
- Consumes: `get_candles`, `local_to_utc_naive` (from `scripts/run_auto_paper_once.py`), `calculate_vwap_bands` (Task 5)
- Produces: a standalone script, run manually, that prints what `VWAP_MEAN_REVERSION` would signal right now for every configured epic — no DB writes.

- [ ] **Step 1: Create `scripts/dry_run_vwap.py`**

```python
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import SessionLocal
from app.epics import STRATEGY_VWAP_MEAN_REVERSION, list_all_epics
from app.strategy.fvg_detector import detect_fvg_at
from app.strategy.sweep_detector import detect_sweep
from app.strategy.vwap import calculate_vwap_bands
from scripts.run_auto_paper_once import get_candles, local_to_utc_naive


def main():
    db = SessionLocal()
    try:
        configs = [cfg for cfg in list_all_epics(db) if cfg.strategy == STRATEGY_VWAP_MEAN_REVERSION]
        if not configs:
            print("No VWAP_MEAN_REVERSION config rows found. Run scripts/migrate_multi_strategy.py first.")
            return

        for cfg in configs:
            TZ = ZoneInfo(cfg.timezone)
            now_local = datetime.now(tz=TZ)
            session_date = now_local.date()

            scan_start_local = datetime.combine(session_date, cfg.trade_start, tzinfo=TZ)
            scan_start_utc = local_to_utc_naive(scan_start_local)
            scan_end_utc = local_to_utc_naive(now_local)
            candles = get_candles(db, cfg.epic, "M5", scan_start_utc, scan_end_utc)

            print(f"[{cfg.epic}] Scanning {len(candles)} M5 candles from {scan_start_utc} to {scan_end_utc}")

            if len(candles) < 5:
                print(f"[{cfg.epic}] Not enough M5 candles to scan.")
                continue

            deviation_threshold = (cfg.params or {}).get("deviation_threshold", 1.5)
            bands = calculate_vwap_bands(candles, deviation_threshold)

            found = None
            for i, candle in enumerate(candles):
                band = bands[i]
                if band is None:
                    continue
                sweep = detect_sweep(candle, band["high"], band["low"])
                if not sweep:
                    continue
                for j in range(max(i + 2, 2), len(candles)):
                    fvg = detect_fvg_at(candles, j)
                    if fvg and fvg.direction == sweep.direction:
                        found = (sweep, fvg, band)
                        break
                if found:
                    break

            if found:
                sweep, fvg, band = found
                print(
                    f"[{cfg.epic}] Would signal: {sweep.direction} fade at {sweep.sweep_price}, "
                    f"FVG midpoint {fvg.midpoint}, VWAP target {band['vwap']:.4f}"
                )
            else:
                print(f"[{cfg.epic}] No VWAP fade setup found right now.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against the real dev database**

Run: `python3 scripts/dry_run_vwap.py`
Expected: For each `VWAP_MEAN_REVERSION` config row (5 of them, seeded disabled), prints either a detected fade setup or "No VWAP fade setup found right now." No exceptions.

- [ ] **Step 3: Commit**

```bash
git add scripts/dry_run_vwap.py
git commit -m "feat: add VWAP dry-run validation script"
```

---

## Task 17: Final integration check

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run: `python3 -m pytest -q`
Expected: All tests pass — original 8 plus every test added in Tasks 1-11, zero failures/errors.

- [ ] **Step 2: Confirm new strategy config rows exist and are disabled in the live dev database**

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('trading_bot.db')
cur = conn.cursor()
cur.execute('SELECT strategy, COUNT(*), SUM(enabled) FROM epic_configs GROUP BY strategy')
for row in cur.fetchall():
    print(row)
"
```

Expected: three rows — `('SWEEP_FVG_OPENING_RANGE', 9, <n enabled by CAPITAL_EPICS>)`, `('SWEEP_FVG_PDH_PDL', 5, 0)`, `('VWAP_MEAN_REVERSION', 5, 0)`. (9 = original 5 + 4 new ICT-window rows.)

- [ ] **Step 3: Confirm the dashboard boots cleanly with the full config set**

Run the same headless boot check used in Tasks 12-14:

```bash
(streamlit run dashboard.py --server.headless true --server.port 8599 > /tmp/dashboard_boot.log 2>&1 &) && \
  sleep 6 && \
  curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8599 && \
  pkill -f "streamlit run dashboard.py" && \
  cat /tmp/dashboard_boot.log
```

Expected: HTTP `200`, no tracebacks. The Epic & Session Management table shows 19 rows; Strategy Performance shows 3 rows (all zero trades unless real signals have fired since migration).

- [ ] **Step 4: Run both dry-run scripts once more for a final sanity check**

```bash
python3 scripts/dry_run_pdh_pdl.py
python3 scripts/dry_run_vwap.py
```

Expected: no exceptions; output reflects current real candle data.

- [ ] **Step 5: Confirm git history is clean**

```bash
git log --oneline -20
git status
```

Expected: one commit per task (11 feature/refactor commits from Tasks 1-2, 3-11, 12-16, plus the earlier design-spec commit), working tree clean.

No commit for this task — it's verification only.
