"""Q2 adapter for the versioned, read-only Q1 strategy-center snapshot."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from .contracts import canonical_hash
from .event_engine import MarketEvent, aggregate_minute_bars


def events_from_q1_snapshot(snapshot: Mapping[str, Any], *, requested_frequency: str) -> tuple[MarketEvent, ...]:
    if snapshot.get("schema_version") != "strategy_center_market_snapshot_v1":
        raise ValueError("unsupported_q1_market_snapshot_schema")
    if snapshot.get("owner_domain") != "Q1_market_data_foundation":
        raise ValueError("q1_market_snapshot_owner_mismatch")
    raw_events = snapshot.get("events")
    if not isinstance(raw_events, list) or not raw_events:
        raise ValueError("q1_market_snapshot_has_no_events")
    if canonical_hash(raw_events) != snapshot.get("events_hash"):
        raise ValueError("q1_market_snapshot_events_hash_mismatch")
    if int(snapshot.get("event_count", -1)) != len(raw_events):
        raise ValueError("q1_market_snapshot_event_count_mismatch")
    events: list[MarketEvent] = []
    for row in raw_events:
        values = dict(row)
        values["timestamp"] = datetime.fromisoformat(str(values["timestamp"]))
        events.append(MarketEvent(**values))
    if requested_frequency in {"5m", "15m", "30m", "60m"}:
        events = aggregate_minute_bars(events, int(requested_frequency[:-1]))
    elif requested_frequency not in {"1d", "1m"}:
        raise ValueError(f"unsupported_q1_snapshot_target_frequency:{requested_frequency}")
    return tuple(events)


def snapshot_source_refs(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for row in snapshot.get("source_members", []):
        if isinstance(row, Mapping) and row.get("archive") and row.get("member"):
            refs.append(f"{row['archive']}#{row['member']}")
    for row in snapshot.get("source_files", []):
        if isinstance(row, Mapping) and row.get("path"):
            refs.append(str(row["path"]))
    return tuple(sorted(refs))


__all__ = ["events_from_q1_snapshot", "snapshot_source_refs"]
