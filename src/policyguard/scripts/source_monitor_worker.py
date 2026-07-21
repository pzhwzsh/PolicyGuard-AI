"""Long-running source monitor worker for a process manager or Task Scheduler."""

import argparse
import subprocess
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=int, default=21600)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.interval_seconds < 300 and not args.once:
        raise SystemExit("interval must be at least 300 seconds")
    while True:
        completed = subprocess.run(
            [sys.executable, "-m", "policyguard.scripts.check_source_updates"],
            check=False,
        )
        if completed.returncode != 0:
            print(f"source monitor failed with exit code {completed.returncode}")
        if args.once:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
