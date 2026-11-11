#!/usr/bin/env python3
"""Rank-change arrows use the same board and sort as the dashboard table."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_etf_trend_decision import previous_trading_session, rank_moves  # noqa: E402


def row(code, rps3, rps20, streak3=0, streak20=0, top3=0, top20=0):
    return {
        "code": code,
        "rps3": rps3,
        "rps5": rps3,
        "rps10": rps3,
        "rps20": rps20,
        "rps50": rps20,
        "rps120": rps20,
        "rps250": rps20,
        "streaks": {"rps3": streak3, "rps5": 0, "rps10": 0, "rps20": streak20, "rps50": 0, "rps120": 0, "rps250": 0},
        "top10Streaks": {"rps3": top3, "rps5": 0, "rps10": 0, "rps20": top20, "rps50": 0, "rps120": 0, "rps250": 0},
    }


def test_strength_board_rank_change():
    # RPS 20 order is the tie break. Strength board keeps only RPS >= 90.
    current = [
        row("AAA", rps3=90.0, rps20=99),
        row("BBB", rps3=95.0, rps20=80),
        row("CCC", rps3=50.0, rps20=70),
        row("EEE", rps3=99.0, rps20=60),
    ]
    previous = [
        row("DDD", rps3=96.0, rps20=90),
        row("AAA", rps3=92.0, rps20=88),
        row("BBB", rps3=91.0, rps20=70),
    ]
    moves = rank_moves(current, previous)
    # Strength board, RPS 3: yesterday DDD, AAA, BBB; today EEE, BBB, AAA.
    assert moves["AAA"]["strong"]["rps3"] == -1
    assert moves["BBB"]["strong"]["rps3"] == 1
    assert moves["EEE"]["strong"]["rps3"] is None
    assert moves["CCC"]["strong"]["rps3"] is None
    assert moves["BBB"]["all"]["rps3"] == 1
    assert moves["AAA"]["all"]["rps20"] == 1
    assert moves["AAA"]["strong"]["rps20"] is None


def test_previous_session_is_the_prior_trading_day_in_the_file():
    dates = pd.read_csv(ROOT / "automation" / "etf_nav_daily_chart_universe_latest.csv", usecols=["date"])
    parsed = pd.to_datetime(dates["date"]).drop_duplicates()
    latest = parsed.max()
    previous = previous_trading_session(parsed, latest)
    assert str(latest.date()) == "2026-11-11"
    assert str(previous.date()) == "2026-11-10"


if __name__ == "__main__":
    test_strength_board_rank_change()
    test_previous_session_is_the_prior_trading_day_in_the_file()
    print("rank move checks passed")
