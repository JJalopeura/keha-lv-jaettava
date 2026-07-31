"""Persistence primitives: artifact CRUD, event log, type helpers. Depends on domain.py types only."""

import os
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Optional

__all__ = [
    "artifact_json",
    "_dict",
    "_list",
    "_plain",
    "save_artifact",
    "event",
    "_real_llm_enabled",
    "_extraction_queue_item",
    "source_ref",
]


def artifact_json(db: Any, case_id: str, type_: str, name: str, default: Any) -> Any:
    art = db.get_artifact(case_id, type_, name)
    data = (art or {}).get("data_json")
    return data if isinstance(data, type(default)) else default


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _plain(items: Any) -> list[Any]:
    """Dataclasses -> dicts, dicts passed through; agent output lists mix both."""
    return [asdict(i) if is_dataclass(i) and not isinstance(i, type) else i for i in items]


def save_artifact(db: Any, case_id: str, type_: str, name: str, data: dict[str, Any]) -> None:
    db.upsert_artifact(case_id, type_, name, data_json=data)


def event(db: Any, case_id: str, action: str, detail: str, data: Optional[dict[str, Any]] = None) -> None:
    payload = {"action": action, "detail": detail, "data": data or {}, "ts": time.time()}
    events = artifact_json(db, case_id, "lupademo_events", "main", {"events": []})
    events.setdefault("events", []).append(payload)
    save_artifact(db, case_id, "lupademo_events", "main", events)


def _real_llm_enabled(settings: Any) -> bool:
    if settings is None:
        return False
    if str(getattr(settings, "llm_provider", "mock") or "mock").lower() == "mock":
        return False
    return str(os.getenv("KEHA_LV_FORCE_MOCK_LLM", "")).strip().lower() not in {"1", "true", "yes", "on"}


def _extraction_queue_item(item: dict[str, Any]) -> dict[str, Any]:
    """Build one ExtractionCandidate-shaped dict (docs/specs/v5/application_package_contract.yaml)."""
    field_id = item.get("field_id")
    # pi-lens-ignore: unchecked-throwing-call-python
    confidence = float(item.get("confidence") or 0.5)
    return {
        "id": f"extraction_{field_id}",
        "field_id": field_id,
        "suggested_value": item.get("extracted_value"),
        "source_document": item.get("source_document"),
        "confidence": confidence,
        "low_confidence": confidence < 0.7,
        "evidence_refs": item.get("evidence_refs", []),
        "status": "pending",
    }


def source_ref(title: str, source_id: str = "local_excerpt", quote: str = "", confidence: float = 0.65) -> dict[str, Any]:
    return {"source_id": source_id, "title": title, "locator": "local_excerpt", "quote": quote[:260], "confidence": confidence}
