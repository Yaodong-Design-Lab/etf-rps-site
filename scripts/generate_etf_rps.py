#!/usr/bin/env python3
import argparse
from pathlib import Path
from typing import Optional

import pandas as pd


RPS_WINDOWS = [3, 5, 10, 20, 50, 60, 120, 250]
SHARE_WINDOWS = [1, 3, 5, 10, 20]


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "日期": "date",
        "代码": "code",
        "名称": "name",
        "收盘": "close",
        "收盘价": "close",
        "单位净值": "close",
        "份额": "shares",
        "基金份额": "shares",
    }
    df = df.rename(columns={col: aliases.get(col, col) for col in df.columns})
    required = {"date", "code", "name", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")
    df["date"] = pd.to_datetime(df["date"])
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    if "shares" in df.columns:
        df["shares"] = pd.to_numeric(df["shares"], errors="coerce")
    return df.sort_values(["code", "date"]).dropna(subset=["close"])


def add_rps(df: pd.DataFrame) -> pd.DataFrame:
    for window in RPS_WINDOWS:
        ret_col = f"ret{window}"
        rps_col = f"rps{window}"
        raw_return = df.groupby("code")["close"].pct_change(window)
        df[ret_col] = raw_return * 100
        df[rps_col] = raw_return.groupby(df["date"]).rank(pct=True) * 100
    return df


def add_share_changes(df: pd.DataFrame) -> pd.DataFrame:
    if "shares" not in df.columns:
        return df
    for window in SHARE_WINDOWS:
        df[f"share_chg_{window}d"] = df.groupby("code")["shares"].pct_change(window) * 100
    return df


def latest_frame(df: pd.DataFrame, date: Optional[str]):
    if date:
        target = pd.to_datetime(date)
        latest = df[df["date"] == target].copy()
        if latest.empty:
            raise ValueError(f"No rows for date {date}")
        return target, latest
    target = df["date"].max()
    return target, df[df["date"] == target].copy()


def stage_highs(df: pd.DataFrame, target_date: pd.Timestamp, windows: list):
    out = {}
    history = df[df["date"] <= target_date].copy()
    for window in windows:
        rolling_high = history.groupby("code")["close"].transform(lambda s: s.rolling(window, min_periods=window).max())
        temp = history[history["date"] == target_date].copy()
        temp["rolling_high"] = rolling_high[history["date"] == target_date].values
        temp = temp[temp["close"] >= temp["rolling_high"]]
        out[f"high_{window}d"] = temp[["code", "name", "close", "rps250", "ret20"]].sort_values(
            ["rps250", "ret20"], ascending=False
        )
    return out


def top_table(df: pd.DataFrame, sort_by: str, columns: list[str], n: int) -> pd.DataFrame:
    available = [col for col in columns if col in df.columns]
    return df.sort_values(sort_by, ascending=False)[available].head(n)


def write_markdown(
    out_file: Path,
    target_date: pd.Timestamp,
    latest: pd.DataFrame,
    full: pd.DataFrame,
    top_n: int,
) -> None:
    base_cols = [
        "code",
        "name",
        "rps3",
        "rps5",
        "rps10",
        "rps20",
        "rps50",
        "rps60",
        "rps120",
        "rps250",
        "ret1",
        "ret3",
        "ret5",
        "ret10",
        "ret20",
    ]
    share_cols = [f"share_chg_{w}d" for w in SHARE_WINDOWS if f"share_chg_{w}d" in latest.columns]
    ranking_cols = [col for col in base_cols + share_cols if col in latest.columns]

    sections = [f"# ETF RPS 每日排名 - {target_date.date()}"]
    sections.append("## 强度指数总览（按 rps20）")
    sections.append(top_table(latest, "rps20", ranking_cols, top_n).to_markdown(index=False, floatfmt=".2f"))

    sections.append("## 每日观察 ETF：长周期")
    sections.append(top_table(latest, "rps250", ["code", "name", "rps50", "rps120", "rps250", "ret20"], top_n).to_markdown(index=False, floatfmt=".2f"))

    sections.append("## 每日观察 ETF：短周期")
    sections.append(top_table(latest, "rps10", ["code", "name", "rps3", "rps5", "rps10", "ret1", "ret3", "ret5"], top_n).to_markdown(index=False, floatfmt=".2f"))

    sections.append("## 20 日强度榜")
    sections.append(top_table(latest, "rps20", ["code", "name", "rps20", "rps5", "ret20", "ret10", "ret5"], top_n).to_markdown(index=False, floatfmt=".2f"))

    highs = stage_highs(full, target_date, [10, 20, 50, 120, 250])
    sections.append("## 阶段新高")
    for label, table in highs.items():
        sections.append(f"### {label}")
        sections.append(table.head(top_n).to_markdown(index=False, floatfmt=".2f") if not table.empty else "无")

    if share_cols:
        sections.append("## 份额变化 Top5")
        for window in SHARE_WINDOWS:
            col = f"share_chg_{window}d"
            if col not in latest.columns:
                continue
            cols = ["code", "name", col, "rps20", "ret20"]
            sections.append(f"### {window} 日份额增幅前 5")
            sections.append(latest.sort_values(col, ascending=False)[cols].head(5).to_markdown(index=False, floatfmt=".2f"))
            sections.append(f"### {window} 日份额减幅前 5")
            sections.append(latest.sort_values(col, ascending=True)[cols].head(5).to_markdown(index=False, floatfmt=".2f"))

    out_file.write_text("\n\n".join(sections) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ETF RPS ranking tables from daily ETF data.")
    parser.add_argument("--input", required=True, help="CSV with date, code, name, close, optional shares.")
    parser.add_argument("--date", help="Target date. Defaults to latest date in input.")
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--out-dir", default="output/etf_rps")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df = normalize_columns(pd.read_csv(args.input))
    for window in [1, 3, 5, 10, 20]:
        df[f"ret{window}"] = df.groupby("code")["close"].pct_change(window) * 100
    df = add_rps(df)
    df = add_share_changes(df)
    target_date, latest = latest_frame(df, args.date)

    latest_out = out_dir / f"etf_rps_{target_date.date()}.csv"
    report_out = out_dir / f"etf_rps_{target_date.date()}.md"
    latest.to_csv(latest_out, index=False)
    write_markdown(report_out, target_date, latest, df, args.top_n)
    print(f"Wrote {latest_out}")
    print(f"Wrote {report_out}")


if __name__ == "__main__":
    main()
