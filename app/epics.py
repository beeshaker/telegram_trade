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

    enabled_epics = set(_parse_capital_epics())
    existing = {
        (cfg.epic, cfg.strategy, cfg.session_name)
        for cfg in db.query(EpicConfig).all()
    }

    added = False
    for seed in _SEED_EPICS:
        key = (seed["epic"], seed["strategy"], seed["session_name"])
        if key in existing:
            continue
        db.add(EpicConfig(enabled=seed["epic"] in enabled_epics, **seed))
        added = True
    for seed in _SEED_EPICS_ADDITIONAL:
        key = (seed["epic"], seed["strategy"], seed["session_name"])
        if key in existing:
            continue
        db.add(EpicConfig(enabled=False, **seed))
        added = True

    if added:
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


def get_epic_config_for_strategy(db: Session, epic: str, strategy: str) -> EpicConfig | None:
    """Return the first (lowest-id / originally-seeded) config row for an epic+strategy.

    Useful for callers like the Telegram /levels command that predate
    multiple session windows per epic+strategy and just want "the" config.
    """
    Base.metadata.create_all(bind=db.get_bind())
    return (
        db.query(EpicConfig)
        .filter(EpicConfig.epic == epic, EpicConfig.strategy == strategy)
        .order_by(EpicConfig.id.asc())
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
