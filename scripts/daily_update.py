#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
AUTOMATION = ROOT / "automation"
HISTORY_CSV = AUTOMATION / "etf_nav_daily_chart_universe_latest.csv"
UNIVERSE_CSV = AUTOMATION / "chart_universe_codes.csv"


def beijing_today() -> str:
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def latest_history_date() -> str:
    history = pd.read_csv(HISTORY_CSV, usecols=["date"])
    return str(pd.to_datetime(history["date"]).max().date())


def run(args: list[str]) -> None:
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def copy_tree_contents(source: Path, target: Path) -> None:
    for item in source.iterdir():
        dest = target / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)


def write_marker(status: str, date_text: str, detail: str = "") -> None:
    marker = {
        "status": status,
        "date": date_text,
        "detail": detail,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    (AUTOMATION / "last_run.json").write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch latest ETF quotes and rebuild the static RPS site.")
    parser.add_argument("--date", default="", help="Target trade date, YYYY-MM-DD. Defaults to Beijing today.")
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--pause", type=float, default=0.6)
    parser.add_argument("--min-rows", type=int, default=160)
    args = parser.parse_args()

    target_date = args.date or beijing_today()
    current_latest = latest_history_date()
    if target_date <= current_latest:
        write_marker("skipped", target_date, f"history already includes {current_latest}")
        print(f"Already up to date: latest={current_latest}, target={target_date}")
        return

    build_dir = ROOT / "_daily_build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    update_out = build_dir / "rps"
    try:
        run(
            [
                str(SCRIPTS / "update_etf_chart_universe_with_tencent.py"),
                "--history-csv",
                str(HISTORY_CSV),
                "--universe",
                str(UNIVERSE_CSV),
                "--date",
                target_date,
                "--out-dir",
                str(update_out),
                "--batch-size",
                str(args.batch_size),
                "--pause",
                str(args.pause),
                "--min-rows",
                str(args.min_rows),
            ]
        )
    except subprocess.CalledProcessError as exc:
        write_marker("skipped", target_date, f"quote fetch/build failed: {exc}")
        print(f"No update generated for {target_date}: {exc}")
        return

    next_history = update_out / f"etf_nav_daily_chart_universe_{target_date}.csv"
    latest_daily = update_out / f"daily_observation_full_{target_date}.csv"
    if not next_history.exists() or not latest_daily.exists():
        raise RuntimeError(f"Missing generated files for {target_date}")

    trend_out = build_dir / "site"
    run(
        [
            str(SCRIPTS / "build_etf_trend_decision.py"),
            "--history-csv",
            str(next_history),
            "--date",
            target_date,
            "--today",
            target_date,
            "--out",
            str(trend_out),
        ]
    )
    run(
        [
            str(SCRIPTS / "build_etf_rps_site_history.py"),
            "--history-csv",
            str(next_history),
            "--latest-date",
            target_date,
            "--out",
            str(trend_out),
        ]
    )

    for name in ("index.html", "payload.json"):
        shutil.copy2(trend_out / name, ROOT / name)
    for dirname in ("reports", "data"):
        copy_tree_contents(trend_out / dirname, ROOT / dirname)

    shutil.copy2(next_history, HISTORY_CSV)
    write_marker("updated", target_date, "site and automation baseline rebuilt")
    print(f"Updated ETF RPS site to {target_date}")


if __name__ == "__main__":
    main()
