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
