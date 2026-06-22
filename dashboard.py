import os
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


def get_levels(symbol: str):
    db = SessionLocal()
    try:
        latest = (
            db.query(Candle)
            .filter(Candle.symbol == symbol, Candle.timeframe == "M1")
            .order_by(Candle.candle_time.desc())
            .first()
        )
        if not latest:
            return None

        latest_ny = utc_naive_to_ny(latest.candle_time)
        session_date = latest_ny.date()

        def range_query(start_ny, end_ny):
            candles = (
                db.query(Candle)
                .filter(
                    Candle.symbol == symbol,
                    Candle.timeframe == "M1",
                    Candle.candle_time >= ny_to_utc_naive(start_ny),
                    Candle.candle_time < ny_to_utc_naive(end_ny),
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
            "latest_ny": latest_ny,
            "overnight": range_query(
                datetime.combine(session_date - timedelta(days=1), dtime(18, 0), tzinfo=NY),
                datetime.combine(session_date, dtime(9, 30), tzinfo=NY),
            ),
            "ny15": range_query(
                datetime.combine(session_date, dtime(9, 30), tzinfo=NY),
                datetime.combine(session_date, dtime(9, 45), tzinfo=NY),
            ),
            "ny30": range_query(
                datetime.combine(session_date, dtime(9, 30), tzinfo=NY),
                datetime.combine(session_date, dtime(10, 0), tzinfo=NY),
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
            "NY 15m high": ("ny15", "high"),
            "NY 15m low": ("ny15", "low"),
            "NY 30m high": ("ny30", "high"),
            "NY 30m low": ("ny30", "low"),
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

with st.sidebar:
    st.header("Controls")
    symbol = st.text_input("Symbol / EPIC", os.getenv("CAPITAL_EPIC", "US100"))
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
            "NY 15m high",
            "NY 15m low",
            "NY 30m high",
            "NY 30m low",
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

levels = get_levels(symbol)
with st.expander("NY session levels", expanded=True):
    if not levels:
        st.write("No levels available yet.")
    else:
        st.write(f"Session date: **{levels['session_date']}**")
        lev_cols = st.columns(3)
        for label, key, col in [("Overnight", "overnight", lev_cols[0]), ("NY 15m", "ny15", lev_cols[1]), ("NY 30m", "ny30", lev_cols[2])]:
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
