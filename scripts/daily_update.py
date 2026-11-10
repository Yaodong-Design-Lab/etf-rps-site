#!/usr/bin/env python3
"""Fetch one A-share session and refresh the ETF RPS site plus the rotation page.

An empty date uses the Beijing clock. Before 15:00, including a scheduled run
that spills into the next morning, the target is the previous weekday. The job
publishes only when Tencent quotes for that exact session cover at least 160
ETFs. A later re-run of a session already in history replaces that session in
place and rebuilds the report and strategy page; it does not append a second
copy and it does not delete any other trading day. A holiday, weekend, or
stale quote fails the process instead of committing a skip marker.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
AUTOMATION = ROOT / "automation"
HISTORY_CSV = AUTOMATION / "etf_nav_daily_chart_universe_latest.csv"
UNIVERSE_CSV = AUTOMATION / "chart_universe_codes.csv"
RET_WINDOWS = [1, 3, 5, 10, 20, 50, 60, 120, 250]
RPS_WINDOWS = [3, 5, 10, 20, 50, 60, 120, 250]
SPLIT_LEVELS = (2, 3, 4, 5, 6, 8, 10)
BEIJING = timezone(timedelta(hours=8))


class UpdateRejected(RuntimeError):
    """The session cannot be published. The repository must stay unchanged."""


def scheduled_target_date(now: datetime | None = None) -> str:
    current = now or datetime.now(BEIJING)
    if current.tzinfo is None:
        current = current.replace(tzinfo=BEIJING)
    else:
        current = current.astimezone(BEIJING)
    target = current.date()
    # A delayed job can wake up after midnight. Before the close, the latest
    # session that can be published is still the previous weekday.
    if current.hour < 15:
        target -= timedelta(days=1)
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    return target.isoformat()


def parse_iso_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise UpdateRejected(f"Date must be YYYY-MM-DD, got {value!r}") from exc


def run(args: list[str]) -> None:
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def copy_tree_contents(source: Path, target: Path) -> None:
    if not source.exists():
        return
    for item in source.iterdir():
        dest = target / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)


def load_universe() -> pd.DataFrame:
    universe = pd.read_csv(UNIVERSE_CSV, dtype={"code": str})
    universe["code"] = universe["code"].astype(str).str.zfill(6)
    return universe


def load_quotes_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"code": str})
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    if "quote_time" not in frame.columns:
        raise UpdateRejected(f"{path} is missing quote_time")
    price_column = "trade_price" if "trade_price" in frame.columns else "close" if "close" in frame.columns else "price"
    if price_column not in frame.columns:
        raise UpdateRejected(f"{path} is missing a price column")
    frame = frame.rename(columns={price_column: "trade_price"})
    if "name" not in frame.columns:
        frame["name"] = frame["code"]
    return frame[["code", "name", "trade_price", "quote_time"]]


def fetch_quotes(batch_size: int, pause: float) -> pd.DataFrame:
    from update_etf_chart_universe_with_tencent import fetch_tencent_quotes

    universe = load_universe()
    quotes = fetch_tencent_quotes(universe["code"].tolist(), batch_size, pause, 3)
    if quotes.empty:
        raise UpdateRejected("No Tencent quotes fetched.")
    quotes = quotes.rename(columns={"close": "trade_price"})
    return quotes


def accept_quotes(quotes: pd.DataFrame, target: str, min_rows: int) -> pd.DataFrame:
    if quotes is None or quotes.empty:
        raise UpdateRejected("No Tencent quotes fetched.")
    frame = quotes.copy()
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    timestamps = frame["quote_time"].fillna("").astype(str)
    dated = timestamps.str.extract(r"^(\d{8})", expand=False)
    if dated.dropna().empty:
        raise UpdateRejected("Tencent quotes did not include a trading-day timestamp.")
    mode = str(dated.mode().iloc[0])
    quote_date = f"{mode[:4]}-{mode[4:6]}-{mode[6:8]}"
    matched = frame[dated == target.replace("-", "")].drop_duplicates("code", keep="last")
    if quote_date != target or len(matched) < min_rows:
        raise UpdateRejected(
            f"Quote check failed for {target}: quote_date={quote_date}, etfs={len(matched)}, minimum={min_rows}."
        )
    return matched


def adjustment_factor(trade: float, previous_close: float | None, carried: float | None) -> float:
    factor = float(carried) if carried and carried > 0 else 1.0
    if previous_close is None or previous_close <= 0 or trade <= 0:
        return factor
    if 0.55 <= (trade * factor) / previous_close <= 1.8:
        return factor
    best: tuple[float, float] | None = None
    for level in SPLIT_LEVELS:
        error = abs(trade * level / previous_close - 1)
        if error <= 0.18 and (best is None or error < best[0]):
            best = (error, float(level))
    return best[1] if best else factor


def _blank(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, str):
        return value
    return format(float(value), ".10g")


def _csv_row(columns: list[str], row: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="")
    writer.writerow([row.get(column, "") if not isinstance(row.get(column, ""), float) else _blank(row.get(column)) for column in columns])
    return buffer.getvalue()


def date_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or "date" not in header:
        raise UpdateRejected("History CSV is missing a date column")
    date_index = header.index("date")
    for row in reader:
        if not row:
            continue
        counts[row[date_index]] = counts.get(row[date_index], 0) + 1
    return counts


def assert_history_preserved(before: str, after: str, target: str, min_rows: int) -> None:
    previous = date_counts(before)
    updated = date_counts(after)
    for session, count in previous.items():
        if session != target and updated.get(session) != count:
            raise UpdateRejected(f"Refusing to publish: {session} changed from {count} rows to {updated.get(session)}")
    if updated.get(target, 0) < min_rows:
        raise UpdateRejected(f"Refusing to publish: {target} has {updated.get(target, 0)} rows, minimum is {min_rows}")
    seen: set[tuple[str, str]] = set()
    reader = csv.reader(io.StringIO(after))
    header = next(reader)
    date_index = header.index("date")
    code_index = header.index("code")
    for row in reader:
        if not row:
            continue
        key = (row[date_index], row[code_index].zfill(6))
        if key in seen:
            raise UpdateRejected(f"Refusing to publish: duplicate history row {key[0]} {key[1]}")
        seen.add(key)


def prepare_history_text(original: str, quotes: pd.DataFrame, target: str, names: dict[str, str]) -> tuple[str, dict]:
    history = pd.read_csv(io.StringIO(original), dtype={"code": str})
    history["code"] = history["code"].astype(str).str.zfill(6)
    history["date"] = pd.to_datetime(history["date"])
    target_ts = pd.Timestamp(target)
    latest = history["date"].max()
    if pd.notna(latest) and target_ts < latest:
        raise UpdateRejected(
            f"Target {target} is before history latest {latest.date()}; refusing to roll the published session backward."
        )

    price_column = "trade_price" if "trade_price" in quotes.columns else "close"
    incoming = quotes.copy()
    incoming["code"] = incoming["code"].astype(str).str.zfill(6)
    incoming[price_column] = pd.to_numeric(incoming[price_column], errors="coerce")
    incoming = incoming.dropna(subset=[price_column]).drop_duplicates("code", keep="last")

    prior = history[history["date"] < target_ts].sort_values(["code", "date"])
    last = prior.groupby("code").tail(1).set_index("code")
    old_target = history[history["date"] == target_ts].copy()
    old_close = {
        code: float(price)
        for code, price in zip(old_target["code"], pd.to_numeric(old_target["close"], errors="coerce"))
        if pd.notna(price)
    }
    new_closes: dict[str, float] = {}
    factors: dict[str, float] = {}
    trade_prices: dict[str, float] = {}
    quote_names = {}
    if "name" in incoming.columns:
        quote_names = dict(zip(incoming["code"], incoming["name"]))
    for row in incoming.itertuples(index=False):
        code = row.code
        trade = float(getattr(row, price_column))
        previous = float(last.at[code, "close"]) if code in last.index and pd.notna(last.at[code, "close"]) else None
        carried = None
        if code in last.index and "close_adjust_factor" in last.columns and pd.notna(last.at[code, "close_adjust_factor"]):
            carried = float(last.at[code, "close_adjust_factor"])
        factor = adjustment_factor(trade, previous, carried)
        new_closes[code] = trade * factor
        factors[code] = factor
        trade_prices[code] = trade

    unchanged = bool(old_close) and all(abs(new_closes[code] - old_close.get(code, -1)) <= 1e-4 for code in new_closes)
    mode = "refreshed" if not old_target.empty else "appended"
    info = {"mode": "unchanged" if unchanged else mode, "etf_count": len(new_closes), "rows": int(len(history))}
    if unchanged:
        info["rows"] = int(len(history))
        return original if original.endswith("\n") else original + "\n", info

    def lookup_name(code: str) -> str:
        existing = old_target[old_target["code"] == code]
        if not existing.empty and isinstance(existing.iloc[0]["name"], str):
            return existing.iloc[0]["name"]
        if code in last.index and isinstance(last.at[code, "name"], str):
            return str(last.at[code, "name"])
        if code in names and isinstance(names[code], str):
            return names[code]
        return str(quote_names.get(code, code))

    additions = []
    for code, close in new_closes.items():
        previous = float(last.at[code, "close"]) if code in last.index and pd.notna(last.at[code, "close"]) else None
        change = None if not previous else (close / previous - 1) * 100
        additions.append(
            {
                "date": target_ts,
                "code": code,
                "name": lookup_name(code),
                "close": close,
                "change_pct": change,
                "trade_close": trade_prices[code],
                "close_adjust_factor": factors[code],
            }
        )
    if not old_target.empty:
        for row in old_target.itertuples(index=False):
            if row.code in new_closes:
                continue
            additions.append(
                {
                    "date": target_ts,
                    "code": row.code,
                    "name": row.name,
                    "close": float(row.close),
                    "change_pct": None if pd.isna(row.change_pct) else float(row.change_pct),
                    "trade_close": None if pd.isna(row.trade_close) else float(row.trade_close),
                    "close_adjust_factor": None if pd.isna(row.close_adjust_factor) else float(row.close_adjust_factor),
                }
            )
    extra = pd.DataFrame(additions)
    base = history[history["date"] != target_ts][["date", "code", "name", "close"]].copy()
    combined = pd.concat([base, extra[["date", "code", "name", "close"]]], ignore_index=True)
    combined["close"] = pd.to_numeric(combined["close"], errors="coerce")
    combined = combined.dropna(subset=["close"]).sort_values(["code", "date"])
    for window in RET_WINDOWS:
        combined[f"ret{window}"] = combined.groupby("code")["close"].pct_change(window) * 100
    for window in RPS_WINDOWS:
        combined[f"rps{window}"] = combined.groupby("date")[f"ret{window}"].rank(pct=True) * 100
    fresh = combined[combined["date"] == target_ts].drop_duplicates("code", keep="last").set_index("code")
    meta = extra.drop_duplicates("code", keep="last").set_index("code")

    header = original.splitlines()[0]
    columns = next(csv.reader([header]))
    formatted: dict[str, str] = {}
    for code, row in fresh.iterrows():
        payload = {column: "" for column in columns}
        payload["date"] = target
        payload["code"] = code
        payload["name"] = meta.at[code, "name"]
        payload["close"] = _blank(row["close"])
        payload["change_pct"] = _blank(meta.at[code, "change_pct"])
        payload["trade_close"] = _blank(meta.at[code, "trade_close"])
        payload["close_adjust_factor"] = _blank(meta.at[code, "close_adjust_factor"])
        for window in RET_WINDOWS:
            payload[f"ret{window}"] = _blank(row.get(f"ret{window}"))
        for window in RPS_WINDOWS:
            payload[f"rps{window}"] = _blank(row.get(f"rps{window}"))
        formatted[code] = _csv_row(columns, payload)

    lines = original.splitlines()
    output = [header]
    seen: set[str] = set()
    date_index = columns.index("date")
    code_index = columns.index("code")
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = next(csv.reader([line]))
        if parts[date_index] != target:
            output.append(line)
            continue
        code = parts[code_index].zfill(6)
        seen.add(code)
        output.append(formatted.get(code, line))
    for code in sorted(formatted):
        if code not in seen:
            output.append(formatted[code])
    newline = "\r\n" if "\r\n" in original else "\n"
    text = newline.join(output) + newline
    info["rows"] = text.count(newline) - 1
    info["etf_count"] = len(fresh)
    return text, info


def write_marker(status: str, target: str, detail: str, etf_count: int, rows: int) -> None:
    marker = {
        "status": status,
        "date": target,
        "detail": detail,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "etfCount": etf_count,
        "quoteDate": target,
        "historyRows": rows,
    }
    (AUTOMATION / "last_run.json").write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def publish(site: Path, history_text: str, target: str, detail: str, etf_count: int, rows: int, status: str) -> None:
    HISTORY_CSV.write_text(history_text, encoding="utf-8")
    for name in ("index.html", "payload.json", "strategy_backtest.html"):
        source = site / name
        if source.exists():
            shutil.copy2(source, ROOT / name)
    copy_tree_contents(site / "data", ROOT / "data")
    copy_tree_contents(site / "reports", ROOT / "reports")
    write_marker(status, target, detail, etf_count, rows)


def build_outputs(history_text: str, target: str, work: Path, skip_site: bool) -> None:
    history_path = work / "history.csv"
    history_path.write_text(history_text, encoding="utf-8")
    site = work / "site"
    site.mkdir(parents=True, exist_ok=True)
    run(
        [
            str(SCRIPTS / "backtest_rps_rule_matrix.py"),
            "--history-csv",
            str(history_path),
            "--date",
            target,
            "--out",
            str(site / "strategy_backtest.html"),
        ]
    )
    if skip_site:
        return
    run(
        [
            str(SCRIPTS / "rebuild_trend_pages.py"),
            "--history-csv",
            str(history_path),
            "--latest-date",
            target,
            "--today",
            target,
            "--dates",
            target,
            "--out",
            str(site),
        ]
    )


def execute(args: argparse.Namespace) -> None:
    target = parse_iso_date(args.date) if args.date else scheduled_target_date()
    if args.quotes_csv and not args.dry_run:
        raise UpdateRejected("--quotes-csv is only available with --dry-run")
    if args.skip_site and not args.dry_run:
        raise UpdateRejected("--skip-site is only available with --dry-run")
    quotes = load_quotes_csv(Path(args.quotes_csv)) if args.quotes_csv else fetch_quotes(args.batch_size, args.pause)
    matched = accept_quotes(quotes, target, args.min_rows)
    original = HISTORY_CSV.read_text(encoding="utf-8")
    universe = load_universe()
    names = dict(zip(universe["code"], universe.get("name_from_chart", universe["code"])))
    updated, info = prepare_history_text(original, matched, target, names)
    assert_history_preserved(original, updated, target, args.min_rows)
    with tempfile.TemporaryDirectory(prefix="etf-rps-") as temporary:
        work = Path(temporary)
        build_outputs(updated, target, work, args.skip_site)
        strategy = (work / "site" / "strategy_backtest.html").read_text(encoding="utf-8")
        if target not in strategy:
            raise UpdateRejected(f"strategy_backtest.html does not mention {target}")
        if not args.skip_site:
            index_html = (work / "site" / "index.html").read_text(encoding="utf-8")
            report_html = (work / "site" / "reports" / f"{target}.html").read_text(encoding="utf-8")
            if target not in index_html or target not in report_html:
                raise UpdateRejected(f"Daily report output does not mention {target}")
        status = "refreshed" if info["mode"] in {"refreshed", "unchanged"} else "updated"
        detail = f"{status} {target} from {info['etf_count']} ETF quotes; history {info['mode']}"
        print(
            f"ETF update {status}: date={target} etfs={info['etf_count']} "
            f"history={info['mode']} rows={date_counts(updated).get(target)}"
        )
        if args.dry_run:
            print("Dry run only; repository files were not changed.")
            return
        publish(work / "site", updated, target, detail, info["etf_count"], info["rows"], status)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch latest ETF quotes and rebuild the static RPS site.")
    parser.add_argument("--date", default="", help="Target trade date, YYYY-MM-DD. Defaults to the Beijing session.")
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--pause", type=float, default=0.6)
    parser.add_argument("--min-rows", type=int, default=160)
    parser.add_argument("--dry-run", action="store_true", help="Build in a temp directory and do not modify the repository.")
    parser.add_argument("--quotes-csv", default="", help="Offline quote file. Only valid with --dry-run.")
    parser.add_argument("--skip-site", action="store_true", help="Skip the daily report builder. Only valid with --dry-run.")
    args = parser.parse_args()
    try:
        execute(args)
    except UpdateRejected as exc:
        print(f"ETF update failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
