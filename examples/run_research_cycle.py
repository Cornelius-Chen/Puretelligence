"""Synthetic Q1–Q5 contract exercise through selected original Guanlan modules."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from a_share_quant.q1_market_data_foundation.quality.strategy_center_snapshot_v1 import strategy_center_window_readiness
from a_share_quant.q2_strategy_research_backtest.strategy_center.contracts import canonical_hash
from a_share_quant.q2_strategy_research_backtest.strategy_center.evidence import diagnose
from a_share_quant.q2_strategy_research_backtest.strategy_center.event_engine import (
    EventEngineConfig, MarketEvent, SignalIntent, UnifiedEventEngine,
)
from a_share_quant.q2_strategy_research_backtest.strategy_center.market_snapshot import events_from_q1_snapshot
from a_share_quant.q3_runtime_signal_serving.strategy_center.research_context import (
    build_research_context, revise_research_context,
)
from a_share_quant.q4_structure_learning_training.model_training_candidate_manifest_v1 import build_manifest
from a_share_quant.q5_platform_control_registry.audit.run_registry import RunRegistry
from a_share_quant.shared.contracts.market_models import BacktestResult, EquityPoint, Fill


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def synthetic_events(prices=None):
    prices = prices or [(1, 10.0, None), (2, 11.0, 20.0), (3, 12.0, 20.5)]
    events = []
    for day, price_a, price_b in prices:
        for symbol, price in (("A", price_a), ("B", price_b)):
            if price is not None:
                events.append(MarketEvent(datetime(2026, 9, day, 15), symbol, "1d", "bar",
                                          open=price, high=price, low=price, close=price, volume=100_000))
    benchmark = [MarketEvent(datetime(2026, 9, day, 15), "INDEX", "1d", "bar",
                             open=100.0, high=100.0, low=100.0, close=100.0, volume=100_000)
                 for day, _, _ in prices]
    return events, benchmark


def snapshot(events, universe):
    rows = [{**asdict(event), "timestamp": event.timestamp.isoformat()} for event in events]
    dates = sorted({event.timestamp.date().isoformat() for event in events})
    payload = {
        "schema_version": "strategy_center_market_snapshot_v1",
        "owner_domain": "Q1_market_data_foundation",
        "requested_frequency": "1d", "data_window": {"start": dates[0], "end": dates[-1]},
        "universe": list(universe), "available_dates": dates,
        "events": rows, "events_hash": canonical_hash(rows), "event_count": len(rows),
        "source_members": [], "source_files": [], "research_only": True,
    }
    payload["snapshot_hash"] = canonical_hash(payload)
    return payload


def signal(identity, day, symbol, action, quantity):
    return SignalIntent(identity, datetime(2026, 9, day, 9), symbol, action, quantity)


def run(output: Path):
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Choose an empty output directory: {output}")
    output.mkdir(parents=True, exist_ok=True)

    market, benchmark = synthetic_events()
    q1_market, q1_benchmark = snapshot(market, ("A", "B")), snapshot(benchmark, ("INDEX",))
    readiness = strategy_center_window_readiness(q1_market, q1_benchmark)
    if not readiness["ready"]:
        raise AssertionError(readiness["blockers"])
    market_events = events_from_q1_snapshot(q1_market, requested_frequency="1d")
    write_json(output / "q1" / "market_snapshot.json", q1_market)
    write_json(output / "q1" / "window_readiness.json", readiness)

    # Publication fixture gate: an earlier event timestamp does not make a late
    # publication visible at the decision cutoff. This adapter is shown as new
    # fixture code; Q1's imported snapshot and readiness code remains original.
    cutoff = datetime.fromisoformat("2026-09-02T09:00:00")
    research_items = [
        {"id": "visible_note", "event_at": "2026-09-01T15:00:00", "available_at": "2026-09-01T17:00:00"},
        {"id": "late_note", "event_at": "2026-09-01T15:00:00", "available_at": "2026-09-02T10:00:00"},
    ]
    visible = [item for item in research_items if datetime.fromisoformat(item["available_at"]) <= cutoff]
    write_json(output / "q1" / "information_cutoff.json", {
        "cutoff": cutoff.isoformat(), "admitted": [item["id"] for item in visible],
        "excluded": [item["id"] for item in research_items if item not in visible],
        "rule": "available_at <= decision cutoff", "fixture_only": True,
    })

    engine = UnifiedEventEngine(EventEngineConfig(initial_cash=100_000,
                                maximum_participation=1.0, base_slippage_bps=0,
                                impact_bps_at_max_participation=0))
    buy_a = signal("buy-a", 1, "A", "buy", 5_000)
    sell_a = signal("sell-a", 2, "A", "sell", 5_000)
    buy_b = signal("buy-b", 2, "B", "buy", 2_000)
    scenarios = {
        "hold_a": engine.run(market_events, [buy_a]),
        "sell_a_to_cash": engine.run(market_events, [buy_a, sell_a]),
        "switch_a_to_b": engine.run(market_events, [buy_a, sell_a, buy_b]),
        "buy_b_with_existing_cash": engine.run(market_events, [buy_a, buy_b]),
    }
    equity = {name: result.equity_curve[-1].equity for name, result in scenarios.items()}
    if not (equity["hold_a"] > equity["switch_a_to_b"] > equity["sell_a_to_cash"]):
        raise AssertionError("synthetic decision contrast did not hold")
    write_json(output / "q2" / "scenario_results.json", {
        name: result.to_dict() for name, result in scenarios.items()})

    context = build_research_context({
        "context_id": "synthetic-switch-question", "workspace_id": "public-fixture", "owner_id": "example",
        "object": {"type": "portfolio", "id": "fictional-a-b", "label": "Fictional A/B comparison"},
        "time_range": {"start": "2026-09-01T00:00:00", "end": "2026-09-03T23:59:59"},
        "frequency": "1d", "research_question": "Does B beating cash justify selling A?",
        "evidence_refs": [q1_market["snapshot_hash"]], "origin": {"surface": "synthetic_research"},
    })
    revised = revise_research_context(context, {
        "expected_revision": 1,
        "research_question": "Compare hold A, sell A to cash, switch A to B, and buy B from existing cash separately.",
        "evidence_refs": [q1_market["snapshot_hash"], "q2:synthetic:scenario_results"],
    })
    write_json(output / "q3" / "research_context_versions.json", [context, revised])

    metrics = {
        "inference_basis": "benchmark_excess_return",
        "total_return": equity["switch_a_to_b"] / 100_000 - 1,
        "excess_total_return": (equity["switch_a_to_b"] - equity["hold_a"]) / 100_000,
    }
    claims = diagnose(metrics, {}, evidence_ref="q2:synthetic:scenario_results")
    write_json(output / "q2" / "diagnosis.json", [asdict(claim) for claim in claims])

    # Q4 receives a candidate-only handoff. No model is trained or promoted.
    write_json(output / "q2" / "model_summary.json", {
        "model_id": "no_model_trained", "feature_columns": [], "training_observation_count": 0})
    write_json(output / "q2" / "promotion.json", {
        "decision": "not_eligible", "promotion_failures": ["no_training_or_external_validation"]})
    write_json(output / "q2" / "lineage.json", {"source": "synthetic_Q1_snapshot"})
    write_json(output / "q2" / "factor_contract.json", {"status": "not_applicable"})
    source_summary = write_json(output / "q2" / "run_summary.json", {
        "run_id": "synthetic_ab_comparison", "decision": "not_eligible",
        "outputs": {"model_training_research_summary": "q2/model_summary.json",
                    "promotion_decision": "q2/promotion.json",
                    "input_lineage_trace": "q2/lineage.json",
                    "factor_input_contract": "q2/factor_contract.json"},
    })
    q4 = build_manifest(source_summary, run_id="synthetic_candidate_only", output_root=output)

    config = write_json(output / "q5" / "config.json", {"source": "synthetic", "live_orders": False})
    registry = RunRegistry(output / "q5" / "runs", output / "q5" / "reports")
    record = registry.create_run(config_path=config, protocol_version="public_synthetic_v1",
                                 run_type="research_replay", strategy_family="none_decision_comparison")
    selected = scenarios["switch_a_to_b"]
    backtest = BacktestResult(
        fills=[Fill(item.timestamp.date(), item.symbol, item.action, item.filled_quantity, item.price, item.fees)
               for item in selected.fills], closed_trades=[],
        equity_curve=[EquityPoint(item.timestamp.date(), item.equity, item.cash)
                      for item in selected.equity_curve], rejected_signals=[],
        summary={"final_equity": equity["switch_a_to_b"]},
    )
    registry.finalize_run(record=record, result=backtest,
                          report_path=output / "result.json", data_source="synthetic_fixture",
                          data_range={"start": "2026-09-01", "end": "2026-09-03"},
                          config_paths=[str(config)], summary_override={"equity": equity, "research_only": True})

    report = {
        "mode": "SYNTHETIC_MARKET_RESEARCH_ONLY", "market_snapshot": q1_market["snapshot_hash"],
        "q1_window_ready": readiness["ready"], "q1_information_visible": [item["id"] for item in visible],
        "q1_information_excluded": [item["id"] for item in research_items if item not in visible],
        "q2_scenario_equity": equity, "q2_diagnostics": [claim.category for claim in claims],
        "q3_context_versions": [context["revision"], revised["revision"]],
        "q4_asset_state": q4["asset_state"], "q4_live_use": q4["live_use"],
        "q4_validation_status": q4["validation_status"],
        "q4_training_observation_count": q4["training_observation_count"],
        "q5_run_record": str(record.metadata_path),
        "no_live_orders": True,
        "limitations": ["All market rows are invented.", "Fills are deterministic proxies, not actual executions.",
                        "Same fixture is used for comparison and diagnostic; no out-of-sample claim.",
                        "Q4 did not train a model.", "Jervis historical trader experience is described separately, not replayed here."],
    }
    path = write_json(output / "result.json", report)
    print(json.dumps({"result": str(path), "equity": equity, "q4": q4["asset_state"],
                      "q5": str(record.metadata_path)}, ensure_ascii=False))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / ".demo" / "research")
    run(parser.parse_args().output.resolve())
