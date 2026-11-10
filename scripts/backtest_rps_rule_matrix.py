#!/usr/bin/env python3
"""Backtest the ETF rotation rule matrix and refresh strategy_backtest.html.

Buy rule (fixed): RPS 20 > 95, RPS 60 > 80, and the ETF is inside the RPS 20
top 5 for three consecutive real sessions. The buy fills on the next session's
close. One ETF is held at 100%. The sell rule is chosen from the matrix by
cumulative return. A session with fewer than 80 ETFs is ignored so a partial
quote file cannot break a streak or invent a trading day.
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STUDY_START = pd.Timestamp("2025-01-01")
MIN_SESSION_ETFS = 80
TOP_N = 5
BUY_RPS20 = 95.0
BUY_RPS60 = 80.0
BUY_STREAK = 3
SPLIT_LEVELS = (2, 3, 4, 5, 6, 8, 10)


@dataclass(frozen=True)
class SellRule:
    label: str
    detail: str
    drawdown: float | None
    ma_window: int | None


RULES = [
    SellRule("回撤5%", "从买入后最高收盘价回撤 5% 卖出", 0.05, None),
    SellRule("回撤6%", "从买入后最高收盘价回撤 6% 卖出", 0.06, None),
    SellRule("回撤7%", "从买入后最高收盘价回撤 7% 卖出", 0.07, None),
    SellRule("回撤8%", "从买入后最高收盘价回撤 8% 卖出", 0.08, None),
    SellRule("回撤9%", "从买入后最高收盘价回撤 9% 卖出", 0.09, None),
    SellRule("回撤10%", "从买入后最高收盘价回撤 10% 卖出", 0.10, None),
    SellRule("跌破10日均线", "收盘价跌破 10 日均线卖出", None, 10),
    SellRule("跌破20日均线", "收盘价跌破 20 日均线卖出", None, 20),
    *[
        SellRule(
            f"回撤{int(drawdown * 100)}% 或跌破20日均线",
            f"从买入后最高收盘价回撤 {int(drawdown * 100)}%，或收盘价跌破 20 日均线，任一触发卖出",
            drawdown,
            20,
        )
        for drawdown in (0.05, 0.06, 0.07, 0.08, 0.09, 0.10)
    ],
]


def split_multiple(ratio: float) -> float | None:
    if ratio <= 0:
        return None
    best: tuple[float, float] | None = None
    for level in SPLIT_LEVELS:
        for target in (level, 1 / level):
            error = abs(ratio / target - 1)
            if error <= 0.08 and (best is None or error < best[0]):
                best = (error, target)
    return None if best is None else best[1]


def adjust_prices(raw: pd.Series) -> pd.Series:
    factor = 1.0
    previous = None
    adjusted = []
    for price in raw.tolist():
        if previous and previous > 0 and price and price > 0:
            multiple = split_multiple(float(price) / float(previous))
            if multiple is not None and multiple < 1:
                factor *= 1 / multiple
            elif multiple is not None and multiple > 1:
                factor /= multiple
        adjusted.append(float(price) * factor if price and price > 0 else price)
        if price and price > 0:
            previous = float(price)
    return pd.Series(adjusted, index=raw.index)


def display_name(name: str) -> str:
    try:
        from generate_etf_observation import simplify_name
    except ImportError:
        return str(name)
    return simplify_name(str(name))


def load_market(history_csv: Path, target: pd.Timestamp):
    frame = pd.read_csv(history_csv, dtype={"code": str}, parse_dates=["date"])
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["rps20"] = pd.to_numeric(frame["rps20"], errors="coerce")
    frame["rps60"] = pd.to_numeric(frame["rps60"], errors="coerce")
    frame = frame.dropna(subset=["close"])
    frame = frame[(frame["date"] >= STUDY_START) & (frame["date"] <= target)].copy()
    if frame.empty or target not in set(frame["date"]):
        raise SystemExit(f"History has no session on {target.date()}")

    counts = frame.groupby("date")["code"].nunique()
    sessions = [date for date in sorted(frame["date"].unique()) if int(counts[date]) >= MIN_SESSION_ETFS]
    if target not in sessions:
        raise SystemExit(f"{target.date()} does not have a full ETF session in history")

    pieces = []
    for _, group in frame.groupby("code", sort=False):
        group = group.sort_values("date").copy()
        group["price"] = adjust_prices(group["close"])
        pieces.append(group)
    frame = pd.concat(pieces, ignore_index=True)

    by_date = {}
    for date, group in frame.groupby("date"):
        if date not in sessions:
            continue
        ranked = group.dropna(subset=["rps20"]).sort_values(["rps20", "code"], ascending=[False, True])
        rows = {}
        for rank, row in enumerate(ranked.itertuples(index=False), start=1):
            rows[row.code] = {
                "rank": rank,
                "rps20": float(row.rps20),
                "rps60": None if pd.isna(row.rps60) else float(row.rps60),
                "price": float(row.price),
                "name": row.name,
            }
        by_date[date] = rows

    streaks: dict[pd.Timestamp, dict[str, int]] = {}
    running: dict[str, int] = {}
    for date in sessions:
        current = {}
        for code, row in by_date[date].items():
            if row["rank"] <= TOP_N:
                current[code] = running.get(code, 0) + 1
        streaks[date] = current
        running = current

    prices = {
        code: group.sort_values("date").set_index("date")["price"]
        for code, group in frame.groupby("code")
    }
    return sessions, by_date, streaks, prices, int(counts[target]), benchmark_curve(frame, sessions)


def benchmark_curve(frame: pd.DataFrame, sessions: list[pd.Timestamp]) -> list[float]:
    ordered = frame.sort_values(["code", "date"]).copy()
    ordered["previous"] = ordered.groupby("code")["close"].shift(1)
    ratio = ordered["close"] / ordered["previous"]

    def residual(value):
        if pd.isna(value) or value <= 0:
            return pd.NA
        multiple = split_multiple(float(value))
        if multiple:
            return float(value) / multiple - 1
        return float(value) - 1

    ordered["residual"] = ratio.map(residual)
    means = ordered.groupby("date")["residual"].mean()
    nav = 1.0
    curve = []
    for index, date in enumerate(sessions):
        if index:
            change = means.get(date, pd.NA)
            if pd.notna(change):
                nav *= 1 + float(change)
        curve.append(nav)
    return curve


def choose_candidate(rows: dict, streak: dict[str, int]) -> str | None:
    candidates = []
    for code, days in streak.items():
        row = rows.get(code)
        if row is None or days < BUY_STREAK or row["rps60"] is None:
            continue
        if row["rps20"] > BUY_RPS20 and row["rps60"] > BUY_RPS60:
            candidates.append((code, row["rps20"], row["rps60"], days))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[1], -item[2], -item[3], item[0]))
    return candidates[0][0]


def moving_average(series: pd.Series, sessions: list[pd.Timestamp], date: pd.Timestamp, window: int) -> float | None:
    history = series.reindex(sessions).loc[:date].dropna().tail(window)
    if len(history) < window:
        return None
    return float(history.mean())


def simulate(rule: SellRule, sessions, by_date, streaks, prices):
    holding = None
    pending = None
    trades = []
    for date in sessions:
        rows = by_date[date]
        if pending and holding is None and pending in rows:
            holding = {
                "code": pending,
                "buy": date,
                "entry": rows[pending]["price"],
                "peak": rows[pending]["price"],
                "name": rows[pending]["name"],
            }
            pending = None
        if holding and holding["code"] in rows and date != holding["buy"]:
            price = rows[holding["code"]]["price"]
            holding["peak"] = max(holding["peak"], price)
            drawdown = price / holding["peak"] - 1
            average = (
                moving_average(prices[holding["code"]], sessions, date, rule.ma_window)
                if rule.ma_window
                else None
            )
            hit_drawdown = rule.drawdown is not None and drawdown <= -rule.drawdown - 1e-12
            hit_average = average is not None and price < average - 1e-9
            if hit_drawdown or hit_average:
                trades.append(
                    {
                        "code": holding["code"],
                        "name": display_name(holding["name"]),
                        "buy": holding["buy"],
                        "sell": date,
                        "entry": holding["entry"],
                        "exit": price,
                        "open": False,
                    }
                )
                holding = None
        if holding is None and pending is None:
            pending = choose_candidate(rows, streaks[date])
    if holding:
        last = sessions[-1]
        price = by_date[last][holding["code"]]["price"] if holding["code"] in by_date[last] else holding["entry"]
        trades.append(
            {
                "code": holding["code"],
                "name": display_name(holding["name"]),
                "buy": holding["buy"],
                "sell": last,
                "entry": holding["entry"],
                "exit": price,
                "open": True,
            }
        )
    return trades


def trade_return(trade: dict) -> float:
    if trade["entry"] <= 0:
        return 0.0
    return trade["exit"] / trade["entry"] - 1


def equity_curves(rule_trades, sessions, by_date, prices):
    strategy = []
    benchmark = []
    nav = 1.0
    bench = 1.0
    active = None
    cursor = 0
    ordered = list(rule_trades)
    for index, date in enumerate(sessions):
        if active is None and cursor < len(ordered) and ordered[cursor]["buy"] == date:
            active = ordered[cursor]
        elif active is not None:
            code = active["code"]
            previous = sessions[index - 1]
            series = prices[code]
            if previous in series.index and date in series.index and float(series.loc[previous]) > 0:
                nav *= float(series.loc[date]) / float(series.loc[previous])
            if date == active["sell"]:
                active = None
                cursor += 1
        if index > 0:
            previous = sessions[index - 1]
            changes = []
            for code, row in by_date[date].items():
                prev_row = by_date[previous].get(code)
                if prev_row and prev_row["price"] > 0:
                    changes.append(row["price"] / prev_row["price"] - 1)
            if changes:
                bench *= 1 + sum(changes) / len(changes)
        strategy.append(nav)
        benchmark.append(bench)
    return strategy, benchmark


def max_drawdown(navs: list[float]) -> float:
    peak = navs[0]
    worst = 0.0
    for value in navs:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst


def summarize(rule: SellRule, trades, strategy_nav, benchmark_nav, sessions, by_date, etf_count: int) -> dict:
    returns = [trade_return(trade) for trade in trades]
    nav = 1.0
    for value in returns:
        nav *= 1 + value
    holds = [(trade["sell"] - trade["buy"]).days for trade in trades]
    target = sessions[-1]
    rows = by_date[target]
    return {
        "rule": rule,
        "trades": trades,
        "nav": nav,
        "cumulative": nav - 1,
        "max_drawdown": max_drawdown(strategy_nav),
        "average_hold": (sum(holds) / len(holds)) if holds else 0.0,
        "strategy_nav": strategy_nav,
        "benchmark_nav": benchmark_nav,
        "sessions": sessions,
        "etf_count": etf_count,
        "candidate_code": None,
        "rows": rows,
    }


def run_matrix(history_csv: Path, target_text: str) -> dict:
    target = pd.Timestamp(target_text)
    sessions, by_date, streaks, prices, etf_count, benchmark_nav = load_market(history_csv, target)
    ranked = []
    for rule in RULES:
        trades = simulate(rule, sessions, by_date, streaks, prices)
        strategy_nav, _ignored = equity_curves(trades, sessions, by_date, prices)
        summary = summarize(rule, trades, strategy_nav, benchmark_nav, sessions, by_date, etf_count)
        summary["candidate_code"] = choose_candidate(by_date[sessions[-1]], streaks[sessions[-1]])
        ranked.append(summary)
    ranked.sort(key=lambda item: (-item["cumulative"], item["max_drawdown"], item["rule"].label))
    best = ranked[0]
    best["matrix"] = [
        {
            "label": item["rule"].label,
            "cumulative": item["cumulative"],
            "max_drawdown": item["max_drawdown"],
            "trades": len(item["trades"]),
        }
        for item in ranked
    ]
    return best


def round_half_up(value: float, digits: int) -> float:
    factor = 10**digits
    scaled = value * factor
    if scaled >= 0:
        scaled = int(scaled + 0.5)
    else:
        scaled = int(scaled - 0.5)
    return scaled / factor


def fmt_pct(value: float) -> str:
    rounded = round_half_up(value * 100, 1)
    if rounded > 0:
        return f"+{rounded:.1f}%"
    return f"{rounded:.1f}%"


def fmt_nav(value: float) -> str:
    return f"{round_half_up(value, 2):.2f}"


def css_block() -> str:
    path = ROOT / "strategy_backtest.html"
    if path.exists():
        match = re.search(r"<style>.*?</style>", path.read_text(encoding="utf-8"), re.S)
        if match:
            return match.group(0)
    return "<style>body{font-family:sans-serif}</style>"


def chart_svg(strategy: list[float], benchmark: list[float]) -> str:
    lo = min([1.0, *strategy, *benchmark])
    hi = max([1.0, *strategy, *benchmark])
    span = max(hi - lo, 0.01)

    def coordinates(values: list[float]) -> str:
        last = max(len(values) - 1, 1)
        points = []
        for index, value in enumerate(values):
            x_pos = 30 + 860 * index / last
            y_pos = 270 - (value - lo) / span * 240
            points.append(f"{x_pos:.1f},{y_pos:.1f}")
        return " ".join(points)

    baseline = 270 - (1 - lo) / span * 240
    strategy_label = f"策略 {fmt_nav(strategy[-1])}"
    benchmark_label = f"ETF池等权 {fmt_nav(benchmark[-1])}"
    benchmark_x = 44 + len(strategy_label) * 11
    return f"""
      <svg viewBox="0 0 920 300" class="equity-svg" role="img" aria-label="策略净值曲线">
        <g class="legend">
          <circle cx="30" cy="16" r="5" class="legend-strategy"></circle>
          <text x="44" y="16" dominant-baseline="middle">{escape(strategy_label)}</text>
          <circle cx="{benchmark_x}" cy="16" r="5" class="legend-benchmark"></circle>
          <text x="{benchmark_x + 14}" y="16" dominant-baseline="middle">{escape(benchmark_label)}</text>
        </g>
        <line x1="30" y1="{baseline:.1f}" x2="890" y2="{baseline:.1f}" class="baseline"></line>
        <polyline points="{coordinates(benchmark)}" class="benchmark-curve"></polyline>
        <polyline points="{coordinates(strategy)}" class="curve"></polyline>
        <text x="30" y="292" class="axis-label">1.00</text>
        <text x="890" y="18" text-anchor="end" class="axis-label">{fmt_nav(strategy[-1])}</text>
      </svg>
    """


def rotation_rows(trades: list[dict]) -> str:
    rows = []
    for trade in reversed(trades):
        ret = trade_return(trade)
        css = "red" if ret >= 0 else "green"
        status = "持有中" if trade["open"] else "已结束"
        rows.append(
            f"""
        <tr>
          <td class="status-cell"><b class="cycle-status">{status}</b><small>买入 {trade['buy'].date()}</small><small>截至 {trade['sell'].date()}</small></td>
          <td class="combo-cell">
            <b class="etf-name">{escape(trade['name'])}</b>
            <small>{escape(trade['code'])}</small>
          </td>
          <td class="num cycle-return {css}">{fmt_pct(ret)}</td>
          <td class="num">{(trade['sell'] - trade['buy']).days}天</td>
        </tr>"""
        )
    return "".join(rows)


def render_page(result: dict) -> str:
    rule: SellRule = result["rule"]
    trades = result["trades"]
    target = result["sessions"][-1]
    rows = result["rows"]
    candidate = rows.get(result["candidate_code"]) if result["candidate_code"] else None
    candidate_name = display_name(candidate["name"]) if candidate else "暂无"
    candidate_code = result["candidate_code"] or ""
    holding = next((trade for trade in trades if trade["open"]), None)
    if holding:
        position = f"已持有 · {holding['name']}（{holding['code']}）"
        explanation = (
            f"第 {len(trades)} 轮已在 {holding['buy'].date()} 买入，当前继续持有，直到触发“{rule.label}”卖出规则。"
        )
        shown_candidate = f"{candidate_name}（{candidate_code}）" if candidate else f"{holding['name']}（{holding['code']}）"
    else:
        position = "空仓"
        explanation = "最近一轮已经结束，等待下一次买入信号。"
        shown_candidate = f"{candidate_name}（{candidate_code}）" if candidate else "暂无"
    buy_rule = "RPS 20 > 95，RPS 60 > 80，且连续 3 个交易日进入 RPS 20 前 5。"
    sell_rule = f"{rule.label}，任一条件触发即退出。"
    tip = (
        "轮动明细记录的是策略实际交易过程：当前规则只筛出 1 只 ETF，按策略以 100% 仓位执行；"
        "买点出现后下一交易日执行，卖点触发即退出，再等待下一次买入信号。<br><br>"
        f"当前仓位：{position}<br>当前说明：{explanation}<br>当日候选：{shown_candidate}<br>"
        f"买入规则：{buy_rule}<br>卖出规则：{sell_rule}"
    )
    tip_attr = escape(tip, quote=True).replace("&lt;br&gt;", "<br>").replace("&lt;br /&gt;", "<br>")
    cum_css = "red" if result["cumulative"] >= 0 else "green"
    dd_css = "red" if result["max_drawdown"] >= 0 else "green"
    hero_candidate = f"{candidate_name}（{candidate_code}）" if candidate else shown_candidate
    body = f"""
  <main class="shell">
    <section class="hero">
      <div class="kicker">ETF STRATEGY BACKTEST</div>
      <div class="title-row"><h1>ETF 趋势轮动策略</h1><span class="beta-badge">Beta</span></div>
      <div class="hero-meta"><span data-target-date="{target.date()}">{STUDY_START.date()} 至 {target.date()} · {result['etf_count']} 只 ETF</span></div>
    </section>
    <section class="panel" id="cycle-detail">
      <div class="panel-head"><h2 class="title-with-tip">组合轮动明细<span class="title-tip-anchor"><button type="button" class="head-tip" data-tip="{tip_attr}"></button><div class="resonance-tip" id="cycleDetailTip" hidden></div></span></h2><span class="panel-head-meta">第 {len(trades)} 轮</span></div>
      <div class="compact-after-cycle">
        <div class="compact-grid">
          <div class="compact-row compact-row-strategy">
            <span>当前策略</span>
            <b>强势方向买入，{escape(rule.label)}卖出</b>
          </div>
          <div class="compact-row compact-row-signal">
            <span>当日候选</span>
            <b>{escape(hero_candidate)}</b>
          </div>
          <div class="compact-statline">
            <span>回测累计收益 <b class="{cum_css}">{fmt_pct(result['cumulative'])}</b></span>
            <span>当前净值 <b>{fmt_nav(result['nav'])}</b></span>
            <span>最大回撤 <b class="{dd_css}">{fmt_pct(result['max_drawdown'])}</b></span>
            <span>平均持仓 <b>{result['average_hold']:.1f}天</b></span>
          </div>
        </div>
      </div>
      <div class="table-wrap cycle-wrap">
        <table class="cycle-table">
          <thead><tr><th>状态</th><th>ETF</th><th>收益</th><th>持仓</th></tr></thead>
          <tbody>
        {rotation_rows(trades)}
        </tbody>
        </table>
      </div>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>策略净值曲线</h2></div>
      <div class="chart-wrap">
      {chart_svg(result['strategy_nav'], result['benchmark_nav'])}
    </div>
    </section>
    <div class="page-footnote">
      <div class="copyright">数据仅供观察，不构成投资建议 · © yaodong-design-lab</div>
    </div>
  </main>
  <nav class="bottom-nav" aria-label="页面切换">
    <a href="./index.html">每日观察</a>
    <a class="active" href="./strategy_backtest.html">轮动策略</a>
  </nav>
"""
    script = """
  <script>
    const cycleDetailTip = document.getElementById("cycleDetailTip");
    const cycleDetailButton = document.querySelector(".head-tip[data-tip]");
    if (cycleDetailButton && cycleDetailTip) {
      let cycleDetailTipTimer = null;
      const closeCycleDetailTip = () => {
        cycleDetailTip.hidden = true;
        if (cycleDetailTipTimer) {
          clearTimeout(cycleDetailTipTimer);
          cycleDetailTipTimer = null;
        }
      };
      const armCycleDetailTipTimer = () => {
        if (cycleDetailTipTimer) clearTimeout(cycleDetailTipTimer);
        cycleDetailTipTimer = setTimeout(() => {
          cycleDetailTip.hidden = true;
          cycleDetailTipTimer = null;
        }, 5000);
      };
      cycleDetailButton.addEventListener("click", (event) => {
        event.stopPropagation();
        const willOpen = cycleDetailTip.hidden;
        cycleDetailTip.hidden = !willOpen;
        if (willOpen) {
          cycleDetailTip.innerHTML = cycleDetailButton.dataset.tip;
          armCycleDetailTipTimer();
        } else {
          closeCycleDetailTip();
        }
      });
      document.addEventListener("click", (event) => {
        if (cycleDetailTip.hidden) return;
        if (!cycleDetailTip.contains(event.target) && !cycleDetailButton.contains(event.target)) {
          closeCycleDetailTip();
        }
      });
    }
  </script>
"""
    return (
        "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n"
        "  <meta charset=\"utf-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "  <title>ETF 规则回测</title>\n"
        "  <meta name=\"description\" content=\"识别市场主线，生成可执行的 ETF 配置方案\">\n"
        f"{css_block()}\n</head>\n<body>\n{body}\n{script}\n</body>\n</html>\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the RPS rotation rule matrix and write the strategy page.")
    parser.add_argument("--history-csv", default=str(ROOT / "automation" / "etf_nav_daily_chart_universe_latest.csv"))
    parser.add_argument("--date", required=True, help="Target session, YYYY-MM-DD.")
    parser.add_argument("--out", default=str(ROOT / "strategy_backtest.html"))
    args = parser.parse_args()

    result = run_matrix(Path(args.history_csv), args.date)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_page(result), encoding="utf-8")
    rule = result["rule"]
    print(
        f"Strategy {rule.label} date={args.date} etfs={result['etf_count']} "
        f"nav={fmt_nav(result['nav'])} cum={fmt_pct(result['cumulative'])} "
        f"maxdd={fmt_pct(result['max_drawdown'])} trades={len(result['trades'])} -> {out}"
    )


if __name__ == "__main__":
    main()
