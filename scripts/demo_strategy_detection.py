import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.risk.risk_manager import RiskManager
from app.strategy.fvg_detector import find_latest_fvg
from app.strategy.sweep_detector import detect_sweep

candles = [
    {"time": "09:45", "open": 100, "high": 104, "low": 99, "close": 103},
    {"time": "09:50", "open": 103, "high": 106, "low": 102, "close": 104},
    {"time": "09:55", "open": 104, "high": 107, "low": 101, "close": 103},
    {"time": "10:00", "open": 103, "high": 108, "low": 102, "close": 104},
    {"time": "10:05", "open": 104, "high": 109, "low": 105, "close": 106},
    {"time": "10:10", "open": 106, "high": 107, "low": 98, "close": 99},
    {"time": "10:15", "open": 99, "high": 100, "low": 95, "close": 96},
]

opening_range_high = 105
opening_range_low = 95

sweep = None
for candle in candles:
    sweep = detect_sweep(candle, opening_range_high, opening_range_low)
    if sweep:
        break

print("Sweep:", sweep)

if sweep:
    fvg = find_latest_fvg(candles, direction=sweep.direction)
    print("FVG:", fvg)
    if fvg:
        plan = RiskManager().build_trade_plan("NAS100", sweep.direction, fvg.midpoint, sweep.sweep_price, buffer=0.5)
        print("Trade Plan:", plan)
