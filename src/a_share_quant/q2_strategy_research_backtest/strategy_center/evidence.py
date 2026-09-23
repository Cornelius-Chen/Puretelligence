"""Grounded evidence packs and deterministic failure diagnostics."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts import DiagnosticClaim, EvidenceLevel, EvidencePack, canonical_hash


def _claim(
    category: str,
    statement: str,
    refs: Sequence[str],
    next_test: str,
    confidence: float,
    *,
    status: str = "supported_hypothesis",
    counter_refs: Sequence[str] = (),
) -> DiagnosticClaim:
    identity = {
        "category": category,
        "statement": statement,
        "confidence": confidence,
        "next_test": next_test,
        "status": status,
    }
    return DiagnosticClaim(
        claim_id=f"claim_{canonical_hash(identity)[:16]}",
        category=category,
        statement=statement,
        evidence_refs=tuple(refs),
        counter_evidence_refs=tuple(counter_refs),
        confidence=confidence,
        next_test=next_test,
        status=status,
    )


def diagnose(
    metrics: Mapping[str, Any],
    robustness: Mapping[str, Any],
    *,
    evidence_ref: str,
) -> tuple[DiagnosticClaim, ...]:
    claims: list[DiagnosticClaim] = []
    gross_profit = metrics.get("gross_profit")
    if gross_profit is not None and float(metrics.get("fees", 0.0) or 0.0) > max(0.0, float(gross_profit)):
        claims.append(_claim(
            "cost_erosion", "交易成本超过毛收益，策略优势可能被执行成本吞噬。",
            [f"{evidence_ref}#metrics.fees", f"{evidence_ref}#metrics.gross_profit"],
            "运行成本减半与加倍压力测试", 0.95,
            counter_refs=[f"{evidence_ref}#metrics.total_return"],
        ))
    baseline_return = metrics.get("total_return")
    stress_tests = robustness.get("stress_tests")
    if isinstance(baseline_return, (int, float)) and float(baseline_return) > 0 and isinstance(stress_tests, Mapping):
        cost_stress = stress_tests.get("cost_x2")
        if isinstance(cost_stress, Mapping) and isinstance(cost_stress.get("total_return"), (int, float)):
            stressed = float(cost_stress["total_return"])
            if stressed <= 0 or stressed / float(baseline_return) < 0.5:
                claims.append(_claim(
                    "cost_erosion",
                    "成本与滑点翻倍后收益转负或保留不足一半，策略对执行成本敏感。",
                    [f"{evidence_ref}#robustness.stress_tests.cost_x2"],
                    "检查低换手版本并复核容量假设",
                    1.0,
                    status="deterministic_finding",
                    counter_refs=[f"{evidence_ref}#metrics.total_return"],
                ))
        delay_stress = stress_tests.get("delay")
        if isinstance(delay_stress, Mapping) and isinstance(delay_stress.get("total_return"), (int, float)):
            stressed = float(delay_stress["total_return"])
            if stressed <= 0 or stressed / float(baseline_return) < 0.5:
                claims.append(_claim(
                    "delay_sensitivity",
                    "增加一个执行事件的延迟后收益转负或保留不足一半，优势依赖及时成交。",
                    [f"{evidence_ref}#robustness.stress_tests.delay"],
                    "扩大延迟梯度并按流动性分层复核",
                    1.0,
                    status="deterministic_finding",
                    counter_refs=[f"{evidence_ref}#metrics.total_return"],
                ))
    uses_excess = metrics.get("inference_basis") in {
        "benchmark_excess_return",
        "nested_selected_benchmark_excess_return",
    }
    corrected_p = metrics.get("corrected_excess_return_p") if uses_excess else metrics.get("corrected_mean_return_p")
    oos_mean = metrics.get("oos_mean_excess_return") if uses_excess else metrics.get("oos_mean_return")
    minimum_effect = metrics.get("minimum_effect")
    if corrected_p is not None and oos_mean is not None and minimum_effect is not None:
        if float(corrected_p) > float(metrics.get("alpha", 0.05)) or float(oos_mean) < float(minimum_effect):
            claims.append(_claim(
                "effect_not_established",
                "样本外超额收益未同时达到预注册的统计显著性和最小经济效应门槛。" if uses_excess else "样本外收益未同时达到预注册的统计显著性和最小经济效应门槛。",
                [
                    f"{evidence_ref}#metrics.corrected_excess_return_p" if uses_excess else f"{evidence_ref}#metrics.corrected_mean_return_p",
                    f"{evidence_ref}#metrics.oos_mean_excess_return" if uses_excess else f"{evidence_ref}#metrics.oos_mean_return",
                    f"{evidence_ref}#metrics.minimum_effect",
                ],
                "扩大冻结样本，或在不触碰测试集的前提下重新预注册假设",
                1.0,
                status="deterministic_finding",
                counter_refs=[f"{evidence_ref}#confidence_intervals"],
            ))
    excess_total = metrics.get("excess_total_return")
    if uses_excess and isinstance(excess_total, (int, float)) and float(excess_total) < 0:
        claims.append(_claim(
            "benchmark_underperformance",
            "策略累计收益低于冻结基准，当前绝对收益不能视为独立策略优势。",
            [f"{evidence_ref}#metrics.excess_total_return"],
            "按同一时间窗复核暴露，并检验超额收益而非绝对收益",
            1.0,
            status="deterministic_finding",
            counter_refs=[f"{evidence_ref}#metrics.total_return"],
        ))
    if float(metrics.get("max_drawdown", 0.0) or 0.0) > 0.25:
        claims.append(_claim(
            "risk_concentration", "最大回撤偏高，需要检查时段和持仓暴露是否集中。",
            [f"{evidence_ref}#metrics.max_drawdown"], "按市场状态和行业拆分回撤贡献", 0.85,
            counter_refs=[f"{evidence_ref}#metrics.total_return"],
        ))
    parameter_score = robustness.get("parameter_stability")
    if isinstance(parameter_score, (int, float)) and float(parameter_score) < 0.6:
        claims.append(_claim(
            "parameter_fragility", "邻近参数表现变化较大，当前结果可能依赖窄参数点。",
            [f"{evidence_ref}#robustness.parameter_stability"], "扩大参数邻域并冻结中心区域", 0.9,
            counter_refs=[f"{evidence_ref}#robustness.stress_tests.parameter_neighborhood"],
        ))
    period_score = robustness.get("period_consistency")
    if isinstance(period_score, (int, float)) and float(period_score) < 0.6:
        claims.append(_claim(
            "period_dependence", "收益主要集中在少数时期，跨时段一致性不足。",
            [f"{evidence_ref}#robustness.period_consistency"], "执行滚动样本外与市场状态分层", 0.9,
            counter_refs=[f"{evidence_ref}#robustness.stress_tests.period_consistency"],
        ))
    regime_score = robustness.get("market_regime_consistency")
    if isinstance(regime_score, (int, float)) and float(regime_score) < 0.6:
        claims.append(_claim(
            "regime_dependence", "超额收益无法同时覆盖上涨与下跌基准环境，策略存在市场状态依赖。",
            [f"{evidence_ref}#robustness.market_regime_consistency"], "分别扩大上涨与下跌市场的冻结样本", 0.9,
            counter_refs=[f"{evidence_ref}#robustness.stress_tests.market_regime"],
        ))
    data_score = robustness.get("data_perturbation_retention")
    if isinstance(data_score, (int, float)) and float(data_score) < 0.6:
        claims.append(_claim(
            "data_fragility",
            "确定性移除少量内部行情观察后结果明显变化，策略对数据完整性较脆弱。",
            [f"{evidence_ref}#robustness.data_perturbation_retention"],
            "扩大缺失比例梯度并按交易日与标的定位敏感来源",
            1.0,
            status="deterministic_finding",
            counter_refs=[f"{evidence_ref}#robustness.stress_tests.data_perturbation_matrix"],
        ))
    factor_score = robustness.get("factor_ablation_retention")
    factor_stress = stress_tests.get("factor_ablation") if isinstance(stress_tests, Mapping) else None
    failed_ablation_labels: list[str] = []
    if (
        isinstance(factor_score, (int, float))
        and isinstance(baseline_return, (int, float))
        and float(baseline_return) > 0
        and isinstance(factor_stress, Mapping)
        and factor_stress.get("status") == "executed"
        and factor_stress.get("score_mode") in {
            "positive_economic_retention",
            "positive_return_and_drawdown_retention",
        }
    ):
        variants = factor_stress.get("variants")
        if isinstance(variants, Sequence) and not isinstance(variants, (str, bytes)):
            failed_ablation_labels = [
                (
                    ("入场" if row.get("removed_rule_side") == "entry" else "退出")
                    + "："
                    + str(row.get("removed_rule_label") or row.get("removed_rule_id"))
                )
                if row.get("removed_rule_side") in {"entry", "exit"}
                else str(row.get("removed_rule_label") or row.get("removed_rule_id"))
                for row in variants
                if isinstance(row, Mapping) and row.get("retention_passed") is False
            ]
    if failed_ablation_labels:
        labels = "、".join(failed_ablation_labels[:3])
        overflow = len(failed_ablation_labels) - 3
        if overflow > 0:
            labels += f"等 {overflow + 3} 条"
        claims.append(_claim(
            "factor_dependency",
            f"移除「{labels}」后，收益保留或回撤约束未通过，策略结果依赖这些规则。",
            [f"{evidence_ref}#robustness.stress_tests.factor_ablation"],
            "分别替换脆弱的入场、退出或规则族定义，并在新冻结样本上复验",
            1.0,
            status="deterministic_finding",
            counter_refs=[f"{evidence_ref}#metrics.total_return"],
        ))
    frequency_score = robustness.get("frequency_consistency_retention")
    frequency_stress = stress_tests.get("frequency_consistency") if isinstance(stress_tests, Mapping) else None
    if (
        isinstance(frequency_score, (int, float))
        and float(frequency_score) < 0.6
        and isinstance(frequency_stress, Mapping)
        and frequency_stress.get("status") == "executed"
    ):
        variants = frequency_stress.get("variants")
        failed_frequencies: list[str] = []
        observed_sources: set[str] = set()
        if isinstance(variants, Sequence) and not isinstance(variants, (str, bytes)):
            for row in variants:
                if not isinstance(row, Mapping) or row.get("retention_passed") is not False:
                    continue
                failed_frequencies.append(str(row.get("target_frequency")))
                sources = row.get("difference_attribution")
                if isinstance(sources, Sequence) and not isinstance(sources, (str, bytes)):
                    observed_sources.update(str(source) for source in sources)
        if failed_frequencies:
            source_labels = {
                "signal_clock_and_bar_aggregation": "信号时钟与K线聚合",
                "execution_opportunity_and_liquidity": "成交机会与流动性",
                "turnover_and_cost_path": "换手与成本路径",
                "mark_to_market_timing": "估值时点",
                "validation_parameter_selection_changed": "验证区间选出的参数发生变化",
            }
            sources = "、".join(source_labels.get(source, source) for source in sorted(observed_sources))
            claims.append(_claim(
                "frequency_dependence",
                f"同一1分钟底座聚合到 {'、'.join(failed_frequencies)} 后，收益或回撤约束未保留；观测差异涉及{sources or '执行路径'}。",
                [f"{evidence_ref}#robustness.stress_tests.frequency_consistency"],
                "按目标持有时长重新换算各频率参数，并在冻结的同源窗口复验",
                1.0,
                status="deterministic_finding",
                counter_refs=[f"{evidence_ref}#metrics.total_return"],
            ))
    exposure = robustness.get("exposure_concentration")
    if isinstance(exposure, Mapping) and exposure.get("status") == "assessed" and exposure.get("finding") is True:
        dimensions = [
            ("标的", exposure.get("symbol")),
            ("行业", exposure.get("industry")),
            ("市值层", exposure.get("size_bucket")),
        ]
        observed = [
            f"{label}「{row.get('top_name')}」{float(row.get('top_share', 0.0)):.0%}"
            for label, row in dimensions
            if isinstance(row, Mapping) and float(row.get("top_share", 0.0) or 0.0) > 0
        ]
        pnl = exposure.get("gross_mark_to_market_pnl_attribution")
        pnl_observed: list[str] = []
        if isinstance(pnl, Mapping) and pnl.get("status") == "assessed":
            pnl_dimensions = [
                ("标的", pnl.get("symbol")),
                ("行业", pnl.get("industry")),
                ("市值层", pnl.get("size_bucket")),
            ]
            pnl_observed = [
                f"{label}「{row.get('top_name')}」{float(row.get('top_share', 0.0)):.0%}"
                for label, row in pnl_dimensions
                if isinstance(row, Mapping) and float(row.get("top_share", 0.0) or 0.0) > 0
            ]
        pnl_sentence = (
            "；持仓期间毛盯市损益绝对贡献为" + "、".join(pnl_observed)
            if pnl_observed
            else "；持仓期间损益贡献尚不足以评估，不能据此断言收益来源"
        )
        claims.append(_claim(
            "exposure_concentration",
            "持仓市值时间加权暴露明显集中：" + "、".join(observed) + pnl_sentence + "。",
            [f"{evidence_ref}#robustness.exposure_concentration"],
            "冻结行业与单一标的上限后重跑，并比较超额收益与最大回撤",
            1.0,
            status="deterministic_finding",
            counter_refs=[f"{evidence_ref}#robustness.exposure_concentration.metadata_value_coverage_rate"],
        ))
    selection = robustness.get("sample_selection_bias")
    if isinstance(selection, Mapping) and selection.get("status") == "assessed" and selection.get("finding") is True:
        claims.append(_claim(
            "sample_selection_bias",
            "冻结策略池相对预注册参考池的行业或市值分布偏移超过阈值，当前结果可能受样本选择影响。",
            [f"{evidence_ref}#robustness.sample_selection_bias"],
            "在预注册参考池内进行分层抽样或全池复验，不得用回测后赢家重新定义样本",
            1.0,
            status="deterministic_finding",
            counter_refs=[f"{evidence_ref}#robustness.sample_selection_bias.reference_universe"],
        ))
    if not claims:
        claims.append(_claim(
            "no_dominant_failure", "当前确定性检查未定位单一主因，结论仍需更多样本验证。",
            [f"{evidence_ref}#robustness"], "继续冻结影子观察并扩大反证集", 0.55,
            counter_refs=[f"{evidence_ref}#limitations"],
        ))
    return tuple(claims)


def diagnostic_coverage(
    metrics: Mapping[str, Any],
    robustness: Mapping[str, Any],
    claims: Sequence[DiagnosticClaim],
) -> dict[str, Any]:
    stress_tests = robustness.get("stress_tests") if isinstance(robustness.get("stress_tests"), Mapping) else {}
    categories = {claim.category for claim in claims}

    def row(
        category: str,
        evidence_paths: Sequence[str],
        *,
        supported: bool,
        reason: str,
    ) -> dict[str, Any]:
        finding_categories = (
            sorted(category_name for category_name in categories if category_name in COVERAGE_FINDINGS[category])
            if supported
            else []
        )
        return {
            "category": category,
            "status": "assessed" if supported else "not_assessed",
            "evidence_paths": list(evidence_paths),
            "finding_categories": finding_categories,
            "reason": reason,
            "ai_may_assert_cause": supported,
        }

    cost_supported = any(
        isinstance(stress_tests.get(name), Mapping) and stress_tests[name].get("status") == "executed"
        for name in ("cost_x2", "delay")
    ) or all(isinstance(metrics.get(name), (int, float)) for name in ("fees", "gross_profit"))
    parameter_supported = isinstance(robustness.get("parameter_stability"), (int, float))
    period_supported = isinstance(robustness.get("period_consistency"), (int, float))
    regime_supported = isinstance(robustness.get("market_regime_consistency"), (int, float))
    data_supported = isinstance(robustness.get("data_perturbation_retention"), (int, float))
    factor_supported = isinstance(robustness.get("factor_ablation_retention"), (int, float))
    frequency_supported = isinstance(robustness.get("frequency_consistency_retention"), (int, float))
    leakage_supported = bool(metrics.get("leakage_safety_verified")) and bool(
        metrics.get("test_set_independence_verified")
    )
    exposure = robustness.get("exposure_concentration")
    selection = robustness.get("sample_selection_bias")
    exposure_supported = isinstance(exposure, Mapping) and exposure.get("status") == "assessed"
    selection_supported = isinstance(selection, Mapping) and selection.get("status") == "assessed"

    rows = [
        row("cost_and_execution", ("metrics.fees", "metrics.gross_profit", "robustness.stress_tests.cost_x2", "robustness.stress_tests.delay"), supported=cost_supported, reason="cost_or_delay_evidence_available" if cost_supported else "cost_and_delay_stress_not_executed"),
        row("parameter", ("robustness.parameter_stability", "robustness.stress_tests.parameter_neighborhood"), supported=parameter_supported, reason="parameter_neighborhood_assessed" if parameter_supported else "parameter_neighborhood_not_assessed"),
        row("time_period", ("robustness.period_consistency", "robustness.stress_tests.period_consistency"), supported=period_supported, reason="period_consistency_assessed" if period_supported else "period_consistency_not_assessed"),
        row("market_regime", ("robustness.market_regime_consistency", "robustness.stress_tests.market_regime"), supported=regime_supported, reason="market_regime_assessed" if regime_supported else "market_regime_not_assessed"),
        row("data_quality", ("robustness.data_perturbation_retention", "robustness.stress_tests.data_perturbation_matrix"), supported=data_supported, reason="data_perturbation_assessed" if data_supported else "data_perturbation_not_assessed"),
        row("factor_or_rule", ("robustness.factor_ablation_retention", "robustness.stress_tests.factor_ablation"), supported=factor_supported, reason="factor_ablation_assessed" if factor_supported else "factor_ablation_not_assessed"),
        row("frequency", ("robustness.frequency_consistency_retention", "robustness.stress_tests.frequency_consistency"), supported=frequency_supported, reason="frequency_consistency_assessed" if frequency_supported else "frequency_consistency_not_assessed"),
        row("exposure_concentration", ("robustness.exposure_concentration",), supported=exposure_supported, reason="exposure_attribution_available" if exposure_supported else "industry_size_and_symbol_exposure_attribution_not_available"),
        row("leakage", ("metrics.purge_embargo_safety_verified", "metrics.test_set_independence_verified", "metrics.walk_forward_fold_count"), supported=leakage_supported, reason="purge_embargo_and_test_set_independence_verified" if leakage_supported else "test_set_independence_or_leakage_safety_not_verified"),
        row("sample_selection_bias", ("robustness.sample_selection_bias",), supported=selection_supported, reason="sample_selection_counterfactual_available" if selection_supported else "sample_selection_counterfactual_not_available"),
    ]
    payload = {
        "schema_version": "diagnostic_coverage_v1",
        "rows": rows,
        "assessed_count": sum(item["status"] == "assessed" for item in rows),
        "total_count": len(rows),
        "policy": "not_assessed_categories_must_not_be_asserted_as_causes",
    }
    return payload | {"content_hash": canonical_hash(payload)}


COVERAGE_FINDINGS = {
    "cost_and_execution": {"cost_erosion", "delay_sensitivity"},
    "parameter": {"parameter_fragility"},
    "time_period": {"period_dependence"},
    "market_regime": {"regime_dependence"},
    "data_quality": {"data_fragility"},
    "factor_or_rule": {"factor_dependency"},
    "frequency": {"frequency_dependence"},
    "exposure_concentration": {"risk_concentration", "exposure_concentration"},
    "leakage": {"leakage_detected"},
    "sample_selection_bias": {"sample_selection_bias"},
}


def build_evidence_pack(
    *,
    run_id: str,
    level: EvidenceLevel,
    metrics: Mapping[str, Any],
    confidence_intervals: Mapping[str, tuple[float | None, float | None]],
    corrected_p_values: Mapping[str, float | None],
    robustness: Mapping[str, Any],
    limitations: Sequence[str],
    artifact_refs: Sequence[str],
) -> EvidencePack:
    primary_ref = artifact_refs[0] if artifact_refs else f"run:{run_id}"
    claims = diagnose(metrics, robustness, evidence_ref=primary_ref)
    coverage = diagnostic_coverage(metrics, robustness, claims)
    if level <= EvidenceLevel.EXECUTABLE:
        summary = "策略可以执行，但统计样本或数据证据不足，不能判断有效。"
    elif level == EvidenceLevel.INITIAL_OOS:
        summary = "样本外出现初步证据，但尚未同时通过统计校正和稳健性检验。"
    elif level == EvidenceLevel.ROBUST:
        summary = "多重检验校正后仍有证据，并通过已声明的稳健性检查。"
    elif level == EvidenceLevel.SHADOW_READY:
        summary = "历史证据已满足进入冻结影子验证的条件，尚未完成未来样本确认。"
    elif level == EvidenceLevel.SHADOW_VALIDATED:
        summary = "冻结影子样本已成熟并通过门槛；结论仍受声明限制约束。"
    else:
        summary = "当前仍处于想法阶段。"
    return EvidencePack(
        run_id=run_id,
        level=level,
        summary=summary,
        metrics=dict(metrics),
        confidence_intervals=dict(confidence_intervals),
        corrected_p_values=dict(corrected_p_values),
        robustness=dict(robustness) | {"diagnostic_coverage": coverage},
        limitations=tuple(limitations),
        diagnostic_claims=claims,
        artifact_refs=tuple(artifact_refs),
    )


__all__ = ["build_evidence_pack", "diagnose", "diagnostic_coverage"]
