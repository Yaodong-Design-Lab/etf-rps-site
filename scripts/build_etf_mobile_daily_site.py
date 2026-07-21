#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pandas as pd

from generate_etf_observation import sub_theme_of, theme_of


SIGNAL_RPS_KEYS = ["rps3", "rps5", "rps10", "rps20", "rps50", "rps120"]
SIGNAL_HOLDS = [5, 10, 20, 40]
SIGNAL_RANGES = {
    "all": ("全区间", "2025-07-01"),
    "6m": ("近6个月", None),
    "3m": ("近3个月", None),
}

MANUAL_STRATEGIES = [
    {
        "key": "steady_r20",
        "name": "稳健单标",
        "entry": "R20≥95，R10≥80，R5≥70，R50≥60，取R20最高",
        "exit": "R5<50且R10<60，或R20<60，或-8%止损，最长40日",
        "max_hold": 40,
        "stop": -0.08,
    },
    {
        "key": "resonance",
        "name": "强共振单标",
        "entry": "R20≥95，且R5/R10/R20/R50/R120中至少4项≥90",
        "exit": "R10<60，或R20<70，或-8%止损，最长40日",
        "max_hold": 40,
        "stop": -0.08,
    },
    {
        "key": "short_trial",
        "name": "短线试错",
        "entry": "R10≥95，R5≥90，R20≥80，取R10最高",
        "exit": "R5<60，或-5%止损，最长10日",
        "max_hold": 10,
        "stop": -0.05,
    },
    {
        "key": "defensive",
        "name": "防守确认",
        "entry": "R20≥90，R50≥80，R120≥70，取均衡分最高",
        "exit": "R20<70，或-8%止损，最长60日",
        "max_hold": 60,
        "stop": -0.08,
    },
]


def clean_number(value, digits: int = 1):
    if pd.isna(value):
        return None
    return round(float(value), digits)


def enrich_theme_columns(df: pd.DataFrame) -> pd.DataFrame:
    enriched = df.copy()
    enriched["theme"] = enriched["name"].map(theme_of)
    enriched["sub_theme"] = enriched["name"].map(sub_theme_of)
    return enriched


def row_short_name(row: pd.Series) -> str:
    for key in ("short_name", "shortName", "name"):
        value = row.get(key)
        if value is not None and not pd.isna(value):
            return str(value)
    return ""


def card_item(row: pd.Series) -> dict:
    return {
        "rank": int(row["rank"]),
        "code": str(row["code"]).zfill(6),
        "name": str(row["name"]),
        "shortName": row_short_name(row),
        "theme": str(row["theme"]),
        "subTheme": str(row.get("sub_theme", row["theme"])),
        "rps20": clean_number(row.get("rps20")),
        "rps3": clean_number(row.get("rps3")),
        "rps5": clean_number(row.get("rps5")),
        "rps10": clean_number(row.get("rps10")),
        "rps50": clean_number(row.get("rps50")),
        "rps60": clean_number(row.get("rps60")),
        "rps120": clean_number(row.get("rps120")),
        "rps250": clean_number(row.get("rps250")),
        "change_pct": clean_number(row.get("change_pct")),
        "ret1": clean_number(row.get("ret1")),
        "ret3": clean_number(row.get("ret3")),
        "ret5": clean_number(row.get("ret5")),
        "ret10": clean_number(row.get("ret10")),
        "ret20": clean_number(row.get("ret20")),
    }


def strategy_window(strategy: str) -> str:
    if strategy in {"r20_short_confirm", "r20_trend_confirm"}:
        return "rps20"
    if strategy in {"rps3", "rps5", "rps10", "rps20", "rps50", "rps120"}:
        return strategy
    return "score"


def strategy_label(row: pd.Series) -> str:
    labels = {
        "rps3": "RPS3",
        "rps5": "RPS5",
        "rps10": "RPS10",
        "rps20": "RPS20",
        "rps50": "RPS50",
        "rps120": "RPS120",
        "r20_short_confirm": "R20 + 短线确认",
        "r20_trend_confirm": "R20 + 趋势确认",
        "balanced_score": "均衡评分",
        "aggressive_score": "进攻评分",
        "defensive_score": "防守评分",
    }
    variants = {
        "base": "基础轮动",
        "stop8": "8%止损",
        "rank_exit60": "R20跌破60退出",
    }
    strategy = labels.get(str(row["strategy"]), str(row["strategy"]))
    variant = variants.get(str(row["variant"]), str(row["variant"]))
    return f"{strategy} · Top{int(row['top_k'])} · {variant}"


def build_strategies(strategy_df: pd.DataFrame | None) -> list[dict]:
    if strategy_df is None or strategy_df.empty:
        return []
    strategies = []
    for _, row in strategy_df.sort_values("total_return", ascending=False).iterrows():
        strategy = str(row["strategy"])
        strategies.append(
            {
                "strategy": strategy,
                "window": strategy_window(strategy),
                "label": strategy_label(row),
                "topK": int(row["top_k"]),
                "trades": int(row["trades"]),
                "totalReturn": clean_number(row["total_return"], 4),
                "annualReturn": clean_number(row["annual_return"], 4),
                "maxDrawdown": clean_number(row["max_drawdown"], 4),
                "winRate": clean_number(row["win_rate"], 4),
                "variant": str(row["variant"]),
            }
        )
    return strategies


def build_signal_study(history_df: pd.DataFrame | None, end_date: str) -> dict:
    if history_df is None or history_df.empty:
        return {}

    df = history_df.copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values(["code", "date"])
    end = pd.Timestamp(end_date)
    ranges = {
        "all": pd.Timestamp("2025-07-01"),
        "6m": end - pd.Timedelta(days=183),
        "3m": end - pd.Timedelta(days=92),
    }

    payload: dict[str, dict] = {}
    for rps_key in SIGNAL_RPS_KEYS:
        payload[rps_key] = {}
        for range_key, start in ranges.items():
            payload[rps_key][range_key] = {}
            for hold in SIGNAL_HOLDS:
                returns = []
                for _, group in df.groupby("code"):
                    group = group.reset_index(drop=True)
                    if rps_key not in group.columns:
                        continue
                    for idx, row in group.iterrows():
                        if row["date"] < start or row["date"] > end:
                            continue
                        if pd.isna(row.get(rps_key)) or float(row[rps_key]) < 90:
                            continue
                        sell_idx = idx + hold
                        if sell_idx >= len(group):
                            continue
                        buy_price = float(row["close"])
                        sell_price = float(group.loc[sell_idx, "close"])
                        if buy_price <= 0 or sell_price <= 0:
                            continue
                        returns.append(sell_price / buy_price - 1)

                if returns:
                    series = pd.Series(returns)
                    payload[rps_key][range_key][str(hold)] = {
                        "signals": int(len(series)),
                        "avgReturn": clean_number(series.mean(), 4),
                        "medianReturn": clean_number(series.median(), 4),
                        "winRate": clean_number((series > 0).mean(), 4),
                        "bestReturn": clean_number(series.max(), 4),
                        "worstReturn": clean_number(series.min(), 4),
                    }
                else:
                    payload[rps_key][range_key][str(hold)] = {
                        "signals": 0,
                        "avgReturn": None,
                        "medianReturn": None,
                        "winRate": None,
                        "bestReturn": None,
                        "worstReturn": None,
                    }
    return {
        "threshold": 90,
        "ranges": {key: label for key, (label, _) in SIGNAL_RANGES.items()},
        "holds": SIGNAL_HOLDS,
        "data": payload,
    }


def manual_candidates(candidates: pd.DataFrame, key: str) -> pd.DataFrame:
    c = candidates.copy()
    if key == "steady_r20":
        c = c[(c["rps20"] >= 95) & (c["rps10"] >= 80) & (c["rps5"] >= 70) & (c["rps50"] >= 60)].copy()
        c["score"] = c["rps20"]
    elif key == "resonance":
        cols = ["rps5", "rps10", "rps20", "rps50", "rps120"]
        c = c[(c["rps20"] >= 95) & ((c[cols] >= 90).sum(axis=1) >= 4)].copy()
        c["score"] = 0.35 * c["rps20"] + 0.25 * c["rps10"] + 0.20 * c["rps50"] + 0.20 * c["rps120"].fillna(50)
    elif key == "short_trial":
        c = c[(c["rps10"] >= 95) & (c["rps5"] >= 90) & (c["rps20"] >= 80)].copy()
        c["score"] = 0.55 * c["rps10"] + 0.30 * c["rps5"] + 0.15 * c["rps20"]
    elif key == "defensive":
        c = c[(c["rps20"] >= 90) & (c["rps50"] >= 80) & (c["rps120"] >= 70)].copy()
        c["score"] = 0.40 * c["rps20"] + 0.30 * c["rps50"] + 0.30 * c["rps120"]
    else:
        return c.iloc[0:0]
    return c.dropna(subset=["score"]).sort_values("score", ascending=False).head(1)


def manual_exit(row: pd.Series, key: str) -> bool:
    if key == "steady_r20":
        return (row.get("rps5", 100) < 50 and row.get("rps10", 100) < 60) or row.get("rps20", 100) < 60
    if key == "resonance":
        return row.get("rps10", 100) < 60 or row.get("rps20", 100) < 70
    if key == "short_trial":
        return row.get("rps5", 100) < 60
    if key == "defensive":
        return row.get("rps20", 100) < 70
    return False


def build_manual_backtest(history_df: pd.DataFrame | None, start_date: str, end_date: str) -> list[dict]:
    if history_df is None or history_df.empty:
        return []

    df = history_df.copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values(["date", "code"])
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    dates = [pd.Timestamp(x) for x in sorted(df[(df["date"] >= start) & (df["date"] <= end)]["date"].unique())]
    by_date = {date: frame.copy() for date, frame in df.groupby("date")}
    price = df.pivot(index="date", columns="code", values="close").sort_index()
    rows_by_code = {code: frame.set_index("date").sort_index() for code, frame in df.groupby("code")}
    fee = 0.001

    summaries = []
    for spec in MANUAL_STRATEGIES:
        equity = 1.0
        equity_curve = []
        trades = []
        idx = 0
        while idx < len(dates) - 2:
            signal_date = dates[idx]
            candidates = by_date.get(signal_date)
            if candidates is None:
                idx += 1
                continue
            pick = manual_candidates(candidates, spec["key"])
            if pick.empty:
                idx += 1
                continue
            code = str(pick.iloc[0]["code"]).zfill(6)
            name = str(pick.iloc[0]["name"])
            buy_idx = idx + 1
            buy_date = dates[buy_idx]
            if code not in price.columns or buy_date not in price.index or pd.isna(price.loc[buy_date, code]):
                idx += 1
                continue
            buy_price = float(price.loc[buy_date, code])
            if buy_price <= 0:
                idx += 1
                continue
            sell_idx = min(buy_idx + spec["max_hold"], len(dates) - 1)
            exit_reason = "max_hold"
            for check_idx in range(buy_idx + 1, sell_idx + 1):
                check_date = dates[check_idx]
                if check_date not in price.index or pd.isna(price.loc[check_date, code]):
                    continue
                current_price = float(price.loc[check_date, code])
                current_return = current_price / buy_price - 1
                if current_return <= spec["stop"]:
                    sell_idx = check_idx
                    exit_reason = "stop_loss"
                    break
                code_rows = rows_by_code.get(code)
                if code_rows is not None and check_date in code_rows.index and manual_exit(code_rows.loc[check_date], spec["key"]):
                    sell_idx = check_idx
                    exit_reason = "rps_weak"
                    break
            sell_date = dates[sell_idx]
            sell_price = float(price.loc[sell_date, code])
            trade_return = sell_price / buy_price - 1 - fee * 2
            equity *= 1 + trade_return
            equity_curve.append(equity)
            trades.append(
                {
                    "code": code,
                    "name": name,
                    "signalDate": str(signal_date.date()),
                    "buyDate": str(buy_date.date()),
                    "sellDate": str(sell_date.date()),
                    "return": clean_number(trade_return, 4),
                    "exitReason": exit_reason,
                }
            )
            idx = max(sell_idx + 1, idx + 1)

        if equity_curve:
            curve = pd.Series(equity_curve)
            returns = pd.Series([trade["return"] for trade in trades])
            drawdown = curve / curve.cummax() - 1
            summaries.append(
                {
                    "key": spec["key"],
                    "name": spec["name"],
                    "entry": spec["entry"],
                    "exit": spec["exit"],
                    "trades": len(trades),
                    "totalReturn": clean_number(equity - 1, 4),
                    "maxDrawdown": clean_number(drawdown.min(), 4),
                    "winRate": clean_number((returns > 0).mean(), 4),
                    "avgReturn": clean_number(returns.mean(), 4),
                    "lastTrades": trades[-5:],
                }
            )
        else:
            summaries.append(
                {
                    "key": spec["key"],
                    "name": spec["name"],
                    "entry": spec["entry"],
                    "exit": spec["exit"],
                    "trades": 0,
                    "totalReturn": None,
                    "maxDrawdown": None,
                    "winRate": None,
                    "avgReturn": None,
                    "lastTrades": [],
                }
            )
    return summaries


def build_summary(df: pd.DataFrame) -> dict:
    by_r20 = df.sort_values("rps20", ascending=False)
    strong = by_r20.head(12)
    theme_strength = (
        strong.groupby("theme")
        .agg(count=("code", "count"), avgRps20=("rps20", "mean"), avgRet20=("ret20", "mean"))
        .sort_values(["count", "avgRps20"], ascending=False)
        .reset_index()
    )
    sub_strength = (
        strong.groupby(["theme", "sub_theme"])
        .agg(count=("code", "count"), avgRps20=("rps20", "mean"), avgRet20=("ret20", "mean"))
        .sort_values(["theme", "count", "avgRps20"], ascending=[True, False, False])
        .reset_index()
    )
    strongest = theme_strength.iloc[0]
    watchers = theme_strength.iloc[1:4]["theme"].tolist()
    high_count = int((df["rps20"] >= 95).sum())
    warm_count = int((df["rps20"] >= 90).sum())
    top = by_r20.iloc[0]
    strongest_theme = str(strongest["theme"])
    position_advice = (
        f"仓位建议：当前强势集中在{strongest_theme}，先小仓观察；若强势扩散到{'、'.join(watchers)}等方向，再考虑加仓。"
        if watchers
        else f"仓位建议：当前强势集中在{strongest_theme}，先小仓观察；等强势方向扩散后再考虑加仓。"
    )
    return {
        "strongestDirection": str(strongest["theme"]),
        "watchDirections": watchers,
        "riskTip": f"RPS20≥95 的标的有 {high_count} 只，强势方向较集中；若高开过多或 RPS5/RPS10 转弱，优先控制仓位。",
        "topEtf": f"{row_short_name(top)}（{str(top['code']).zfill(6)}）",
        "buyAdvice": "买入观察：优先 R20≥95，且 R5/R10 同步走强、R60/R120 不弱的 ETF；不追高开，等盘中回踩后仍保持强势再试。",
        "sellAdvice": "卖出纪律：回测里 R20 跌破 60 退出表现最好；风险敏感时叠加 8% 止损，若 R5/R10 先转弱可先减仓。",
        "positionAdvice": position_advice,
        "highCount": high_count,
        "warmCount": warm_count,
        "themeStrength": [
            {
                "theme": str(row["theme"]),
                "count": int(row["count"]),
                "avgRps20": clean_number(row["avgRps20"]),
                "avgRet20": clean_number(row["avgRet20"]),
                "subThemes": [
                    {
                        "theme": str(sub["sub_theme"]),
                        "count": int(sub["count"]),
                        "avgRps20": clean_number(sub["avgRps20"]),
                        "avgRet20": clean_number(sub["avgRet20"]),
                    }
                    for _, sub in sub_strength[sub_strength["theme"] == row["theme"]].iterrows()
                ],
            }
            for _, row in theme_strength.head(8).iterrows()
        ],
    }


def build_payload(
    date: str,
    df: pd.DataFrame,
    strategy_df: pd.DataFrame | None = None,
    history_df: pd.DataFrame | None = None,
) -> dict:
    df = df.copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df = enrich_theme_columns(df)
    df = df.sort_values("rps20", ascending=False)
    if "rank" not in df.columns:
        df["rank"] = range(1, len(df) + 1)

    seen = set()
    deduped = []
    for _, row in df.iterrows():
        theme = str(row["sub_theme"])
        if theme in seen:
            continue
        seen.add(theme)
        deduped.append(card_item(row))

    return {
        "date": date,
        "universe": 175,
        "validCount": int(len(df)),
        "summary": build_summary(df),
        "cards": deduped[:24],
        "all": [card_item(row) for _, row in df.iterrows()],
        "history": [{"date": date, "title": f"{date} ETF RPS 日报", "url": f"reports/{date}.html"}],
    }


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="format-detection" content="telephone=no">
  <title>ETF RPS 日报</title>
  <link rel="stylesheet" href="./assets/styles.css">
</head>
<body>
  <main id="app" class="shell">
    <section class="hero">
      <h1>每日观察 ETF RPS</h1>
      <p id="subtitle" class="subtitle">加载中...</p>
      <div class="rps-intro">RPS 是相对价格强度，按 0-100 给 ETF 做强弱排名；分数越高，代表近期相对表现越强。</div>
    </section>

      <section class="report-section conclusion">
        <div class="section-head">
          <div class="section-title">今日关注</div>
          <span>RPS 20 主视角</span>
        </div>
        <div class="conclusion-list">
          <div class="conclusion-row">
            <span>最强方向</span>
            <strong id="strongestDirection">-</strong>
          </div>
          <div class="conclusion-row">
            <span>观察方向</span>
            <strong id="watchDirections">-</strong>
          </div>
          <div class="conclusion-row">
            <span>代表标的</span>
            <strong id="topEtf">-</strong>
          </div>
        </div>
      </section>

      <section class="report-section ranking">
        <div class="ranking-head">
          <div class="ranking-copy">
            <div class="ranking-title-row">
              <div class="section-title">ETF 排名</div>
              <button id="toggleAll" class="mode-toggle" type="button"><span>强度榜</span><b>全部ETF</b></button>
            </div>
            <p id="rankingHint">RPS 20 ≥ 90 强度榜</p>
            <div class="ranking-note">
              <details class="res-tip"><summary><i class="res-swatch hot"></i>强共振</summary><div>5 个及以上 RPS 周期同时 ≥ 90。</div></details>
              <details class="res-tip"><summary><i class="res-swatch warm"></i>中等共振</summary><div>3-4 个 RPS 周期同时 ≥ 90。</div></details>
              <details class="res-tip"><summary><i class="res-swatch soft"></i>轻度共振</summary><div>2 个 RPS 周期同时 ≥ 90。</div></details>
            </div>
          </div>
        </div>
        <div class="rps-switcher" aria-label="切换 RPS 排序">
          <button class="rps-sort" type="button" data-key="rps3">RPS 3</button>
          <button class="rps-sort" type="button" data-key="rps5">RPS 5</button>
          <button class="rps-sort" type="button" data-key="rps10">RPS 10</button>
          <button class="rps-sort active" type="button" data-key="rps20">RPS 20</button>
          <button class="rps-sort" type="button" data-key="rps50">RPS 50</button>
          <button class="rps-sort" type="button" data-key="rps120">RPS 120</button>
          <button class="rps-sort" type="button" data-key="rps250">RPS 250</button>
        </div>
        <div class="table-scroll">
          <table class="etf-table">
            <thead>
              <tr class="group-row"><th colspan="4">基础信息</th><th colspan="1" id="rpsGroupHead">RPS 强度</th><th colspan="4">净值涨跌（%）</th></tr>
              <tr>
                <th>排</th><th>代码</th><th>名称</th><th>主题</th>
                <th id="activeRpsHead">RPS 20</th>
                <th>1日</th><th>3日</th><th>5日</th><th>20日</th>
              </tr>
            </thead>
            <tbody id="etfTableBody"></tbody>
          </table>
        </div>
        <div class="table-note">默认看 RPS 20：回测里收益最好，兼顾趋势和频率；净值涨跌为当前单位净值相对 1 / 3 / 5 / 20 个交易日前的涨跌幅。</div>
      </section>
      <section class="report-section history">
        <div class="section-head">
          <div class="section-title">历史日报</div>
          <span>归档</span>
        </div>
        <div id="historyList" class="history-list"></div>
      </section>

    <footer>数据仅供观察，不构成投资建议。</footer>
  </main>
  <script src="./data/latest.js"></script>
  <script src="./assets/app.js"></script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="output/etf_rps_chart_universe/daily_observation_full_2026-06-12.csv")
    parser.add_argument("--strategy-csv", default="output/etf_rps_strategy_variants/all_strategy_summary.csv")
    parser.add_argument("--history-csv", default="output/etf_rps_chart_universe/etf_nav_daily_chart_universe_2026-06-12.csv")
    parser.add_argument("--date", default="2026-06-12")
    parser.add_argument("--out", default="etf-rps-mobile-daily")
    args = parser.parse_args()

    out = Path(args.out)
    (out / "assets").mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(parents=True, exist_ok=True)
    (out / "reports").mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.csv, dtype={"code": str})
    payload = build_payload(args.date, df)
    data_js = "window.ETF_RPS_DAILY = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n"
    (out / "data" / "latest.js").write_text(data_js, encoding="utf-8")
    (out / "data" / f"{args.date}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (out / "reports" / f"{args.date}.html").write_text(INDEX_HTML.replace("./assets/", "../assets/").replace("./data/latest.js", f"../data/latest.js").replace("reports/{date}.html", f"{args.date}.html"), encoding="utf-8")

    source_html = Path(f"output/etf_rps_chart_universe/daily_observation_full_{args.date}.html")
    if source_html.exists():
        shutil.copyfile(source_html, out / "reports" / f"{args.date}-desktop.html")

    print(out / "index.html")
    print(out / "data" / "latest.js")
    print(out / "reports" / f"{args.date}.html")


if __name__ == "__main__":
    main()
