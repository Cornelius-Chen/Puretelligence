"""Versioned public contracts for the strategy research center.

The objects are deliberately dependency-free. AI output, UI forms, DSL input,
and expert plugins must all compile into these same contracts before research
can run.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum, IntEnum
from typing import Any, Mapping


class Frequency(str, Enum):
    DAILY = "1d"
    MINUTE_1 = "1m"
    MINUTE_5 = "5m"
    MINUTE_15 = "15m"
    MINUTE_30 = "30m"
    MINUTE_60 = "60m"
    TICK_L1 = "tick_l1"


SUPPORTED_STRESS_TESTS = frozenset({
    "cost_x2",
    "delay",
    "missing_data",
    "data_perturbation_matrix",
    "parameter_neighborhood",
    "factor_ablation",
    "frequency_consistency",
    "period_consistency",
    "market_regime",
    "exposure_concentration",
    "sample_selection_bias",
})


class EvidenceLevel(IntEnum):
    IDEA = 1
    EXECUTABLE = 2
    INITIAL_OOS = 3
    ROBUST = 4
    SHADOW_READY = 5
    SHADOW_VALIDATED = 6

    @property
    def label_zh(self) -> str:
        return {
            1: "想法待验证",
            2: "可执行且数据合格",
            3: "样本外有初步证据",
            4: "多重检验后仍稳健",
            5: "可进入冻结影子验证",
            6: "影子验证达标",
        }[int(self)]


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    field: str
    code: str
    message: str
    severity: str = "error"


@dataclass(frozen=True, slots=True)
class AnnotationSpec:
    annotation_id: str
    kind: str
    symbol: str
    start_at: str
    end_at: str | None = None
    price_low: float | None = None
    price_high: float | None = None
    label: str = ""
    polarity: str = "neutral"
    points: tuple[tuple[str, float], ...] = ()

    def validate(self) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if self.kind not in {"positive_window", "negative_window", "shape_region", "anchor", "entry", "exit", "invalidation"}:
            issues.append(ValidationIssue("kind", "unsupported_annotation_kind", "不支持的图形标注类型"))
        if not self.symbol:
            issues.append(ValidationIssue("symbol", "missing_symbol", "标注必须关联股票"))
        if not self.start_at:
            issues.append(ValidationIssue("start_at", "missing_start", "标注必须包含起始时间"))
        start = None
        end = None
        try:
            start = datetime.fromisoformat(self.start_at.replace("Z", "+00:00")) if self.start_at else None
        except ValueError:
            issues.append(ValidationIssue("start_at", "invalid_start", "标注起始时间必须是有效日期或时间"))
        if self.kind in {"positive_window", "negative_window", "shape_region"} and not self.end_at:
            issues.append(ValidationIssue("end_at", "window_requires_end", "区间标注必须包含结束时间"))
        if self.end_at:
            try:
                end = datetime.fromisoformat(self.end_at.replace("Z", "+00:00"))
            except ValueError:
                issues.append(ValidationIssue("end_at", "invalid_end", "标注结束时间必须是有效日期或时间"))
        if start is not None and end is not None and end < start:
            issues.append(ValidationIssue("end_at", "end_before_start", "标注结束时间不能早于起始时间"))
        if self.price_low is not None and self.price_high is not None and self.price_high < self.price_low:
            issues.append(ValidationIssue("price_high", "price_range_reversed", "价格上界不能低于价格下界"))
        expected_polarity = {"positive_window": "positive", "negative_window": "negative"}.get(self.kind)
        if expected_polarity is not None and self.polarity != expected_polarity:
            issues.append(ValidationIssue("polarity", "polarity_kind_mismatch", "正反例类型与标注极性不一致"))
        return issues


@dataclass(frozen=True, slots=True)
class StrategyIntentDraft:
    draft_id: str
    workspace_id: str
    owner_id: str
    raw_text: str
    annotations: tuple[AnnotationSpec, ...] = ()
    understood_fields: Mapping[str, Any] = field(default_factory=dict)
    field_provenance: Mapping[str, str] = field(default_factory=dict)
    ambiguities: tuple[str, ...] = ()
    ambiguity_reasons: Mapping[str, str] = field(default_factory=dict)
    field_candidates: Mapping[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    revision: int = 1
    parent_draft_id: str | None = None
    confirmation_history: tuple[Mapping[str, Any], ...] = ()
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True, slots=True)
class RuleSpec:
    rule_id: str
    side: str
    expression: str
    human_label: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if self.side not in {"entry", "exit", "filter", "rank", "risk"}:
            issues.append(ValidationIssue(f"rules.{self.rule_id}.side", "unsupported_rule_side", "规则作用位置无效"))
        if not self.expression.strip():
            issues.append(ValidationIssue(f"rules.{self.rule_id}.expression", "missing_expression", "规则表达式不能为空"))
        if "factor_family" in self.parameters and (
            not isinstance(self.parameters["factor_family"], str)
            or not self.parameters["factor_family"].strip()
        ):
            issues.append(ValidationIssue(
                f"rules.{self.rule_id}.parameters.factor_family",
                "invalid_factor_family",
                "规则族名称必须是非空文字",
            ))
        return issues


@dataclass(frozen=True, slots=True)
class StrategySpec:
    strategy_id: str
    version: int
    workspace_id: str
    owner_id: str
    title: str
    universe: tuple[str, ...]
    frequency: Frequency
    signal_time: str
    price_basis: str
    entry_rules: tuple[RuleSpec, ...]
    exit_rules: tuple[RuleSpec, ...]
    holding_period: str
    rebalance: str
    position_sizing: Mapping[str, Any]
    risk: Mapping[str, Any]
    execution: Mapping[str, Any]
    costs: Mapping[str, Any]
    benchmark: str
    data_window: Mapping[str, str]
    parameters: Mapping[str, Any] = field(default_factory=dict)
    plugin_refs: tuple[str, ...] = ()
    entry_rule_logic: str = "all"
    exit_rule_logic: str = "all"
    schema_version: str = "strategy_spec_v1"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    frozen_at: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StrategySpec":
        return cls(
            strategy_id=str(payload.get("strategy_id", "")),
            version=int(payload.get("version", 1)),
            workspace_id=str(payload.get("workspace_id", "local")),
            owner_id=str(payload.get("owner_id", "local-user")),
            title=str(payload.get("title", "")),
            universe=tuple(str(value) for value in payload.get("universe", [])),
            frequency=Frequency(str(payload.get("frequency", "1d"))),
            signal_time=str(payload.get("signal_time", "")),
            price_basis=str(payload.get("price_basis", "")),
            entry_rules=tuple(RuleSpec(**dict(value)) for value in payload.get("entry_rules", [])),
            exit_rules=tuple(RuleSpec(**dict(value)) for value in payload.get("exit_rules", [])),
            holding_period=str(payload.get("holding_period", "")),
            rebalance=str(payload.get("rebalance", "")),
            position_sizing=dict(payload.get("position_sizing", {})),
            risk=dict(payload.get("risk", {})),
            execution=dict(payload.get("execution", {})),
            costs=dict(payload.get("costs", {})),
            benchmark=str(payload.get("benchmark", "")),
            data_window={str(k): str(v) for k, v in dict(payload.get("data_window", {})).items()},
            parameters=dict(payload.get("parameters", {})),
            plugin_refs=tuple(str(value) for value in payload.get("plugin_refs", [])),
            entry_rule_logic=str(payload.get("entry_rule_logic", "all")),
            exit_rule_logic=str(payload.get("exit_rule_logic", "all")),
            schema_version=str(payload.get("schema_version", "strategy_spec_v1")),
            created_at=str(payload.get("created_at") or datetime.now(UTC).isoformat()),
            frozen_at=str(payload["frozen_at"]) if payload.get("frozen_at") else None,
        )

    def validate(self) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if self.version < 1:
            issues.append(ValidationIssue("version", "invalid_strategy_version", "策略版本必须从 1 开始"))
        required_text = {
            "strategy_id": (self.strategy_id, "策略编号不能为空"),
            "title": (self.title, "策略名称不能为空"),
            "signal_time": (self.signal_time, "必须确认信号生效时点"),
            "price_basis": (self.price_basis, "必须确认价格口径"),
            "holding_period": (self.holding_period, "必须确认持有期"),
            "rebalance": (self.rebalance, "必须确认调仓方式"),
            "benchmark": (self.benchmark, "必须选择比较基准"),
        }
        for field_name, (value, message) in required_text.items():
            if not str(value).strip():
                issues.append(ValidationIssue(field_name, "critical_field_missing", message))
        if not self.universe:
            issues.append(ValidationIssue("universe", "critical_field_missing", "必须确认股票范围"))
        allowed_selectors = {"CN_A_SHARES", "CN_A_SHARES_HS", "CHINEXT", "BSE", "SSE_MAIN"}
        invalid_symbols = [
            value for value in self.universe
            if value not in allowed_selectors and not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", value)
        ]
        if invalid_symbols:
            issues.append(ValidationIssue("universe", "invalid_universe_symbol", "股票代码必须是六位代码加 .SH、.SZ 或 .BJ，或使用受支持的市场范围"))
        if not self.entry_rules:
            issues.append(ValidationIssue("entry_rules", "critical_field_missing", "至少需要一条入场规则"))
        if not self.exit_rules:
            issues.append(ValidationIssue("exit_rules", "critical_field_missing", "至少需要一条退出规则"))
        if not self.execution:
            issues.append(ValidationIssue("execution", "critical_field_missing", "必须确认成交方式"))
        if not self.costs:
            issues.append(ValidationIssue("costs", "critical_field_missing", "必须确认费用口径"))
        if "start" not in self.data_window or "end" not in self.data_window:
            issues.append(ValidationIssue("data_window", "critical_field_missing", "必须确认回测起止时间"))
        else:
            try:
                start = datetime.fromisoformat(str(self.data_window["start"]))
                end = datetime.fromisoformat(str(self.data_window["end"]))
                if end < start:
                    issues.append(ValidationIssue("data_window", "data_window_reversed", "回测结束时间不能早于开始时间"))
            except ValueError:
                issues.append(ValidationIssue("data_window", "invalid_data_window", "回测起止时间必须是有效日期或时间"))
        if self.benchmark and not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", self.benchmark):
            issues.append(ValidationIssue("benchmark", "invalid_benchmark_symbol", "基准代码必须包含交易所后缀"))
        if self.schema_version != "strategy_spec_v1":
            issues.append(ValidationIssue("schema_version", "unsupported_schema", "不支持的策略定义版本"))
        if self.execution.get("live_trading") not in (None, False, "forbidden"):
            issues.append(ValidationIssue("execution.live_trading", "live_forbidden", "策略中心禁止实盘下单"))
        order_type = self.execution.get("order_type", "market")
        if order_type not in {"market", "limit"}:
            issues.append(ValidationIssue(
                "execution.order_type", "unsupported_order_type", "订单类型只支持市价可成交或限价"
            ))
        time_in_force = self.execution.get("time_in_force", "ioc")
        if time_in_force not in {"ioc", "day", "gtc"}:
            issues.append(ValidationIssue(
                "execution.time_in_force", "unsupported_time_in_force", "订单有效期只支持立即成交否则撤销、当日有效或指定到期"
            ))
        if order_type == "limit":
            common_limit = self.execution.get("limit_price")
            missing_sides = [
                side for side in ("buy", "sell")
                if common_limit in (None, "") and self.execution.get(f"{side}_limit_price") in (None, "")
            ]
            if missing_sides:
                issues.append(ValidationIssue(
                    "execution.limit_price",
                    "limit_price_missing",
                    "限价订单必须同时确认买入和卖出限价",
                ))
            for field_name in ("limit_price", "buy_limit_price", "sell_limit_price"):
                value = self.execution.get(field_name)
                if value not in (None, "") and (isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0):
                    issues.append(ValidationIssue(
                        f"execution.{field_name}", "invalid_limit_price", "限价必须是正数"
                    ))
        if time_in_force == "gtc":
            raw_expiry = self.execution.get("expires_at")
            if not raw_expiry:
                issues.append(ValidationIssue(
                    "execution.expires_at", "gtc_requires_expiry", "指定到期订单必须确认到期时间"
                ))
            else:
                try:
                    datetime.fromisoformat(str(raw_expiry))
                except ValueError:
                    issues.append(ValidationIssue(
                        "execution.expires_at", "invalid_expiry", "订单到期时间格式无效"
                    ))
        management_rules = self.execution.get("order_management_rules", [])
        if not isinstance(management_rules, list):
            issues.append(ValidationIssue(
                "execution.order_management_rules",
                "invalid_order_management_rules",
                "未成交处理规则必须是按优先顺序排列的列表",
            ))
            management_rules = []
        if management_rules and time_in_force == "ioc":
            issues.append(ValidationIssue(
                "execution.order_management_rules",
                "management_requires_persistent_order",
                "只有当日持续等待或指定到期订单才能设置未成交后的撤单或改价",
            ))
        seen_management_rule_ids: set[str] = set()
        for index, raw_rule in enumerate(management_rules):
            field_prefix = f"execution.order_management_rules.{index}"
            if not isinstance(raw_rule, Mapping):
                issues.append(ValidationIssue(
                    field_prefix, "invalid_order_management_rule", "每条未成交处理规则都必须是完整规则"
                ))
                continue
            rule_id = str(raw_rule.get("rule_id", "")).strip()
            if not rule_id:
                issues.append(ValidationIssue(
                    f"{field_prefix}.rule_id", "management_rule_id_missing", "每条未成交处理规则都需要审计编号"
                ))
            elif rule_id in seen_management_rule_ids:
                issues.append(ValidationIssue(
                    f"{field_prefix}.rule_id", "duplicate_management_rule_id", "未成交处理规则的审计编号不能重复"
                ))
            seen_management_rule_ids.add(rule_id)
            action = raw_rule.get("action")
            if action not in {"cancel", "replace"}:
                issues.append(ValidationIssue(
                    f"{field_prefix}.action", "unsupported_management_action", "未成交后只支持撤单或改价"
                ))
            after_events = raw_rule.get("after_events")
            if isinstance(after_events, bool) or not isinstance(after_events, int) or after_events <= 0:
                issues.append(ValidationIssue(
                    f"{field_prefix}.after_events", "invalid_management_wait", "等待次数必须是正整数"
                ))
            max_uses = raw_rule.get("max_uses", 1)
            if isinstance(max_uses, bool) or not isinstance(max_uses, int) or max_uses <= 0:
                issues.append(ValidationIssue(
                    f"{field_prefix}.max_uses", "invalid_management_max_uses", "规则最多执行次数必须是正整数"
                ))
            if action == "replace":
                if order_type != "limit":
                    issues.append(ValidationIssue(
                        f"{field_prefix}.action",
                        "managed_replace_requires_limit_order",
                        "自动改价只适用于限价订单",
                    ))
                price_mode = raw_rule.get("price_mode")
                if price_mode not in {"best_quote", "fixed", "offset_bps"}:
                    issues.append(ValidationIssue(
                        f"{field_prefix}.price_mode",
                        "unsupported_management_price_mode",
                        "改价方式只支持当前可成交价、固定价格或相对盘口偏移",
                    ))
                price_value = raw_rule.get("price_value")
                if price_mode == "fixed" and (
                    isinstance(price_value, bool)
                    or not isinstance(price_value, (int, float))
                    or float(price_value) <= 0
                ):
                    issues.append(ValidationIssue(
                        f"{field_prefix}.price_value", "invalid_management_price", "固定改价必须是正数"
                    ))
                if price_mode == "offset_bps" and (
                    isinstance(price_value, bool)
                    or not isinstance(price_value, (int, float))
                    or float(price_value) < 0
                ):
                    issues.append(ValidationIssue(
                        f"{field_prefix}.price_value",
                        "invalid_management_offset",
                        "相对盘口偏移必须是大于或等于零的基点数",
                    ))
        max_positions = self.position_sizing.get("max_positions", 1)
        if isinstance(max_positions, bool) or not isinstance(max_positions, int) or max_positions <= 0:
            issues.append(ValidationIssue(
                "position_sizing.max_positions",
                "invalid_max_positions",
                "最大同时持仓数必须是正整数",
            ))
        selection_policy = self.position_sizing.get("selection_policy", "universe_order")
        if selection_policy not in {"universe_order", "symbol_asc"}:
            issues.append(ValidationIssue(
                "position_sizing.selection_policy",
                "unsupported_selection_policy",
                "同刻候选排序只支持股票池顺序或证券代码顺序",
            ))
        bounded_rates = (
            ("risk.max_position_weight", self.risk.get("max_position_weight"), 0.0, 1.0),
            ("risk.max_drawdown_stop", self.risk.get("max_drawdown_stop"), 0.0, 1.0),
            ("execution.maximum_participation", self.execution.get("maximum_participation"), 0.0, 1.0),
            ("costs.commission_rate", self.costs.get("commission_rate"), 0.0, 0.1),
            ("costs.stamp_duty_sell", self.costs.get("stamp_duty_sell"), 0.0, 0.1),
        )
        for field_name, value, lower, upper in bounded_rates:
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not lower <= float(value) <= upper:
                issues.append(ValidationIssue(field_name, "invalid_rate_range", f"{field_name} 必须在 {lower:g} 到 {upper:g} 之间"))
        positive_numbers = (
            ("execution.board_lot", self.execution.get("board_lot", self.execution.get("lot_size"))),
            ("position_sizing.initial_cash", self.position_sizing.get("initial_cash")),
        )
        for field_name, value in positive_numbers:
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                issues.append(ValidationIssue(field_name, "invalid_positive_value", f"{field_name} 必须是正数"))
        slippage = self.costs.get("slippage_bps")
        if slippage is not None and (
            isinstance(slippage, bool) or not isinstance(slippage, (int, float)) or not 0 <= float(slippage) <= 10_000
        ):
            issues.append(ValidationIssue("costs.slippage_bps", "invalid_slippage_range", "滑点必须在 0 到 10000bp 之间"))
        for field_name, value in (
            ("entry_rule_logic", self.entry_rule_logic),
            ("exit_rule_logic", self.exit_rule_logic),
        ):
            if value not in {"all", "any"}:
                issues.append(ValidationIssue(field_name, "unsupported_rule_logic", "多条规则只能选择全部满足或任一满足"))
        rule_ids = [rule.rule_id for rule in (*self.entry_rules, *self.exit_rules)]
        duplicate_rule_ids = sorted({rule_id for rule_id in rule_ids if rule_ids.count(rule_id) > 1})
        if duplicate_rule_ids:
            issues.append(ValidationIssue(
                "rules.rule_id",
                "duplicate_rule_id",
                "规则编号不能重复：" + "、".join(duplicate_rule_ids),
            ))
        for rule in (*self.entry_rules, *self.exit_rules):
            issues.extend(rule.validate())
        return issues

    def freeze(self, at: str | None = None) -> "StrategySpec":
        if self.validate():
            raise ValueError("StrategySpec cannot be frozen while validation errors remain")
        payload = self.to_dict()
        payload["frozen_at"] = at or datetime.now(UTC).isoformat()
        return StrategySpec.from_dict(payload)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["frequency"] = self.frequency.value
        return payload

    def content_hash(self) -> str:
        return canonical_hash(self.to_dict())


@dataclass(frozen=True, slots=True)
class ExperimentProtocol:
    protocol_id: str
    version: int
    hypothesis: str
    baseline_strategy_id: str
    split_policy: Mapping[str, Any]
    purge_periods: int
    embargo_periods: int
    multiple_testing: str
    confidence_level: float
    alpha: float
    target_power: float
    max_experiments: int
    metrics: tuple[str, ...]
    stress_tests: tuple[str, ...]
    reference_universe: tuple[str, ...] = ()
    frozen_at: str | None = None
    schema_version: str = "experiment_protocol_v1"

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExperimentProtocol":
        return cls(
            protocol_id=str(payload.get("protocol_id", "")),
            version=int(payload.get("version", 1)),
            hypothesis=str(payload.get("hypothesis", "")),
            baseline_strategy_id=str(payload.get("baseline_strategy_id", "")),
            split_policy=dict(payload.get("split_policy", {})),
            purge_periods=int(payload.get("purge_periods", 0)),
            embargo_periods=int(payload.get("embargo_periods", 0)),
            multiple_testing=str(payload.get("multiple_testing", "holm")),
            confidence_level=float(payload.get("confidence_level", 0.95)),
            alpha=float(payload.get("alpha", 0.05)),
            target_power=float(payload.get("target_power", 0.8)),
            max_experiments=int(payload.get("max_experiments", 1)),
            metrics=tuple(str(value) for value in payload.get("metrics", [])),
            stress_tests=tuple(str(value) for value in payload.get("stress_tests", [])),
            reference_universe=tuple(str(value).strip().upper() for value in payload.get("reference_universe", []) if str(value).strip()),
            frozen_at=str(payload["frozen_at"]) if payload.get("frozen_at") else None,
            schema_version=str(payload.get("schema_version", "experiment_protocol_v1")),
        )

    def validate(self) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not self.hypothesis.strip():
            issues.append(ValidationIssue("hypothesis", "missing_hypothesis", "实验必须预先写明假设"))
        if not self.baseline_strategy_id:
            issues.append(ValidationIssue("baseline_strategy_id", "missing_baseline", "实验必须有比较基线"))
        if self.multiple_testing not in {"holm", "bh_fdr"}:
            issues.append(ValidationIssue("multiple_testing", "unsupported_correction", "多重检验方法必须为 Holm 或 BH-FDR"))
        family_id = str(self.split_policy.get("hypothesis_family_id") or self.protocol_id).strip()
        try:
            family_size = int(self.split_policy.get("hypothesis_family_size", 1))
            family_attempt = int(self.split_policy.get("hypothesis_attempt", 1))
        except (TypeError, ValueError):
            family_size, family_attempt = 0, 0
        if not family_id or len(family_id) > 128:
            issues.append(ValidationIssue(
                "split_policy.hypothesis_family_id",
                "invalid_hypothesis_family_id",
                "实验族名称不能为空且最多128个字符",
            ))
        if not 1 <= family_size <= 100:
            issues.append(ValidationIssue(
                "split_policy.hypothesis_family_size",
                "invalid_hypothesis_family_size",
                "预注册实验族必须包含1到100个假设",
            ))
        if not 1 <= family_attempt <= max(1, family_size):
            issues.append(ValidationIssue(
                "split_policy.hypothesis_attempt",
                "invalid_hypothesis_attempt",
                "当前尝试序号必须位于预注册实验族范围内",
            ))
        raw_family_manifest = self.split_policy.get("hypothesis_family_manifest", [])
        family_manifest = (
            [str(value).strip() for value in raw_family_manifest]
            if isinstance(raw_family_manifest, (list, tuple))
            else []
        )
        if family_size == 1 and not family_manifest:
            family_manifest = [self.hypothesis.strip()]
        if len(family_manifest) != family_size or any(not value for value in family_manifest):
            issues.append(ValidationIssue(
                "split_policy.hypothesis_family_manifest",
                "incomplete_hypothesis_family_manifest",
                "实验族中的全部假设必须在第一次运行前完整冻结",
            ))
        elif 1 <= family_attempt <= family_size and family_manifest[family_attempt - 1] != self.hypothesis.strip():
            issues.append(ValidationIssue(
                "hypothesis",
                "hypothesis_does_not_match_family_attempt",
                "当前假设必须与实验族清单中对应序号完全一致",
            ))
        if len(set(family_manifest)) != len(family_manifest):
            issues.append(ValidationIssue(
                "split_policy.hypothesis_family_manifest",
                "duplicate_hypothesis_in_family",
                "同一实验族不能重复登记完全相同的假设",
            ))
        parameter_selection_mode = str(
            self.split_policy.get("parameter_selection_mode", "fixed_frozen")
        ).strip()
        if parameter_selection_mode not in {
            "fixed_frozen",
            "nested_walk_forward_daily_v1",
            "nested_walk_forward_session_v1",
        }:
            issues.append(ValidationIssue(
                "split_policy.parameter_selection_mode",
                "nested_parameter_selection_not_available",
                "参数选择模式只支持冻结单一参数、日频嵌套或日内交易会话嵌套 walk-forward",
            ))
        always_forbidden_tuning_fields = sorted(
            field_name
            for field_name in (
                "parameter_grid",
                "auto_tune",
                "search_space",
                "tuner",
            )
            if field_name in self.split_policy
        )
        if always_forbidden_tuning_fields:
            issues.append(ValidationIssue(
                "split_policy",
                "automatic_parameter_search_requires_nested_validation",
                "不允许动态参数搜索或自动优化器：" + "、".join(always_forbidden_tuning_fields),
            ))
        if parameter_selection_mode == "fixed_frozen":
            fixed_mode_extras = sorted(
                field_name
                for field_name in ("parameter_candidates", "optimization_objective")
                if field_name in self.split_policy
            )
            if fixed_mode_extras:
                issues.append(ValidationIssue(
                    "split_policy",
                    "automatic_parameter_search_requires_nested_validation",
                    "冻结单一参数模式不能携带候选或优化目标：" + "、".join(fixed_mode_extras),
                ))
        if parameter_selection_mode in {
            "nested_walk_forward_daily_v1",
            "nested_walk_forward_session_v1",
        }:
            candidates = self.split_policy.get("parameter_candidates")
            if not isinstance(candidates, (list, tuple)) or not 2 <= len(candidates) <= 20:
                issues.append(ValidationIssue(
                    "split_policy.parameter_candidates",
                    "invalid_parameter_candidate_manifest",
                    "嵌套验证必须在运行前冻结 2 到 20 个参数候选",
                ))
            if self.split_policy.get("optimization_objective") != "validation_mean_excess_return":
                issues.append(ValidationIssue(
                    "split_policy.optimization_objective",
                    "unsupported_nested_selection_objective",
                    "嵌套验证只允许按验证集平均超额收益选择参数",
                ))
        if not 0.0 < self.alpha < 1.0 or not 0.0 < self.confidence_level < 1.0:
            issues.append(ValidationIssue("statistics", "invalid_probability", "统计概率参数超出范围"))
        if self.max_experiments < 1:
            issues.append(ValidationIssue("max_experiments", "invalid_budget", "实验预算必须大于零"))
        if not self.metrics:
            issues.append(ValidationIssue("metrics", "missing_metrics", "冻结前必须选择评价指标"))
        unknown_stress_tests = sorted(set(self.stress_tests) - SUPPORTED_STRESS_TESTS)
        if unknown_stress_tests:
            issues.append(ValidationIssue(
                "stress_tests",
                "unsupported_stress_test",
                "不支持的压力测试：" + "、".join(unknown_stress_tests),
            ))
        if len(set(self.stress_tests)) != len(self.stress_tests):
            issues.append(ValidationIssue("stress_tests", "duplicate_stress_test", "同一压力测试不能重复登记"))
        if self.reference_universe:
            if len(self.reference_universe) < 5:
                issues.append(ValidationIssue("reference_universe", "reference_universe_too_small", "样本选择反证的参考池至少需要5只股票"))
            if len(set(self.reference_universe)) != len(self.reference_universe):
                issues.append(ValidationIssue("reference_universe", "duplicate_reference_symbol", "参考池证券不能重复"))
            invalid_reference = [value for value in self.reference_universe if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", value)]
            if invalid_reference:
                issues.append(ValidationIssue("reference_universe", "invalid_reference_symbol", "参考池证券必须包含交易所后缀"))
        elif "sample_selection_bias" in self.stress_tests:
            issues.append(ValidationIssue(
                "reference_universe", "missing_pre_registered_reference_universe",
                "执行样本选择反证前必须冻结至少5只股票的参考池",
            ))
        mode = str(self.split_policy.get("mode", ""))
        if mode not in {"rolling", "expanding"}:
            issues.append(ValidationIssue("split_policy.mode", "invalid_walk_forward_mode", "必须使用滚动或扩展式 walk-forward"))
        for field_name in ("observation_count", "train_size", "validation_size", "test_size"):
            try:
                value = int(self.split_policy.get(field_name, 0))
            except (TypeError, ValueError):
                value = 0
            if value <= 0:
                issues.append(ValidationIssue(f"split_policy.{field_name}", "missing_split_size", "实验切分必须预先给出正整数样本数"))
        if self.purge_periods <= 0:
            issues.append(ValidationIssue("purge_periods", "purge_required", "必须按标签或持有期设置 purge，禁止设为零"))
        if self.embargo_periods <= 0:
            issues.append(ValidationIssue("embargo_periods", "embargo_required", "必须设置 embargo，禁止设为零"))
        return issues

    def freeze(self, at: str | None = None) -> "ExperimentProtocol":
        if self.validate():
            raise ValueError("ExperimentProtocol cannot be frozen while validation errors remain")
        payload = asdict(self)
        payload["frozen_at"] = at or datetime.now(UTC).isoformat()
        return ExperimentProtocol.from_dict(payload)

    def content_hash(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class RunManifest:
    run_id: str
    workspace_id: str
    strategy_hash: str
    protocol_hash: str
    data_snapshot_hash: str
    code_version: str
    rules_version: str
    cost_model_hash: str
    plugin_hashes: Mapping[str, str]
    random_seed: int
    frequency: str
    started_at: str
    status: str
    research_only: bool = True
    live_order_path: str = "forbidden"
    data_snapshot_schema: str = "strategy_center_market_snapshot_v1"
    data_source_refs: tuple[str, ...] = ()
    hypothesis_family_id: str = ""
    hypothesis_family_size: int = 1
    hypothesis_attempt: int = 1
    hypothesis_attempt_registry_hash: str = ""
    test_set_fingerprint: str = ""
    test_set_exposure_hash: str = ""
    test_set_independence_verified: bool = False
    parameter_selection_mode: str = "fixed_frozen"
    parameter_candidate_manifest_hash: str = ""
    parameter_selection_trace_hash: str = ""
    parameter_selection_stress_trace_hash: str = ""
    window_readiness_hash: str = ""


@dataclass(frozen=True, slots=True)
class DiagnosticClaim:
    claim_id: str
    category: str
    statement: str
    evidence_refs: tuple[str, ...]
    counter_evidence_refs: tuple[str, ...]
    confidence: float
    next_test: str
    status: str = "supported_hypothesis"


@dataclass(frozen=True, slots=True)
class EvidencePack:
    run_id: str
    level: EvidenceLevel
    summary: str
    metrics: Mapping[str, Any]
    confidence_intervals: Mapping[str, tuple[float | None, float | None]]
    corrected_p_values: Mapping[str, float | None]
    robustness: Mapping[str, Any]
    limitations: tuple[str, ...]
    diagnostic_claims: tuple[DiagnosticClaim, ...]
    artifact_refs: tuple[str, ...]
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def semantic_payload(self) -> dict[str, Any]:
        semantic_metrics = dict(self.metrics)
        semantic_metrics.pop("test_set_exposure_hash", None)
        return {
            "schema_version": "evidence_semantics_v1",
            "level": int(self.level),
            "summary": self.summary,
            "metrics": semantic_metrics,
            "confidence_intervals": dict(self.confidence_intervals),
            "corrected_p_values": dict(self.corrected_p_values),
            "robustness": dict(self.robustness),
            "limitations": list(self.limitations),
            "diagnostic_claims": [
                {
                    "claim_id": claim.claim_id,
                    "category": claim.category,
                    "statement": claim.statement,
                    "confidence": claim.confidence,
                    "next_test": claim.next_test,
                    "status": claim.status,
                }
                for claim in self.diagnostic_claims
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["level"] = int(self.level)
        payload["level_label"] = self.level.label_zh
        payload["evidence_hash"] = canonical_hash(self.semantic_payload())
        payload["evidence_hash_scope"] = "semantic_result_excludes_run_id_timestamps_and_run_specific_refs"
        return payload


@dataclass(frozen=True, slots=True)
class AiTrace:
    trace_id: str
    provider: str
    model: str
    prompt_template_version: str
    input_hash: str
    output_hash: str
    started_at: str
    duration_ms: int
    cost: float | None
    human_confirmed: bool
    mode: str = "manual_bridge"


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


__all__ = [
    "AiTrace",
    "AnnotationSpec",
    "DiagnosticClaim",
    "EvidenceLevel",
    "EvidencePack",
    "ExperimentProtocol",
    "Frequency",
    "RuleSpec",
    "RunManifest",
    "StrategyIntentDraft",
    "StrategySpec",
    "ValidationIssue",
    "canonical_hash",
    "canonical_json",
]
