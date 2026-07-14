import subprocess
import sys
import time
from datetime import datetime


def run_command(command):
    print("\n" + "=" * 80)
    print("Running:", " ".join(command))
    print("Time:", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"))
    print("=" * 80)

    result = subprocess.run(command, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)


def main():
    print("Starting AUTO_PAPER loop.")
    print("Press CTRL + C to stop.")

    py = sys.executable

    while True:
        run_command([py, "scripts/sync_capital_candles.py"])
        run_command([py, "scripts/build_m5_candles.py"])
        run_command([py, "scripts/run_auto_paper_once.py"])
        print("\nSleeping for 60 seconds...")
        time.sleep(60)


if __name__ == "__main__":
    main()
