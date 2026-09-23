from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from a_share_quant.shared.contracts.market_models import BacktestResult, RunRecord


class RunRegistry:
    """Persist run metadata to a structured directory for later audit."""

    def __init__(self, runs_dir: Path, reports_dir: Path) -> None:
        self.runs_dir = runs_dir
        self.reports_dir = reports_dir

    def create_run(
        self,
        *,
        config_path: Path,
        protocol_version: str,
        run_type: str,
        strategy_family: str,
        notes: str = "",
    ) -> RunRecord:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}"
        metadata_path = self.runs_dir / f"{run_id}.json"
        record = RunRecord(
            run_id=run_id,
            run_type=run_type,
            protocol_version=protocol_version,
            strategy_family=strategy_family,
            config_path=config_path,
            metadata_path=metadata_path,
            notes=notes,
        )
        payload = {
            "run_id": run_id,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "run_type": run_type,
            "protocol_version": protocol_version,
            "strategy_family": strategy_family,
            "config_path": str(config_path),
            "notes": notes,
            "status": "started",
        }
        with metadata_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        return record

    def finalize_run(
        self,
        *,
        record: RunRecord,
        result: BacktestResult,
        report_path: Path,
        data_source: str,
        data_range: dict[str, str],
        config_paths: list[str],
        summary_override: dict[str, object] | None = None,
    ) -> None:
        payload = {
            "run_id": record.run_id,
            "completed_at_utc": datetime.now(UTC).isoformat(),
            "run_type": record.run_type,
            "protocol_version": record.protocol_version,
            "strategy_family": record.strategy_family,
            "config_path": str(record.config_path),
            "config_paths": config_paths,
            "report_path": str(report_path),
            "data_source": data_source,
            "data_range": data_range,
            "summary": summary_override or result.summary,
            "notes": record.notes,
            "status": "completed",
        }
        with record.metadata_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

