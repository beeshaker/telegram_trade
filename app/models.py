from datetime import datetime, time

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, Time, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "candle_time", name="uq_candle_symbol_tf_time"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(50), index=True)
    timeframe: Mapped[str] = mapped_column(String(10), index=True)
    candle_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    open: Mapped[float] = mapped_column(Numeric(18, 5))
    high: Mapped[float] = mapped_column(Numeric(18, 5))
    low: Mapped[float] = mapped_column(Numeric(18, 5))
    close: Mapped[float] = mapped_column(Numeric(18, 5))
    volume: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SessionLevel(Base):
    __tablename__ = "session_levels"
    __table_args__ = (UniqueConstraint("symbol", "session_date", name="uq_session_symbol_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(50), index=True)
    session_date = mapped_column(Date, index=True)
    timezone: Mapped[str] = mapped_column(String(50))
    previous_session_high: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)
    previous_session_low: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)
    ny_15m_high: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)
    ny_15m_low: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)
    ny_30m_high: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)
    ny_30m_low: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(50), index=True)
    strategy: Mapped[str] = mapped_column(String(50), index=True, default="SWEEP_FVG_OPENING_RANGE")
    signal_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    direction: Mapped[str] = mapped_column(String(10), index=True)
    setup_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50), default="DETECTED", index=True)

    session_high: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)
    session_low: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)
    opening_range_high: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)
    opening_range_low: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)

    sweep_level: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sweep_price: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)

    fvg_low: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)
    fvg_high: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)

    entry_price: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)
    risk_reward: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)

    mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    telegram_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    paper_trade = relationship("PaperTrade", back_populates="signal", uselist=False)


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(50), index=True)
    direction: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", index=True)

    entry_price: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)

    entry_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)

    risk_amount: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    position_size: Mapped[float | None] = mapped_column(Numeric(18, 5), nullable=True)

    result: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pnl_amount: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    r_multiple: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    signal = relationship("Signal", back_populates="paper_trade")


class BotLog(Base):
    __tablename__ = "bot_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(20), index=True)
    module: Mapped[str | None] = mapped_column(String(100), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)



class PaperAccount(Base):
    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), default="default", index=True)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    starting_balance: Mapped[float] = mapped_column(Numeric(18, 2), default=1000)
    balance: Mapped[float] = mapped_column(Numeric(18, 2), default=1000)
    equity: Mapped[float] = mapped_column(Numeric(18, 2), default=1000)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BotState(Base):
    __tablename__ = "bot_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), index=True)
    value: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
