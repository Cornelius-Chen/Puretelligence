from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    for candidate in (Path.cwd(), *Path.cwd().parents, Path(__file__).resolve(), *Path(__file__).resolve().parents):
        if (candidate / "AGENTS.md").exists() and (candidate / "CCOS").exists():
            return candidate
    raise RuntimeError("Unable to locate repository root.")


def _path(repo_root: Path, raw: str | Path) -> Path:
    path = Path(str(raw))
    return path if path.is_absolute() else repo_root / path


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON must decode to a mapping: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{datetime.now().strftime('%H%M%S%f')}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp_path.replace(path)
    return path


def _source_run_id(summary: dict[str, Any]) -> str:
    return str(summary.get("run_id") or "unknown_q2_run")


def build_manifest(source_summary_path: Path, *, run_id: str | None = None, output_root: Path | None = None) -> dict[str, Any]:
    repo_root = Path(output_root) if output_root is not None else _repo_root()
    summary = _read_json(source_summary_path)
    source_run_id = _source_run_id(summary)
    manifest_run_id = run_id or f"q4_model_candidate_from_{source_run_id}"
    out_dir = (
        repo_root
        / "QUANT_WAREHOUSE"
        / "storage"
        / "q4_structure_learning_training"
        / "model_training_candidates"
        / manifest_run_id
    )
    outputs = dict(summary.get("outputs", {}))
    model_summary_path = _path(repo_root, outputs.get("model_training_research_summary", ""))
    promotion_decision_path = _path(repo_root, outputs.get("promotion_decision", ""))
    lineage_path = _path(repo_root, outputs.get("input_lineage_trace", ""))
    factor_contract_path = _path(repo_root, outputs.get("factor_input_contract", ""))
    model_summary = _read_json(model_summary_path) if model_summary_path.exists() else {}
    promotion_decision = _read_json(promotion_decision_path) if promotion_decision_path.exists() else {}

    manifest_path = out_dir / "training_manifest.json"
    model_card_path = out_dir / "model_candidate_card.json"
    state = {
        "run_id": manifest_run_id,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "owner_domain": "Q4_structure_learning_training",
        "source_domain": "Q2_strategy_research_backtest",
        "asset_state": "candidate",
        "accepted_or_canonical": False,
        "live_use": "forbidden",
        "parent_data_refs": [
            str(source_summary_path),
            str(model_summary_path),
            str(promotion_decision_path),
            str(lineage_path),
            str(factor_contract_path),
        ],
        "parent_domain": "Q2_strategy_research_backtest",
        "packetizer_ref": "institutional_pit_alpha_factory_v2",
        "truth_source_ref": str(promotion_decision_path),
        "manifest_ref": str(manifest_path),
        "retrain_run_ref": "",
        "consumed_by": [],
        "writeback_target": "none_candidate_only",
        "validation_status": promotion_decision.get("decision", summary.get("decision", "unknown")),
        "model_id": model_summary.get("model_id", "unknown_model"),
        "feature_columns": model_summary.get("feature_columns", []),
        "training_observation_count": model_summary.get("training_observation_count", 0),
        "promotion_failures": promotion_decision.get("promotion_failures", summary.get("promotion_failures", [])),
        "notes": [
            "This is a governed Q4 candidate manifest, not a production model.",
            "No live trading path is created.",
            "Accepted/canonical promotion requires separate human and master-control approval.",
        ],
    }
    model_card = {
        "model_id": state["model_id"],
        "asset_state": "candidate",
        "source_q2_run_id": source_run_id,
        "coefficients": model_summary.get("coefficients", {}),
        "feature_columns": state["feature_columns"],
        "validation_status": state["validation_status"],
        "live_use": "forbidden",
    }
    _write_json(manifest_path, state)
    _write_json(model_card_path, model_card)
    state["outputs"] = {
        "training_manifest": str(manifest_path),
        "model_candidate_card": str(model_card_path),
    }
    _write_json(out_dir / "run_summary.json", state)
    return state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create Q4 candidate model manifest from a Q2 alpha-factory run.")
    parser.add_argument("--source-run-summary", required=True)
    parser.add_argument("--run-id", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = _repo_root()
    result = build_manifest(_path(repo_root, args.source_run_summary), run_id=args.run_id)
    print(json.dumps({"run_id": result["run_id"], "outputs": result["outputs"]}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
