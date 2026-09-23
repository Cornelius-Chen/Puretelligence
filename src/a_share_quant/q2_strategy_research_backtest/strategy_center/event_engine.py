"""Deterministic event engine for daily, minute, and L1 trade replay."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Callable, Iterable, Mapping, Sequence

from a_share_quant.q2_strategy_research_backtest.backtest.cost_model import CostModel

from .adapters import adapter_for


@dataclass(frozen=True, slots=True)
class MarketEvent:
    timestamp: datetime
    symbol: str
    frequency: str
    event_kind: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float = 0.0
    turnover: float = 0.0
    last: float | None = None
    bid1: float | None = None
    ask1: float | None = None
    bid1_volume: float | None = None
    ask1_volume: float | None = None
    trade_volume: float | None = None
    pre_close: float | None = None
    limit_up: float | None = None
    limit_down: float | None = None
    is_suspended: bool = False
    is_listed: bool = True
    is_st: bool = False
    session_phase: str = "continuous"
    source_sequence: int = 0

    @property
    def mark_price(self) -> float:
        for value in (self.close, self.last, self.open, self.bid1, self.ask1):
            if value is not None and value > 0:
                return float(value)
        return 0.0


@dataclass(frozen=True, slots=True)
class OrderManagementRule:
    rule_id: str
    action: str
    after_events: int
    price_mode: str | None = None
    price_value: float | None = None
    max_uses: int = 1


@dataclass(frozen=True, slots=True)
class SignalIntent:
    signal_id: str
    signal_at: datetime
    symbol: str
    action: str
    quantity: int
    quantity_mode: str = "fixed"
    priority: int = 0
    order_type: str = "market"
    limit_price: float | None = None
    time_in_force: str = "ioc"
    expires_at: datetime | None = None
    management_rules: tuple[OrderManagementRule, ...] = ()


@dataclass(frozen=True, slots=True)
class OrderCommand:
    command_id: str
    command_at: datetime
    symbol: str
    command_type: str
    target_signal_id: str
    replacement: SignalIntent | None = None


@dataclass(frozen=True, slots=True)
class EventFill:
    signal_id: str
    timestamp: datetime
    symbol: str
    action: str
    requested_quantity: int
    filled_quantity: int
    price: float
    fees: float
    slippage_bps: float
    status: str
    quantity_mode: str = "fixed"
    target_quantity: int | None = None
    restriction_reason: str | None = None
    execution_priority: int = 0


@dataclass(frozen=True, slots=True)
class EventRejection:
    signal_id: str
    timestamp: datetime | None
    symbol: str
    action: str
    reason: str


@dataclass(frozen=True, slots=True)
class EventOrderUpdate:
    signal_id: str
    timestamp: datetime | None
    symbol: str
    status: str
    remaining_quantity: int | None
    reason: str | None = None
    command_id: str | None = None
    queue_ahead: int | None = None


@dataclass(frozen=True, slots=True)
class PortfolioPoint:
    timestamp: datetime
    equity: float
    cash: float
    market_value: float


@dataclass(frozen=True, slots=True)
class UnifiedRunResult:
    fills: tuple[EventFill, ...]
    rejections: tuple[EventRejection, ...]
    equity_curve: tuple[PortfolioPoint, ...]
    ending_positions: dict[str, int]
    initial_cash: float
    ending_cash: float
    order_updates: tuple[EventOrderUpdate, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "fills": [asdict(item) for item in self.fills],
            "rejections": [asdict(item) for item in self.rejections],
            "equity_curve": [asdict(item) for item in self.equity_curve],
            "ending_positions": self.ending_positions,
            "initial_cash": self.initial_cash,
            "ending_cash": self.ending_cash,
            "order_updates": [asdict(item) for item in self.order_updates],
            "order_lifecycle_model": "explicit_and_managed_cancel_replace_v2",
            "order_management_semantics": "ordered_rules_over_cumulative_wait_events_v1",
            "l1_queue_model": "visible_best_queue_conservative_v1",
            "l1_queue_limitations": [
                "L1不能识别逐笔委托撤单与隐藏流动性",
                "仅在限价位处于最佳报价且成交价触及时扣减可见排队量",
                "真实L2逐委托数据接入前不得宣称精确复原交易所队列",
            ],
            "research_only": True,
            "live_order_path": "forbidden",
        }


@dataclass(frozen=True, slots=True)
class EventEngineConfig:
    initial_cash: float = 1_000_000.0
    board_lot: int = 100
    maximum_participation: float = 0.10
    base_slippage_bps: float = 1.0
    impact_bps_at_max_participation: float = 5.0
    enforce_t_plus_one: bool = True
    latency_ms: int = 0
    allow_auction_execution: bool = False
    max_positions: int | None = None


class UnifiedEventEngine:
    """Execute each intent at the next eligible market event for its symbol."""

    def __init__(self, config: EventEngineConfig, cost_model: CostModel | None = None) -> None:
        self.config = config
        self.cost_model = cost_model or CostModel()

    @staticmethod
    def _index(events: Iterable[MarketEvent]) -> tuple[list[MarketEvent], dict[str, list[MarketEvent]]]:
        ordered = sorted(events, key=lambda item: (item.timestamp, item.source_sequence, item.symbol))
        by_symbol: dict[str, list[MarketEvent]] = defaultdict(list)
        for event in ordered:
            by_symbol[event.symbol].append(event)
        return ordered, by_symbol

    def _next_event(self, signal: SignalIntent, events: Sequence[MarketEvent]) -> MarketEvent | None:
        eligible_at = signal.signal_at + timedelta(milliseconds=max(0, self.config.latency_ms))
        return next(
            (
                event for event in events
                if event.timestamp > signal.signal_at
                and event.timestamp >= eligible_at
                and adapter_for(event.frequency).executable(event, allow_auction=self.config.allow_auction_execution)
            ),
            None,
        )

    def _available_quantity(self, event: MarketEvent, action: str) -> int:
        visible = event.ask1_volume if action == "buy" else event.bid1_volume
        base = visible if visible is not None else event.trade_volume if event.trade_volume is not None else event.volume
        return max(0, int(math.floor(float(base or 0.0) * self.config.maximum_participation)))

    def _raw_price(self, event: MarketEvent, action: str) -> float:
        preferred = event.ask1 if action == "buy" else event.bid1
        if preferred is not None and preferred > 0:
            return float(preferred)
        if event.event_kind == "tick" and event.last is not None:
            return float(event.last)
        if event.open is not None:
            return float(event.open)
        return event.mark_price

    @staticmethod
    def _blocked_reason(event: MarketEvent, action: str, raw_price: float) -> str | None:
        if event.is_suspended:
            return "suspended"
        epsilon = 1e-8
        if action == "buy" and event.limit_up is not None and raw_price >= event.limit_up - epsilon:
            return "limit_up_unreliable"
        if action == "sell" and event.limit_down is not None and raw_price <= event.limit_down + epsilon:
            return "limit_down_unreliable"
        if raw_price <= 0:
            return "missing_execution_price"
        return None

    def run(
        self,
        events: Iterable[MarketEvent],
        signals: Iterable[SignalIntent],
        *,
        commands: Iterable[OrderCommand] = (),
        signal_provider: Callable[
            [MarketEvent, Mapping[str, int], Mapping[str, tuple[str, ...]]], Iterable[SignalIntent]
        ] | None = None,
        signal_batch_provider: Callable[
            [Sequence[MarketEvent], Mapping[str, int], Mapping[str, tuple[str, ...]]], Iterable[SignalIntent]
        ] | None = None,
    ) -> UnifiedRunResult:
        if signal_provider is not None and signal_batch_provider is not None:
            raise ValueError("only_one_signal_provider_mode_is_allowed")
        provider_owners = {
            owner for owner in (
                getattr(signal_provider, "__self__", None),
                getattr(signal_batch_provider, "__self__", None),
            ) if owner is not None
        }
        ordered_events, by_symbol = self._index(events)
        for event in ordered_events:
            errors = adapter_for(event.frequency).validate(event)
            if errors:
                raise ValueError(f"invalid_market_event:{event.symbol}:{event.timestamp.isoformat()}:{','.join(errors)}")
        scheduled: dict[tuple[datetime, int, str], list[tuple[SignalIntent, MarketEvent]]] = defaultdict(list)
        scheduled_commands: dict[tuple[datetime, int, str], list[OrderCommand]] = defaultdict(list)
        pending_actions: dict[str, list[str]] = defaultdict(list)
        rejections: list[EventRejection] = []
        order_updates: list[EventOrderUpdate] = []
        known_order_ids: set[str] = set()
        cancelled_order_ids: set[str] = set()
        completed_order_ids: set[str] = set()
        order_actions: dict[str, str] = {}
        order_session_dates: dict[str, date] = {}
        queue_ahead: dict[str, int] = {}
        order_chain_roots: dict[str, str] = {}
        chain_wait_events: dict[str, int] = defaultdict(int)
        management_rule_uses: dict[tuple[str, str], int] = defaultdict(int)
        replacement_serials: dict[str, int] = defaultdict(int)

        def schedule_signal(signal: SignalIntent, *, continuation: bool = False) -> None:
            if not continuation and signal.signal_id in known_order_ids:
                rejections.append(EventRejection(signal.signal_id, None, signal.symbol, signal.action, "duplicate_signal_id"))
                return
            if signal.action not in {"buy", "sell"}:
                rejections.append(EventRejection(signal.signal_id, None, signal.symbol, signal.action, "unsupported_action"))
                return
            if signal.quantity_mode not in {"fixed", "close_position", "target_position"} or (
                signal.action == "buy" and signal.quantity_mode == "close_position"
            ):
                rejections.append(EventRejection(signal.signal_id, None, signal.symbol, signal.action, "unsupported_quantity_mode"))
                return
            if signal.order_type not in {"market", "limit"}:
                rejections.append(EventRejection(signal.signal_id, None, signal.symbol, signal.action, "unsupported_order_type"))
                return
            if signal.order_type == "limit" and (signal.limit_price is None or signal.limit_price <= 0):
                rejections.append(EventRejection(signal.signal_id, None, signal.symbol, signal.action, "invalid_limit_price"))
                return
            if signal.time_in_force not in {"ioc", "day", "gtc"}:
                rejections.append(EventRejection(signal.signal_id, None, signal.symbol, signal.action, "unsupported_time_in_force"))
                return
            if signal.time_in_force == "gtc" and signal.expires_at is None:
                rejections.append(EventRejection(signal.signal_id, None, signal.symbol, signal.action, "gtc_requires_expiry"))
                return
            seen_rule_ids: set[str] = set()
            for rule in signal.management_rules:
                if (
                    not rule.rule_id
                    or rule.rule_id in seen_rule_ids
                    or rule.action not in {"cancel", "replace"}
                    or isinstance(rule.after_events, bool)
                    or not isinstance(rule.after_events, int)
                    or rule.after_events <= 0
                    or isinstance(rule.max_uses, bool)
                    or not isinstance(rule.max_uses, int)
                    or rule.max_uses <= 0
                    or (
                        rule.action == "replace"
                        and (
                            rule.price_mode not in {"best_quote", "fixed", "offset_bps"}
                            or (
                                rule.price_mode in {"fixed", "offset_bps"}
                                and (
                                    isinstance(rule.price_value, bool)
                                    or not isinstance(rule.price_value, (int, float))
                                    or float(rule.price_value) < (0.0 if rule.price_mode == "offset_bps" else 1e-12)
                                )
                            )
                        )
                    )
                ):
                    rejections.append(EventRejection(
                        signal.signal_id, None, signal.symbol, signal.action, "invalid_order_management_rule"
                    ))
                    return
                seen_rule_ids.add(rule.rule_id)
            if signal.management_rules and signal.time_in_force == "ioc":
                rejections.append(EventRejection(
                    signal.signal_id, None, signal.symbol, signal.action, "management_requires_persistent_order"
                ))
                return
            if any(rule.action == "replace" for rule in signal.management_rules) and signal.order_type != "limit":
                rejections.append(EventRejection(
                    signal.signal_id, None, signal.symbol, signal.action, "managed_replace_requires_limit_order"
                ))
                return
            event = self._next_event(signal, by_symbol.get(signal.symbol, ()))
            if event is None:
                if continuation:
                    completed_order_ids.add(signal.signal_id)
                    order_updates.append(EventOrderUpdate(
                        signal.signal_id, signal.signal_at, signal.symbol, "expired", signal.quantity,
                        reason="end_of_data",
                    ))
                else:
                    rejections.append(EventRejection(signal.signal_id, None, signal.symbol, signal.action, "no_future_market_event"))
                return
            if signal.expires_at is not None and event.timestamp > signal.expires_at:
                completed_order_ids.add(signal.signal_id)
                order_updates.append(EventOrderUpdate(
                    signal.signal_id, event.timestamp, signal.symbol, "expired", signal.quantity,
                    reason="explicit_expiry_reached",
                ))
                return
            if continuation and signal.time_in_force == "day" and order_session_dates.get(signal.signal_id) != event.timestamp.date():
                completed_order_ids.add(signal.signal_id)
                order_updates.append(EventOrderUpdate(
                    signal.signal_id, signal.signal_at, signal.symbol, "expired", signal.quantity,
                    reason="day_session_ended",
                ))
                return
            if not continuation:
                known_order_ids.add(signal.signal_id)
                order_actions[signal.signal_id] = signal.action
                root_id = order_chain_roots.setdefault(signal.signal_id, signal.signal_id)
                chain_wait_events.setdefault(root_id, 0)
            scheduled[(event.timestamp, event.source_sequence, event.symbol)].append((signal, event))
            pending_actions[signal.symbol].append(signal.action)
            order_updates.append(EventOrderUpdate(
                signal.signal_id,
                event.timestamp,
                signal.symbol,
                "accepted" if not continuation else "working",
                signal.quantity,
                reason=None if not continuation else "carried_to_next_event",
                queue_ahead=queue_ahead.get(signal.signal_id),
            ))

        for signal in sorted(signals, key=lambda item: (item.signal_at, item.signal_id)):
            schedule_signal(signal)

        for command in sorted(commands, key=lambda item: (item.command_at, item.command_id)):
            if command.command_type not in {"cancel", "replace"}:
                order_updates.append(EventOrderUpdate(
                    command.target_signal_id, None, command.symbol, "command_rejected", None,
                    reason="unsupported_command_type", command_id=command.command_id,
                ))
                continue
            if command.command_type == "replace" and command.replacement is None:
                order_updates.append(EventOrderUpdate(
                    command.target_signal_id, None, command.symbol, "command_rejected", None,
                    reason="replacement_missing", command_id=command.command_id,
                ))
                continue
            eligible_at = command.command_at + timedelta(milliseconds=max(0, self.config.latency_ms))
            command_event = next((
                event for event in by_symbol.get(command.symbol, ())
                if event.timestamp > command.command_at
                and event.timestamp >= eligible_at
                and adapter_for(event.frequency).executable(event, allow_auction=self.config.allow_auction_execution)
            ), None)
            if command_event is None:
                order_updates.append(EventOrderUpdate(
                    command.target_signal_id, None, command.symbol, "command_rejected", None,
                    reason="no_future_market_event", command_id=command.command_id,
                ))
                continue
            scheduled_commands[(command_event.timestamp, command_event.source_sequence, command_event.symbol)].append(command)

        def event_execution_key(event: MarketEvent) -> tuple[int, int, int, str]:
            intents = scheduled.get((event.timestamp, event.source_sequence, event.symbol), ())
            has_sell = any(signal.action == "sell" for signal, _ in intents)
            has_buy = any(signal.action == "buy" for signal, _ in intents)
            priorities = [signal.priority for signal, _ in intents if signal.action == "buy"]
            return (
                0 if has_sell else 1 if has_buy else 2,
                min(priorities, default=0),
                event.source_sequence,
                event.symbol,
            )

        def sort_next_timestamp_group(start: int) -> None:
            if start >= len(ordered_events):
                return
            timestamp = ordered_events[start].timestamp
            end = start + 1
            while end < len(ordered_events) and ordered_events[end].timestamp == timestamp:
                end += 1
            ordered_events[start:end] = sorted(ordered_events[start:end], key=event_execution_key)

        sort_next_timestamp_group(0)

        cash = self.config.initial_cash
        positions: dict[str, int] = {}
        position_lots: dict[str, list[tuple[date, int]]] = defaultdict(list)
        fills: list[EventFill] = []
        equity_curve: list[PortfolioPoint] = []
        marks: dict[str, float] = {}

        def notify_portfolio_state(timestamp: datetime) -> None:
            market_value = sum(quantity * marks.get(symbol, 0.0) for symbol, quantity in positions.items())
            equity = cash + market_value
            for owner in provider_owners:
                on_portfolio_state = getattr(owner, "on_portfolio_state", None)
                if callable(on_portfolio_state):
                    on_portfolio_state(timestamp, equity)

        def managed_limit_price(signal: SignalIntent, rule: OrderManagementRule, event: MarketEvent) -> float:
            if rule.price_mode == "fixed":
                return round(float(rule.price_value or 0.0), 8)
            quote = event.ask1 if signal.action == "buy" else event.bid1
            base = float(quote) if quote is not None and quote > 0 else self._raw_price(event, signal.action)
            if rule.price_mode == "offset_bps":
                direction = 1.0 if signal.action == "buy" else -1.0
                base *= 1.0 + direction * float(rule.price_value or 0.0) / 10_000.0
            return round(base, 8)

        def carry_order(signal: SignalIntent, event: MarketEvent, remaining_quantity: int, reason: str) -> str:
            if signal.time_in_force == "ioc":
                return "ioc"
            root_id = order_chain_roots.setdefault(signal.signal_id, signal.signal_id)
            chain_wait_events[root_id] += 1
            waited_events = chain_wait_events[root_id]
            for rule in signal.management_rules:
                use_key = (root_id, rule.rule_id)
                uses = management_rule_uses[use_key]
                if waited_events < rule.after_events or uses >= rule.max_uses:
                    continue
                management_rule_uses[use_key] += 1
                use_number = uses + 1
                command_id = f"auto_{root_id}_{rule.rule_id}_{use_number}"
                queue_ahead.pop(signal.signal_id, None)
                if rule.action == "cancel":
                    managed_command = OrderCommand(
                        command_id, event.timestamp, signal.symbol, "cancel", signal.signal_id
                    )
                    cancelled_order_ids.add(signal.signal_id)
                    completed_order_ids.add(signal.signal_id)
                    order_updates.append(EventOrderUpdate(
                        signal.signal_id,
                        event.timestamp,
                        signal.symbol,
                        "cancelled",
                        remaining_quantity,
                        reason=f"management_rule:{rule.rule_id}:{reason}:waited_{waited_events}",
                        command_id=managed_command.command_id,
                    ))
                    return "cancelled"
                cancelled_order_ids.add(signal.signal_id)
                replacement_serials[root_id] += 1
                replacement_id = f"{root_id}__managed_replace_{replacement_serials[root_id]}"
                replacement = replace(
                    signal,
                    signal_id=replacement_id,
                    signal_at=event.timestamp,
                    quantity=remaining_quantity if signal.quantity_mode == "fixed" else signal.quantity,
                    order_type="limit",
                    limit_price=managed_limit_price(signal, rule, event),
                )
                managed_command = OrderCommand(
                    command_id, event.timestamp, signal.symbol, "replace", signal.signal_id, replacement
                )
                order_updates.append(EventOrderUpdate(
                    signal.signal_id,
                    event.timestamp,
                    signal.symbol,
                    "replaced",
                    remaining_quantity,
                    reason=f"management_rule:{rule.rule_id}:{reason}:waited_{waited_events}",
                    command_id=managed_command.command_id,
                ))
                order_chain_roots[replacement_id] = root_id
                order_session_dates[replacement_id] = order_session_dates.get(signal.signal_id, event.timestamp.date())
                known_order_ids.add(replacement_id)
                order_actions[replacement_id] = signal.action
                before = len(order_updates)
                schedule_signal(replacement, continuation=True)
                if len(order_updates) > before and order_updates[-1].status == "working":
                    order_updates[-1] = replace(
                        order_updates[-1],
                        reason=f"management_rule_replacement:{rule.rule_id}:limit_{replacement.limit_price}",
                    )
                    return "replaced"
                return "expired"
            continued = replace(
                signal,
                signal_at=event.timestamp,
                quantity=remaining_quantity if signal.quantity_mode == "fixed" else signal.quantity,
            )
            before = len(order_updates)
            schedule_signal(continued, continuation=True)
            if len(order_updates) > before and order_updates[-1].status == "working":
                order_updates[-1] = replace(order_updates[-1], reason=reason)
                return "working"
            return "expired"

        def limit_execution(
            signal: SignalIntent,
            event: MarketEvent,
            raw_price: float,
        ) -> tuple[bool, float, int | None, str | None]:
            if signal.order_type == "market":
                return True, raw_price, None, None
            limit_price = float(signal.limit_price or 0.0)
            if event.event_kind != "tick":
                touched = (
                    signal.action == "buy" and event.low is not None and float(event.low) <= limit_price
                ) or (
                    signal.action == "sell" and event.high is not None and float(event.high) >= limit_price
                )
                if not touched:
                    return False, raw_price, None, "limit_not_marketable"
                price = min(raw_price, limit_price) if signal.action == "buy" else max(raw_price, limit_price)
                return True, price, None, "bar_limit_touched"
            marketable = (
                signal.action == "buy" and event.ask1 is not None and float(event.ask1) <= limit_price
            ) or (
                signal.action == "sell" and event.bid1 is not None and float(event.bid1) >= limit_price
            )
            if marketable:
                queue_ahead.pop(signal.signal_id, None)
                price = min(raw_price, limit_price) if signal.action == "buy" else max(raw_price, limit_price)
                return True, price, None, None
            best_price = event.bid1 if signal.action == "buy" else event.ask1
            best_volume = event.bid1_volume if signal.action == "buy" else event.ask1_volume
            at_best = best_price is not None and abs(float(best_price) - limit_price) <= 1e-8
            if not at_best:
                queue_ahead.pop(signal.signal_id, None)
                return False, raw_price, None, "limit_not_at_best"
            if signal.signal_id not in queue_ahead:
                queue_ahead[signal.signal_id] = max(0, int(best_volume or 0))
                return False, limit_price, None, "joined_l1_queue"
            trade_reaches_price = event.last is not None and (
                (signal.action == "buy" and float(event.last) <= limit_price)
                or (signal.action == "sell" and float(event.last) >= limit_price)
            )
            traded = max(0, int(event.trade_volume or 0)) if trade_reaches_price else 0
            ahead = queue_ahead[signal.signal_id]
            consumed_ahead = min(ahead, traded)
            ahead -= consumed_ahead
            queue_ahead[signal.signal_id] = ahead
            executable_after_queue = max(0, traded - consumed_ahead)
            if executable_after_queue <= 0:
                return False, limit_price, None, "l1_queue_ahead"
            return True, limit_price, executable_after_queue, "l1_queue_reached"

        for event_index, event in enumerate(ordered_events):
            marks[event.symbol] = event.mark_price
            key = (event.timestamp, event.source_sequence, event.symbol)
            for command in sorted(scheduled_commands.get(key, ()), key=lambda item: item.command_id):
                target_id = command.target_signal_id
                if target_id not in known_order_ids or target_id in cancelled_order_ids or target_id in completed_order_ids:
                    order_updates.append(EventOrderUpdate(
                        target_id, event.timestamp, command.symbol, "command_rejected", None,
                        reason="target_order_not_working", command_id=command.command_id,
                    ))
                    continue
                cancelled_order_ids.add(target_id)
                target_action = order_actions.get(target_id)
                if target_action in pending_actions.get(command.symbol, []):
                    pending_actions[command.symbol].remove(target_action)
                queue_ahead.pop(target_id, None)
                order_updates.append(EventOrderUpdate(
                    target_id,
                    event.timestamp,
                    command.symbol,
                    "cancelled" if command.command_type == "cancel" else "replaced",
                    None,
                    command_id=command.command_id,
                ))
                if command.command_type == "replace" and command.replacement is not None:
                    replacement = command.replacement
                    if replacement.symbol != command.symbol:
                        order_updates.append(EventOrderUpdate(
                            replacement.signal_id, event.timestamp, replacement.symbol, "command_rejected", None,
                            reason="replacement_symbol_mismatch", command_id=command.command_id,
                        ))
                        continue
                    schedule_signal(replace(replacement, signal_at=command.command_at))
            intents = sorted(
                (item for item in scheduled.get(key, []) if item[0].signal_id not in cancelled_order_ids),
                key=lambda item: (0 if item[0].action == "sell" else 1, item[0].priority, item[0].signal_id),
            )
            for signal, execution_event in intents:
                action = signal.action
                if action in pending_actions.get(signal.symbol, []):
                    pending_actions[signal.symbol].remove(action)
                order_session_dates.setdefault(signal.signal_id, execution_event.timestamp.date())
                current_quantity = positions.get(signal.symbol, 0)
                target_quantity: int | None = None
                if signal.quantity_mode == "close_position":
                    requested_quantity = current_quantity
                    target_quantity = 0
                elif signal.quantity_mode == "target_position":
                    target_quantity = signal.quantity
                    if target_quantity < 0 or target_quantity % self.config.board_lot != 0:
                        rejections.append(
                            EventRejection(
                                signal.signal_id,
                                execution_event.timestamp,
                                signal.symbol,
                                action,
                                "invalid_target_position",
                            )
                        )
                        continue
                    if (action == "buy" and target_quantity < current_quantity) or (
                        action == "sell" and target_quantity > current_quantity
                    ):
                        rejections.append(
                            EventRejection(
                                signal.signal_id,
                                execution_event.timestamp,
                                signal.symbol,
                                action,
                                "target_direction_mismatch",
                            )
                        )
                        continue
                    requested_quantity = (
                        max(0, target_quantity - current_quantity)
                        if action == "buy"
                        else max(0, current_quantity - target_quantity)
                    )
                    if requested_quantity == 0:
                        rejections.append(
                            EventRejection(
                                signal.signal_id,
                                execution_event.timestamp,
                                signal.symbol,
                                action,
                                "target_already_satisfied",
                            )
                        )
                        continue
                else:
                    requested_quantity = signal.quantity
                if (
                    action == "buy"
                    and current_quantity <= 0
                    and self.config.max_positions is not None
                    and len(positions) >= self.config.max_positions
                ):
                    carry_outcome = carry_order(signal, execution_event, requested_quantity, "max_positions_reached")
                    if carry_outcome != "ioc":
                        continue
                    rejections.append(EventRejection(
                        signal.signal_id, execution_event.timestamp, signal.symbol, action, "max_positions_reached"
                    ))
                    continue
                raw_price = self._raw_price(execution_event, action)
                limit_ready, raw_price, queue_available, limit_reason = limit_execution(signal, execution_event, raw_price)
                if not limit_ready:
                    carry_outcome = carry_order(signal, execution_event, requested_quantity, str(limit_reason))
                    if carry_outcome != "ioc":
                        continue
                    rejections.append(EventRejection(
                        signal.signal_id,
                        execution_event.timestamp,
                        signal.symbol,
                        action,
                        str(limit_reason),
                    ))
                    completed_order_ids.add(signal.signal_id)
                    continue
                blocked = self._blocked_reason(execution_event, action, raw_price)
                if blocked:
                    carry_outcome = carry_order(signal, execution_event, requested_quantity, blocked)
                    if carry_outcome != "ioc":
                        continue
                    rejections.append(EventRejection(signal.signal_id, execution_event.timestamp, signal.symbol, action, blocked))
                    completed_order_ids.add(signal.signal_id)
                    continue
                if requested_quantity <= 0 or requested_quantity % self.config.board_lot != 0:
                    rejections.append(EventRejection(signal.signal_id, execution_event.timestamp, signal.symbol, action, "invalid_board_lot"))
                    continue
                if action == "sell" and positions.get(signal.symbol, 0) < requested_quantity:
                    rejections.append(EventRejection(signal.signal_id, execution_event.timestamp, signal.symbol, action, "insufficient_position"))
                    continue
                restriction_reason: str | None = limit_reason
                executable_quantity = requested_quantity
                if action == "sell" and self.config.enforce_t_plus_one:
                    sellable_quantity = sum(
                        quantity
                        for acquired_on, quantity in position_lots.get(signal.symbol, ())
                        if acquired_on < execution_event.timestamp.date()
                    )
                    if sellable_quantity <= 0:
                        carry_outcome = carry_order(signal, execution_event, requested_quantity, "t_plus_one_block")
                        if carry_outcome != "ioc":
                            continue
                        rejections.append(
                            EventRejection(signal.signal_id, execution_event.timestamp, signal.symbol, action, "t_plus_one_block")
                        )
                        completed_order_ids.add(signal.signal_id)
                        continue
                    if sellable_quantity < requested_quantity:
                        executable_quantity = sellable_quantity
                        restriction_reason = "t_plus_one_unsellable_remainder"
                available = queue_available if queue_available is not None else self._available_quantity(execution_event, action)
                fill_quantity = min(executable_quantity, available)
                fill_quantity = int(math.floor(fill_quantity / self.config.board_lot)) * self.config.board_lot
                if fill_quantity <= 0:
                    carry_outcome = carry_order(signal, execution_event, requested_quantity, "no_executable_liquidity")
                    if carry_outcome != "ioc":
                        continue
                    rejections.append(EventRejection(signal.signal_id, execution_event.timestamp, signal.symbol, action, "no_executable_liquidity"))
                    completed_order_ids.add(signal.signal_id)
                    continue
                participation = fill_quantity / max(1.0, float(available) / self.config.maximum_participation)
                impact_fraction = min(1.0, participation / max(self.config.maximum_participation, 1e-12))
                slippage_bps = (
                    0.0
                    if limit_reason in {"bar_limit_touched", "l1_queue_reached"}
                    else self.config.base_slippage_bps
                    + self.config.impact_bps_at_max_participation * math.sqrt(impact_fraction)
                )
                direction = 1.0 if action == "buy" else -1.0
                price = raw_price * (1.0 + direction * slippage_bps / 10_000.0)
                if execution_event.low is not None:
                    price = max(float(execution_event.low), price)
                if execution_event.high is not None:
                    price = min(float(execution_event.high), price)
                if signal.order_type == "limit" and signal.limit_price is not None:
                    price = min(price, signal.limit_price) if action == "buy" else max(price, signal.limit_price)
                notional = price * fill_quantity
                fees = self.cost_model.calculate(notional, action)
                if action == "buy":
                    initially_affordable = fill_quantity
                    affordable = fill_quantity
                    while affordable > 0 and price * affordable + self.cost_model.calculate(price * affordable, action) > cash:
                        affordable -= self.config.board_lot
                    fill_quantity = affordable
                    if fill_quantity <= 0:
                        carry_outcome = carry_order(signal, execution_event, requested_quantity, "insufficient_cash")
                        if carry_outcome != "ioc":
                            continue
                        rejections.append(EventRejection(signal.signal_id, execution_event.timestamp, signal.symbol, action, "insufficient_cash"))
                        completed_order_ids.add(signal.signal_id)
                        continue
                    if fill_quantity < initially_affordable:
                        restriction_reason = "cash_limited_remainder"
                    notional = price * fill_quantity
                    fees = self.cost_model.calculate(notional, action)
                    cash -= notional + fees
                    positions[signal.symbol] = positions.get(signal.symbol, 0) + fill_quantity
                    position_lots[signal.symbol].append((execution_event.timestamp.date(), fill_quantity))
                else:
                    cash += notional - fees
                    positions[signal.symbol] -= fill_quantity
                    remaining_to_consume = fill_quantity
                    retained_lots: list[tuple[date, int]] = []
                    for acquired_on, lot_quantity in position_lots.get(signal.symbol, ()):
                        eligible = not self.config.enforce_t_plus_one or acquired_on < execution_event.timestamp.date()
                        consumed = min(lot_quantity, remaining_to_consume) if eligible else 0
                        remaining_to_consume -= consumed
                        if lot_quantity > consumed:
                            retained_lots.append((acquired_on, lot_quantity - consumed))
                    if retained_lots:
                        position_lots[signal.symbol] = retained_lots
                    else:
                        position_lots.pop(signal.symbol, None)
                    if positions[signal.symbol] == 0:
                        positions.pop(signal.symbol)
                remaining_quantity = max(0, requested_quantity - fill_quantity)
                continuation_outcome = "filled"
                if remaining_quantity > 0:
                    order_updates.append(EventOrderUpdate(
                        signal.signal_id,
                        execution_event.timestamp,
                        signal.symbol,
                        "partially_filled",
                        remaining_quantity,
                        reason=restriction_reason or limit_reason,
                        queue_ahead=queue_ahead.get(signal.signal_id),
                    ))
                    continuation_outcome = carry_order(
                        signal,
                        execution_event,
                        remaining_quantity,
                        restriction_reason or limit_reason or "partial_fill_remainder",
                    )
                if remaining_quantity == 0:
                    completed_order_ids.add(signal.signal_id)
                    queue_ahead.pop(signal.signal_id, None)
                    order_updates.append(EventOrderUpdate(
                        signal.signal_id, execution_event.timestamp, signal.symbol, "filled", 0,
                        reason=limit_reason,
                    ))
                elif continuation_outcome not in {"working", "replaced"}:
                    completed_order_ids.add(signal.signal_id)
                    queue_ahead.pop(signal.signal_id, None)
                    if signal.time_in_force == "ioc":
                        order_updates.append(EventOrderUpdate(
                            signal.signal_id, execution_event.timestamp, signal.symbol, "expired", remaining_quantity,
                            reason="ioc_remainder_expired",
                        ))
                fill = EventFill(
                        signal_id=signal.signal_id,
                        timestamp=execution_event.timestamp,
                        symbol=signal.symbol,
                        action=action,
                        requested_quantity=requested_quantity,
                        filled_quantity=fill_quantity,
                        price=round(price, 8),
                        fees=round(fees, 8),
                        slippage_bps=round(slippage_bps, 6),
                        status=(
                            "filled"
                            if fill_quantity == requested_quantity
                            else "partially_filled_open"
                            if continuation_outcome == "working"
                            else "partially_filled_replaced"
                            if continuation_outcome == "replaced"
                            else "partially_filled_cancelled"
                            if continuation_outcome == "cancelled"
                            else "partially_filled_expired"
                        ),
                        quantity_mode=signal.quantity_mode,
                        target_quantity=target_quantity,
                        restriction_reason=restriction_reason,
                        execution_priority=signal.priority,
                    )
                fills.append(fill)
                for owner in provider_owners:
                    on_fill = getattr(owner, "on_fill", None)
                    if callable(on_fill):
                        on_fill(fill)
            if signal_provider is not None:
                notify_portfolio_state(event.timestamp)
                generated = signal_provider(
                    event,
                    dict(positions),
                    {symbol: tuple(actions) for symbol, actions in pending_actions.items() if actions},
                )
                for signal in sorted(generated, key=lambda item: (item.signal_at, item.signal_id)):
                    schedule_signal(signal)
            next_timestamp = ordered_events[event_index + 1].timestamp if event_index + 1 < len(ordered_events) else None
            if next_timestamp != event.timestamp:
                if signal_batch_provider is not None:
                    notify_portfolio_state(event.timestamp)
                    start = event_index
                    while start > 0 and ordered_events[start - 1].timestamp == event.timestamp:
                        start -= 1
                    generated = signal_batch_provider(
                        tuple(ordered_events[start:event_index + 1]),
                        dict(positions),
                        {symbol: tuple(actions) for symbol, actions in pending_actions.items() if actions},
                    )
                    for signal in sorted(generated, key=lambda item: (item.signal_at, item.priority, item.signal_id)):
                        schedule_signal(signal)
                market_value = sum(quantity * marks.get(symbol, 0.0) for symbol, quantity in positions.items())
                equity_curve.append(PortfolioPoint(event.timestamp, cash + market_value, cash, market_value))
                sort_next_timestamp_group(event_index + 1)
        return UnifiedRunResult(
            fills=tuple(fills),
            rejections=tuple(rejections),
            equity_curve=tuple(equity_curve),
            ending_positions=dict(sorted(positions.items())),
            initial_cash=self.config.initial_cash,
            ending_cash=cash,
            order_updates=tuple(order_updates),
        )


def aggregate_minute_bars(events: Iterable[MarketEvent], minutes: int) -> list[MarketEvent]:
    """Aggregate Q1 right-labelled 1-minute bars on the A-share session clock.

    Q1 carries 09:30 as an opening-auction/helper observation, followed by the
    120 continuous minute closes from 09:31 through 11:30.  The helper belongs
    to the first morning bar; it must not create a 25th five-minute bucket.
    The same boundary rule accepts an optional 13:00 helper before the normal
    13:01 through 15:00 afternoon stream.
    """

    if minutes not in {5, 15, 30, 60}:
        raise ValueError("Supported aggregation windows are 5, 15, 30, and 60 minutes")
    buckets: dict[tuple[str, object, int], list[MarketEvent]] = defaultdict(list)
    for event in sorted(events, key=lambda item: (item.symbol, item.timestamp, item.source_sequence)):
        if event.frequency != "1m" or event.event_kind != "bar":
            raise ValueError("Only 1-minute bar events can be aggregated")
        current = event.timestamp.time()
        if time(9, 30) <= current <= time(11, 30):
            session_start = time(9, 30)
            session_key = 0
        elif time(13, 0) <= current <= time(15, 0):
            session_start = time(13, 0)
            session_key = 10_000
        else:
            raise ValueError("Minute bar is outside the A-share continuous-session clock")
        offset = (event.timestamp.hour * 60 + event.timestamp.minute) - (session_start.hour * 60 + session_start.minute)
        bucket = 0 if offset == 0 else (offset - 1) // minutes
        buckets[(event.symbol, event.timestamp.date(), session_key + bucket)].append(event)
    output: list[MarketEvent] = []
    for (_, _, _), rows in sorted(buckets.items(), key=lambda item: (item[1][0].timestamp, item[0][0])):
        first, last = rows[0], rows[-1]
        output.append(
            MarketEvent(
                timestamp=last.timestamp,
                symbol=first.symbol,
                frequency=f"{minutes}m",
                event_kind="bar",
                open=first.open,
                high=max(float(row.high) for row in rows if row.high is not None),
                low=min(float(row.low) for row in rows if row.low is not None),
                close=last.close,
                volume=sum(row.volume for row in rows),
                turnover=sum(row.turnover for row in rows),
                pre_close=first.pre_close,
                limit_up=last.limit_up,
                limit_down=last.limit_down,
                is_suspended=all(row.is_suspended for row in rows),
                source_sequence=last.source_sequence,
            )
        )
    return output


__all__ = [
    "EventEngineConfig", "EventFill", "EventOrderUpdate", "EventRejection", "MarketEvent", "OrderCommand",
    "OrderManagementRule",
    "PortfolioPoint", "SignalIntent", "UnifiedEventEngine", "UnifiedRunResult", "aggregate_minute_bars",
]
