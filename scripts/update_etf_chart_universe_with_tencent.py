#!/usr/bin/env python3
import argparse
import json
import re
import ssl
import time
import urllib.request
from pathlib import Path
from typing import Optional

import pandas as pd

from generate_etf_rps import add_rps


def market_prefix(code: str) -> str:
    return "sh" if code.startswith(("5", "6")) else "sz"


def parse_float(value: str) -> Optional[float]:
    try:
        if not value or value == "-":
            return None
        parsed = float(value)
        return parsed if parsed > 0 else None
    except ValueError:
        return None


def fetch_tencent_quotes(codes: list[str], batch_size: int, pause: float, retries: int) -> pd.DataFrame:
    rows: list[dict] = []
    ssl_context = ssl._create_unverified_context()
    for start in range(0, len(codes), batch_size):
        batch = codes[start : start + batch_size]
        query = ",".join(f"{market_prefix(code)}{code}" for code in batch)
        url = "https://qt.gtimg.cn/q=" + query
        last_error = None
        text = ""
        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15, context=ssl_context) as response:
                    text = response.read().decode("gbk", errors="ignore")
                if text:
                    break
            except Exception as exc:
                last_error = exc
                time.sleep(pause * (attempt + 1))
        if not text:
            print(f"FAILED batch {start}: {last_error}")
            continue

        for line in text.strip().split(";"):
            if not line.strip() or "=" not in line or '"' not in line:
                continue
            vals = line.split('"', 2)[1].split("~")
            if len(vals) < 34:
                continue
            code = vals[2].zfill(6)
            price = parse_float(vals[3])
            if price is None:
                continue
            quote_time = ""
            for item in vals:
                if re.fullmatch(r"\d{14}", item or ""):
                    quote_time = item
                    break
            rows.append(
                {
                    "code": code,
                    "name": vals[1],
                    "close": price,
                    "quote_time": quote_time,
                    "change_pct": parse_float(vals[32]),
                    "amount_wan": parse_float(vals[37]) if len(vals) > 37 else None,
                }
            )
        print(f"Fetched Tencent quotes {min(start + batch_size, len(codes))}/{len(codes)}; rows={len(rows)}")
        time.sleep(pause)
    return pd.DataFrame(rows).drop_duplicates("code", keep="last")


def main() -> None:
    parser = argparse.ArgumentParser(description="Append same-day Tencent ETF quotes to chart-universe history and recalc RPS.")
    parser.add_argument("--history-csv", default="output/etf_rps_chart_universe/etf_nav_daily_chart_universe_2026-06-12.csv")
    parser.add_argument("--universe", default="output/wechat_articles/etf_strength_advantage/chart_universe_codes.csv")
    parser.add_argument("--date", required=True, help="Target trade date, e.g. 2026-06-15.")
    parser.add_argument("--out-dir", default="output/etf_rps_chart_universe")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--pause", type=float, default=0.5)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--min-rows", type=int, default=160)
    args = parser.parse_args()

    target = pd.Timestamp(args.date)
    target_key = target.strftime("%Y%m%d")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    universe = pd.read_csv(args.universe)
    universe["code"] = universe["code"].astype(str).str.zfill(6)
    universe_names = dict(zip(universe["code"], universe.get("name_from_chart", universe["code"])))
    codes = universe["code"].tolist()

    quotes = fetch_tencent_quotes(codes, args.batch_size, args.pause, args.retries)
    if quotes.empty:
        raise SystemExit("No Tencent quotes fetched.")
    quotes = quotes[quotes["quote_time"].str.startswith(target_key, na=False)].copy()
    missing = sorted(set(codes) - set(quotes["code"]))
    print(f"Target-date quotes: {len(quotes)}; missing={len(missing)}")
    if missing:
        print("Missing sample:", ",".join(missing[:30]))
    if len(quotes) < args.min_rows:
        raise SystemExit(f"Only {len(quotes)} target-date quotes; below min rows {args.min_rows}.")

    history = pd.read_csv(args.history_csv, parse_dates=["date"])
    history["code"] = history["code"].astype(str).str.zfill(6)
    history = history[history["code"].isin(codes)].copy()
    history = history[history["date"] < target].copy()

    quote_rows = quotes[["code", "name", "close"]].copy()
    quote_rows["name"] = quote_rows.apply(lambda row: universe_names.get(row["code"], row["name"]), axis=1)
    quote_rows["date"] = target

    combined = pd.concat([history[["date", "code", "name", "close"]], quote_rows], ignore_index=True)
    combined["close"] = pd.to_numeric(combined["close"], errors="coerce")
    combined = combined.dropna(subset=["close"]).sort_values(["code", "date"])
    for window in [1, 3, 5, 10, 20]:
        combined[f"ret{window}"] = combined.groupby("code")["close"].pct_change(window) * 100
    combined = add_rps(combined)

    history_out = out_dir / f"etf_nav_daily_chart_universe_{args.date}.csv"
    latest_out = out_dir / f"daily_observation_full_{args.date}.csv"
    meta_out = out_dir / f"tencent_quote_meta_{args.date}.json"
    combined.to_csv(history_out, index=False)
    latest = combined[combined["date"] == target].sort_values("rps20", ascending=False)
    latest.to_csv(latest_out, index=False)
    meta_out.write_text(
        json.dumps(
            {
                "date": args.date,
                "source": "tencent_quote",
                "target_rows": int(len(latest)),
                "valid_rps20": int(latest["rps20"].notna().sum()),
                "missing": missing,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(history_out)
    print(latest_out)
    print(meta_out)
    print(latest[["code", "name", "close", "rps20", "ret20"]].head(12).to_string(index=False))


if __name__ == "__main__":
    main()
