"""Domain types, schemas, path constants, and prompt loading. No intra-package deps."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Template

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
    "permit_basis",
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
    "permit_basis": "",
}


PERMIT_SCHEMAS = {
    "building_residential": "rakennuslupa_asuinrakennus.schema.json",
    "environmental_industrial": "ymparistolupa_teollisuus.schema.json",
    "event_public": "tapahtumalupa_julkinen.schema.json",
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
    criteria_id: str | None = None
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
    criteria_results: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    degraded: dict = field(default_factory=dict)
    readiness: str = ""


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
    strengths: list[dict[str, Any]] = field(default_factory=list)
    improvements: list[dict[str, Any]] = field(default_factory=list)
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


@dataclass
class ProseSuggestionField:
    field_id: str
    label: str
    value: str
    source: str = ""


@dataclass
class ProseSuggestionResponse:
    request_id: str
    criteria_version: str
    prose_md: str = ""
    fields: list[ProseSuggestionField] = field(default_factory=list)
    provider: str = ""
    fallback_used: bool = False


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_form_schema(permit_type: str) -> dict[str, Any]:
    filename = PERMIT_SCHEMAS.get(permit_type, PERMIT_SCHEMAS["building_residential"])
    return load_json(SPEC_ROOT / "form_schemas" / filename, {})


def _load_prompt(agent: str) -> str:
    prompt_agent = {
        "field_suggestion_agent": "field_suggestion",
        "form_suggestion_agent": "form_suggestion",
        "caseworker_summary_agent": "caseworker_summary",
        "additional_info_request_agent": "additional_info_request",
        "authority_memo_draft_agent": "authority_memo_draft",
        "prose_suggestion_agent": "prose_suggestion",
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
    text = _load_prompt(agent)
    return Template(text).render(**values)
