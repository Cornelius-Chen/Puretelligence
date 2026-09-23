"""Read-only, content-addressed Q1 market snapshots for strategy research.

The facade returns neutral dictionaries so Q1 keeps data ownership while Q2
remains responsible for strategy and execution semantics. It never writes to
the warehouse and never treats fixtures as admitted market data.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterable, Mapping
from zipfile import ZipFile


SUPPORTED_FREQUENCIES = {"1d", "1m", "5m", "15m", "30m", "60m"}
MINUTE_FREQUENCIES = SUPPORTED_FREQUENCIES - {"1d"}


def _repo_root() -> Path:
    for candidate in (Path.cwd(), *Path.cwd().parents, Path(__file__).resolve(), *Path(__file__).resolve().parents):
        if (candidate / "AGENTS.md").exists() and (candidate / "CCOS").exists():
            return candidate
    raise RuntimeError("Unable to locate repository root")


def _storage_roots(repo_root: Path) -> tuple[Path, Path, Path]:
    control_dir = repo_root / "CCOS" / "00_master_control"
    if str(control_dir) not in sys.path:
        sys.path.insert(0, str(control_dir))
    from quant_warehouse_roots_v1 import Q1_RAW_ROOT, Q1_REFERENCE_ROOT, Q1_STORAGE_ROOT  # type: ignore

    return Q1_STORAGE_ROOT, Q1_RAW_ROOT, Q1_REFERENCE_ROOT


def _date(value: str) -> date:
    text = value.strip().replace("/", "-")
    if re.fullmatch(r"\d{8}", text):
        return datetime.strptime(text, "%Y%m%d").date()
    return date.fromisoformat(text)


def _canonical_symbol(value: str, *, dataset_kind: str = "stock") -> str:
    text = value.strip().upper()
    if not text:
        raise ValueError("universe_contains_empty_symbol")
    if re.fullmatch(r"(SH|SZ|BJ)\d{6}", text):
        text = f"{text[2:]}.{text[:2]}"
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", text):
        return text
    if re.fullmatch(r"\d{6}", text):
        if dataset_kind == "index":
            raise ValueError(f"index_symbol_requires_exchange:{value}")
        exchange = "SH" if text.startswith(("5", "6")) else "SZ" if text.startswith(("0", "3")) else "BJ"
        return f"{text}.{exchange}"
    raise ValueError(f"unsupported_symbol_format:{value}")


def _member_name(symbol: str, dataset: str) -> str:
    code, exchange = symbol.split(".")
    if dataset == "index_1min":
        return f"{code}.csv"
    return f"{exchange.lower()}{code}.csv"


def _archive_trade_date(path: Path) -> date | None:
    match = re.search(r"(?<!\d)(20\d{6})(?!\d)", path.name)
    return datetime.strptime(match.group(1), "%Y%m%d").date() if match else None


def _minute_archive_roots(raw_root: Path) -> dict[str, Path]:
    return {
        "stock_hs_1min": raw_root / "intraday_a_share_1min_monthly" / "user_supplied_stock_hs",
        "bse_1min": raw_root / "intraday_bse_1min_monthly" / "user_supplied_bse",
        "index_1min": raw_root / "minute_bars" / "user_supplied_index_1min_daily",
    }


def _dataset_for_symbol(symbol: str, *, dataset_kind: str) -> str:
    if dataset_kind == "index":
        return "index_1min"
    return "bse_1min" if symbol.endswith(".BJ") else "stock_hs_1min"


def _float(row: Mapping[str, str], key: str, default: float = 0.0) -> float:
    text = str(row.get(key, "")).strip().replace(",", "")
    try:
        return float(text) if text else default
    except ValueError:
        return default


def _reference_rows(reference_root: Path, dataset: str, trade_date: date) -> list[dict[str, str]]:
    stamp = trade_date.strftime("%Y%m%d")
    roots = {
        "stk_limit": reference_root / "stk_limit",
        "suspend_d": reference_root / "suspend_d",
    }
    base = roots[dataset]
    if not base.is_dir():
        return []
    candidates = sorted(
        (path for path in base.rglob(f"*{stamp}*.csv") if path.is_file()),
        key=lambda path: (0 if "backfill_v1" in path.as_posix() else 1, str(path)),
    )
    if not candidates:
        return []
    with candidates[0].open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _daily_references(reference_root: Path, trade_date: date) -> tuple[dict[str, tuple[float, float]], set[str]]:
    limits: dict[str, tuple[float, float]] = {}
    for row in _reference_rows(reference_root, "stk_limit", trade_date):
        try:
            symbol = _canonical_symbol(str(row.get("ts_code", "")))
            limits[symbol] = (_float(row, "up_limit"), _float(row, "down_limit"))
        except ValueError:
            continue
    suspended: set[str] = set()
    for row in _reference_rows(reference_root, "suspend_d", trade_date):
        try:
            suspended.add(_canonical_symbol(str(row.get("ts_code", ""))))
        except ValueError:
            continue
    return limits, suspended


def _canonical_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def strategy_center_window_readiness(
    strategy_snapshot: Mapping[str, Any],
    benchmark_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove one immutable snapshot window without changing global freshness claims."""

    snapshots = (strategy_snapshot, benchmark_snapshot)
    if any(row.get("schema_version") != "strategy_center_market_snapshot_v1" for row in snapshots):
        raise ValueError("window_readiness_snapshot_schema_mismatch")
    if any(row.get("owner_domain") != "Q1_market_data_foundation" for row in snapshots):
        raise ValueError("window_readiness_snapshot_owner_mismatch")
    frequency = str(strategy_snapshot.get("requested_frequency", ""))
    if frequency != str(benchmark_snapshot.get("requested_frequency", "")):
        raise ValueError("window_readiness_frequency_mismatch")
    window = dict(strategy_snapshot.get("data_window", {}))
    if window != dict(benchmark_snapshot.get("data_window", {})):
        raise ValueError("window_readiness_data_window_mismatch")
    blockers: list[str] = []
    for role, snapshot in (("strategy", strategy_snapshot), ("benchmark", benchmark_snapshot)):
        raw_events = snapshot.get("events", [])
        if not isinstance(raw_events, list):
            blockers.append(f"{role}_snapshot_events_not_a_list")
            continue
        if int(snapshot.get("event_count", -1)) != len(raw_events):
            blockers.append(f"{role}_snapshot_event_count_mismatch")
        if _canonical_hash(raw_events) != snapshot.get("events_hash"):
            blockers.append(f"{role}_snapshot_events_hash_mismatch")
    strategy_dates = set(str(value) for value in strategy_snapshot.get("available_dates", []))
    benchmark_dates = set(str(value) for value in benchmark_snapshot.get("available_dates", []))
    if not strategy_dates:
        blockers.append("strategy_snapshot_has_no_trade_dates")
    if strategy_dates != benchmark_dates:
        blockers.append("strategy_and_benchmark_trade_dates_differ")
    session_reports: list[dict[str, Any]] = []
    if frequency in MINUTE_FREQUENCIES:
        for role, snapshot in (("strategy", strategy_snapshot), ("benchmark", benchmark_snapshot)):
            universe = tuple(str(value) for value in snapshot.get("universe", ()))
            events = [dict(row) for row in snapshot.get("events", []) if isinstance(row, Mapping)]
            for trade_date in sorted(strategy_dates | benchmark_dates):
                for symbol in universe:
                    rows = [
                        row for row in events
                        if str(row.get("symbol")) == symbol and str(row.get("timestamp", ""))[:10] == trade_date
                    ]
                    timestamps = sorted({str(row.get("timestamp", "")) for row in rows})
                    times = [value[11:16] for value in timestamps if len(value) >= 16]
                    explicitly_suspended = bool(rows) and all(bool(row.get("is_suspended")) for row in rows)
                    row_blockers: list[str] = []
                    if not rows:
                        row_blockers.append("symbol_session_missing_without_status_marker")
                    elif not explicitly_suspended:
                        if len(timestamps) < 228:
                            row_blockers.append("one_minute_density_below_95_percent")
                        if not times or min(times) > "10:30":
                            row_blockers.append("opening_session_not_covered")
                        if not any("11:20" <= value <= "11:30" for value in times):
                            row_blockers.append("morning_close_not_covered")
                        if not any("13:01" <= value <= "14:10" for value in times):
                            row_blockers.append("afternoon_session_not_covered")
                        if not times or max(times) < "14:50":
                            row_blockers.append("market_close_not_covered")
                    if row_blockers:
                        blockers.append(f"{role}:{trade_date}:{symbol}:{','.join(row_blockers)}")
                    session_reports.append({
                        "role": role,
                        "trade_date": trade_date,
                        "symbol": symbol,
                        "state": "explicitly_suspended" if explicitly_suspended else "trading",
                        "unique_timestamp_count": len(timestamps),
                        "complete": not row_blockers,
                        "blockers": row_blockers,
                    })
    proof: dict[str, Any] = {
        "schema_version": "strategy_center_window_readiness_v1",
        "owner_domain": "Q1_market_data_foundation",
        "frequency": frequency,
        "data_window": window,
        "strategy_snapshot_hash": str(strategy_snapshot.get("snapshot_hash", "")),
        "benchmark_snapshot_hash": str(benchmark_snapshot.get("snapshot_hash", "")),
        "available_dates": sorted(strategy_dates & benchmark_dates),
        "trade_date_count": len(strategy_dates & benchmark_dates),
        "session_reports": session_reports,
        "ready": not blockers,
        "state": "window_ready" if not blockers else "window_incomplete",
        "blockers": blockers,
        "global_frequency_readiness_unchanged": True,
        "research_only": True,
    }
    proof["content_hash"] = _canonical_hash(proof)
    return proof


def _minute_snapshot(
    *,
    storage_root: Path,
    raw_root: Path,
    reference_root: Path,
    frequency: str,
    symbols: tuple[str, ...],
    start: date,
    end: date,
    dataset_kind: str,
    max_events: int,
) -> dict[str, Any]:
    requested: dict[str, tuple[str, ...]] = {}
    for dataset in _minute_archive_roots(raw_root):
        selected = tuple(symbol for symbol in symbols if _dataset_for_symbol(symbol, dataset_kind=dataset_kind) == dataset)
        if selected:
            requested[dataset] = selected
    events: list[dict[str, Any]] = []
    source_members: list[dict[str, Any]] = []
    found_symbols: set[str] = set()
    available_dates: set[str] = set()
    for dataset, selected_symbols in sorted(requested.items()):
        base = _minute_archive_roots(raw_root)[dataset]
        archives = [] if not base.is_dir() else sorted(base.rglob("*.zip"))
        for archive in archives:
            trade_date = _archive_trade_date(archive)
            if trade_date is None or trade_date < start or trade_date > end:
                continue
            limits, suspended = _daily_references(reference_root, trade_date)
            available_dates.add(trade_date.isoformat())
            with ZipFile(archive) as package:
                names = set(package.namelist())
                for symbol in selected_symbols:
                    member = _member_name(symbol, dataset)
                    if member not in names:
                        continue
                    raw = package.read(member)
                    info = package.getinfo(member)
                    source_members.append(
                        {
                            "archive": archive.relative_to(storage_root).as_posix(),
                            "member": member,
                            "crc32": f"{info.CRC:08x}",
                            "size": info.file_size,
                            "sha256": hashlib.sha256(raw).hexdigest(),
                        }
                    )
                    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
                    for sequence, row in enumerate(reader):
                        timestamp = datetime.strptime(row["时间"].strip(), "%Y/%m/%d %H:%M")
                        if timestamp.date() != trade_date:
                            raise ValueError(f"archive_member_trade_date_mismatch:{archive.name}:{member}")
                        limit_up, limit_down = limits.get(symbol, (0.0, 0.0))
                        name = str(row.get("名称", "")).strip().upper()
                        original_volume = _float(row, "成交量")
                        events.append(
                            {
                                "timestamp": timestamp.isoformat(),
                                "symbol": symbol,
                                "frequency": "1m",
                                "event_kind": "bar",
                                "open": _float(row, "开盘价"),
                                "high": _float(row, "最高价"),
                                "low": _float(row, "最低价"),
                                "close": _float(row, "收盘价"),
                                "volume": original_volume * (100.0 if dataset != "index_1min" else 1.0),
                                "turnover": _float(row, "成交额"),
                                "limit_up": limit_up or None,
                                "limit_down": limit_down or None,
                                "is_suspended": symbol in suspended,
                                "is_listed": True,
                                "is_st": name.startswith(("ST", "*ST")),
                                "session_phase": "continuous",
                                "source_sequence": sequence,
                            }
                        )
                        if len(events) > max_events:
                            raise ValueError(f"market_snapshot_event_limit_exceeded:{max_events}")
                    found_symbols.add(symbol)
    missing_symbols = sorted(set(symbols) - found_symbols)
    if missing_symbols:
        raise ValueError(f"market_snapshot_symbols_not_found:{','.join(missing_symbols)}")
    if not events:
        raise ValueError("market_snapshot_contains_no_events")
    events.sort(key=lambda row: (row["timestamp"], row["source_sequence"], row["symbol"]))
    source_members.sort(key=lambda row: (row["archive"], row["member"]))
    snapshot_identity = {
        "schema_version": "strategy_center_market_snapshot_v1",
        "requested_frequency": frequency,
        "base_frequency": "1m",
        "dataset_kind": dataset_kind,
        "universe": symbols,
        "data_window": {"start": start.isoformat(), "end": end.isoformat()},
        "source_members": source_members,
        "events_hash": _canonical_hash(events),
    }
    return {
        **snapshot_identity,
        "owner_domain": "Q1_market_data_foundation",
        "snapshot_hash": _canonical_hash(snapshot_identity),
        "events": events,
        "event_count": len(events),
        "observation_count": max(0, len({event["timestamp"] for event in events}) - 1),
        "available_dates": sorted(available_dates),
        "timezone": "Asia/Shanghai",
        "volume_normalization": "stock_and_bse_source_lots_multiplied_by_100_to_shares",
        "research_only": True,
    }


def _daily_snapshot(
    *,
    storage_root: Path,
    raw_root: Path,
    reference_root: Path,
    frequency: str,
    symbols: tuple[str, ...],
    start: date,
    end: date,
    max_events: int,
) -> dict[str, Any]:
    by_date = raw_root / "daily_bars" / "tushare_backfill_v1" / "by_date"
    if not by_date.is_dir():
        raise ValueError("admitted_daily_by_date_dataset_missing")
    wanted = set(symbols)
    found: set[str] = set()
    events: list[dict[str, Any]] = []
    source_files: list[dict[str, Any]] = []
    available_dates: set[str] = set()
    candidates: list[tuple[date, Path]] = []
    for path in by_date.glob("tushare_daily_*.csv"):
        trade_date = _archive_trade_date(path)
        if trade_date is not None and start <= trade_date <= end:
            candidates.append((trade_date, path))
    for trade_date, path in sorted(candidates):
        raw = path.read_bytes()
        limits, suspended = _daily_references(reference_root, trade_date)
        available_dates.add(trade_date.isoformat())
        source_files.append(
            {
                "path": path.relative_to(storage_root).as_posix(),
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        for row in reader:
            try:
                symbol = _canonical_symbol(str(row.get("ts_code", "")))
            except ValueError:
                continue
            if symbol not in wanted:
                continue
            limit_up, limit_down = limits.get(symbol, (0.0, 0.0))
            events.append(
                {
                    "timestamp": datetime.combine(trade_date, time(15, 0)).isoformat(),
                    "symbol": symbol,
                    "frequency": "1d",
                    "event_kind": "bar",
                    "open": _float(row, "open"),
                    "high": _float(row, "high"),
                    "low": _float(row, "low"),
                    "close": _float(row, "close"),
                    "volume": _float(row, "vol") * 100.0,
                    "turnover": _float(row, "amount") * 1_000.0,
                    "pre_close": _float(row, "pre_close"),
                    "limit_up": limit_up or None,
                    "limit_down": limit_down or None,
                    "is_suspended": symbol in suspended,
                    "is_listed": True,
                    "is_st": False,
                    "session_phase": "close",
                    "source_sequence": len(events),
                }
            )
            found.add(symbol)
            if len(events) > max_events:
                raise ValueError(f"market_snapshot_event_limit_exceeded:{max_events}")
    missing = sorted(wanted - found)
    if missing:
        raise ValueError(f"market_snapshot_symbols_not_found:{','.join(missing)}")
    events.sort(key=lambda row: (row["timestamp"], row["symbol"]))
    identity = {
        "schema_version": "strategy_center_market_snapshot_v1",
        "requested_frequency": frequency,
        "base_frequency": "1d",
        "dataset_kind": "stock",
        "universe": symbols,
        "data_window": {"start": start.isoformat(), "end": end.isoformat()},
        "source_files": source_files,
        "events_hash": _canonical_hash(events),
    }
    return {
        **identity,
        "owner_domain": "Q1_market_data_foundation",
        "snapshot_hash": _canonical_hash(identity),
        "events": events,
        "event_count": len(events),
        "observation_count": max(0, len({event["timestamp"] for event in events}) - 1),
        "available_dates": sorted(available_dates),
        "timezone": "Asia/Shanghai",
        "volume_normalization": "tushare_daily_vol_lots_multiplied_by_100_to_shares_and_amount_thousands_multiplied_by_1000",
        "field_completeness": {"is_st": "not_resolved_false_placeholder"},
        "research_only": True,
    }


def _daily_index_snapshot(
    *,
    storage_root: Path,
    raw_root: Path,
    frequency: str,
    symbols: tuple[str, ...],
    start: date,
    end: date,
    max_events: int,
) -> dict[str, Any]:
    """Expose admitted daily index rows with deterministic source precedence."""

    base = raw_root / "index_daily_bars"
    by_code = {symbol.split(".")[0]: symbol for symbol in symbols}
    selected: dict[tuple[date, str], dict[str, Any]] = {}
    used_sources: dict[Path, bytes] = {}
    source_members: list[dict[str, Any]] = []
    candidates = sorted(
        base.glob("*.csv") if base.is_dir() else (),
        key=lambda path: (0 if path.name.startswith("tushare_") else 1, path.name),
    )
    for path in candidates:
        raw = path.read_bytes()
        contributed = False
        for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
            code = str(row.get("symbol", "")).strip().zfill(6)
            symbol = by_code.get(code)
            if symbol is None:
                continue
            try:
                trade_date = _date(str(row.get("trade_date", "")))
            except ValueError:
                continue
            key = (trade_date, symbol)
            if trade_date < start or trade_date > end or key in selected:
                continue
            selected[key] = {
                "timestamp": datetime.combine(trade_date, time(15, 0)).isoformat(),
                "symbol": symbol,
                "frequency": "1d",
                "event_kind": "bar",
                "open": _float(row, "open"),
                "high": _float(row, "high"),
                "low": _float(row, "low"),
                "close": _float(row, "close"),
                "volume": _float(row, "volume"),
                "turnover": _float(row, "turnover"),
                "pre_close": _float(row, "pre_close"),
                "is_suspended": False,
                "is_listed": True,
                "is_st": False,
                "session_phase": "close",
                "source_sequence": 0,
            }
            contributed = True
            if len(selected) > max_events:
                raise ValueError(f"market_snapshot_event_limit_exceeded:{max_events}")
        if contributed:
            used_sources[path] = raw
    try:
        minute = _minute_snapshot(
            storage_root=storage_root,
            raw_root=raw_root,
            reference_root=storage_root / "reference",
            frequency=frequency,
            symbols=symbols,
            start=start,
            end=end,
            dataset_kind="index",
            max_events=max_events,
        )
    except ValueError:
        minute = None
    if minute is not None:
        grouped: dict[tuple[date, str], list[dict[str, Any]]] = {}
        for event in minute["events"]:
            trade_date = datetime.fromisoformat(str(event["timestamp"])).date()
            grouped.setdefault((trade_date, str(event["symbol"])), []).append(event)
        for key, rows in grouped.items():
            if key in selected:
                continue
            rows.sort(key=lambda row: (row["timestamp"], row["source_sequence"]))
            selected[key] = {
                "timestamp": datetime.combine(key[0], time(15, 0)).isoformat(),
                "symbol": key[1],
                "frequency": "1d",
                "event_kind": "bar",
                "open": rows[0]["open"],
                "high": max(float(row["high"]) for row in rows),
                "low": min(float(row["low"]) for row in rows),
                "close": rows[-1]["close"],
                "volume": sum(float(row.get("volume", 0.0)) for row in rows),
                "turnover": sum(float(row.get("turnover", 0.0)) for row in rows),
                "pre_close": rows[0].get("pre_close"),
                "is_suspended": all(bool(row.get("is_suspended")) for row in rows),
                "is_listed": True,
                "is_st": False,
                "session_phase": "close",
                "source_sequence": 0,
            }
        source_members = list(minute.get("source_members", []))
    found = {symbol for _, symbol in selected}
    missing = sorted(set(symbols) - found)
    if missing:
        raise ValueError(f"market_snapshot_symbols_not_found:{','.join(missing)}")
    events = [selected[key] for key in sorted(selected)]
    for sequence, event in enumerate(events):
        event["source_sequence"] = sequence
    source_files = [
        {
            "path": path.relative_to(storage_root).as_posix(),
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        for path, raw in sorted(used_sources.items(), key=lambda item: str(item[0]))
    ]
    identity = {
        "schema_version": "strategy_center_market_snapshot_v1",
        "requested_frequency": frequency,
        "base_frequency": "1d",
        "dataset_kind": "index",
        "universe": symbols,
        "data_window": {"start": start.isoformat(), "end": end.isoformat()},
        "source_files": source_files,
        "source_members": source_members,
        "events_hash": _canonical_hash(events),
    }
    return {
        **identity,
        "owner_domain": "Q1_market_data_foundation",
        "snapshot_hash": _canonical_hash(identity),
        "events": events,
        "event_count": len(events),
        "observation_count": max(0, len({event["timestamp"] for event in events}) - 1),
        "available_dates": sorted({event["timestamp"][:10] for event in events}),
        "timezone": "Asia/Shanghai",
        "volume_normalization": "daily_index_native_units_with_missing_dates_aggregated_from_admitted_index_1m",
        "research_only": True,
    }


def get_strategy_center_market_snapshot(
    *,
    frequency: str,
    universe: Iterable[str],
    start: str,
    end: str,
    dataset_kind: str = "stock",
    repo_root: Path | None = None,
    storage_root: Path | None = None,
    max_events: int = 2_000_000,
) -> dict[str, Any]:
    """Return a deterministic read-only data snapshot for an explicit universe."""

    if frequency == "tick_l1":
        raise ValueError("authorized_tick_snapshot_not_available")
    if frequency not in SUPPORTED_FREQUENCIES:
        raise ValueError(f"unsupported_snapshot_frequency:{frequency}")
    if dataset_kind not in {"stock", "index"}:
        raise ValueError(f"unsupported_snapshot_dataset_kind:{dataset_kind}")
    start_date, end_date = _date(start), _date(end)
    if start_date > end_date:
        raise ValueError("market_snapshot_start_after_end")
    symbols = tuple(sorted({_canonical_symbol(value, dataset_kind=dataset_kind) for value in universe}))
    if not symbols:
        raise ValueError("market_snapshot_requires_explicit_universe")
    root = (repo_root or _repo_root()).resolve()
    if storage_root is None:
        q1_storage, raw_root, reference_root = _storage_roots(root)
    else:
        q1_storage = storage_root.resolve()
        raw_root = q1_storage / "raw"
        reference_root = q1_storage / "reference"
    if frequency == "1d":
        if dataset_kind == "index":
            return _daily_index_snapshot(
                storage_root=q1_storage,
                raw_root=raw_root,
                frequency=frequency,
                symbols=symbols,
                start=start_date,
                end=end_date,
                max_events=max_events,
            )
        return _daily_snapshot(
            storage_root=q1_storage,
            raw_root=raw_root,
            reference_root=reference_root,
            frequency=frequency,
            symbols=symbols,
            start=start_date,
            end=end_date,
            max_events=max_events,
        )
    return _minute_snapshot(
        storage_root=q1_storage,
        raw_root=raw_root,
        reference_root=reference_root,
        frequency=frequency,
        symbols=symbols,
        start=start_date,
        end=end_date,
        dataset_kind=dataset_kind,
        max_events=max_events,
    )


def get_strategy_center_stock_universe(
    *,
    as_of: str,
    repo_root: Path | None = None,
    storage_root: Path | None = None,
    main_board_only: bool = False,
    minimum_listed_days: int = 0,
) -> dict[str, Any]:
    """Resolve an explicit point-in-time research universe from Q1 identity data.

    This only returns symbols and a content hash.  Strategy ranking and all
    trading semantics remain outside Q1.
    """

    cutoff = _date(as_of)
    root = (repo_root or _repo_root()).resolve()
    if storage_root is None:
        q1_storage, _, reference_root = _storage_roots(root)
    else:
        q1_storage = storage_root.resolve()
        reference_root = q1_storage / "reference"
    source = reference_root / "security_master" / "tushare_stock_basic_backfill_v1.csv"
    if not source.is_file():
        raise ValueError("strategy_center_security_master_missing")
    raw = source.read_bytes()
    symbols: list[str] = []
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
        try:
            symbol = _canonical_symbol(str(row.get("ts_code", "")))
            listed = _date(str(row.get("list_date", "")))
        except ValueError:
            continue
        delist_text = str(row.get("delist_date", "")).strip()
        if listed > cutoff or (delist_text and _date(delist_text) <= cutoff):
            continue
        if (cutoff - listed).days < max(0, int(minimum_listed_days)):
            continue
        if symbol.endswith(".BJ"):
            continue
        code = symbol.split(".")[0]
        if main_board_only and not code.startswith(("000", "001", "002", "003", "600", "601", "603", "605")):
            continue
        symbols.append(symbol)
    symbols = sorted(set(symbols))
    identity = {
        "schema_version": "strategy_center_stock_universe_v1",
        "owner_domain": "Q1_market_data_foundation",
        "as_of": cutoff.isoformat(),
        "main_board_only": bool(main_board_only),
        "minimum_listed_days": max(0, int(minimum_listed_days)),
        "source_ref": source.relative_to(q1_storage).as_posix(),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "symbols": symbols,
    }
    return {**identity, "symbol_count": len(symbols), "content_hash": _canonical_hash(identity)}


__all__ = [
    "get_strategy_center_market_snapshot",
    "get_strategy_center_stock_universe",
    "strategy_center_window_readiness",
]
