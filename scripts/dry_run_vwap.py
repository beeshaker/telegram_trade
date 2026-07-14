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
