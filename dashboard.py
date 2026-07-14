import json
import sys
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import func

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import SessionLocal
from app.models import Candle, PaperAccount, PaperTrade, Signal
from app.paper.auto_paper import (
    cancel_pending_trades,
    get_open_trades,
    is_paused,
    is_stopped_today,
    reset_paper_account,
    set_paused,
    stop_trading_today,
)
from app.epics import ALL_STRATEGIES, CURATED_TIMEZONES, delete_epic_config, list_all_epics, list_enabled_epics, upsert_epic_config

load_dotenv()

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

st.set_page_config(
    page_title="NY Open FVG Bot Dashboard",
    page_icon="📈",
    layout="wide",
)


def utc_naive_to_ny(dt):
    if not dt:
        return None
    return dt.replace(tzinfo=UTC).astimezone(NY)


def ny_to_utc_naive(dt):
    return dt.astimezone(UTC).replace(tzinfo=None)


def fmt_money(value):
    if value is None:
        return "$0.00"
    return f"${float(value):,.2f}"


def fmt_num(value, decimals=2):
    if value is None:
        return "-"
    return f"{float(value):,.{decimals}f}"


def today_start_utc():
    today_ny = datetime.now(tz=NY).date()
    start_ny = datetime.combine(today_ny, dtime.min, tzinfo=NY)
    return ny_to_utc_naive(start_ny)


@st.cache_data(ttl=10)
def load_dashboard_data(symbol: str, candle_limit: int):
    db = SessionLocal()
    try:
        account = db.query(PaperAccount).filter(PaperAccount.name == "default").first()
        latest_m1 = (
            db.query(Candle)
            .filter(Candle.symbol == symbol, Candle.timeframe == "M1")
            .order_by(Candle.candle_time.desc())
            .first()
        )
        latest_m5 = (
            db.query(Candle)
            .filter(Candle.symbol == symbol, Candle.timeframe == "M5")
            .order_by(Candle.candle_time.desc())
            .first()
        )
        open_trades = (
            db.query(PaperTrade)
            .filter(PaperTrade.status.in_(["PENDING", "ACTIVE"]))
            .order_by(PaperTrade.created_at.asc())
            .all()
        )
        all_trades = (
            db.query(PaperTrade)
            .order_by(PaperTrade.created_at.desc())
            .limit(500)
            .all()
        )
        signals = (
            db.query(Signal)
            .order_by(Signal.signal_time.desc())
            .limit(200)
            .all()
        )
        candles_m1 = (
            db.query(Candle)
            .filter(Candle.symbol == symbol, Candle.timeframe == "M1")
            .order_by(Candle.candle_time.desc())
            .limit(candle_limit)
            .all()
        )
        candles_m5 = (
            db.query(Candle)
            .filter(Candle.symbol == symbol, Candle.timeframe == "M5")
            .order_by(Candle.candle_time.desc())
            .limit(candle_limit)
            .all()
        )
        start_utc = today_start_utc()
        trades_today = db.query(PaperTrade).filter(PaperTrade.created_at >= start_utc).count()
        wins_today = db.query(PaperTrade).filter(PaperTrade.created_at >= start_utc, PaperTrade.result == "WIN").count()
        losses_today = db.query(PaperTrade).filter(PaperTrade.created_at >= start_utc, PaperTrade.result == "LOSS").count()
        pnl_today = db.query(func.coalesce(func.sum(PaperTrade.pnl_amount), 0)).filter(PaperTrade.created_at >= start_utc).scalar()
        paused = is_paused(db)
        stopped_today = is_stopped_today(db)

        return {
            "account": account,
            "latest_m1": latest_m1,
            "latest_m5": latest_m5,
            "open_trades": open_trades,
            "all_trades": all_trades,
            "signals": signals,
            "candles_m1": list(reversed(candles_m1)),
            "candles_m5": list(reversed(candles_m5)),
            "trades_today": trades_today,
            "wins_today": wins_today,
            "losses_today": losses_today,
            "pnl_today": pnl_today,
            "paused": paused,
            "stopped_today": stopped_today,
        }
    finally:
        db.close()


def get_levels(symbol: str, cfg):
    db = SessionLocal()
    try:
        tz = ZoneInfo(cfg.timezone)
        latest = (
            db.query(Candle)
            .filter(Candle.symbol == symbol, Candle.timeframe == "M1")
            .order_by(Candle.candle_time.desc())
            .first()
        )
        if not latest:
            return None

        latest_local = latest.candle_time.replace(tzinfo=UTC).astimezone(tz)
        session_date = latest_local.date()

        def range_query(start_local, end_local):
            candles = (
                db.query(Candle)
                .filter(
                    Candle.symbol == symbol,
                    Candle.timeframe == "M1",
                    Candle.candle_time >= start_local.astimezone(UTC).replace(tzinfo=None),
                    Candle.candle_time < end_local.astimezone(UTC).replace(tzinfo=None),
                )
                .all()
            )
            if not candles:
                return None
            return {
                "high": max(float(c.high) for c in candles),
                "low": min(float(c.low) for c in candles),
                "count": len(candles),
            }

        return {
            "session_date": session_date,
            "latest_local": latest_local,
            "overnight": range_query(
                datetime.combine(session_date - timedelta(days=1), dtime(18, 0), tzinfo=tz),
                datetime.combine(session_date, cfg.range_short_start, tzinfo=tz),
            ),
            "range_short": range_query(
                datetime.combine(session_date, cfg.range_short_start, tzinfo=tz),
                datetime.combine(session_date, cfg.range_short_end, tzinfo=tz),
            ),
            "range_long": range_query(
                datetime.combine(session_date, cfg.range_long_start, tzinfo=tz),
                datetime.combine(session_date, cfg.range_long_end, tzinfo=tz),
            ),
        }
    finally:
        db.close()


def trade_rows(trades):
    rows = []
    for t in trades:
        rows.append(
            {
                "ID": t.id,
                "Created NY": utc_naive_to_ny(t.created_at).strftime("%Y-%m-%d %H:%M") if t.created_at else "-",
                "Symbol": t.symbol,
                "Direction": t.direction,
                "Status": t.status,
                "Entry": float(t.entry_price) if t.entry_price is not None else None,
                "SL": float(t.stop_loss) if t.stop_loss is not None else None,
                "TP": float(t.take_profit) if t.take_profit is not None else None,
                "Result": t.result,
                "R": float(t.r_multiple) if t.r_multiple is not None else None,
                "P/L": float(t.pnl_amount) if t.pnl_amount is not None else None,
                "Risk": float(t.risk_amount) if t.risk_amount is not None else None,
            }
        )
    return pd.DataFrame(rows)


def signal_rows(signals):
    rows = []
    for s in signals:
        rows.append(
            {
                "ID": s.id,
                "Time NY": utc_naive_to_ny(s.signal_time).strftime("%Y-%m-%d %H:%M") if s.signal_time else "-",
                "Symbol": s.symbol,
                "Direction": s.direction,
                "Setup": s.setup_type,
                "Status": s.status,
                "Entry": float(s.entry_price) if s.entry_price is not None else None,
                "SL": float(s.stop_loss) if s.stop_loss is not None else None,
                "TP": float(s.take_profit) if s.take_profit is not None else None,
                "OR High": float(s.opening_range_high) if s.opening_range_high is not None else None,
                "OR Low": float(s.opening_range_low) if s.opening_range_low is not None else None,
            }
        )
    return pd.DataFrame(rows)


def candle_rows(candles):
    rows = []
    for c in candles:
        rows.append(
            {
                "Time NY": utc_naive_to_ny(c.candle_time).strftime("%Y-%m-%d %H:%M"),
                "Open": float(c.open),
                "High": float(c.high),
                "Low": float(c.low),
                "Close": float(c.close),
                "Volume": float(c.volume or 0),
            }
        )
    return pd.DataFrame(rows)


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


def price_chart_df(candles, levels, open_trades, overlays):
    rows = []
    for c in candles:
        rows.append(
            {
                "Time NY": utc_naive_to_ny(c.candle_time).strftime("%H:%M"),
                "Close": float(c.close),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    if levels:
        mapping = {
            "Overnight high": ("overnight", "high"),
            "Overnight low": ("overnight", "low"),
            "Range short high": ("range_short", "high"),
            "Range short low": ("range_short", "low"),
            "Range long high": ("range_long", "high"),
            "Range long low": ("range_long", "low"),
        }
        for label, path in mapping.items():
            if label in overlays:
                block = levels.get(path[0])
                if block:
                    df[label] = block[path[1]]

    if open_trades:
        trade = open_trades[0]
        if "Active entry" in overlays and trade.entry_price is not None:
            df["Active entry"] = float(trade.entry_price)
        if "Active SL" in overlays and trade.stop_loss is not None:
            df["Active SL"] = float(trade.stop_loss)
        if "Active TP" in overlays and trade.take_profit is not None:
            df["Active TP"] = float(trade.take_profit)

    return df


def balance_curve(account, trades):
    if not account:
        return pd.DataFrame()
    chronological = sorted(
        [t for t in trades if t.status == "CLOSED" and t.pnl_amount is not None],
        key=lambda x: x.exit_time or x.updated_at or x.created_at,
    )
    balance = float(account.starting_balance)
    rows = [{"Trade": 0, "Balance": balance}]
    for i, trade in enumerate(chronological, start=1):
        balance += float(trade.pnl_amount)
        rows.append({"Trade": i, "Balance": balance})
    return pd.DataFrame(rows)


def daily_pnl_df(trades):
    rows = []
    for t in trades:
        if t.pnl_amount is None:
            continue
        dt = t.exit_time or t.updated_at or t.created_at
        if not dt:
            continue
        rows.append({"Date": utc_naive_to_ny(dt).strftime("%Y-%m-%d"), "P/L": float(t.pnl_amount)})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.groupby("Date", as_index=False)["P/L"].sum()


def r_multiple_df(trades):
    chronological = sorted(
        [t for t in trades if t.r_multiple is not None],
        key=lambda x: x.exit_time or x.updated_at or x.created_at,
    )
    rows = []
    for i, t in enumerate(chronological, start=1):
        rows.append({"Trade": i, "R Multiple": float(t.r_multiple)})
    return pd.DataFrame(rows)


def win_loss_df(trades):
    wins = sum(1 for t in trades if t.result == "WIN")
    losses = sum(1 for t in trades if t.result == "LOSS")
    manual = sum(1 for t in trades if t.result == "MANUAL_CLOSE")
    cancelled = sum(1 for t in trades if t.status == "CANCELLED")
    return pd.DataFrame(
        [
            {"Result": "Wins", "Count": wins},
            {"Result": "Losses", "Count": losses},
            {"Result": "Manual close", "Count": manual},
            {"Result": "Cancelled", "Count": cancelled},
        ]
    )


st.title("📈 NY Open FVG Bot Dashboard")
st.caption("US100 AUTO_PAPER monitoring, selectable charts, history, levels, and paper balance.")

st.subheader("Strategy performance")
st.dataframe(strategy_performance_rows(), hide_index=True, use_container_width=True)

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

with st.sidebar:
    st.header("Controls")

    db = SessionLocal()
    try:
        enabled_configs = list_enabled_epics(db)
    finally:
        db.close()

    if not enabled_configs:
        st.warning("No epics enabled. Enable at least one epic in the Epic & Session Management panel above.")
        st.stop()

    epic_options = [cfg.epic for cfg in enabled_configs]
    symbol = st.selectbox("Symbol / EPIC", epic_options, index=0)
    selected_epic_config = next(cfg for cfg in enabled_configs if cfg.epic == symbol)

    refresh_seconds = st.selectbox("Auto refresh", [0, 10, 30, 60], index=2, format_func=lambda x: "Off" if x == 0 else f"{x}s")

    st.divider()
    st.header("Chart options")
    visible_charts = st.multiselect(
        "Select dashboard charts",
        [
            "Price chart",
            "Balance curve",
            "Daily P/L",
            "Trade R multiples",
            "Win/loss breakdown",
        ],
        default=["Price chart", "Balance curve"],
    )
    chart_timeframe = st.selectbox("Price chart timeframe", ["M1", "M5"], index=0)
    candle_limit = st.slider("Candles to load", min_value=100, max_value=1000, value=300, step=50)
    price_points = st.slider("Candles displayed on price chart", min_value=50, max_value=500, value=150, step=25)
    overlays = st.multiselect(
        "Price chart overlays",
        [
            "Overnight high",
            "Overnight low",
            "Range short high",
            "Range short low",
            "Range long high",
            "Range long low",
            "Active entry",
            "Active SL",
            "Active TP",
        ],
        default=["Active entry", "Active SL", "Active TP"],
    )

    st.divider()
    if st.button("🔄 Manual refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    db = SessionLocal()
    try:
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("⏸ Pause", use_container_width=True):
                set_paused(db, True)
                st.cache_data.clear()
                st.rerun()
        with col_b:
            if st.button("▶️ Resume", use_container_width=True):
                set_paused(db, False)
                st.cache_data.clear()
                st.rerun()

        if st.button("🛑 Stop today", use_container_width=True):
            stop_trading_today(db)
            st.cache_data.clear()
            st.rerun()

        if st.button("❌ Cancel pending", use_container_width=True):
            cancel_pending_trades(db)
            st.cache_data.clear()
            st.rerun()

        confirm_reset = st.checkbox("Confirm paper reset")
        if st.button("♻️ Reset paper account", use_container_width=True, disabled=not confirm_reset):
            reset_paper_account(db)
            st.cache_data.clear()
            st.rerun()
    finally:
        db.close()

try:
    data = load_dashboard_data(symbol, candle_limit)
except Exception as exc:
    st.error(f"Dashboard failed to load: {exc}")
    st.stop()

account = data["account"]
latest_m1 = data["latest_m1"]
latest_price = float(latest_m1.close) if latest_m1 else None
latest_ny = utc_naive_to_ny(latest_m1.candle_time).strftime("%Y-%m-%d %H:%M:%S %Z") if latest_m1 else "No candle"

col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("Paper balance", fmt_money(account.balance if account else 0))
col2.metric("Latest price", fmt_num(latest_price))
col3.metric("Open trades", len(data["open_trades"]))
col4.metric("Trades today", data["trades_today"])
col5.metric("Wins / Losses", f"{data['wins_today']} / {data['losses_today']}")
col6.metric("P/L today", fmt_money(data["pnl_today"]))

status_col1, status_col2, status_col3 = st.columns(3)
status_col1.info(f"Latest NY candle: {latest_ny}")
status_col2.warning("Paused" if data["paused"] else "Trading allowed")
status_col3.warning("Stopped today" if data["stopped_today"] else "Not stopped today")

levels = get_levels(symbol, selected_epic_config)
with st.expander(f"{selected_epic_config.session_name} session levels", expanded=True):
    if not levels:
        st.write("No levels available yet.")
    else:
        st.write(f"Session date: **{levels['session_date']}**")
        lev_cols = st.columns(3)
        for label, key, col in [("Overnight", "overnight", lev_cols[0]), ("Range short", "range_short", lev_cols[1]), ("Range long", "range_long", lev_cols[2])]:
            val = levels[key]
            if val:
                col.metric(f"{label} high", fmt_num(val["high"]))
                col.metric(f"{label} low", fmt_num(val["low"]))
                col.caption(f"Candles: {val['count']}")
            else:
                col.info(f"{label} not ready")

st.subheader("Selected charts")
chart_cols = st.columns(2)
chart_index = 0

def next_chart_container():
    global chart_index
    c = chart_cols[chart_index % 2]
    chart_index += 1
    return c

if "Price chart" in visible_charts:
    with next_chart_container():
        st.markdown(f"### {symbol} price chart ({chart_timeframe})")
        candles = data["candles_m1"] if chart_timeframe == "M1" else data["candles_m5"]
        chart_df = price_chart_df(candles[-price_points:], levels, data["open_trades"], overlays)
        if not chart_df.empty:
            st.line_chart(chart_df, x="Time NY", use_container_width=True)
        else:
            st.write("No candle data available.")

if "Balance curve" in visible_charts:
    with next_chart_container():
        st.markdown("### Balance curve")
        curve = balance_curve(account, data["all_trades"])
        if not curve.empty:
            st.line_chart(curve, x="Trade", y="Balance", use_container_width=True)
        else:
            st.write("No closed trades yet.")

if "Daily P/L" in visible_charts:
    with next_chart_container():
        st.markdown("### Daily P/L")
        pnl_df = daily_pnl_df(data["all_trades"])
        if not pnl_df.empty:
            st.bar_chart(pnl_df, x="Date", y="P/L", use_container_width=True)
        else:
            st.write("No P/L history yet.")

if "Trade R multiples" in visible_charts:
    with next_chart_container():
        st.markdown("### Trade R multiples")
        r_df = r_multiple_df(data["all_trades"])
        if not r_df.empty:
            st.bar_chart(r_df, x="Trade", y="R Multiple", use_container_width=True)
        else:
            st.write("No closed trades yet.")

if "Win/loss breakdown" in visible_charts:
    with next_chart_container():
        st.markdown("### Win/loss breakdown")
        wl_df = win_loss_df(data["all_trades"])
        st.bar_chart(wl_df, x="Result", y="Count", use_container_width=True)

st.divider()
st.subheader("Open / pending paper trades")
open_df = trade_rows(data["open_trades"])
if open_df.empty:
    st.write("No open or pending paper trades.")
else:
    st.dataframe(open_df, use_container_width=True, hide_index=True)

st.subheader("Paper trade history")
history_df = trade_rows(data["all_trades"])
if history_df.empty:
    st.write("No paper trades yet.")
else:
    st.dataframe(history_df, use_container_width=True, hide_index=True)

st.subheader("Signal history")
signal_df = signal_rows(data["signals"])
if signal_df.empty:
    st.write("No signals yet.")
else:
    st.dataframe(signal_df, use_container_width=True, hide_index=True)

st.subheader("Recent candles")
tab1, tab2 = st.tabs(["M1", "M5"])
with tab1:
    st.dataframe(candle_rows(data["candles_m1"][-100:]), use_container_width=True, hide_index=True)
with tab2:
    st.dataframe(candle_rows(data["candles_m5"][-100:]), use_container_width=True, hide_index=True)

if refresh_seconds:
    time.sleep(refresh_seconds)
    st.cache_data.clear()
    st.rerun()
