#!/usr/bin/env python3
"""Offline checks for the daily ETF update and rotation backtest."""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backtest_rps_rule_matrix import run_matrix  # noqa: E402
from daily_update import (  # noqa: E402
    UpdateRejected,
    accept_quotes,
    assert_history_preserved,
    date_counts,
    prepare_history_text,
    scheduled_target_date,
)


HISTORY = ROOT / "automation" / "etf_nav_daily_chart_universe_latest.csv"
EXPECTED = [
    ("2025-04-10", "2025-04-16", "159698"),
    ("2025-04-17", "2025-05-12", "518880"),
    ("2025-05-14", "2025-06-13", "513030"),
    ("2025-06-16", "2025-06-19", "513120"),
    ("2025-06-20", "2025-07-01", "513780"),
    ("2025-07-02", "2025-09-04", "515880"),
    ("2025-09-05", "2025-09-09", "515880"),
    ("2025-09-10", "2025-10-10", "159796"),
    ("2025-10-13", "2025-10-14", "159796"),
    ("2025-10-15", "2025-10-17", "159516"),
    ("2025-10-20", "2025-11-05", "513310"),
    ("2025-11-07", "2025-11-17", "159326"),
    ("2025-11-18", "2025-11-21", "159870"),
    ("2025-11-26", "2025-12-09", "513290"),
    ("2025-12-10", "2025-12-16", "159583"),
    ("2025-12-17", "2026-01-15", "159206"),
    ("2026-01-16", "2026-01-26", "159218"),
    ("2026-01-27", "2026-01-28", "159218"),
    ("2026-01-29", "2026-01-30", "517520"),
    ("2026-02-02", "2026-02-05", "517520"),
    ("2026-02-06", "2026-02-09", "517520"),
    ("2026-02-10", "2026-02-11", "517520"),
    ("2026-02-12", "2026-03-09", "561360"),
    ("2026-03-10", "2026-03-16", "159326"),
    ("2026-03-17", "2026-04-09", "159981"),
    ("2026-04-10", "2026-04-13", "159981"),
    ("2026-04-14", "2026-04-28", "159583"),
    ("2026-04-29", "2026-05-15", "513310"),
    ("2026-05-18", "2026-06-05", "513310"),
    ("2026-06-08", "2026-07-02", "588170"),
    ("2026-07-03", "2026-07-10", "588170"),
    ("2026-07-13", "2026-07-15", "588170"),
    ("2026-07-20", "2026-07-24", "159992"),
    ("2026-08-04", "2026-08-13", "513050"),
    ("2026-08-18", "2026-09-07", "517520"),
    ("2026-09-08", "2026-09-18", "159518"),
    ("2026-09-21", "2026-09-23", "159981"),
    ("2026-09-24", "2026-10-21", "159509"),
    ("2026-10-22", "2026-10-23", "159509"),
    ("2026-10-26", "2026-11-10", "159529"),
]


def test_backtest_matches_published_rounds() -> None:
    result = run_matrix(HISTORY, "2026-11-10")
    got = [(str(trade["buy"].date()), str(trade["sell"].date()), trade["code"]) for trade in result["trades"]]
    assert got == EXPECTED, list(zip(got, EXPECTED))
    assert result["rule"].label == "回撤6% 或跌破20日均线"
    assert abs(result["nav"] - 2.855) < 0.01
    assert abs(result["cumulative"] * 100 - 185.5) < 0.15
    assert abs(result["max_drawdown"] * 100 + 31.4) < 0.15
    assert abs(result["average_hold"] - 12.4) < 0.05
    assert result["etf_count"] == 178
    assert result["trades"][-1]["open"] is True
    page_nav = f"{result['nav']:.2f}"
    assert page_nav.startswith("2.8")
    print(
        "backtest",
        result["rule"].label,
        "nav",
        round(result["nav"], 4),
        "etfs",
        result["etf_count"],
        "candidate",
        result["candidate_code"],
        "benchmark",
        round(result["benchmark_nav"][-1], 4),
    )


def test_morning_delay_uses_previous_weekday() -> None:
    monday_morning = datetime(2026, 11, 16, 8, 30, tzinfo=timezone(timedelta(hours=8)))
    assert scheduled_target_date(monday_morning) == "2026-11-13"
    friday_evening = datetime(2026, 11, 13, 18, 5, tzinfo=timezone(timedelta(hours=8)))
    assert scheduled_target_date(friday_evening) == "2026-11-13"
    saturday = datetime(2026, 11, 14, 18, 0, tzinfo=timezone(timedelta(hours=8)))
    assert scheduled_target_date(saturday) == "2026-11-13"


def test_quote_failures_raise() -> None:
    stale = pd.DataFrame(
        {"code": [f"{index:06d}" for index in range(180)], "trade_price": [1.0] * 180, "quote_time": ["20261109150000"] * 180}
    )
    try:
        accept_quotes(stale, "2026-11-10", 160)
    except UpdateRejected as exc:
        assert "quote_date=2026-11-09" in str(exc)
    else:
        raise AssertionError("stale quotes were accepted")
    thin = stale.copy()
    thin["quote_time"] = "20261110150000"
    thin = thin.head(10)
    try:
        accept_quotes(thin, "2026-11-10", 160)
    except UpdateRejected as exc:
        assert "etfs=10" in str(exc)
    else:
        raise AssertionError("thin quotes were accepted")


def test_refresh_keeps_every_prior_session() -> None:
    original = HISTORY.read_text(encoding="utf-8")
    day = pd.read_csv(HISTORY, dtype={"code": str}, parse_dates=["date"])
    day["code"] = day["code"].astype(str).str.zfill(6)
    day = day[day["date"] == "2026-11-10"][["code", "name", "trade_close", "close"]].copy()
    day["trade_price"] = day["trade_close"].fillna(day["close"])
    day["quote_time"] = "20261110150000"
    updated, info = prepare_history_text(original, day, "2026-11-10", {})
    assert info["mode"] == "unchanged"
    assert updated == (original if original.endswith("\n") else original + "\n")
    assert_history_preserved(original, updated, "2026-11-10", 160)
    before = date_counts(original)
    after = date_counts(updated)
    assert before == after
    assert after["2026-11-10"] == 178
    assert sum(before.values()) == sum(after.values())
    print("refresh rows", sum(after.values()), "sessions", len(after), "target", after["2026-11-10"])


def test_append_keeps_existing_sessions() -> None:
    original = HISTORY.read_text(encoding="utf-8")
    day = pd.read_csv(HISTORY, dtype={"code": str}, parse_dates=["date"])
    day["code"] = day["code"].astype(str).str.zfill(6)
    day = day[day["date"] == "2026-11-10"][["code", "name", "close"]].copy()
    day = day.rename(columns={"close": "trade_price"})
    day["quote_time"] = "20261111150000"
    updated, info = prepare_history_text(original, day, "2026-11-11", {})
    assert info["mode"] == "appended"
    assert_history_preserved(original, updated, "2026-11-11", 160)
    before = date_counts(original)
    after = date_counts(updated)
    for session, count in before.items():
        assert after[session] == count
    assert after["2026-11-11"] == before["2026-11-10"]
    assert sum(after.values()) == sum(before.values()) + before["2026-11-10"]
    print("append preserved", len(before), "sessions and added", after["2026-11-11"])


def test_older_target_and_cli_failures() -> None:
    original = HISTORY.read_text(encoding="utf-8")
    quotes = pd.DataFrame(
        {
            "code": [f"{index:06d}" for index in range(1, 181)],
            "trade_price": [1.0] * 180,
            "name": ["测试ETF"] * 180,
        }
    )
    try:
        prepare_history_text(original, quotes, "2026-11-09", {})
    except UpdateRejected as exc:
        assert "before history latest" in str(exc)
    else:
        raise AssertionError("older target was allowed to rewrite history")

    quote_path = ROOT / "tests" / "_quotes_thin.csv"
    quote_path.write_text("code,trade_price,quote_time\n159529,1.0,20261110150000\n", encoding="utf-8")
    try:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "daily_update.py"), "--dry-run", "--skip-site", "--date", "2026-11-10", "--quotes-csv", str(quote_path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode != 0, completed.stdout + completed.stderr
        assert "ETF update failed" in completed.stderr
    finally:
        quote_path.unlink(missing_ok=True)


if __name__ == "__main__":
    test_morning_delay_uses_previous_weekday()
    test_quote_failures_raise()
    test_refresh_keeps_every_prior_session()
    test_append_keeps_existing_sessions()
    test_older_target_and_cli_failures()
    test_backtest_matches_published_rounds()
    print("all pipeline checks passed")
