"""Versioned research context carried across Guanlan product surfaces.

The context is Q3 interaction state.  It references market, evidence, and
strategy objects owned by other domains without copying or changing their
semantic truth.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence
import uuid

from a_share_quant.q2_strategy_research_backtest.strategy_center.contracts import canonical_hash


SCHEMA_VERSION = "guanlan_research_context_v1"
OBJECT_TYPES = {"market", "sector", "theme", "instrument", "event", "portfolio"}
FREQUENCIES = {"1d", "1m", "5m", "15m", "30m", "60m", "tick_l1"}
STATUSES = {"active", "bookmarked", "completed", "archived"}
EDITABLE_FIELDS = {
    "object", "time_range", "frequency", "phenomenon", "research_question",
    "evidence_refs", "status",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _required_text(value: Any, field: str, *, maximum: int = 500) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"research_context_{field}_required")
    if len(text) > maximum:
        raise ValueError(f"research_context_{field}_too_long")
    return text


def _timestamp(value: Any, field: str, *, allow_latest: bool = False) -> str:
    text = _required_text(value, field, maximum=64)
    if allow_latest and text == "latest":
        return text
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"research_context_{field}_invalid_iso8601") from error
    return text


def _normalize_object(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("research_context_object_must_be_object")
    object_type = _required_text(value.get("type"), "object_type", maximum=32)
    if object_type not in OBJECT_TYPES:
        raise ValueError("research_context_object_type_unsupported")
    object_id = _required_text(value.get("id"), "object_id", maximum=128)
    label = str(value.get("label") or object_id).strip()
    if len(label) > 128:
        raise ValueError("research_context_object_label_too_long")
    return {"type": object_type, "id": object_id, "label": label}


def _normalize_time_range(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("research_context_time_range_must_be_object")
    start = _timestamp(value.get("start"), "time_range_start")
    end = _timestamp(value.get("end"), "time_range_end", allow_latest=True)
    if end != "latest":
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if end_dt < start_dt:
            raise ValueError("research_context_time_range_reversed")
    return {"start": start, "end": end}


def _normalize_phenomenon(value: Any) -> dict[str, str] | None:
    if value in (None, "", {}):
        return None
    if isinstance(value, str):
        return {"id": value.strip(), "type": "observation", "label": value.strip()}
    if not isinstance(value, Mapping):
        raise ValueError("research_context_phenomenon_must_be_object")
    phenomenon_id = _required_text(value.get("id"), "phenomenon_id", maximum=128)
    phenomenon_type = str(value.get("type") or "observation").strip()
    label = str(value.get("label") or phenomenon_id).strip()
    if not phenomenon_type or len(phenomenon_type) > 64 or len(label) > 200:
        raise ValueError("research_context_phenomenon_invalid")
    return {"id": phenomenon_id, "type": phenomenon_type, "label": label}


def _normalize_refs(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("research_context_evidence_refs_must_be_array")
    refs = []
    for item in value:
        ref = _required_text(item, "evidence_ref", maximum=500)
        if ref not in refs:
            refs.append(ref)
    if len(refs) > 200:
        raise ValueError("research_context_evidence_refs_too_many")
    return refs


def _normalize_origin(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("research_context_origin_must_be_object")
    origin = {"surface": _required_text(value.get("surface"), "origin_surface", maximum=64)}
    if value.get("reference_id"):
        origin["reference_id"] = _required_text(value.get("reference_id"), "origin_reference_id", maximum=160)
    return origin


def _with_hash(context: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(context))
    result.pop("content_hash", None)
    result["content_hash"] = canonical_hash(result)
    return result


def build_research_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    now = _now()
    context_id = str(payload.get("context_id") or f"ctx_{uuid.uuid4().hex}")
    frequency = _required_text(payload.get("frequency"), "frequency", maximum=16)
    if frequency not in FREQUENCIES:
        raise ValueError("research_context_frequency_unsupported")
    status = str(payload.get("status") or "active")
    if status not in STATUSES:
        raise ValueError("research_context_status_unsupported")
    context = {
        "schema_version": SCHEMA_VERSION,
        "context_id": context_id,
        "workspace_id": str(payload.get("workspace_id") or "local"),
        "owner_id": str(payload.get("owner_id") or "local-user"),
        "object": _normalize_object(payload.get("object")),
        "time_range": _normalize_time_range(payload.get("time_range")),
        "frequency": frequency,
        "phenomenon": _normalize_phenomenon(payload.get("phenomenon")),
        "research_question": _required_text(payload.get("research_question"), "research_question", maximum=1000),
        "evidence_refs": _normalize_refs(payload.get("evidence_refs")),
        "origin": _normalize_origin(payload.get("origin")),
        "lineage": {
            "root_context_id": context_id,
            "parent_context_id": None,
            "depth": 0,
        },
        "revision": 1,
        "status": status,
        "created_at": now,
        "updated_at": now,
        "research_only": True,
        "live_trading": False,
    }
    return _with_hash(context)


def revise_research_context(current: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(patch) - EDITABLE_FIELDS - {"expected_revision"}
    if unknown:
        raise ValueError(f"research_context_patch_fields_forbidden:{','.join(sorted(unknown))}")
    expected = patch.get("expected_revision")
    if expected is not None and int(expected) != int(current.get("revision", 0)):
        raise ValueError("research_context_revision_conflict")
    result = deepcopy(dict(current))
    if "object" in patch:
        result["object"] = _normalize_object(patch["object"])
    if "time_range" in patch:
        result["time_range"] = _normalize_time_range(patch["time_range"])
    if "frequency" in patch:
        frequency = _required_text(patch["frequency"], "frequency", maximum=16)
        if frequency not in FREQUENCIES:
            raise ValueError("research_context_frequency_unsupported")
        result["frequency"] = frequency
    if "phenomenon" in patch:
        result["phenomenon"] = _normalize_phenomenon(patch["phenomenon"])
    if "research_question" in patch:
        result["research_question"] = _required_text(patch["research_question"], "research_question", maximum=1000)
    if "evidence_refs" in patch:
        result["evidence_refs"] = _normalize_refs(patch["evidence_refs"])
    if "status" in patch:
        status = str(patch["status"])
        if status not in STATUSES:
            raise ValueError("research_context_status_unsupported")
        result["status"] = status
    result["revision"] = int(current.get("revision", 0)) + 1
    result["updated_at"] = _now()
    return _with_hash(result)


def fork_research_context(current: Mapping[str, Any], overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    patch = dict(overrides or {})
    forbidden = set(patch) - EDITABLE_FIELDS - {"origin"}
    if forbidden:
        raise ValueError(f"research_context_fork_fields_forbidden:{','.join(sorted(forbidden))}")
    base = {
        "workspace_id": current["workspace_id"],
        "owner_id": current["owner_id"],
        "object": current["object"],
        "time_range": current["time_range"],
        "frequency": current["frequency"],
        "phenomenon": current.get("phenomenon"),
        "research_question": current["research_question"],
        "evidence_refs": current.get("evidence_refs", []),
        "origin": patch.pop("origin", {"surface": "context_fork", "reference_id": current["context_id"]}),
        "status": "active",
    }
    base.update(patch)
    result = build_research_context(base)
    result["lineage"] = {
        "root_context_id": current.get("lineage", {}).get("root_context_id") or current["context_id"],
        "parent_context_id": current["context_id"],
        "depth": int(current.get("lineage", {}).get("depth", 0)) + 1,
    }
    return _with_hash(result)


__all__ = [
    "EDITABLE_FIELDS", "FREQUENCIES", "OBJECT_TYPES", "SCHEMA_VERSION", "STATUSES",
    "build_research_context", "fork_research_context", "revise_research_context",
]
