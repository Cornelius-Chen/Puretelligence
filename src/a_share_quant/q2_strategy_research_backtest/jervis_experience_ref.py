"""Public bridge from a Quant research packet to an exact Jervis Registry version.

This is new release code. It does not claim the private historical trader used
this function. The caller supplies an isolated Jervis StateStore, never a path
provided by a model response.
"""
from __future__ import annotations

from datetime import datetime


def resolve_jervis_experience(store, *, object_id: str, version: str,
                              trader_id: str, scope: str, decision_cutoff: datetime) -> dict:
    payload = store.registry.get_version(object_id, version)
    if payload["object_type"] != "ObjectEnvelope" or "learning_runtime_v1" not in payload.get("tags", ()):
        raise ValueError("unsupported_jervis_experience_object")
    body = store.memory._body(payload)  # The original Jervis attachment integrity check.
    if body.get("trader_id") != trader_id or body.get("scope") != scope:
        raise ValueError("experience_owner_or_scope_mismatch")
    if datetime.fromisoformat(body["available_at"]) > decision_cutoff:
        raise ValueError("experience_unavailable_at_decision")
    source = body["source_ref"]
    source_payload = store.registry.get_version(source["object_id"], source["version"])
    if source_payload["object_type"] != "ObjectEnvelope" or "learning_runtime_v1" not in source_payload.get("tags", ()):
        raise ValueError("unsupported_jervis_source_object")
    source_body = store.memory._body(source_payload)
    if datetime.fromisoformat(source_body["available_at"]) > decision_cutoff:
        raise ValueError("source_unavailable_at_decision")
    return {
        "reference": f"registry:{object_id}@{version}",
        "source_reference": f"registry:{source['object_id']}@{source['version']}",
        "candidate_state": payload["lifecycle_state"],
        "active": body["active"], "proposal": body.get("proposal"),
        "review_decision": body.get("review_decision"),
        "admission": "candidate_research_only",
    }
