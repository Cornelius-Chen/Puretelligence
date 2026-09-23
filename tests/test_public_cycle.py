"""Public synthetic run exercises the selected original Q1–Q5 modules."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from a_share_quant.q2_strategy_research_backtest.strategy_center.market_snapshot import events_from_q1_snapshot


ROOT = Path(__file__).resolve().parents[1]


def test_research_cycle_from_empty_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "run_research_cycle.py"),
         "--output", str(tmp_path / "run")], check=True, capture_output=True, text=True,
    )
    report = json.loads(Path(json.loads(result.stdout)["result"]).read_text(encoding="utf-8"))
    assert report["q1_window_ready"]
    assert report["q1_information_visible"] == ["visible_note"]
    assert report["q1_information_excluded"] == ["late_note"]
    equity = report["q2_scenario_equity"]
    assert equity["hold_a"] > equity["switch_a_to_b"] > equity["sell_a_to_cash"]
    assert equity["buy_b_with_existing_cash"] > equity["hold_a"]
    assert "benchmark_underperformance" in report["q2_diagnostics"]
    assert report["q3_context_versions"] == [1, 2]
    assert report["q4_asset_state"] == "candidate"
    assert report["q4_live_use"] == "forbidden"
    assert report["q4_validation_status"] == "not_eligible"
    assert report["q4_training_observation_count"] == 0
    assert report["no_live_orders"]
    run_record = json.loads(Path(report["q5_run_record"]).read_text(encoding="utf-8"))
    assert run_record["status"] == "completed"
    assert run_record["data_source"] == "synthetic_fixture"


def test_q2_refuses_mutated_q1_snapshot(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "run_research_cycle.py"),
         "--output", str(tmp_path / "run")], check=True, capture_output=True, text=True,
    )
    output = Path(json.loads(result.stdout)["result"]).parent
    snapshot = json.loads((output / "q1" / "market_snapshot.json").read_text(encoding="utf-8"))
    snapshot["events"][0]["close"] = 999.0
    with pytest.raises(ValueError, match="events_hash_mismatch"):
        events_from_q1_snapshot(snapshot, requested_frequency="1d")
