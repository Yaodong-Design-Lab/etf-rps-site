#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from datetime import date as date_cls
from pathlib import Path

from build_etf_trend_decision import build_payload, render, set_history_prefix


def write_json_pair(data_dir: Path, date_text: str, payload: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / f"{date_text}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (data_dir / f"{date_text}.js").write_text(
        "window.COCKPIT_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild selected ETF trend pages from a known-good history CSV.")
    parser.add_argument("--history-csv", default="automation/etf_nav_daily_chart_universe_latest.csv")
    parser.add_argument("--latest-date", default="")
    parser.add_argument("--today", default=str(date_cls.today()))
    parser.add_argument("--dates", nargs="*", default=[])
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    history_csv = Path(args.history_csv)
    if not history_csv.is_absolute():
        history_csv = root / history_csv

    latest_date = args.latest_date
    if not latest_date:
        latest_meta = json.loads((root / "data" / "latest.json").read_text(encoding="utf-8"))
        latest_date = latest_meta["date"]

    latest_payload = build_payload(history_csv, latest_date, args.today)
    index_payload = set_history_prefix(latest_payload, "reports/")
    index_payload["navPrefix"] = "./"
    report_history = set_history_prefix(latest_payload, "")["history"]
    dates = args.dates or [item["date"] for item in latest_payload["history"]]

    (root / "index.html").write_text(render(index_payload), encoding="utf-8")
    (root / "payload.json").write_text(json.dumps(latest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_json_pair(root / "data", latest_date, index_payload)
    shutil.copyfile(root / "data" / f"{latest_date}.js", root / "data" / "latest.js")
    (root / "data" / "latest.json").write_text(
        json.dumps(
            {
                "date": latest_date,
                "latestReport": {
                    "date": latest_date,
                    "title": f"{latest_date} ETF RPS 日报",
                    "url": f"reports/{latest_date}.html?v=trend-decision",
                    "summary": latest_payload["decision"],
                },
                "history": index_payload["history"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    for date_text in dates:
        payload = build_payload(history_csv, date_text, args.today)
        payload["history"] = report_history
        payload["navPrefix"] = "../"
        (reports / f"{date_text}.html").write_text(render(payload), encoding="utf-8")
        write_json_pair(root / "data", date_text, payload)
        print(reports / f"{date_text}.html")


if __name__ == "__main__":
    main()
