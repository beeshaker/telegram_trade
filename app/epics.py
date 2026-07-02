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

_SEED_EPICS = [
    {
        "epic": "US100",
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
    db.commit()


def list_all_epics(db: Session) -> list[EpicConfig]:
    ensure_seeded(db)
    return db.query(EpicConfig).order_by(EpicConfig.epic.asc()).all()


def list_enabled_epics(db: Session) -> list[EpicConfig]:
    ensure_seeded(db)
    return (
        db.query(EpicConfig)
        .filter(EpicConfig.enabled.is_(True))
        .order_by(EpicConfig.epic.asc())
        .all()
    )


def get_epic_config(db: Session, epic: str) -> EpicConfig | None:
    Base.metadata.create_all(bind=db.get_bind())
    return db.query(EpicConfig).filter(EpicConfig.epic == epic).first()


def upsert_epic_config(db: Session, epic: str, **fields) -> EpicConfig:
    cfg = get_epic_config(db, epic)
    if cfg is None:
        cfg = EpicConfig(epic=epic, **fields)
        db.add(cfg)
    else:
        for key, value in fields.items():
            setattr(cfg, key, value)

    db.commit()
    db.refresh(cfg)
    return cfg


def delete_epic_config(db: Session, epic: str) -> bool:
    cfg = get_epic_config(db, epic)
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
