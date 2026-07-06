#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import shutil
from datetime import date as date_cls
from pathlib import Path

import pandas as pd

from generate_etf_observation import simplify_name, theme_of


def clean(value, digits: int = 1):
    if pd.isna(value):
        return None
    return round(float(value), digits)


def pct(value) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):+.1f}%"


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def set_history_prefix(payload: dict, prefix: str) -> dict:
    copied = json.loads(json.dumps(payload, ensure_ascii=False))
    for item in copied.get("history", []):
        item["url"] = f"{prefix}{item['date']}.html?v=trend-decision"
    return copied


def signal_of(row: pd.Series) -> tuple[str, str]:
    rank = int(row["rank"])
    rps20 = float(row["rps20"])
    streak = int(row["strong_streak"])
    if rank > 40 or rps20 < 60:
        return "回避", f"RPS 20={rps20:.1f}，强势天数={streak} 天"
    if rank > 20:
        return "回避", "跌出前 20，趋势退潮"
    if streak >= 10:
        return "持有", "连续强势超过 10 天，趋势质量高"
    if streak >= 3 and rps20 >= 90:
        return "建仓", "连续强势超过 3 天，进入启动观察"
    if rps20 >= 80:
        return "观察", "强度尚可，但连续性不足"
    return "回避", f"RPS 20={rps20:.1f}，趋势不在强势区"


def phase_of(row: pd.Series) -> tuple[str, str]:
    rank = int(row["rank"])
    rps20 = float(row["rps20"])
    streak = int(row["strong_streak"])
    if rps20 < 60:
        return "下跌趋势", f"RPS 20={rps20:.1f}"
    if rank > 20:
        return "退潮期", "跌出前 20"
    if streak >= 10 and rps20 >= 90:
        return "主升浪", f"连续强势：{streak} 天"
    if streak >= 3:
        return "启动期", f"连续强势：{streak} 天"
    if rank > 10:
        return "分歧期", "排名下降但仍在前 20"
    return "观察期", f"连续强势：{streak} 天"


def build_payload(history_csv: Path, latest_date: str, today: str) -> dict:
    df = pd.read_csv(history_csv, dtype={"code": str})
    df["date"] = pd.to_datetime(df["date"])
    df["code"] = df["code"].astype(str).str.zfill(6)
    latest = pd.Timestamp(latest_date)
    dates = sorted(df["date"].drop_duplicates())
    rps_cols = ["rps3", "rps5", "rps10", "rps20", "rps50", "rps120", "rps250"]

    ranked_frames = []
    for date, day in df.dropna(subset=["rps20"]).groupby("date"):
        day = day.copy()
        day["rank"] = day["rps20"].rank(method="first", ascending=False).astype(int)
        day["is_top10"] = day["rank"] <= 10
        day["is_top20"] = day["rank"] <= 20
        ranked_frames.append(day)
    ranked = pd.concat(ranked_frames, ignore_index=True)

    latest_day = ranked[ranked["date"] == latest].copy()
    if latest_day.empty:
        raise RuntimeError(f"No rows for {latest_date}")

    latest_day["short_name"] = latest_day["name"].map(simplify_name)
    latest_day["theme"] = latest_day["name"].map(theme_of)

    ranked = ranked.sort_values(["code", "date"]).copy()

    def calc_streak_frame(rps_col: str, label: str, predicate) -> pd.DataFrame:
        frames = []
        for day_date, day in df.dropna(subset=[rps_col]).groupby("date"):
            day = day.copy()
            day[f"is_{label}_for_col"] = predicate(day, rps_col)
            frames.append(day[["date", "code", f"is_{label}_for_col"]])
        if not frames:
            return pd.DataFrame(columns=["date", "code", f"{rps_col}_{label}_streak_all"])
        streak_ranked = pd.concat(frames, ignore_index=True).sort_values(["code", "date"])
        streak_rows = []
        for code, group in streak_ranked.groupby("code"):
            streak = 0
            for _, row in group.sort_values("date").iterrows():
                streak = streak + 1 if bool(row[f"is_{label}_for_col"]) else 0
                streak_rows.append({"date": row["date"], "code": code, f"{rps_col}_{label}_streak_all": streak})
        return pd.DataFrame(streak_rows)

    def top10_predicate(day: pd.DataFrame, rps_col: str) -> pd.Series:
        return day[rps_col].rank(method="first", ascending=False).astype(int) <= 10

    def strong_predicate(day: pd.DataFrame, rps_col: str) -> pd.Series:
        return day[rps_col] >= 90

    rps20_top10_streak_frame = calc_streak_frame("rps20", "top10", top10_predicate)
    rps20_strong_streak_frame = calc_streak_frame("rps20", "strong", strong_predicate)
    ranked = ranked.merge(rps20_top10_streak_frame, on=["date", "code"], how="left")
    ranked = ranked.merge(rps20_strong_streak_frame, on=["date", "code"], how="left")
    ranked["rps20_top10_streak_all"] = ranked["rps20_top10_streak_all"].fillna(0).astype(int)
    ranked["rps20_strong_streak_all"] = ranked["rps20_strong_streak_all"].fillna(0).astype(int)

    def calc_current_streak(rps_col: str, label: str, predicate) -> dict[str, int]:
        frames = []
        for _, day in df.dropna(subset=[rps_col]).groupby("date"):
            day = day.copy()
            day[f"is_{label}_for_col"] = predicate(day, rps_col)
            frames.append(day[["date", "code", f"is_{label}_for_col"]])
        if not frames:
            return {}
        streak_ranked = pd.concat(frames, ignore_index=True).sort_values(["code", "date"])
        streak_map = {}
        for code, group in streak_ranked.groupby("code"):
            streak = 0
            for _, row in group.sort_values("date", ascending=False).iterrows():
                if row["date"] > latest:
                    continue
                if bool(row[f"is_{label}_for_col"]):
                    streak += 1
                else:
                    break
            streak_map[code] = streak
        return streak_map

    top10_streak_maps = {col: calc_current_streak(col, "top10", top10_predicate) for col in rps_cols}
    strong_streak_maps = {col: calc_current_streak(col, "strong", strong_predicate) for col in rps_cols}
    for col, streak_map in top10_streak_maps.items():
        latest_day[f"{col}_top10_streak"] = latest_day["code"].map(streak_map).fillna(0).astype(int)
    for col, streak_map in strong_streak_maps.items():
        latest_day[f"{col}_strong_streak"] = latest_day["code"].map(streak_map).fillna(0).astype(int)
    latest_day["top10_streak"] = latest_day["rps20_top10_streak"]
    latest_day["strong_streak"] = latest_day["rps20_strong_streak"]
    latest_day["trend_score"] = latest_day["rps20"] * latest_day["strong_streak"] + latest_day["rps20"] * latest_day["top10_streak"] * 0.5

    top10_avg = float(latest_day.sort_values("rps20", ascending=False).head(10)["rps20"].mean())
    count90 = int((latest_day["rps20"] >= 90).sum())
    count80 = int((latest_day["rps20"] >= 80).sum())
    count50 = int((latest_day["rps20"] >= 50).sum())
    market_temp = round(top10_avg * 0.6 + min(count90 / 20 * 100, 100) * 0.4)
    if market_temp >= 88:
        market_status, position = "强趋势", 90
    elif market_temp >= 74:
        market_status, position = "震荡轮动", 70
    elif market_temp >= 58:
        market_status, position = "谨慎试错", 30
    else:
        market_status, position = "风险市场", 0

    themes = (
        latest_day.sort_values("rps20", ascending=False)
        .head(12)
        .groupby("theme")
        .agg(count=("code", "count"), avg_rps20=("rps20", "mean"), avg_ret20=("ret20", "mean"))
        .sort_values(["count", "avg_rps20"], ascending=False)
        .reset_index()
    )
    mainlines = themes.head(3)["theme"].tolist()

    prev_mainlines: list[str] = []
    past_dates = [d for d in dates if d < latest]
    if past_dates:
        compare_date = past_dates[-1]
        prev_day = ranked[ranked["date"] == compare_date].copy()
        if not prev_day.empty:
            prev_day["theme"] = prev_day["name"].map(theme_of)
            prev_themes = (
                prev_day.sort_values("rps20", ascending=False)
                .head(12)
                .groupby("theme")
                .agg(count=("code", "count"), avg_rps20=("rps20", "mean"))
                .sort_values(["count", "avg_rps20"], ascending=False)
                .reset_index()
            )
            prev_mainlines = prev_themes.head(3)["theme"].tolist()
    representatives = latest_day.sort_values(["theme", "trend_score", "rps20"], ascending=[True, False, False]).drop_duplicates("theme")
    latest_theme_rep = {
        row["theme"]: f'{row["short_name"]}（{row["code"]}）'
        for _, row in representatives.iterrows()
    }

    prev_theme_rep: dict[str, str] = {}
    if past_dates and prev_mainlines:
        compare_date = past_dates[-1]
        prev_day = ranked[ranked["date"] == compare_date].copy()
        if not prev_day.empty:
            prev_day["short_name"] = prev_day["name"].map(simplify_name)
            prev_day["theme"] = prev_day["name"].map(theme_of)
            prev_rep = prev_day.sort_values(["theme", "rps20"], ascending=[True, False]).drop_duplicates("theme")
            prev_theme_rep = {
                row["theme"]: f'{row["short_name"]}（{row["code"]}）'
                for _, row in prev_rep.iterrows()
            }

    entered = [theme for theme in mainlines if theme not in prev_mainlines]
    exited = [theme for theme in prev_mainlines if theme not in mainlines]
    weekly_changes = []
    if entered:
        weekly_changes.append("↑ " + " / ".join(entered[:2]))
    if exited:
        weekly_changes.append("↓ " + " / ".join(exited[:2]))
    if not weekly_changes:
        weekly_changes.append("主线延续")

    current_holdings = []
    weights = [40, 30, 30]
    for weight, theme in zip(weights, mainlines):
        row = representatives[representatives["theme"] == theme].iloc[0]
        current_holdings.append(
            {
                "theme": theme,
                "name": row["short_name"],
                "code": row["code"],
                "weight": weight,
                "rps20": clean(row["rps20"]),
                "streak": int(row["top10_streak"]),
            }
        )
    portfolio_score = round(sum(item["rps20"] * item["weight"] for item in current_holdings) / 100) if current_holdings else 0

    close_map = df.set_index(["date", "code"])["close"].to_dict()

    def portfolio_for_date(start_date: pd.Timestamp, end_date: pd.Timestamp | None = None) -> dict | None:
        end_date = end_date or latest
        start_day = ranked[ranked["date"] == start_date].copy()
        if start_day.empty:
            return None
        start_day["short_name"] = start_day["name"].map(simplify_name)
        start_day["theme"] = start_day["name"].map(theme_of)
        start_day["trend_score_for_day"] = (
            start_day["rps20"] * start_day["rps20_strong_streak_all"]
            + start_day["rps20"] * start_day["rps20_top10_streak_all"] * 0.5
        )
        start_themes = (
            start_day.sort_values("rps20", ascending=False)
            .head(12)
            .groupby("theme")
            .agg(count=("code", "count"), avg_rps20=("rps20", "mean"), avg_ret20=("ret20", "mean"))
            .sort_values(["count", "avg_rps20"], ascending=False)
            .reset_index()
        )
        start_mainlines = start_themes.head(3)["theme"].tolist()
        if len(start_mainlines) < 3:
            return None
        start_reps = (
            start_day.sort_values(["theme", "trend_score_for_day", "rps20"], ascending=[True, False, False])
            .drop_duplicates("theme")
        )
        items = []
        total_return = 0.0
        valid_weight = 0
        for weight, theme in zip(weights, start_mainlines):
            rep = start_reps[start_reps["theme"] == theme]
            if rep.empty:
                continue
            row = rep.iloc[0]
            start_close = close_map.get((start_date, row["code"]))
            end_close = close_map.get((end_date, row["code"]))
            item_return = None
            if start_close and end_close and float(start_close) > 0:
                item_return = (float(end_close) / float(start_close) - 1) * 100
                total_return += item_return * weight / 100
                valid_weight += weight
            items.append(
                {
                    "theme": theme,
                    "name": row["short_name"],
                    "code": row["code"],
                    "weight": weight,
                    "return": clean(item_return),
                    "rps20": clean(row["rps20"]),
                    "streak": int(row["rps20_strong_streak_all"]),
                }
            )
        if not items:
            return None
        if valid_weight and valid_weight != 100:
            total_return = total_return * 100 / valid_weight
        hold_days = len([d for d in dates if start_date < d <= end_date])
        return {
            "startDate": str(start_date.date()),
            "endDate": str(end_date.date()),
            "label": f"{hold_days}个交易日前",
            "holdDays": hold_days,
            "return": clean(total_return),
            "items": items,
        }

    portfolio_backtests = []
    latest_index = dates.index(latest)
    for offset in [5, 10, 20]:
        if latest_index - offset >= 0:
            result = portfolio_for_date(dates[latest_index - offset])
            if result:
                result["targetDays"] = offset
                portfolio_backtests.append(result)

    hold_windows = [5, 10, 20]
    replay_rows = []
    latest_index = dates.index(latest)
    for idx, start_date in enumerate(dates):
        if start_date > latest:
            continue
        results = []
        for hold in hold_windows:
            end_idx = min(idx + hold, latest_index)
            end_date = dates[end_idx]
            if end_date > latest:
                continue
            result = portfolio_for_date(start_date, end_date)
            if result:
                result["targetDays"] = hold
                result["isPartial"] = end_idx - idx < hold
                results.append(result)
        if results:
            replay_rows.append(
                {
                    "date": str(start_date.date()),
                    "results": results,
                }
            )

    rolling_windows = []
    for window_label, window_days in [("近1个月", 31), ("近3个月", 92)]:
        window_start = latest - pd.Timedelta(days=window_days)
        period_stats = []
        for hold in hold_windows:
            samples = []
            for idx, start_date in enumerate(dates):
                if start_date < window_start or start_date >= latest:
                    continue
                end_idx = idx + hold
                if end_idx >= len(dates):
                    continue
                end_date = dates[end_idx]
                if end_date > latest:
                    continue
                result = portfolio_for_date(start_date, end_date)
                if result and result.get("return") is not None:
                    samples.append(float(result["return"]))
            if samples:
                avg_return = sum(samples) / len(samples)
                wins = [value for value in samples if value > 0]
                period_stats.append(
                    {
                        "holdDays": hold,
                        "count": len(samples),
                        "avgReturn": clean(avg_return),
                        "winRate": clean(len(wins) / len(samples) * 100),
                        "best": clean(max(samples)),
                        "worst": clean(min(samples)),
                    }
                )
            else:
                period_stats.append(
                    {
                        "holdDays": hold,
                        "count": 0,
                        "avgReturn": None,
                        "winRate": None,
                        "best": None,
                        "worst": None,
                    }
                )
        available = [item for item in period_stats if item["avgReturn"] is not None]
        best_hold = max(available, key=lambda item: item["avgReturn"])["holdDays"] if available else None
        rolling_windows.append(
            {
                "label": window_label,
                "days": window_days,
                "bestHoldDays": best_hold,
                "stats": period_stats,
            }
        )

    def signal_for_metrics(rank: int, rps20: float, streak: int) -> str:
        if pd.isna(rps20) or rps20 < 60 or rank > 20:
            return "回避"
        if streak >= 10 and rps20 >= 90:
            return "持有"
        if streak >= 3 and rps20 >= 90:
            return "建仓"
        if rps20 >= 80:
            return "观察"
        return "回避"

    def forward_return(code: str, start_date: pd.Timestamp, hold_days: int) -> tuple[float | None, pd.Timestamp | None]:
        if start_date not in dates:
            return None, None
        start_idx = dates.index(start_date)
        end_idx = start_idx + hold_days
        if end_idx >= len(dates) or dates[end_idx] > latest:
            return None, None
        end_date = dates[end_idx]
        start_close = close_map.get((start_date, code))
        end_close = close_map.get((end_date, code))
        if not start_close or not end_close or float(start_close) <= 0:
            return None, None
        return (float(end_close) / float(start_close) - 1) * 100, end_date

    ranked_for_signal = ranked[ranked["date"] <= latest].copy()
    ranked_for_signal["short_name"] = ranked_for_signal["name"].map(simplify_name)
    ranked_for_signal["theme"] = ranked_for_signal["name"].map(theme_of)
    ranked_for_signal["signal20"] = ranked_for_signal.apply(
        lambda row: signal_for_metrics(int(row["rank"]), float(row["rps20"]), int(row["rps20_strong_streak_all"])),
        axis=1,
    )

    def stats_from_samples(samples: list[float]) -> dict:
        if not samples:
            return {"count": 0, "avgReturn": None, "winRate": None, "best": None, "worst": None}
        wins = [value for value in samples if value > 0]
        return {
            "count": len(samples),
            "avgReturn": clean(sum(samples) / len(samples)),
            "winRate": clean(len(wins) / len(samples) * 100),
            "best": clean(max(samples)),
            "worst": clean(min(samples)),
        }

    signal_summary = []
    for signal in ["建仓", "持有", "观察"]:
        signal_rows = ranked_for_signal[ranked_for_signal["signal20"] == signal]
        hold_stats = []
        for hold in hold_windows:
            samples = []
            for _, row in signal_rows.iterrows():
                value, _ = forward_return(row["code"], row["date"], hold)
                if value is not None:
                    samples.append(value)
            item = stats_from_samples(samples)
            item["holdDays"] = hold
            hold_stats.append(item)
        signal_summary.append({"signal": signal, "stats": hold_stats})

    etf_signal_backtests = []
    latest_signal_map = latest_day.set_index("code")
    for code, group in ranked_for_signal.groupby("code"):
        group = group.sort_values("date")
        latest_row = latest_signal_map.loc[code] if code in latest_signal_map.index else group.iloc[-1]
        buy_rows = group[group["signal20"].isin(["建仓", "持有"])]
        hold_stats = []
        for hold in hold_windows:
            samples = []
            for _, row in buy_rows.iterrows():
                value, _ = forward_return(code, row["date"], hold)
                if value is not None:
                    samples.append(value)
            item = stats_from_samples(samples)
            item["holdDays"] = hold
            hold_stats.append(item)
        recent_signals = []
        for _, row in buy_rows.tail(5).iloc[::-1].iterrows():
            value, end_date = forward_return(code, row["date"], 10)
            recent_signals.append(
                {
                    "date": str(row["date"].date()),
                    "signal": row["signal20"],
                    "rps20": clean(row["rps20"]),
                    "streak": int(row["rps20_strong_streak_all"]),
                    "ret10": clean(value),
                    "endDate": str(end_date.date()) if end_date is not None else None,
                }
            )
        latest_signal = signal_for_metrics(
            int(latest_row["rank"]),
            float(latest_row["rps20"]),
            int(latest_row.get("rps20_strong_streak_all", latest_row.get("strong_streak", 0))),
        )
        etf_signal_backtests.append(
            {
                "code": code,
                "name": simplify_name(latest_row["name"]),
                "theme": theme_of(latest_row["name"]),
                "currentSignal": latest_signal,
                "currentRps20": clean(latest_row["rps20"]),
                "currentStreak": int(latest_row.get("rps20_strong_streak_all", latest_row.get("strong_streak", 0))),
                "stats": hold_stats,
                "recentSignals": recent_signals,
            }
        )
    etf_signal_backtests = sorted(
        etf_signal_backtests,
        key=lambda item: (
            item["currentSignal"] not in ["持有", "建仓"],
            -(item["currentRps20"] or 0),
            item["code"],
        ),
    )

    def stat_for_hold(stats: list[dict], hold_days: int) -> dict:
        return next((item for item in stats if item.get("holdDays") == hold_days), {})

    actionable_candidates = []
    for item in etf_signal_backtests:
        if item["currentSignal"] not in ["持有", "建仓"]:
            continue
        stat20 = stat_for_hold(item["stats"], 20)
        if (stat20.get("count") or 0) < 8 or stat20.get("avgReturn") is None or stat20.get("winRate") is None:
            continue
        quality_score = (
            float(stat20["avgReturn"]) * 0.65
            + float(stat20["winRate"]) / 100 * 8
            + min(int(stat20["count"]), 50) / 50 * 2
        )
        actionable_candidates.append(
            {
                "code": item["code"],
                "name": item["name"],
                "theme": item["theme"],
                "currentSignal": item["currentSignal"],
                "rps20": item["currentRps20"],
                "streak": item["currentStreak"],
                "avg20": stat20["avgReturn"],
                "win20": stat20["winRate"],
                "count20": stat20["count"],
                "score": clean(quality_score),
            }
        )
    actionable_candidates = sorted(actionable_candidates, key=lambda item: item["score"], reverse=True)[:6]

    signal_hold_rank = []
    for row in signal_summary:
        for stat in row["stats"]:
            if stat.get("avgReturn") is None:
                continue
            signal_hold_rank.append(
                {
                    "signal": row["signal"],
                    "holdDays": stat["holdDays"],
                    "avgReturn": stat["avgReturn"],
                    "winRate": stat["winRate"],
                    "count": stat["count"],
                }
            )
    signal_hold_rank = sorted(signal_hold_rank, key=lambda item: (item["avgReturn"], item["winRate"] or 0), reverse=True)
    best_signal = signal_hold_rank[0] if signal_hold_rank else None
    insight_lines = []
    if best_signal:
        insight_lines.append(
            f'历史样本里，“{best_signal["signal"]}后持有{best_signal["holdDays"]}日”平均收益最高：{pct(best_signal["avgReturn"])}，胜率{best_signal["winRate"]:.1f}%'
        )
    if actionable_candidates:
        top_candidate = actionable_candidates[0]
        insight_lines.append(
            f'当前可操作标的优先看：{top_candidate["name"]}，20日回测均值{pct(top_candidate["avg20"])}，胜率{top_candidate["win20"]:.1f}%'
        )
    insight_lines.append("筛选口径：当前信号为建仓/持有、20日样本≥8，再按收益、胜率、样本数综合排序")

    leaders = []
    for _, row in latest_day.sort_values(["trend_score", "rps20"], ascending=False).head(8).iterrows():
        phase, phase_note = phase_of(row)
        signal, signal_note = signal_of(row)
        leaders.append(
            {
                "name": row["short_name"],
                "code": row["code"],
                "theme": row["theme"],
                "rank": int(row["rank"]),
                "rps20": clean(row["rps20"]),
                "streak": int(row["strong_streak"]),
                "top10Streak": int(row["top10_streak"]),
                "trendScore": clean(row["trend_score"], 0),
                "phase": phase,
                "phaseNote": phase_note,
                "signal": signal,
                "signalNote": signal_note,
                "ret20": clean(row["ret20"]),
            }
        )

    lifecycle = []
    selected = latest_day.sort_values(["trend_score", "rps20"], ascending=False).head(8)
    weak = latest_day.sort_values("rps20", ascending=True).head(3)
    for _, row in pd.concat([selected, weak]).drop_duplicates("code").head(10).iterrows():
        phase, note = phase_of(row)
        signal, signal_note = signal_of(row)
        lifecycle.append(
            {
                "name": row["short_name"],
                "theme": row["theme"],
                "rank": int(row["rank"]),
                "streak": int(row["strong_streak"]),
                "top10Streak": int(row["top10_streak"]),
                "rps20": clean(row["rps20"]),
                "phase": phase,
                "phaseNote": note,
                "signal": signal,
                "signalNote": signal_note,
            }
        )

    theme_map = (
        latest_day.groupby("theme")
        .agg(count=("code", "count"), avg_rps20=("rps20", "mean"), max_rps20=("rps20", "max"))
        .sort_values(["max_rps20", "count"], ascending=False)
        .head(10)
        .reset_index()
    )
    max_bar = float(theme_map["max_rps20"].max()) if not theme_map.empty else 100
    map_items = [
        {
            "theme": row["theme"],
            "count": int(row["count"]),
            "avgRps20": clean(row["avg_rps20"]),
            "width": round(float(row["max_rps20"]) / max_bar * 100),
        }
        for _, row in theme_map.iterrows()
    ]

    ranking_rows = []
    for _, row in latest_day.sort_values("rps20", ascending=False).iterrows():
        phase, phase_note = phase_of(row)
        signal, signal_note = signal_of(row)
        ranking_rows.append(
            {
                "rank": int(row["rank"]),
                "code": row["code"],
                "name": row["short_name"],
                "theme": row["theme"],
                "streak": int(row["strong_streak"]),
                "top10Streak": int(row["top10_streak"]),
                "trendScore": clean(row["trend_score"], 0),
                "phase": phase,
                "phaseNote": phase_note,
                "signal": signal,
                "signalNote": signal_note,
                "rps3": clean(row.get("rps3")),
                "rps5": clean(row.get("rps5")),
                "rps10": clean(row.get("rps10")),
                "rps20": clean(row.get("rps20")),
                "rps50": clean(row.get("rps50")),
                "rps120": clean(row.get("rps120")),
                "rps250": clean(row.get("rps250")),
                "streaks": {col: int(row.get(f"{col}_strong_streak", 0)) for col in rps_cols},
                "top10Streaks": {col: int(row.get(f"{col}_top10_streak", 0)) for col in rps_cols},
            }
        )

    action = "持有"
    risk = "低" if position >= 70 else "中" if position >= 30 else "高"
    if position == 0:
        action = "空仓等待"
    elif position <= 30:
        action = "小仓试错"
    elif count90 >= 18:
        action = "持有为主"

    history_start = latest - pd.Timedelta(days=31)
    history_entries = [
        {
            "date": str(item.date()),
            "title": f"{item.date()} ETF RPS 日报",
            "url": f"reports/{item.date()}.html",
        }
        for item in reversed([item for item in dates if history_start <= item <= latest])
    ]

    return {
        "today": today,
        "date": latest_date,
        "market": {
            "status": market_status,
            "temperature": market_temp,
            "position": position,
            "top10AvgRps": clean(top10_avg),
            "count90": count90,
            "count80": count80,
            "count50": count50,
            "risk": risk,
            "action": action,
            "statusRule": f"市场温度={market_temp}：前10平均 RPS {top10_avg:.1f} × 60% + RPS 90+数量 {count90} 只折算 × 40%",
            "positionRule": "仓位按市场温度分档：≥88 为 90%，74-87 为 70%，58-73 为 30%，低于 58 为空仓观察",
        },
        "decision": {
            "mainlines": mainlines,
            "action": action,
            "position": position,
            "risk": risk,
            "topName": leaders[0]["name"] if leaders else "-",
            "topStreak": leaders[0]["streak"] if leaders else 0,
            "weeklyChanges": weekly_changes,
            "weeklyRule": "对比上一个交易日的主线前三方向",
            "portfolioRule": "取主线前三方向，每个方向选择趋势分最高的代表 ETF",
        },
        "holdings": current_holdings,
        "portfolioBacktests": portfolio_backtests,
        "strategyBacktest": {
            "holdWindows": hold_windows,
            "replays": list(reversed(replay_rows)),
            "rolling": rolling_windows,
            "signalSummary": signal_summary,
            "etfSignals": etf_signal_backtests,
            "insights": {
                "bestSignal": best_signal,
                "candidates": actionable_candidates,
                "notes": insight_lines,
            },
        },
        "portfolioScore": portfolio_score,
        "leaders": leaders,
        "lifecycle": lifecycle,
        "marketMap": map_items,
        "rankings": ranking_rows,
        "history": history_entries,
    }


def render(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    nav_prefix = payload.get("navPrefix", "./")
    mainlines_html = '<div class="mainline-combo-list">' + "".join(
        f'<div class="mainline-combo-row"><span>{idx}.</span><b>{esc(item["theme"])}</b></div>'
        for idx, item in enumerate(payload["holdings"], 1)
    ) + "</div>"
    weekly_html = "".join(
        (
            f'<div class="weekly-change"><span class="weekly-arrow up">↑</span><span>{esc(item[2:] if item.startswith("↑ ") else item)}</span></div>'
            if item.startswith("↑ ")
            else f'<div class="weekly-change"><span class="weekly-arrow down">↓</span><span>{esc(item[2:] if item.startswith("↓ ") else item)}</span></div>'
            if item.startswith("↓ ")
            else f'<div class="weekly-change"><span>{esc(item)}</span></div>'
        )
        for item in payload["decision"]["weeklyChanges"]
    )
    backtest_cards = []
    for bt in payload.get("portfolioBacktests", []):
        return_value = bt.get("return")
        return_class = "up" if (return_value or 0) >= 0 else "down"
        item_lines = "".join(
            f'<div>{esc(item["name"])} {item["weight"]}% · {pct(item.get("return"))}</div>'
            for item in bt["items"]
        )
        backtest_cards.append(
            f'<div class="portfolio-test-card">'
            f'<strong class="{return_class}">{pct(return_value)}</strong>'
            f'<span>{esc(bt["label"])}买入 · 持有{bt["holdDays"]}日</span>'
            f'<div class="portfolio-test-list">{item_lines}</div>'
            f'</div>'
        )
    portfolio_backtest_html = (
        '<div class="portfolio-test"><div class="portfolio-test-title">历史推荐持有到当前交易日</div>'
        f'<div class="portfolio-test-grid">{"".join(backtest_cards)}</div></div>'
        if backtest_cards
        else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="format-detection" content="telephone=no">
  <title>ETF 趋势轮动看板</title>
  <style>
    :root {{
      --background: oklch(1 0 0);
      --foreground: oklch(0.141 0.005 285.823);
      --card: oklch(1 0 0);
      --card-foreground: oklch(0.141 0.005 285.823);
      --primary: oklch(0.21 0.006 285.885);
      --primary-foreground: oklch(0.985 0 0);
      --secondary: oklch(0.967 0.001 286.375);
      --secondary-foreground: oklch(0.21 0.006 285.885);
      --muted-token: oklch(0.967 0.001 286.375);
      --muted-foreground: oklch(0.552 0.016 285.938);
      --accent: oklch(0.967 0.001 286.375);
      --accent-foreground: oklch(0.21 0.006 285.885);
      --destructive: oklch(0.577 0.245 27.325);
      --border: oklch(0.92 0.004 286.32);
      --input: oklch(0.92 0.004 286.32);
      --ring: oklch(0.705 0.015 286.067);
      --chart-1: oklch(0.809 0.105 251.813);
      --chart-2: oklch(0.623 0.214 259.815);
      --chart-3: oklch(0.546 0.245 262.881);
      --radius: 0.625rem;
      --bg: oklch(0.97 0 0);
      --panel: var(--card);
      --ink: var(--foreground);
      --muted: var(--muted-foreground);
      --line: var(--border);
      --red: #b91c1c;
      --green: #047857;
      --blue: #2563eb;
      --yellow: #f5b943;
      --hot: #e85a7a;
      --soft-panel: var(--secondary);
    }}
    * {{ box-sizing: border-box; }}
    html {{ background: var(--bg); color: var(--ink); font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; -webkit-font-smoothing: antialiased; text-rendering: geometricPrecision; }}
    body {{ margin: 0; background: var(--bg); }}
    .shell {{ width: min(100%, 940px); margin: 0 auto; padding: 20px 14px 96px; }}
    .hero {{ display: grid; gap: 12px; padding: 4px 2px 16px; }}
    .eyebrow {{ color: var(--red); font-size: 11px; font-weight: 800; letter-spacing: .12em; }}
    h1 {{ margin: 0; font-size: 30px; line-height: 1.1; letter-spacing: 0; font-weight: 800; }}
    h2 {{ margin: 0; font-size: 18px; letter-spacing: 0; font-weight: 750; }}
    .hero-meta {{ display: flex; flex-wrap: wrap; gap: 8px; color: var(--muted); font-size: 13px; font-weight: 400; }}
    .status-card, .position-card, .panel {{ border: 1px solid var(--line); border-radius: 22px; background: var(--panel); box-shadow: 0 10px 28px rgba(24, 24, 27, .08), 0 1px 2px rgba(24, 24, 27, .04); overflow: hidden; }}
    .status-card, .position-card {{ padding: 14px; }}
    .label {{ color: var(--muted); font-size: 12px; font-weight: 850; }}
    .status-main {{ margin-top: 8px; font-size: 26px; font-weight: 950; }}
    .position-number {{ margin-top: 5px; color: var(--red); font-size: 36px; line-height: 1; font-weight: 950; }}
    .meter {{ height: 8px; margin-top: 12px; border-radius: 999px; background: var(--muted-token); overflow: hidden; }}
    .meter i {{ display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--chart-2), var(--hot)); }}
    .panel {{ margin-top: 16px; }}
    .panel-head {{ display: flex; justify-content: space-between; gap: 10px; align-items: baseline; border-bottom: 1px solid var(--line); background: var(--panel); padding: 16px 18px; }}
    .panel-head span {{ color: var(--muted); font-size: 12px; font-weight: 650; }}
    .today-board {{ display: grid; gap: 14px; padding: 18px; }}
    .board-line {{ display: grid; grid-template-columns: 88px 1fr; gap: 12px; align-items: start; border-bottom: 1px solid var(--line); padding-bottom: 12px; }}
    .board-line:last-child {{ border-bottom: 0; padding-bottom: 0; }}
    .board-label {{ color: var(--muted); font-size: 13px; font-weight: 650; }}
    .board-value {{ color: var(--ink); font-size: 18px; line-height: 1.4; font-weight: 800; }}
    .board-value.small {{ color: var(--secondary-foreground); font-size: 14px; font-weight: 650; }}
    .mainline-combo-list {{ display: inline-grid; gap: 4px; padding: 0; min-width: min(100%, 220px); }}
    .mainline-combo-row {{ display: grid; grid-template-columns: 18px minmax(0, 1fr); align-items: baseline; column-gap: 8px; }}
    .mainline-combo-row span {{ color: var(--muted); text-align: center; font-size: 18px; line-height: 1.35; font-weight: 650; font-variant-numeric: tabular-nums; }}
    .mainline-combo-row b {{ color: var(--ink); font-size: 20px; line-height: 1.35; font-weight: 800; }}
    .weekly-change {{ display: grid; grid-template-columns: 18px minmax(0, 1fr); column-gap: 6px; align-items: start; font-size: 15px; line-height: 1.5; font-weight: 400; }}
    .weekly-change > span:only-child {{ grid-column: 1 / -1; white-space: nowrap; }}
    .weekly-arrow {{ font-weight: 800; line-height: 1.45; text-align: center; }}
    .weekly-arrow.up {{ color: var(--red); }}
    .weekly-arrow.down {{ color: var(--green); }}
    .rule-note {{ margin-top: 5px; color: var(--muted); font-size: 12px; line-height: 1.45; font-weight: 400; }}
    .backtest-wrap {{ display: grid; gap: 14px; padding: 18px; }}
    .backtest-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    .backtest-box {{ border: 1px solid var(--line); border-radius: 18px; background: var(--panel); padding: 14px; box-shadow: 0 1px 2px rgba(24, 24, 27, .04); }}
    .box-title {{ display: flex; justify-content: space-between; gap: 10px; align-items: baseline; margin-bottom: 10px; }}
    .box-title h3 {{ margin: 0; font-size: 16px; line-height: 1.25; font-weight: 780; }}
    .box-title span {{ color: var(--muted); font-size: 12px; font-weight: 400; }}
    .control-row {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }}
    .select {{ min-height: 38px; border: 1px solid var(--line); border-radius: 12px; background: var(--secondary); color: var(--ink); font: inherit; font-size: 13px; font-weight: 650; padding: 0 36px 0 12px; }}
    .mini-tabs {{ display: inline-flex; gap: 2px; border-radius: 999px; background: var(--muted-token); padding: 3px; }}
    .mini-tabs button {{ border: 0; border-radius: 999px; background: transparent; color: var(--muted); cursor: pointer; font: inherit; font-size: 12px; font-weight: 650; padding: 7px 10px; }}
    .mini-tabs button.active {{ background: var(--panel); color: var(--ink); box-shadow: 0 1px 2px rgba(24, 24, 27, .08); }}
    .replay-cards {{ display: grid; gap: 9px; }}
    .replay-card {{ border: 1px solid var(--line); border-radius: 14px; background: var(--secondary); padding: 11px 12px; }}
    .replay-card-head {{ display: flex; justify-content: space-between; gap: 10px; align-items: baseline; }}
    .replay-card strong {{ font-size: 22px; line-height: 1; font-weight: 850; }}
    .replay-card .up, .stat-up {{ color: var(--red); }}
    .replay-card .down, .stat-down {{ color: var(--green); }}
    .replay-card span {{ color: var(--muted); font-size: 12px; font-weight: 400; }}
    .replay-list {{ display: grid; gap: 4px; margin-top: 9px; color: var(--muted); font-size: 12px; line-height: 1.4; }}
    .rolling-note {{ color: var(--muted); font-size: 12px; line-height: 1.45; font-weight: 400; }}
    .rolling-table {{ width: 100%; border-collapse: collapse; table-layout: fixed; margin-top: 10px; }}
    .rolling-table th, .rolling-table td {{ border-bottom: 1px solid var(--line); padding: 9px 6px; text-align: right; font-size: 12px; }}
    .rolling-table th:first-child, .rolling-table td:first-child {{ text-align: left; }}
    .rolling-table th {{ color: var(--muted); font-weight: 650; }}
    .rolling-table td {{ color: var(--ink); font-weight: 650; }}
    .backtest-wide {{ grid-column: 1 / -1; }}
    .insight-list {{ display: grid; gap: 8px; margin-bottom: 12px; }}
    .insight-line {{ border-left: 3px solid var(--foreground); background: var(--secondary); border-radius: 12px; color: var(--muted); font-size: 12px; line-height: 1.5; font-weight: 400; padding: 9px 10px; }}
    .candidate-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }}
    .candidate-card {{ border: 1px solid var(--line); border-radius: 16px; background: var(--panel); padding: 11px 12px; box-shadow: 0 1px 2px rgba(24, 24, 27, .04); }}
    .candidate-card h4 {{ margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; font-weight: 800; }}
    .candidate-card strong {{ display: block; margin-top: 9px; color: var(--red); font-size: 22px; line-height: 1; font-weight: 850; }}
    .candidate-card span {{ display: block; margin-top: 6px; color: var(--muted); font-size: 11px; line-height: 1.35; font-weight: 400; }}
    .signal-summary {{ display: grid; gap: 10px; }}
    .signal-row {{ display: grid; grid-template-columns: 58px repeat(3, 1fr); gap: 8px; align-items: stretch; }}
    .signal-name {{ display: flex; align-items: center; justify-content: center; border: 1px solid var(--line); border-radius: 14px; background: var(--secondary); font-size: 13px; font-weight: 800; }}
    .signal-stat {{ border: 1px solid var(--line); border-radius: 14px; background: var(--panel); padding: 9px 10px; }}
    .signal-stat b {{ display: block; font-size: 17px; line-height: 1.1; font-weight: 850; }}
    .signal-stat span {{ display: block; margin-top: 5px; color: var(--muted); font-size: 11px; line-height: 1.35; font-weight: 400; }}
    .etf-signal-grid {{ display: grid; grid-template-columns: minmax(180px, 240px) 1fr; gap: 12px; align-items: start; }}
    .etf-stats {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    .etf-stats th, .etf-stats td {{ border-bottom: 1px solid var(--line); padding: 8px 6px; text-align: right; font-size: 12px; }}
    .etf-stats th:first-child, .etf-stats td:first-child {{ text-align: left; }}
    .etf-stats th {{ color: var(--muted); font-weight: 650; }}
    .etf-stats td {{ color: var(--ink); font-weight: 650; }}
    .recent-signals {{ display: grid; gap: 7px; margin-top: 10px; }}
    .recent-signal {{ display: grid; grid-template-columns: 86px 44px 1fr; gap: 8px; border: 1px solid var(--line); border-radius: 12px; background: var(--secondary); padding: 8px 10px; color: var(--muted); font-size: 12px; font-weight: 400; }}
    .recent-signal b {{ color: var(--ink); font-weight: 750; }}
    .weekly {{ display: grid; gap: 5px; }}
    .row-title {{ font-size: 15px; font-weight: 900; }}
    .holding small, .muted {{ color: var(--muted); font-size: 12px; font-weight: 750; }}
    .weight {{ color: var(--red); text-align: right; font-size: 22px; font-weight: 950; }}
    .badge {{ display: inline-block; min-width: 46px; border-radius: 999px; font-size: 12px; font-weight: 950; padding: 5px 8px; text-align: center; white-space: nowrap; }}
    .badge.build {{ background: color-mix(in oklab, var(--chart-1) 24%, white); color: #1d4ed8; }}
    .badge.hold {{ background: #ecfdf5; color: var(--green); }}
    .badge.watch {{ background: var(--muted-token); color: #475569; }}
    .badge.avoid {{ background: color-mix(in oklab, var(--destructive) 14%, white); color: var(--red); }}
    .badge.reduce {{ background: #fff1e7; color: #c2410c; }}
    .badge.clear {{ background: #fee2e2; color: #b91c1c; }}
    .rank-panel .panel-head {{ align-items: center; border-bottom: 0; }}
    .switch {{ display: inline-flex; gap: 2px; border: 0; border-radius: 999px; background: var(--muted-token); padding: 4px; box-shadow: inset 0 0 0 1px color-mix(in oklab, var(--border) 80%, transparent); }}
    .switch button {{ border: 0; border-radius: 999px; background: transparent; color: var(--muted); cursor: pointer; font: inherit; font-size: 12px; font-weight: 650; padding: 8px 12px; transition: background .16s ease, color .16s ease, box-shadow .16s ease; }}
    .switch button.active {{ background: var(--panel); color: var(--foreground); box-shadow: 0 1px 2px rgba(24, 24, 27, .08); }}
    .rank-intro {{ display: grid; gap: 8px; padding: 4px 18px 14px; border-bottom: 0; background: var(--panel); color: var(--muted); font-size: 14px; font-weight: 400; }}
    .rps-explain {{ color: var(--muted); line-height: 1.55; }}
    .resonance-legend {{ position: relative; display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; }}
    .legend-item {{ display: inline-flex; align-items: center; gap: 6px; border: 0; background: transparent; color: var(--muted); cursor: pointer; font: inherit; font-size: 13px; font-weight: 400; padding: 2px 0; }}
    .legend-dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 999px; }}
    .legend-dot.strong {{ background: #ef5d7e; }}
    .legend-dot.medium {{ background: #f5c451; }}
    .legend-dot.light {{ background: #3b82f6; }}
    .resonance-tip {{ position: absolute; left: 0; top: calc(100% + 8px); z-index: 5; max-width: min(320px, 86vw); border: 1px solid var(--line); border-radius: var(--radius); background: var(--popover, #fff); box-shadow: 0 16px 36px rgba(24, 24, 27, .12); color: var(--secondary-foreground); font-size: 13px; font-weight: 850; line-height: 1.45; padding: 10px 12px; }}
    .head-tip {{ display: inline-flex; align-items: center; justify-content: flex-end; border: 0; background: transparent; color: inherit; cursor: pointer; font: inherit; font-weight: inherit; padding: 0; text-align: inherit; white-space: nowrap; }}
    .head-tip::after {{ content: "?"; display: inline-flex; align-items: center; justify-content: center; width: 13px; height: 13px; margin-left: 4px; border-radius: 999px; background: color-mix(in oklab, var(--ring) 22%, white); color: var(--muted); font-size: 9px; font-weight: 950; }}
    .rank-tabs {{ display: flex; gap: 8px; overflow-x: auto; padding: 14px 18px; border-bottom: 1px solid var(--line); background: var(--panel); }}
    .rank-tabs button {{ flex: 0 0 auto; min-width: 74px; border: 1px solid var(--line); border-radius: 12px; background: var(--panel); color: var(--secondary-foreground); cursor: pointer; font: inherit; font-size: 14px; font-weight: 700; padding: 10px 12px; box-shadow: 0 1px 2px rgba(24, 24, 27, .04); }}
    .rank-tabs button.active {{ border-color: var(--foreground); background: var(--foreground); color: var(--primary-foreground); box-shadow: 0 6px 16px rgba(24, 24, 27, .16); }}
    .table-scroll {{ overflow-x: auto; }}
    .rank-table {{ width: 100%; min-width: 680px; border-collapse: collapse; table-layout: fixed; background: var(--panel); }}
    .rank-table th, .rank-table td {{ border: 1px solid var(--line); padding: 9px 8px; text-align: right; font-size: 14px; font-weight: 650; }}
    .rank-table th {{ background: var(--muted-token); color: #334155; font-weight: 700; }}
    .rank-table th.left, .rank-table td.left {{ text-align: left; }}
    .rank-table .rank-col {{ width: 48px; text-align: center; }}
    .rank-table .code-col {{ width: 78px; }}
    .rank-table .name-col {{ width: 160px; }}
    .rank-table .rps-col {{ width: 88px; }}
    .rank-table .streak-col {{ width: 90px; text-align: center; }}
    .rank-table .signal-col {{ width: 86px; text-align: center; }}
    .rank-table .phase-col {{ width: 92px; text-align: center; }}
    .rank-table .score-col {{ width: 86px; }}
    .rank-name {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .tone-hot {{ background: #f39ab0; }}
    .tone-mid {{ background: #ffd88e; }}
    .tone-soft {{ background: #fff2bb; }}
    .resonance-row-strong td.name-cell {{ border-left: 4px solid #ef5d7e; background: #fff2f5; }}
    .resonance-row-medium td.name-cell {{ border-left: 4px solid #f5c451; background: #fff9e9; }}
    .resonance-row-light td.name-cell {{ border-left: 4px solid #3b82f6; background: color-mix(in oklab, var(--chart-1) 14%, white); }}
    .streak-chip {{ display: inline-block; min-width: 46px; border-radius: 999px; padding: 4px 8px; text-align: center; font-weight: 750; }}
    .streak-gray {{ background: #eef2f7; color: #475569; }}
    .streak-blue {{ background: #e8f1ff; color: #1d4ed8; }}
    .streak-orange {{ background: #fff1d6; color: #b45309; }}
    .streak-red {{ background: #fee2e2; color: #b91c1c; }}
    .score-text {{ color: var(--red); font-weight: 800; }}
    .red {{ color: #c0181f; }}
    .green {{ color: #067a73; }}
    .rank-empty {{ padding: 18px 14px; color: var(--muted); font-weight: 800; }}
    .history-list {{ display: grid; padding: 14px 18px; gap: 10px; }}
    .history-item {{ display: flex; align-items: center; justify-content: space-between; border: 1px solid var(--line); border-radius: 14px; color: inherit; padding: 11px 13px; text-decoration: none; background: var(--panel); box-shadow: 0 1px 2px rgba(24, 24, 27, .04); }}
    .history-item strong {{ font-size: 14px; }}
    .history-item span {{ color: var(--muted); font-size: 12px; font-weight: 800; }}
    .history-pager {{ display: flex; align-items: center; justify-content: center; gap: 10px; border-top: 1px solid var(--line); padding: 11px 12px 13px; }}
    .history-pager button {{ min-width: 74px; border: 1px solid var(--line); border-radius: 999px; background: var(--panel); color: var(--ink); font: inherit; font-size: 12px; font-weight: 850; padding: 7px 10px; }}
    .history-pager button:disabled {{ opacity: .42; }}
    .history-pager span {{ color: var(--muted); font-size: 12px; font-weight: 900; }}
    .footer {{ margin-top: 20px; color: var(--muted); text-align: center; font-size: 13px; font-weight: 400; }}
    .bottom-nav {{ position: fixed; left: 50%; bottom: 14px; z-index: 30; transform: translateX(-50%); display: inline-flex; gap: 3px; width: min(calc(100vw - 28px), 340px); border: 1px solid var(--line); border-radius: 999px; background: color-mix(in oklab, var(--panel) 92%, transparent); padding: 4px; box-shadow: 0 14px 34px rgba(24, 24, 27, .14), 0 1px 2px rgba(24, 24, 27, .08); backdrop-filter: blur(14px); }}
    .bottom-nav a {{ flex: 1 1 0; min-width: 132px; border-radius: 999px; color: var(--muted); text-align: center; text-decoration: none; font-size: 13px; font-weight: 650; padding: 9px 14px; }}
    .bottom-nav a.active {{ background: var(--foreground); color: var(--primary-foreground); box-shadow: 0 1px 2px rgba(24, 24, 27, .12); }}
    @media (max-width: 520px) {{
      .shell {{ padding-inline: 12px; }}
      h1 {{ font-size: 28px; }}
      .status-main {{ font-size: 22px; }}
      .position-number {{ font-size: 34px; }}
      .board-line {{ grid-template-columns: 74px 1fr; }}
      .backtest-grid {{ grid-template-columns: 1fr; }}
      .candidate-grid {{ grid-template-columns: 1fr; }}
      .signal-row {{ grid-template-columns: 1fr; }}
      .signal-name {{ justify-content: flex-start; padding: 9px 10px; }}
      .etf-signal-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="eyebrow">ETF TREND COCKPIT</div>
      <h1>ETF 趋势轮动看板</h1>
      <div class="hero-meta"><span>用数据发现主线，用 ETF 跟踪趋势</span></div>
    </section>

    <section class="panel">
      <div class="panel-head"><h2>今日主线</h2><span>当前交易日 {payload['date']}</span></div>
      <div class="today-board">
        <div class="board-line"><div class="board-label">主线方向</div><div><div class="board-value">{mainlines_html}</div></div></div>
        <div class="board-line"><div class="board-label">今日变化</div><div><div class="board-value small weekly">{weekly_html}</div><div class="rule-note">{payload['decision']['weeklyRule']}</div></div></div>
      </div>
    </section>

    <section class="panel rank-panel">
      <div class="panel-head">
        <h2 id="rankTitle">ETF 强度榜</h2>
        <div class="switch"><button class="active" data-mode="strong">强度榜</button><button data-mode="trend">趋势榜</button><button data-mode="all">全部ETF</button></div>
      </div>
      <div class="rank-intro">
        <div id="rankSubtitle">RPS 20 ≥ 90 强度榜</div>
        <div class="rps-explain" id="rpsExplain">RPS 20 代表近 20 个交易日相对价格强度；90 分以上约等于强度排名进入前 10%</div>
        <div class="resonance-legend" id="resonanceLegend">
          <button type="button" class="legend-item" data-tip="3 个及以上 RPS 周期同时 ≥ 90，代表多周期强度共振"><span class="legend-dot strong"></span>强共振</button>
          <button type="button" class="legend-item" data-tip="2 个 RPS 周期同时 ≥ 90，代表中等共振"><span class="legend-dot medium"></span>中等共振</button>
          <button type="button" class="legend-item" data-tip="1 个 RPS 周期 ≥ 90，代表轻度共振"><span class="legend-dot light"></span>轻度共振</button>
          <div class="resonance-tip" id="resonanceTip" hidden></div>
        </div>
      </div>
      <div class="rank-tabs">
        <button data-rps="rps3">RPS 3</button>
        <button data-rps="rps5">RPS 5</button>
        <button data-rps="rps10">RPS 10</button>
        <button class="active" data-rps="rps20">RPS 20</button>
        <button data-rps="rps50">RPS 50</button>
        <button data-rps="rps120">RPS 120</button>
        <button data-rps="rps250">RPS 250</button>
      </div>
      <div class="table-scroll">
        <table class="rank-table">
          <thead>
            <tr>
              <th class="rank-col">排</th>
              <th class="code-col left">代码</th>
              <th class="name-col left">名称</th>
              <th class="rps-col" id="rpsHead">RPS 20</th>
              <th class="streak-col"><button type="button" class="head-tip" data-tip="强势天数：连续多少个交易日当前所选 RPS ≥ 90，用来判断趋势是否稳定">强势天数</button></th>
              <th class="signal-col"><button type="button" class="head-tip" data-tip="信号规则：持有=强势天数≥10天且当前 RPS≥90；建仓=强势天数≥3天且当前 RPS≥90；观察=当前 RPS≥80但连续性不足；回避=跌出前20、排名靠后或当前 RPS&lt;60">信号</button></th>
              <th class="phase-col"><button type="button" class="head-tip" data-tip="阶段规则：主升浪=当前 RPS≥90 且强势天数≥10天；启动期=强势天数≥3天且当前 RPS≥90；分歧期=跌出前10但仍在前20；退潮期=跌出前20；下跌趋势=当前 RPS&lt;60">阶段</button></th>
              <th class="score-col"><button type="button" class="head-tip" data-tip="趋势分 = 当前 RPS × 强势天数 + 当前 RPS × 前10天数 × 0.5；既看持续强势，也给长期霸榜额外加分">趋势分</button></th>
            </tr>
          </thead>
          <tbody id="rankBody"></tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head"><h2>历史日报</h2></div>
      <div id="historyList" class="history-list"></div>
      <div class="history-pager" id="historyPager">
        <button type="button" id="historyPrev">上一页</button>
        <span id="historyPageText"></span>
        <button type="button" id="historyNext">下一页</button>
      </div>
    </section>

    <div class="footer">数据仅供观察，不构成投资建议 · © yaodong-design-lab</div>
  </main>
  <nav class="bottom-nav" aria-label="页面切换">
    <a class="active" href="{nav_prefix}index.html">每日观察</a>
    <a href="{nav_prefix}strategy_backtest.html">轮动策略</a>
  </nav>
  <script>
    window.COCKPIT_DATA = {data};
    const STORAGE_KEY = "etf-trend-rank-state";
    const defaultState = {{ rps: "rps20", mode: "strong" }};
    const rpsLabels = {{ rps3: "RPS 3", rps5: "RPS 5", rps10: "RPS 10", rps20: "RPS 20", rps50: "RPS 50", rps120: "RPS 120", rps250: "RPS 250" }};
    const rpsDays = {{ rps3: 3, rps5: 5, rps10: 10, rps20: 20, rps50: 50, rps120: 120, rps250: 250 }};
    const rpsCycles = ["rps3", "rps5", "rps10", "rps20", "rps50", "rps120", "rps250"];
    const modeOptions = ["strong", "trend", "all"];
    function normalizeState(raw) {{
      const next = {{ ...defaultState }};
      if (raw && rpsCycles.includes(raw.rps)) next.rps = raw.rps;
      if (raw && modeOptions.includes(raw.mode)) next.mode = raw.mode;
      return next;
    }}
    function stateFromUrl() {{
      const params = new URLSearchParams(window.location.search);
      return normalizeState({{
        rps: params.get("rps"),
        mode: params.get("mode"),
      }});
    }}
    function stateFromStorage() {{
      try {{
        const stored = window.localStorage.getItem(STORAGE_KEY);
        return stored ? normalizeState(JSON.parse(stored)) : null;
      }} catch (_error) {{
        return null;
      }}
    }}
    function persistState() {{
      const params = new URLSearchParams(window.location.search);
      params.set("rps", state.rps);
      params.set("mode", state.mode);
      const nextUrl = `${{window.location.pathname}}?${{params.toString()}}${{window.location.hash}}`;
      window.history.replaceState(null, "", nextUrl);
      try {{
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      }} catch (_error) {{
      }}
    }}
    function withStateParams(url) {{
      try {{
        const target = new URL(url, window.location.href);
        target.searchParams.set("rps", state.rps);
        target.searchParams.set("mode", state.mode);
        const sameOrigin = target.origin === window.location.origin;
        return sameOrigin
          ? `${{target.pathname.replace(window.location.origin, "")}}${{target.search}}${{target.hash}}`
          : target.toString();
      }} catch (_error) {{
        return url;
      }}
    }}
    const state = stateFromUrl().rps !== defaultState.rps || stateFromUrl().mode !== defaultState.mode
      ? stateFromUrl()
      : (stateFromStorage() || {{ ...defaultState }});
    const strategyBacktest = window.COCKPIT_DATA.strategyBacktest || {{ replays: [], rolling: [] }};
    const body = document.getElementById("rankBody");
    const title = document.getElementById("rankTitle");
    const subtitle = document.getElementById("rankSubtitle");
    const rpsExplain = document.getElementById("rpsExplain");
    const resonanceTip = document.getElementById("resonanceTip");
    const rpsHead = document.getElementById("rpsHead");
    let resonanceTimer = null;
    let historyPage = 1;
    const historyPageSize = 8;
    function syncRankControls() {{
      document.querySelectorAll(".rank-tabs button").forEach(button => {{
        button.classList.toggle("active", button.dataset.rps === state.rps);
      }});
      document.querySelectorAll(".switch button").forEach(button => {{
        button.classList.toggle("active", button.dataset.mode === state.mode);
      }});
    }}
    function syncNavLinks() {{
      document.querySelectorAll(".bottom-nav a").forEach(link => {{
        link.href = withStateParams(link.getAttribute("href"));
      }});
    }}
    function tone(value) {{
      if (value >= 98) return "hot";
      if (value >= 95) return "mid";
      if (value >= 90) return "soft";
      return "";
    }}
    function fmt(value, suffix = "") {{
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
      return Number(value).toFixed(1) + suffix;
    }}
    function fmtPct(value) {{
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
      const number = Number(value);
      return `${{number >= 0 ? "+" : ""}}${{number.toFixed(1)}}%`;
    }}
    function returnClass(value) {{
      return Number(value) >= 0 ? "up" : "down";
    }}
    function streakTone(days) {{
      const n = Number(days) || 0;
      if (n >= 20) return "streak-red";
      if (n >= 10) return "streak-orange";
      if (n >= 4) return "streak-blue";
      return "streak-gray";
    }}
    function signalClass(signal) {{
      if (signal === "持有") return "hold";
      if (signal === "建仓") return "build";
      if (signal === "回避") return "avoid";
      return "watch";
    }}
    function resonanceCount(row) {{
      return rpsCycles.reduce((count, key) => count + (Number(row[key]) >= 90 ? 1 : 0), 0);
    }}
    function resonanceClass(row) {{
      const count = resonanceCount(row);
      if (count >= 3) return "strong";
      if (count >= 2) return "medium";
      if (count >= 1) return "light";
      return "";
    }}
    function currentStreak(row) {{
      return row.streaks && row.streaks[state.rps] !== undefined ? Number(row.streaks[state.rps]) : Number(row.streak || 0);
    }}
    function currentTop10Streak(row) {{
      return row.top10Streaks && row.top10Streaks[state.rps] !== undefined ? Number(row.top10Streaks[state.rps]) : Number(row.top10Streak || 0);
    }}
    function currentTrendScore(row) {{
      const value = Number(row[state.rps]);
      const streak = currentStreak(row);
      const top10Streak = currentTop10Streak(row);
      if (Number.isNaN(value)) return 0;
      return value * streak + value * top10Streak * 0.5;
    }}
    function currentSignal(row, rank) {{
      const value = Number(row[state.rps]);
      const streak = currentStreak(row);
      if (Number.isNaN(value) || value < 60 || rank > 20) return "回避";
      if (streak >= 10 && value >= 90) return "持有";
      if (streak >= 3 && value >= 90) return "建仓";
      if (value >= 80) return "观察";
      return "回避";
    }}
    function currentPhase(row, rank) {{
      const value = Number(row[state.rps]);
      const streak = currentStreak(row);
      if (Number.isNaN(value) || value < 60) return "下跌趋势";
      if (rank > 20) return "退潮期";
      if (streak >= 10 && value >= 90) return "主升浪";
      if (streak >= 3 && value >= 90) return "启动期";
      if (rank > 10) return "分歧期";
      return "观察期";
    }}
    function renderRank() {{
      const rows = [...window.COCKPIT_DATA.rankings]
        .filter(row => state.mode === "all" || Number(row[state.rps]) >= 90)
        .sort((a, b) => {{
          if (state.mode === "trend") return currentTrendScore(b) - currentTrendScore(a);
          return (Number(b[state.rps]) || -1) - (Number(a[state.rps]) || -1);
        }});
      const visible = rows;
      rpsHead.textContent = rpsLabels[state.rps];
      title.textContent = state.mode === "all"
        ? "ETF 全量表"
        : state.mode === "trend"
          ? "ETF 趋势榜"
          : "ETF 强度榜";
      subtitle.textContent = state.mode === "all"
        ? `${{rpsLabels[state.rps]}} 全部ETF排序 · ${{visible.length}} 只`
        : state.mode === "trend"
          ? `${{rpsLabels[state.rps]}} 趋势榜 · 按趋势分排序 · ${{visible.length}} 只`
          : `${{rpsLabels[state.rps]}} ≥ 90 强度榜 · ${{visible.length}} 只`;
      rpsExplain.textContent = `${{rpsLabels[state.rps]}} 代表近 ${{rpsDays[state.rps]}} 个交易日相对价格强度；90 分以上约等于强度排名进入前 10%`;
      if (!visible.length) {{
        body.innerHTML = `<tr><td colspan="8" class="rank-empty">当前没有 ${{rpsLabels[state.rps]}} ≥ 90 的 ETF</td></tr>`;
        return;
      }}
      body.innerHTML = visible.map((row, idx) => {{
        const v = Number(row[state.rps]);
        const t = tone(v);
        const resonance = resonanceClass(row);
        const streak = currentStreak(row);
        const trendScore = currentTrendScore(row);
        const signal = currentSignal(row, idx + 1);
        const phase = currentPhase(row, idx + 1);
        return `<tr class="${{resonance ? `resonance-row-${{resonance}}` : ""}}">
          <td class="rank-col">${{idx + 1}}</td>
          <td class="left code-col">${{row.code}}</td>
          <td class="left name-col name-cell"><div class="rank-name">${{row.name}}</div></td>
          <td class="rps-col ${{t ? `tone-${{t}}` : ""}}">${{fmt(v)}}</td>
          <td class="streak-col"><span class="streak-chip ${{streakTone(streak)}}">${{streak}}天</span></td>
          <td class="signal-col"><span class="badge ${{signalClass(signal)}}">${{signal}}</span></td>
          <td class="phase-col">${{phase}}</td>
          <td class="score-col score-text">${{fmt(trendScore, "")}}</td>
        </tr>`;
      }}).join("");
    }}
    function showTip(text) {{
      resonanceTip.textContent = text;
      resonanceTip.hidden = false;
      clearTimeout(resonanceTimer);
      resonanceTimer = setTimeout(() => {{
        resonanceTip.hidden = true;
      }}, 5000);
    }}
    function setupReplay() {{
      const select = document.getElementById("replayDate");
      const cards = document.getElementById("replayCards");
      const replays = strategyBacktest.replays || [];
      if (!select || !cards) return;
      if (!replays.length) {{
        cards.innerHTML = `<div class="rolling-note">当前历史数据不足，暂时无法回放</div>`;
        return;
      }}
      select.innerHTML = replays.map(item => `<option value="${{item.date}}">${{item.date}}</option>`).join("");
      const preferred = replays.find(item => item.results && item.results.length >= 3) || replays[0];
      select.value = preferred.date;
      function renderReplay() {{
        const current = replays.find(item => item.date === select.value);
        if (!current) return;
        cards.innerHTML = current.results.map(result => {{
          const lines = result.items.map(item => `
            <div>${{item.name}} ${{item.weight}}% · ${{fmtPct(item.return)}}</div>
          `).join("");
          const holdText = result.isPartial
            ? `计划持有 ${{result.targetDays}} 个交易日 · 已持有至当前交易日`
            : `持有 ${{result.targetDays}} 个交易日`;
          return `<div class="replay-card">
            <div class="replay-card-head">
              <strong class="${{returnClass(result.return)}}">${{fmtPct(result.return)}}</strong>
              <span>${{holdText}}</span>
            </div>
            <div class="replay-list">
              <div>${{result.startDate}} 买入，${{result.endDate}} 卖出</div>
              ${{lines}}
            </div>
          </div>`;
        }}).join("");
      }}
      select.addEventListener("change", renderReplay);
      renderReplay();
    }}
    function setupRolling() {{
      const body = document.getElementById("rollingBody");
      const note = document.getElementById("rollingNote");
      const tabs = document.querySelectorAll("#rollingTabs button");
      const windows = strategyBacktest.rolling || [];
      if (!body || !note) return;
      function renderRolling(label) {{
        const current = windows.find(item => item.label === label) || windows[0];
        if (!current) {{
          note.textContent = "当前历史数据不足，暂时无法统计";
          body.innerHTML = "";
          return;
        }}
        note.textContent = current.bestHoldDays
          ? `${{current.label}}样本里，持有 ${{current.bestHoldDays}} 个交易日的平均收益最高`
          : `${{current.label}}样本不足，继续积累历史日报后再比较`;
        body.innerHTML = current.stats.map(item => `
          <tr>
            <td>${{item.holdDays}}日</td>
            <td>${{item.count}}</td>
            <td class="${{Number(item.avgReturn) >= 0 ? "stat-up" : "stat-down"}}">${{fmtPct(item.avgReturn)}}</td>
            <td>${{item.winRate === null ? "-" : Number(item.winRate).toFixed(1) + "%"}}</td>
            <td class="${{Number(item.worst) >= 0 ? "stat-up" : "stat-down"}}">${{fmtPct(item.worst)}}</td>
          </tr>
        `).join("");
      }}
      tabs.forEach(button => {{
        button.addEventListener("click", () => {{
          tabs.forEach(item => item.classList.remove("active"));
          button.classList.add("active");
          renderRolling(button.dataset.window);
        }});
      }});
      renderRolling("近1个月");
    }}
    function setupSignalSummary() {{
      const el = document.getElementById("signalSummary");
      const rows = strategyBacktest.signalSummary || [];
      if (!el) return;
      if (!rows.length) {{
        el.innerHTML = `<div class="rolling-note">当前历史数据不足，暂时无法统计信号表现</div>`;
        return;
      }}
      el.innerHTML = rows.map(row => `
        <div class="signal-row">
          <div class="signal-name">${{row.signal}}</div>
          ${{row.stats.map(stat => `
            <div class="signal-stat">
              <b class="${{Number(stat.avgReturn) >= 0 ? "stat-up" : "stat-down"}}">${{fmtPct(stat.avgReturn)}}</b>
              <span>${{stat.holdDays}}日 · 样本${{stat.count}} · 胜率${{stat.winRate === null ? "-" : Number(stat.winRate).toFixed(1) + "%"}}</span>
            </div>
          `).join("")}}
        </div>
      `).join("");
    }}
    function setupBacktestInsights() {{
      const insightEl = document.getElementById("backtestInsights");
      const candidateEl = document.getElementById("backtestCandidates");
      const insights = strategyBacktest.insights || {{}};
      const notes = insights.notes || [];
      const candidates = insights.candidates || [];
      if (insightEl) {{
        insightEl.innerHTML = notes.length
          ? notes.map(note => `<div class="insight-line">${{note}}</div>`).join("")
          : `<div class="rolling-note">当前历史数据不足，暂时无法归纳规律</div>`;
      }}
      if (candidateEl) {{
        candidateEl.innerHTML = candidates.length
          ? candidates.slice(0, 6).map(item => `
            <div class="candidate-card">
              <h4>${{item.name}}</h4>
              <strong>${{fmtPct(item.avg20)}}</strong>
              <span>${{item.currentSignal}} · RPS 20=${{fmt(item.rps20)}} · 连续${{item.streak}}天</span>
              <span>20日胜率${{Number(item.win20).toFixed(1)}}% · 样本${{item.count20}} · 评分${{fmt(item.score)}}</span>
            </div>
          `).join("")
          : `<div class="rolling-note">当前没有满足样本数和信号条件的候选</div>`;
      }}
    }}
    function setupEtfSignalBacktest() {{
      const select = document.getElementById("etfSignalSelect");
      const note = document.getElementById("etfSignalNote");
      const statsBody = document.getElementById("etfSignalStats");
      const recent = document.getElementById("recentSignals");
      const items = strategyBacktest.etfSignals || [];
      if (!select || !note || !statsBody || !recent) return;
      if (!items.length) {{
        note.textContent = "当前历史数据不足，暂时无法统计单只 ETF";
        return;
      }}
      select.innerHTML = items.map(item => `
        <option value="${{item.code}}">${{item.name}}（${{item.code}}）</option>
      `).join("");
      const preferred = items.find(item => item.currentSignal === "持有") || items.find(item => item.currentSignal === "建仓") || items[0];
      select.value = preferred.code;
      function renderEtf() {{
        const current = items.find(item => item.code === select.value);
        if (!current) return;
        note.textContent = `当前信号：${{current.currentSignal}} · RPS 20=${{fmt(current.currentRps20)}} · 强势天数=${{current.currentStreak}}天`;
        statsBody.innerHTML = current.stats.map(stat => `
          <tr>
            <td>${{stat.holdDays}}日</td>
            <td>${{stat.count}}</td>
            <td class="${{Number(stat.avgReturn) >= 0 ? "stat-up" : "stat-down"}}">${{fmtPct(stat.avgReturn)}}</td>
            <td>${{stat.winRate === null ? "-" : Number(stat.winRate).toFixed(1) + "%"}}</td>
            <td class="${{Number(stat.worst) >= 0 ? "stat-up" : "stat-down"}}">${{fmtPct(stat.worst)}}</td>
          </tr>
        `).join("");
        recent.innerHTML = current.recentSignals && current.recentSignals.length
          ? current.recentSignals.map(item => `
            <div class="recent-signal">
              <b>${{item.date}}</b>
              <span>${{item.signal}}</span>
              <span>RPS ${{fmt(item.rps20)}} · 连续${{item.streak}}天 · 10日${{fmtPct(item.ret10)}}</span>
            </div>
          `).join("")
          : `<div class="rolling-note">这只 ETF 暂无历史建仓/持有信号</div>`;
      }}
      select.addEventListener("change", renderEtf);
      renderEtf();
    }}
    document.querySelectorAll(".legend-item, .head-tip").forEach(button => {{
      button.addEventListener("click", () => {{
        showTip(button.dataset.tip);
      }});
    }});
    document.querySelectorAll(".rank-tabs button").forEach(button => {{
      button.addEventListener("click", () => {{
        state.rps = button.dataset.rps;
        syncRankControls();
        persistState();
        renderRank();
        renderHistory();
        syncNavLinks();
      }});
    }});
    document.querySelectorAll(".switch button").forEach(button => {{
      button.addEventListener("click", () => {{
        state.mode = button.dataset.mode;
        syncRankControls();
        persistState();
        renderRank();
        renderHistory();
        syncNavLinks();
      }});
    }});
    function renderHistory() {{
      const history = window.COCKPIT_DATA.history || [];
      const totalPages = Math.max(1, Math.ceil(history.length / historyPageSize));
      historyPage = Math.min(Math.max(1, historyPage), totalPages);
      const start = (historyPage - 1) * historyPageSize;
      const items = history.slice(start, start + historyPageSize);
      document.getElementById("historyList").innerHTML = items.map(item => `
        <a class="history-item" href="${{withStateParams(item.url)}}">
          <strong>${{item.title}}</strong>
          <span>查看</span>
        </a>
      `).join("");
      document.getElementById("historyPageText").textContent = `${{historyPage}} / ${{totalPages}}`;
      document.getElementById("historyPrev").disabled = historyPage <= 1;
      document.getElementById("historyNext").disabled = historyPage >= totalPages;
      document.getElementById("historyPager").hidden = history.length <= historyPageSize;
    }}
    document.getElementById("historyPrev").addEventListener("click", () => {{
      historyPage -= 1;
      renderHistory();
    }});
    document.getElementById("historyNext").addEventListener("click", () => {{
      historyPage += 1;
      renderHistory();
    }});
    syncRankControls();
    persistState();
    syncNavLinks();
    renderRank();
    renderHistory();
    setupReplay();
    setupRolling();
    setupBacktestInsights();
    setupSignalSummary();
    setupEtfSignalBacktest();
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history-csv", default="output/etf_rps_chart_universe/etf_nav_daily_chart_universe_2026-06-12.csv")
    parser.add_argument("--date", default="2026-06-12")
    parser.add_argument("--today", default=str(date_cls.today()))
    parser.add_argument("--out", default="etf-rps-trend-cockpit")
    args = parser.parse_args()

    history_csv = Path(args.history_csv)
    payload = build_payload(history_csv, args.date, args.today)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    source_site = Path(".")
    for dirname in ("reports", "data", "assets"):
        source = source_site / dirname
        target = out / dirname
        if source.exists():
            shutil.copytree(source, target, dirs_exist_ok=True)
    index_payload = set_history_prefix(payload, "reports/")
    index_payload["navPrefix"] = "./"
    report_history = set_history_prefix(payload, "")["history"]
    (out / "index.html").write_text(render(index_payload), encoding="utf-8")
    (out / "payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    reports = out / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    for item in payload["history"]:
        report_payload = build_payload(history_csv, item["date"], args.today)
        report_payload["history"] = report_history
        report_payload["navPrefix"] = "../"
        report_html = render(report_payload)
        (reports / f"{item['date']}.html").write_text(report_html, encoding="utf-8")
    print(out / "index.html")
    print(out / "payload.json")


if __name__ == "__main__":
    main()
