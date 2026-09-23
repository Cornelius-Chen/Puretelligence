"""Synthetic Jervis version binding and new-material Quant research replay."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from ironman.mission.state import StateStore

from a_share_quant.q2_strategy_research_backtest.jervis_experience_ref import resolve_jervis_experience
from a_share_quant.q2_strategy_research_backtest.strategy_center.event_engine import EventEngineConfig, UnifiedEventEngine
from a_share_quant.q2_strategy_research_backtest.strategy_center.market_snapshot import events_from_q1_snapshot
from a_share_quant.q3_runtime_signal_serving.strategy_center.research_context import build_research_context
from run_research_cycle import signal, snapshot, synthetic_events, write_json


ROOT = Path(__file__).resolve().parents[1]
TRADER = "synthetic-continuing-trader"
EXPERIENCE_ID = "experience:synthetic-independent-decisions"
SOURCE_ID = "source:synthetic-decision-case"


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def run(output: Path):
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Choose an empty output directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    jervis_root = output / "jervis"
    for relative in ("schemas/ironman.schema.yaml", "control/LIFECYCLE_POLICY.yaml"):
        target = jervis_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "examples" / "jervis_contracts" / relative, target)
    store = StateStore(jervis_root, jervis_root / "registry.sqlite")
    try:
        source_version = store.save(SOURCE_ID, {"title": "Invented A/B comparison",
            "available_at": "2026-09-01T17:00:00+00:00", "source_kind": "synthetic_fixture"})
        source = {"object_id": SOURCE_ID, "version": source_version}
        k0 = {"title": "Tentative A/B research rule", "trader_id": TRADER, "scope": "q2/research",
              "available_at": "2026-09-02T08:00:00+00:00", "source_ref": source,
              "active": "Evaluate buying B against cash with the same cost assumptions.",
              "proposal": None, "review_decision": "experimental"}
        v0 = store.save(EXPERIENCE_ID, k0, provenance=(SOURCE_ID,))
        k1 = {**k0, "available_at": "2026-09-04T08:00:00+00:00",
              "active": "Evaluate selling A, buying B, and switching A into B separately; compare a switch with holding A.",
              "proposal": "Split sell, buy and switch into separate decision checks.",
              "review_decision": "experimental"}
        v1 = store.save(EXPERIENCE_ID, k1, expected=v0, provenance=(SOURCE_ID,))
        k2 = {**k1, "available_at": "2026-09-04T10:00:00+00:00",
              "proposal": "Always switch from A whenever B beats cash.", "review_decision": "no_update"}
        v2 = store.save(EXPERIENCE_ID, k2, expected=v1, provenance=(SOURCE_ID,))

        original = resolve_jervis_experience(store, object_id=EXPERIENCE_ID, version=v0,
            trader_id=TRADER, scope="q2/research", decision_cutoff=utc("2026-09-02T09:00:00"))
        revised = resolve_jervis_experience(store, object_id=EXPERIENCE_ID, version=v2,
            trader_id=TRADER, scope="q2/research", decision_cutoff=utc("2026-09-05T09:00:00"))
        middle = resolve_jervis_experience(store, object_id=EXPERIENCE_ID, version=v1,
            trader_id=TRADER, scope="q2/research", decision_cutoff=utc("2026-09-05T09:00:00"))
        if revised["active"] != middle["active"] or original["active"] == revised["active"]:
            raise AssertionError("Candidate version replay did not preserve the expected active text")
        write_json(output / "experience_versions.json", {
            "object_id": EXPERIENCE_ID, "versions": list(store.registry.list_versions(EXPERIENCE_ID)),
            "original": original, "revised": revised,
            "active_changed_between_k0_and_k1": original["active"] != middle["active"],
            "no_update_preserved_k1_active_in_k2": revised["active"] == middle["active"],
            "historic_trader_replay": False,
        })

        market, _ = synthetic_events([(4, 10.0, None), (5, 10.8, 20.0), (6, 10.4, 23.5)])
        q1 = snapshot(market, ("A", "B"))
        events = events_from_q1_snapshot(q1, requested_frequency="1d")
        context = build_research_context({
            "context_id": "synthetic-new-material", "workspace_id": "public-fixture", "owner_id": TRADER,
            "object": {"type": "portfolio", "id": "fictional-a-b", "label": "Fictional A/B retest"},
            "time_range": {"start": "2026-09-04T00:00:00", "end": "2026-09-06T23:59:59"},
            "frequency": "1d", "research_question": "Does switching from A into B beat holding A on this new invented path?",
            "evidence_refs": [q1["snapshot_hash"], revised["reference"]],
            "origin": {"surface": "synthetic_research"},
        })
        packet = {"mode": "SYNTHETIC_FIXED_SIGNAL_RETEST", "decision_cutoff": "2026-09-05T09:00:00+00:00",
                  "research_context": context, "resolved_experience": revised,
                  "use_notice": "The fixed fixture supplies signals; no model reads this packet or makes a new decision."}
        write_json(output / "decision_packet.json", packet)

        engine = UnifiedEventEngine(EventEngineConfig(initial_cash=100_000,
            maximum_participation=1.0, base_slippage_bps=0, impact_bps_at_max_participation=0))
        buy_a = signal("buy-a", 4, "A", "buy", 5_000)
        sell_a = signal("sell-a", 5, "A", "sell", 5_000)
        buy_b = signal("buy-b", 5, "B", "buy", 2_000)
        scenarios = {
            "hold_a": engine.run(events, [buy_a]),
            "sell_a_to_cash": engine.run(events, [buy_a, sell_a]),
            "switch_a_to_b": engine.run(events, [buy_a, sell_a, buy_b]),
            "buy_b_with_existing_cash": engine.run(events, [buy_a, buy_b]),
        }
        equity = {name: result.equity_curve[-1].equity for name, result in scenarios.items()}
        report = {"fixture": "new_invented_market_path", "experience_reference": revised["reference"],
                  "q1_snapshot_hash": q1["snapshot_hash"], "scenario_equity": equity,
                  "switch_beats_hold_a_in_this_fixture": equity["switch_a_to_b"] > equity["hold_a"],
                  "fixed_signals": True, "real_trader_retest": False, "live_orders": False,
                  "limitations": ["Invented market rows and fixed actions.",
                                  "No model judgment, unseen-material guarantee, profit or execution claim."]}
        write_json(output / "retest.json", report)
        print(json.dumps({"result": str(output / "retest.json"),
                          "experience": revised["reference"], "equity": equity}, ensure_ascii=False))
        return report
    finally:
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".demo" / "experience-bridge")
    run(parser.parse_args().output.resolve())
