"""Auto-split from the former monolithic services.py (2024 cleanup, Phase 2).

Module boundaries (see /workspace/plan.md Phase 2):
  core        - dataclasses/type contracts, form schemas & criteria, artifact/db
                primitives, prompt rendering, common-data sync. No intra-package deps.
  jobs        - generic AI job lifecycle (start/advance/complete/cancel/retry).
                Depends on core only (one deliberate deferred import into
                `application` inside _advance_ai_job_in_store - see comment there).
  intake      - attachments, material packages, ingestion, text/structure
                extraction, classification, retrieval/vector-store context.
                Depends on core + jobs.
  application - suggestions, criteria checks, package analysis, submission
                packaging, authority views, chat/voice, metrics/benchmark
                reporting, external suggestion-service API. Depends on
                core + jobs + intake.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import tempfile
import time
import unicodedata
import uuid
import zipfile
from pathlib import Path
from typing import Any, Literal, Optional

import openpyxl
import pdfplumber
from dataclasses import asdict, dataclass, field, is_dataclass
from dataclasses import fields as dataclass_fields
from docx import Document as DocxDocument
from fastapi import HTTPException
from jinja2 import Template

from app.core.llm_factory import create_llm_client
from app.core.llm_utils import call_llm_json, vector_store_ids

ROOT = Path(__file__).resolve().parents[4]
DOMAIN_ROOT = Path(__file__).resolve().parents[1]
SPEC_ROOT = DOMAIN_ROOT / "specs"


COMMON_FIELDS = [
    "applicant_name",
    "business_id",
    "contact_person",
    "phone",
    "email",
    "municipality",
    "address",
    "property_id",
    "project_name",
]


DEFAULT_COMMON_DATA = {
    "applicant_name": "Aurora Kodit Oy",
    "business_id": "1234567-8",
    "contact_person": "Aino Hakija",
    "phone": "+358 40 123 4567",
    "email": "aino.hakija@example.fi",
    "municipality": "Vantaa",
    "address": "Koivutie 12",
    "property_id": "092-416-12-34",
    "project_name": "Koivutien pientalohanke",
}


EVENT_PUBLIC_SCHEMA = {
    "schema_id": "event_public_v1",
    "permit_type": "event_public",
    "title": "Tapahtumalupa / julkinen kaupunkitapahtuma",
    "authority_default": "Kunnan tapahtumapalvelut / poliisi / pelastusviranomainen",
    "tabs": [
        {"id": "basic", "label": "Tapahtuma"},
        {"id": "safety", "label": "Turvallisuus ja järjestelyt"},
        {"id": "attachments", "label": "Liitteet"},
        {"id": "summary", "label": "Yhteenveto"},
    ],
    "fields": [
        {"id": "applicant_name", "tab": "basic", "label": "Hakija", "type": "text", "required": True, "common_data": True},
        {"id": "business_id", "tab": "basic", "label": "Y-tunnus", "type": "text", "required": True, "common_data": True},
        {"id": "event_name", "tab": "basic", "label": "Tapahtuman nimi", "type": "text", "required": True},
        {"id": "event_description", "tab": "basic", "label": "Tapahtuman kuvaus", "type": "textarea", "required": True, "ai_suggest": True},
        {"id": "event_location", "tab": "basic", "label": "Paikka ja alue", "type": "textarea", "required": True, "ai_suggest": True},
        {"id": "expected_attendees", "tab": "basic", "label": "Arvioitu osallistujamäärä", "type": "number", "required": True},
        {"id": "landowner_permission", "tab": "safety", "label": "Maanomistajan lupa", "type": "textarea", "required": True, "ai_suggest": True},
        {"id": "rescue_plan", "tab": "safety", "label": "Pelastussuunnitelma", "type": "textarea", "required": True, "ai_suggest": True},
        {"id": "noise_notice", "tab": "safety", "label": "Melu ja ilmoitustarve", "type": "textarea", "required": False, "ai_suggest": True},
        {"id": "traffic_arrangements", "tab": "safety", "label": "Liikennejärjestelyt", "type": "textarea", "required": True, "ai_suggest": True},
        {"id": "waste_management", "tab": "safety", "label": "Jätehuolto", "type": "textarea", "required": True, "ai_suggest": True},
    ],
    "required_attachments": ["landowner_permission", "rescue_plan"],
    "recommended_attachments": ["traffic_plan", "site_plan", "noise_notice"],
}


PERMIT_SCHEMAS = {
    "building_residential": "rakennuslupa_asuinrakennus.schema.json",
    "environmental_industrial": "ymparistolupa_teollisuus.schema.json",
    "event_public": "__event_public__",
}


@dataclass
class EvidenceRef:
    source_id: str
    title: str
    locator: str | None = None
    quote: str | None = None
    confidence: float = 0.7


@dataclass
class Issue:
    id: str
    type: str
    severity: str
    title: str
    detail: str
    confidence: float
    field_id: str | None = None
    attachment_id: str | None = None
    suggested_fix: str | None = None
    evidence_refs: list = field(default_factory=list)
    resolved: bool = False


@dataclass
class Suggestion:
    field_id: str
    suggested_value: str
    id: str = field(default_factory=lambda: f"sug_{uuid.uuid4().hex[:10]}")
    current_value: str = ""
    diff_summary: str = ""
    rationale: str = ""
    confidence: float = 0.5
    evidence_refs: list = field(default_factory=list)
    criteria_version: str = ""
    agent: str = "field_suggestion_agent"
    fallback_used: bool = False
    warnings: list[str] = field(default_factory=list)
    status: str = "pending"
    created_at: float = field(default_factory=time.time)


@dataclass
class FieldSuggestionRequest:
    request_id: str
    tenant_id: str
    permit_type: str
    form_schema: dict[str, Any]
    field_id: str
    form_data: dict[str, Any]
    criteria_set_id: str | None = None
    common_data: dict[str, Any] = field(default_factory=dict)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class FieldSuggestionResponse:
    request_id: str
    criteria_version: str
    suggestions: list[Suggestion] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ApplicationCheckRequest:
    request_id: str
    tenant_id: str
    permit_type: str
    form_schema: dict[str, Any]
    form_data: dict[str, Any]
    criteria_set_id: str | None = None
    common_data: dict[str, Any] = field(default_factory=dict)
    attachments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ApplicationCheckResponse:
    request_id: str
    criteria_version: str
    score: int
    summary: str
    issues: list[Issue] = field(default_factory=list)


@dataclass
class IssueListResponse:
    issues: list[Issue] = field(default_factory=list)


@dataclass
class AttachmentClassificationResponse:
    file_id: str
    primary_type: str
    summary: str
    type_tags: list[str] = field(default_factory=list)
    suggested_delivery_scope: str = "unknown"
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    confidence: float = 0.5


@dataclass
class DocumentAsset:
    document_id: str
    package_id: str
    filename: str
    mime_type: str = "application/octet-stream"
    size_bytes: int = 0
    document_role: str = "unknown"
    delivery_scope: str = "official_submission"
    extraction_status: str = "not_started"
    text_excerpt: str = ""
    text_chars: int = 0
    confidence: float = 0.5
    evidence_refs: list[EvidenceRef] = field(default_factory=list)


@dataclass
class MaterialPackage:
    package_id: str
    project_id: str
    application_id: str
    display_name: str
    source_channel: str = "browser_upload"
    package_scope: str = "current_application_material"
    documents: list[DocumentAsset] = field(default_factory=list)
    expected_document_count: int = 0
    status: str = "ready"
    created_at: float = field(default_factory=time.time)


@dataclass
class PackageFinding:
    id: str
    title: str
    detail: str
    severity: str = "info"
    # category matches docs/specs/v5/application_package_contract.yaml Finding.category
    category: str = "missing_required_info"
    field_id: str | None = None
    attachment_id: str | None = None
    suggested_fix: str | None = None
    question_to_applicant: str | None = None
    criteria_refs: list[str] = field(default_factory=list)
    status: str = "open"
    confidence: float = 0.6
    evidence_refs: list[EvidenceRef] = field(default_factory=list)


@dataclass
class PackageAnalysisResult:
    package_id: str
    permit_type: str
    summary: str
    readiness: str = "needs_completion"
    readiness_score: int = 0
    findings: list[PackageFinding] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    document_inventory: list[dict[str, Any]] = field(default_factory=list)
    field_extractions: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


@dataclass
class CriteriaResult:
    criteria_id: str
    status: str
    rationale: str
    related_fields: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    confidence: float = 0.5


@dataclass
class CriteriaAssessmentResponse:
    criteria_results: list[CriteriaResult] = field(default_factory=list)


@dataclass
class ApplicantPrecheckReport:
    summary: str
    readiness: str
    score: int = 0
    sections: list[dict[str, Any]] = field(default_factory=list)
    blocking_issue_count: int = 0
    major_issue_count: int = 0


@dataclass
class CaseworkerSummaryResponse:
    summary_md: str
    key_facts: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    confidence: float = 0.5


@dataclass
class AdditionalInfoRequestDraft:
    subject: str
    body_md: str
    requested_items: list[dict[str, Any]] = field(default_factory=list)
    review_notes_for_caseworker: list[str] = field(default_factory=list)


@dataclass
class AuthorityMemoDraft:
    memo_md: str
    facts: list[str] = field(default_factory=list)
    ai_observations: list[str] = field(default_factory=list)
    caseworker_checks: list[str] = field(default_factory=list)
    evidence_refs: list[EvidenceRef] = field(default_factory=list)


@dataclass
class StructureExtractionRequest:
    request_id: str
    permit_type: str
    form_schema: dict[str, Any]
    source_text: str
    source_refs: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ExtractedField:
    field_id: str
    value: str
    confidence: float
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class StructureExtractionResponse:
    request_id: str
    extracted_fields: list[ExtractedField] = field(default_factory=list)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_form_schema(permit_type: str) -> dict[str, Any]:
    filename = PERMIT_SCHEMAS.get(permit_type, PERMIT_SCHEMAS["building_residential"])
    if filename == "__event_public__":
        return EVENT_PUBLIC_SCHEMA
    return load_json(SPEC_ROOT / "form_schemas" / filename, {})


def load_prompt(agent: str) -> str:
    prompt_agent = {
        "field_suggestion_agent": "field_suggestion",
        "caseworker_summary_agent": "caseworker_summary",
        "additional_info_request_agent": "additional_info_request",
        "authority_memo_draft_agent": "authority_memo_draft",
    }.get(agent, agent)
    filename = f"{prompt_agent}.prompt.md"
    if not filename:
        return ""
    path = SPEC_ROOT / "prompts" / filename
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return (
            "Olet Lupademon AI-agentti. Prompt-tiedostoa ei löytynyt, joten käytä vain annettua "
            "rakenteista kontekstia. Palauta pyydetyn JSON-skeeman mukainen vastaus. Merkitse "
            "epävarmat havainnot matalalla confidence-arvolla ja lisää evidence_refs, jos "
            "lähdeaineistoa on saatavilla."
        )
    except Exception:
        return (
            "Olet Lupademon AI-agentti. Promptin lataus epäonnistui. Palauta pyydetyn JSON-skeeman "
            "mukainen varovainen vastaus annettuun kontekstiin perustuen."
        )
    return text


def render_agent_prompt(agent: str, values: dict[str, Any]) -> str:
    text = load_prompt(agent)
    return Template(text).render(**values)


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


def _as_dict(value: Any) -> dict[str, Any]:
    """Agent results arrive as either a response dataclass or a plain dict."""
    return asdict(value) if is_dataclass(value) and not isinstance(value, type) else _dict(value)


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


def ensure_demo_case(db: Any) -> str:
    cases = db.list_cases()
    if cases:
        return cases[0]["id"]
    case_id = db.create_case("Lupademo: Koivutien pientalohanke", language="fi")
    db.set_case_status(case_id, "application_draft")
    ensure_seed_state(db, case_id)
    db.set_app_setting("lupademo_active_case_id", case_id)
    return case_id


def ensure_seed_state(db: Any, case_id: str) -> None:
    if not db.get_artifact(case_id, "lv_common_data", "main"):
        save_artifact(db, case_id, "lv_common_data", "main", {"data": DEFAULT_COMMON_DATA.copy(), "version": 1})
    if not db.get_artifact(case_id, "common_data_sync_state", "main"):
        save_artifact(db, case_id, "common_data_sync_state", "main", {"base": DEFAULT_COMMON_DATA.copy(), "changes": []})
    project = artifact_json(db, case_id, "lv_project", "main", {})
    if not project:
        save_artifact(
            db,
            case_id,
            "lv_project",
            "main",
            {
                "project_id": "lv-demo-001",
                "name": DEFAULT_COMMON_DATA["project_name"],
                "statuses": {
                    "building_residential": "draft",
                    "environmental_industrial": "not_started",
                    "event_public": "not_started",
                },
            },
        )
    else:
        statuses = project.setdefault("statuses", {})
        changed = False
        for permit_type in PERMIT_SCHEMAS:
            if permit_type not in statuses:
                statuses[permit_type] = "not_started"
                changed = True
        if changed:
            save_artifact(db, case_id, "lv_project", "main", project)
    for permit_type in PERMIT_SCHEMAS:
        ensure_application(db, case_id, permit_type)


def application_id_for(permit_type_or_id: str) -> str:
    if permit_type_or_id in PERMIT_SCHEMAS:
        return permit_type_or_id
    if permit_type_or_id in {"building", "rakennuslupa"}:
        return "building_residential"
    if permit_type_or_id in {"environmental", "ymparistolupa"}:
        return "environmental_industrial"
    if permit_type_or_id in {"event", "event_public", "tapahtumalupa"}:
        return "event_public"
    return permit_type_or_id


def ensure_application(db: Any, case_id: str, permit_type: str) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    schema = load_form_schema(permit_type)
    app = artifact_json(db, case_id, "permit_application", permit_type, {})
    if app:
        return app
    common = artifact_json(db, case_id, "lv_common_data", "main", {"data": DEFAULT_COMMON_DATA.copy()})["data"]
    form_data = {f["id"]: common.get(f["id"], "") for f in schema.get("fields", [])}
    if permit_type == "building_residential":
        form_data.update({"intended_use": "Asuinrakennus", "floor_area_m2": "145", "distance_to_boundary_m": "4"})
    elif permit_type == "environmental_industrial":
        form_data.update({"activity_name": "Vihreän vedyn tuotantolaitos", "competent_authority": "Aluehallintovirasto"})
    elif permit_type == "event_public":
        form_data.update({"event_name": "Koivutien korttelijuhla", "expected_attendees": "250", "event_location": common.get("address", "")})
    app = {
        "application_id": permit_type,
        "permit_type": permit_type,
        "schema_id": schema.get("schema_id"),
        "status": "draft",
        "form_data": form_data,
        "field_sources": {k: "lv_common_data" for k in COMMON_FIELDS if k in form_data},
        "versions": [],
        "updated_at": time.time(),
    }
    save_artifact(db, case_id, "permit_application", permit_type, app)
    return app


def update_lv_common_data(db: Any, case_id: str, updates: dict[str, str]) -> dict[str, Any]:
    ensure_seed_state(db, case_id)
    current = artifact_json(db, case_id, "lv_common_data", "main", {"data": DEFAULT_COMMON_DATA.copy(), "version": 1})
    current["data"].update({k: v for k, v in updates.items() if k in COMMON_FIELDS})
    # pi-lens-ignore: unchecked-throwing-call-python
    current["version"] = int(current.get("version", 1)) + 1
    save_artifact(db, case_id, "lv_common_data", "main", current)
    detect_common_data_changes(db, case_id)
    event(db, case_id, "lv.common_data.updated", "LV common data updated", {"fields": list(updates)})
    return current


def update_application_data(db: Any, case_id: str, permit_type: str, updates: dict[str, str]) -> dict[str, Any]:
    app = ensure_application(db, case_id, permit_type)
    before = dict(app.get("form_data") or {})
    app.setdefault("form_data", {}).update(updates)
    app.setdefault("versions", []).append({"ts": time.time(), "source": "user", "before": before, "after": dict(app["form_data"])})
    app["updated_at"] = time.time()
    save_artifact(db, case_id, "permit_application", app["permit_type"], app)
    detect_common_data_changes(db, case_id)
    event(db, case_id, "application.saved", "Application fields saved", {"application_id": app["permit_type"], "fields": list(updates)})
    return app


def detect_common_data_changes(db: Any, case_id: str) -> dict[str, Any]:
    common = artifact_json(db, case_id, "lv_common_data", "main", {"data": DEFAULT_COMMON_DATA.copy()})["data"]
    sync = artifact_json(db, case_id, "common_data_sync_state", "main", {"base": DEFAULT_COMMON_DATA.copy(), "changes": []})
    base = sync.get("base") or DEFAULT_COMMON_DATA.copy()
    changes: list[dict[str, Any]] = []
    for schema_field in COMMON_FIELDS:
        lv_value = common.get(schema_field, "")
        base_value = base.get(schema_field, "")
        app_entries = []
        for permit_type in PERMIT_SCHEMAS:
            app = ensure_application(db, case_id, permit_type)
            form_data = app.get("form_data", {})
            app_entries.append({"application_id": permit_type, "value": form_data.get(schema_field, base_value)})
        if not app_entries:
            continue
        app_values = [entry["value"] for entry in app_entries]
        changed_entries = [entry for entry in app_entries if entry["value"] != base_value]
        app_changed = bool(changed_entries)
        lv_changed = lv_value != base_value
        if lv_changed and app_changed and any(v != lv_value for v in app_values):
            direction = "conflict"
        elif lv_changed:
            direction = "lv_to_app"
        elif app_changed:
            direction = "app_to_lv"
        else:
            continue
        source_entry = changed_entries[0] if changed_entries else (app_entries[0] if app_entries else {"application_id": "", "value": ""})
        changes.append(
            {
                "id": f"chg_{schema_field}_{source_entry.get('application_id') or 'lv'}",
                "field_id": schema_field,
                "direction": direction,
                "lv_value": lv_value,
                "app_value": source_entry.get("value", ""),
                "app_values": app_values,
                "app_entries": app_entries,
                "base_value": base_value,
                "source_system": "kevytasiointi" if direction in {"app_to_lv", "conflict"} else "lv",
                "source_application_id": source_entry.get("application_id", ""),
                "status": "pending",
                "created_at": time.time(),
                "resolved_at": None,
                "resolution": None,
            }
        )
    sync["changes"] = changes
    save_artifact(db, case_id, "common_data_sync_state", "main", sync)
    change_log = artifact_json(db, case_id, "common_data_change_log", "main", {"changes": []})
    previous = {item.get("id"): item for item in change_log.get("changes", []) if item.get("id")}
    merged = []
    for change in changes:
        old = previous.get(change["id"], {})
        if old.get("status") == "resolved":
            continue
        merged.append({**old, **change, "created_at": old.get("created_at") or change.get("created_at")})
    change_log["changes"] = merged
    save_artifact(db, case_id, "common_data_change_log", "main", change_log)
    return sync


def accept_common_data_sync(
    db: Any,
    case_id: str,
    field_id: str,
    direction: str,
    *,
    source_application_id: str = "",
    resolution: str = "",
    manual_value: str = "",
) -> dict[str, Any]:
    common = artifact_json(db, case_id, "lv_common_data", "main", {"data": DEFAULT_COMMON_DATA.copy(), "version": 1})
    value_for_base = common["data"].get(field_id, "")
    if direction == "lv_to_app" or (direction == "conflict" and resolution == "lv_wins"):
        for permit_type in PERMIT_SCHEMAS:
            app = ensure_application(db, case_id, permit_type)
            if field_id in COMMON_FIELDS or field_id in app.get("form_data", {}):
                app["form_data"][field_id] = common["data"].get(field_id, "")
                app["updated_at"] = time.time()
                save_artifact(db, case_id, "permit_application", permit_type, app)
    elif direction == "app_to_lv" or (direction == "conflict" and resolution == "app_wins"):
        source_id = source_application_id or "building_residential"
        app = ensure_application(db, case_id, source_id)
        common["data"][field_id] = app.get("form_data", {}).get(field_id, common["data"].get(field_id, ""))
        # pi-lens-ignore: unchecked-throwing-call-python
        common["version"] = int(common.get("version", 1)) + 1
        save_artifact(db, case_id, "lv_common_data", "main", common)
        value_for_base = common["data"].get(field_id, "")
        if direction == "conflict":
            for permit_type in PERMIT_SCHEMAS:
                app_item = ensure_application(db, case_id, permit_type)
                if field_id in COMMON_FIELDS or field_id in app_item.get("form_data", {}):
                    app_item["form_data"][field_id] = value_for_base
                    app_item["updated_at"] = time.time()
                    save_artifact(db, case_id, "permit_application", permit_type, app_item)
    elif direction == "conflict" and resolution == "manual":
        common["data"][field_id] = manual_value
        # pi-lens-ignore: unchecked-throwing-call-python
        common["version"] = int(common.get("version", 1)) + 1
        save_artifact(db, case_id, "lv_common_data", "main", common)
        value_for_base = manual_value
    elif direction == "conflict" and resolution == "keep_separate":
        value_for_base = common["data"].get(field_id, "")
    sync = artifact_json(db, case_id, "common_data_sync_state", "main", {"base": DEFAULT_COMMON_DATA.copy(), "changes": []})
    sync["base"][field_id] = value_for_base
    save_artifact(db, case_id, "common_data_sync_state", "main", sync)
    change_log = artifact_json(db, case_id, "common_data_change_log", "main", {"changes": []})
    for change in change_log.get("changes", []):
        if change.get("field_id") == field_id and change.get("status") == "pending":
            change["status"] = "resolved"
            change["resolved_at"] = time.time()
            change["resolution"] = resolution or direction
    save_artifact(db, case_id, "common_data_change_log", "main", change_log)
    event(db, case_id, "common_data.sync.accepted", "Common data sync accepted", {"field_id": field_id, "direction": direction, "source_application_id": source_application_id, "resolution": resolution})
    return detect_common_data_changes(db, case_id)


def source_ref(title: str, source_id: str = "local_excerpt", quote: str = "", confidence: float = 0.65) -> dict[str, Any]:
    return {"source_id": source_id, "title": title, "locator": "local_excerpt", "quote": quote[:260], "confidence": confidence}
