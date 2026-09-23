"""The optional public bridge resolves an exact Jervis version before research use."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from ironman.mission.state import StateStore
from ironman.storage import ObjectNotFoundError

from a_share_quant.q2_strategy_research_backtest.jervis_experience_ref import resolve_jervis_experience


ROOT = Path(__file__).resolve().parents[1]
OBJECT_ID = "experience:synthetic-independent-decisions"


def test_exact_version_resolution_and_new_synthetic_retest(tmp_path):
    output = tmp_path / "bridge"
    subprocess.run([sys.executable, str(ROOT / "examples" / "run_experience_bridge.py"),
                    "--output", str(output)], check=True, capture_output=True, text=True)
    versions = json.loads((output / "experience_versions.json").read_text(encoding="utf-8"))
    packet = json.loads((output / "decision_packet.json").read_text(encoding="utf-8"))
    retest = json.loads((output / "retest.json").read_text(encoding="utf-8"))
    assert versions["versions"] == ["0.1.0", "0.2.0", "0.3.0"]
    assert versions["active_changed_between_k0_and_k1"]
    assert versions["no_update_preserved_k1_active_in_k2"]
    assert versions["original"]["active"] != versions["revised"]["active"]
    assert packet["resolved_experience"]["reference"] in packet["research_context"]["evidence_refs"]
    assert packet["mode"] == "SYNTHETIC_FIXED_SIGNAL_RETEST"
    assert retest["fixed_signals"] and not retest["real_trader_retest"] and not retest["live_orders"]
    assert retest["scenario_equity"]["switch_a_to_b"] > retest["scenario_equity"]["hold_a"]

    store = StateStore(output / "jervis", output / "jervis" / "registry.sqlite")
    from datetime import datetime, timezone
    early = datetime(2026, 9, 2, 9, tzinfo=timezone.utc)
    later = datetime(2026, 9, 5, 9, tzinfo=timezone.utc)
    def resolve(version, trader="synthetic-continuing-trader", scope="q2/research", cutoff=later):
        return resolve_jervis_experience(store, object_id=OBJECT_ID, version=version,
                                         trader_id=trader, scope=scope, decision_cutoff=cutoff)
    try:
        assert resolve("0.1.0", cutoff=early)["active"] == versions["original"]["active"]
        with pytest.raises(ValueError, match="experience_owner_or_scope_mismatch"):
            resolve("0.3.0", trader="another-trader")
        with pytest.raises(ValueError, match="experience_owner_or_scope_mismatch"):
            resolve("0.3.0", scope="another-scope")
        with pytest.raises(ValueError, match="experience_unavailable_at_decision"):
            resolve("0.3.0", cutoff=early)
        with pytest.raises(ObjectNotFoundError):
            resolve("9.9.9")
        payload = store.registry.get_version(OBJECT_ID, "0.3.0")
        Path(payload["content_ref"]).write_text("tampered", encoding="utf-8")
        with pytest.raises(ValueError, match="attachment integrity failure"):
            resolve("0.3.0")
    finally:
        store.close()
