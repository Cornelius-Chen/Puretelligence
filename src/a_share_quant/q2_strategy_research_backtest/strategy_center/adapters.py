"""Frequency-specific event validation and execution eligibility."""

from __future__ import annotations

from datetime import time
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .event_engine import MarketEvent


class EventAdapter(Protocol):
    frequency_family: str
    def validate(self, event: "MarketEvent") -> tuple[str, ...]: ...
    def executable(self, event: "MarketEvent", *, allow_auction: bool) -> bool: ...


class DailyBarAdapter:
    frequency_family = "daily"

    def validate(self, event: "MarketEvent") -> tuple[str, ...]:
        errors = []
        if event.frequency != "1d" or event.event_kind != "bar":
            errors.append("daily_adapter_requires_daily_bar")
        if event.open is None or event.close is None:
            errors.append("daily_bar_ohlc_missing")
        return tuple(errors)

    def executable(self, event: "MarketEvent", *, allow_auction: bool) -> bool:
        return not event.is_suspended and event.is_listed


class MinuteBarAdapter:
    frequency_family = "minute"
    frequencies = {"1m", "5m", "15m", "30m", "60m"}

    @staticmethod
    def _in_session(value: time) -> bool:
        return time(9, 25) <= value <= time(11, 30) or time(13, 0) <= value <= time(15, 0)

    def validate(self, event: "MarketEvent") -> tuple[str, ...]:
        errors = []
        if event.frequency not in self.frequencies or event.event_kind not in {"bar", "auction"}:
            errors.append("minute_adapter_requires_minute_bar_or_auction")
        if not self._in_session(event.timestamp.time()):
            errors.append("minute_event_outside_a_share_session")
        return tuple(errors)

    def executable(self, event: "MarketEvent", *, allow_auction: bool) -> bool:
        auction = event.event_kind == "auction" or event.session_phase in {"open_auction", "close_auction"}
        return not event.is_suspended and event.is_listed and (allow_auction or not auction)


class TickL1Adapter:
    frequency_family = "tick_l1"

    def validate(self, event: "MarketEvent") -> tuple[str, ...]:
        errors = []
        if event.frequency != "tick_l1" or event.event_kind not in {"tick", "snapshot", "auction"}:
            errors.append("tick_adapter_requires_tick_or_snapshot")
        if event.bid1 is not None and event.ask1 is not None and event.bid1 > event.ask1:
            errors.append("crossed_l1_book")
        if event.source_sequence < 0:
            errors.append("negative_source_sequence")
        return tuple(errors)

    def executable(self, event: "MarketEvent", *, allow_auction: bool) -> bool:
        auction = event.event_kind == "auction" or event.session_phase in {"open_auction", "close_auction"}
        return not event.is_suspended and event.is_listed and (allow_auction or not auction)


def adapter_for(frequency: str) -> EventAdapter:
    if frequency == "1d":
        return DailyBarAdapter()
    if frequency in MinuteBarAdapter.frequencies:
        return MinuteBarAdapter()
    if frequency == "tick_l1":
        return TickL1Adapter()
    raise ValueError(f"unsupported_frequency:{frequency}")


__all__ = ["DailyBarAdapter", "EventAdapter", "MinuteBarAdapter", "TickL1Adapter", "adapter_for"]
