#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from build_etf_mobile_daily_site import INDEX_HTML, build_payload
from generate_etf_observation import simplify_name, theme_of


def report_html(data_file: str) -> str:
    return (
        INDEX_HTML
        .replace("./assets/", "../assets/")
        .replace("./data/latest.js", f"../data/{data_file}")
    )


def build_daily_frame(history: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
    day = history[history["date"] == date].copy()
    day = day.dropna(subset=["rps20"]).copy()
    day["code"] = day["code"].astype(str).str.zfill(6)
    day["short_name"] = day["name"].map(simplify_name)
    day["theme"] = day["name"].map(theme_of)
    day = day.sort_values("rps20", ascending=False).reset_index(drop=True)
    day.insert(0, "rank", range(1, len(day) + 1))
    return day


def main() -> None:
    parser = argparse.ArgumentParser(description="Build one month of ETF RPS static report pages.")
    parser.add_argument("--history-csv", default="output/etf_rps_chart_universe/etf_nav_daily_chart_universe_2026-06-12.csv")
    parser.add_argument("--out", default="etf-rps-site")
    parser.add_argument("--latest-date", default="2026-06-12")
    parser.add_argument("--days", type=int, default=31)
    args = parser.parse_args()

    out = Path(args.out)
    (out / "assets").mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(parents=True, exist_ok=True)
    (out / "reports").mkdir(parents=True, exist_ok=True)

    history = pd.read_csv(args.history_csv, dtype={"code": str})
    history["date"] = pd.to_datetime(history["date"])
    latest = pd.Timestamp(args.latest_date)
    start = latest - pd.Timedelta(days=args.days)
    dates = sorted(date for date in history["date"].drop_duplicates() if start <= date <= latest)

    history_entries = [
        {
            "date": str(date.date()),
            "title": f"{date.date()} ETF RPS 日报",
            "url": f"reports/{date.date()}.html",
        }
        for date in reversed(dates)
    ]

    built = []
    latest_payload = None
    for date in dates:
        date_text = str(date.date())
        day = build_daily_frame(history, date)
        if day.empty:
            continue

        payload = build_payload(date_text, day)
        payload["history"] = history_entries
        data_js = "window.ETF_RPS_DAILY = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n"
        (out / "data" / f"{date_text}.js").write_text(data_js, encoding="utf-8")
        (out / "data" / f"{date_text}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if date != latest:
            (out / "reports" / f"{date_text}.html").write_text(report_html(f"{date_text}.js"), encoding="utf-8")
        built.append(date_text)
        if date == latest:
            latest_payload = payload
            (out / "data" / "latest.js").write_text(data_js, encoding="utf-8")
            (out / "data" / "latest.json").write_text(
                json.dumps(
                    {
                        "date": date_text,
                        "latestReport": {
                            "date": date_text,
                            "title": f"{date_text} ETF RPS 日报",
                            "url": f"reports/{date_text}.html",
                            "summary": payload["summary"],
                        },
                        "history": history_entries,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    if latest_payload is None:
        raise RuntimeError(f"No payload generated for latest date {args.latest_date}")

    print(f"Built {len(built)} report pages")
    print(f"Latest: {args.latest_date}")
    print(f"Range: {built[0]} to {built[-1]}")


if __name__ == "__main__":
    main()
