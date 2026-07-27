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


ROOT = Path(__file__).resolve().parents[3]
DOMAIN_ROOT = Path(__file__).resolve().parent
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

CRITERIA_FILES = {
    "building_residential": "vantaa_building_residential_demo_v1.json",
    "environmental_industrial": "national_environmental_industrial_demo_v1.json",
    "event_public": "__event_public__",
}

ENVIRONMENTAL_MATERIAL_STORIES = [
    {
        "id": "generic_industrial_environmental_demo",
        "label": "Teollinen ympäristölupahanke / kemikaalit / BAT / tarkkailu",
        "documents": [
            {
                "filename": "hakemus_tiivistelma.txt",
                "primary_type": "environmental_application",
                "delivery_scope": "official_submission",
                "text": "Toiminta koostuu teollisesta tuotannosta, raaka-aineiden käsittelystä, varastoinnista ja päästöjen hallinnasta. Hakija esittää luvanvaraisuuden ympäristönsuojelulain 27 §:n perusteella ja toimivaltaiseksi viranomaiseksi aluehallintovirastoa.",
            },
            {
                "filename": "kemikaaliluettelo_tyoaineisto.txt",
                "primary_type": "chemical_inventory",
                "delivery_scope": "ai_context_only",
                "text": "Kemikaaliluettelo sisältää prosessin raaka-aineet, varastoitavat kemikaalit ja käyttöturvallisuustiedotteiden koosteen. Aineisto on hakijan työversio ja tarvitsee julkisuusluokittelun ennen viranomaispakettia.",
            },
            {
                "filename": "bat_arviointi.txt",
                "primary_type": "bat_assessment",
                "delivery_scope": "official_submission",
                "text": "BAT-arviointi kuvaa energiatehokkuuden, päästöjen minimoinnin, prosessiturvallisuuden ja poikkeustilanteiden hallinnan.",
            },
            {
                "filename": "tarkkailusuunnitelma.txt",
                "primary_type": "monitoring_plan",
                "delivery_scope": "official_submission",
                "text": "Tarkkailusuunnitelma kattaa käyttötarkkailun, päästöt ilmaan, jätevesien seurannan, melun ja vuosiraportoinnin.",
            },
            {
                "filename": "viranomaiskuulutus_benchmark.txt",
                "primary_type": "authority_notice",
                "delivery_scope": "internal_authority_only",
                "text": "Viranomaisesimerkki kuulutuksesta. Tätä käytetään vain benchmark-aineistona, ei hakijan paketin osana.",
            },
        ],
    },
    {
        "id": "p2x_hydrogen_demo",
        "label": "Vihreän vedyn tuotanto / kemikaalit / YVA/BAT",
        "documents": [
            {
                "filename": "P2X_lupahakemus_tiivistelma.txt",
                "primary_type": "environmental_application",
                "delivery_scope": "official_submission",
                "text": "Toiminta koostuu vihreän vedyn tuotannosta, elektrolyysistä ja synteettisen polttoaineen valmistuksesta. Hakija esittää luvanvaraisuuden ympäristönsuojelulain 27 §:n perusteella ja toimivaltaiseksi viranomaiseksi AVI:a.",
            },
            {
                "filename": "P2X_kemikaaliluettelo.txt",
                "primary_type": "chemical_inventory",
                "delivery_scope": "ai_context_only",
                "text": "Kemikaaliluettelo sisältää metanolin, vedyn, hapen ja prosessikemikaalit. Aineisto on hakijan työversio ja tarvitsee julkisuusluokittelun.",
            },
            {
                "filename": "P2X_BAT_arviointi.txt",
                "primary_type": "bat_assessment",
                "delivery_scope": "official_submission",
                "text": "BAT-arviointi kuvaa energiatehokkuuden, päästöjen minimoinnin, prosessiturvallisuuden ja poikkeustilanteiden hallinnan.",
            },
            {
                "filename": "P2X_tarkkailusuunnitelma.txt",
                "primary_type": "monitoring_plan",
                "delivery_scope": "official_submission",
                "text": "Tarkkailusuunnitelma kattaa käyttötarkkailun, päästöt ilmaan, jätevesien seurannan, melun ja vuosiraportoinnin.",
            },
            {
                "filename": "AVI_kuulutus_esimerkki.txt",
                "primary_type": "authority_notice",
                "delivery_scope": "internal_authority_only",
                "text": "Viranomaisesimerkki kuulutuksesta. Tätä käytetään vain benchmark-aineistona, ei hakijan paketin osana.",
            },
        ],
    },
    {
        "id": "rengas_energy_demo",
        "label": "Uusiutuvan energian tuotantolaitos / putkistot / päästöt",
        "documents": [
            {
                "filename": "RenGas_hakemusrunko.txt",
                "primary_type": "environmental_application",
                "delivery_scope": "official_submission",
                "text": "Uusiutuvan energian tuotantolaitos vastaanottaa biogeenistä hiilidioksidia ja tuottaa synteettistä metaania. Hakemus kuvaa putkistot, varastosäiliöt ja ilmapäästöt.",
            },
            {
                "filename": "RenGas_ilmapaastomallinnus.txt",
                "primary_type": "air_quality_model",
                "delivery_scope": "official_submission",
                "text": "Ilmapäästömallinnus arvioi typen oksidit, hajut ja häiriötilanteiden vaikutukset lähialueella.",
            },
            {
                "filename": "RenGas_varautumissuunnitelma.txt",
                "primary_type": "risk_preparedness_plan",
                "delivery_scope": "official_submission",
                "text": "Varautumissuunnitelma käsittelee vedyn, metaanin ja paineellisten putkistojen poikkeustilanteet.",
            },
        ],
    },
    {
        "id": "sharpcell_industrial_demo",
        "label": "Teollinen laitos / päästöt / tarkkailu / täydennykset",
        "documents": [
            {
                "filename": "SharpCell_prosessikuvaus.txt",
                "primary_type": "environmental_application",
                "delivery_scope": "official_submission",
                "text": "Teollinen laitos valmistaa akkukomponentteja. Prosessikuvaus sisältää raaka-aineet, energiankäytön, vesien käsittelyn ja jätteet.",
            },
            {
                "filename": "SharpCell_tarkkailu.txt",
                "primary_type": "monitoring_plan",
                "delivery_scope": "official_submission",
                "text": "Tarkkailu sisältää jätevesien metallipitoisuudet, pölypäästöt, melun ja raportointitiheyden.",
            },
            {
                "filename": "SharpCell_taydennyspyynto_esimerkki.txt",
                "primary_type": "authority_additional_info_request",
                "delivery_scope": "internal_authority_only",
                "text": "Viranomaisen täydennyspyyntöesimerkki aiemmasta prosessista. Ei ingestoida hakemusmateriaaliksi.",
            },
        ],
    },
]


SEVERITY_ORDER = {"recommendation": 0, "minor": 1, "major": 2, "blocking": 3}
PRECHECK_MAX_AGE_SECONDS = 24 * 60 * 60
MAX_UPLOAD_FILE_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_ZIP_BYTES = 100 * 1024 * 1024
MAX_UPLOAD_ZIP_TOTAL_BYTES = 150 * 1024 * 1024
MAX_UPLOAD_ZIP_FILE_COUNT = 200
ALLOWED_UPLOAD_EXTENSIONS = {".txt", ".md", ".csv", ".tsv", ".pdf", ".docx", ".xlsx"}

AI_JOB_AGENT_LABELS = {
    "archive_validator": "Paketin tarkistus",
    "text_extractor": "Tekstin poiminta",
    "material_classifier": "Aineiston luokittelu",
    "vector_indexer": "Hakukontekstin valmistelu",
    "structure_extractor": "Rakenteistusagentti",
    "gap_analyzer": "Puutteiden tunnistus",
    "question_generator": "Kysymysten muodostus",
    "field_suggestion_agent": "Kenttäehdotusagentti",
    "form_suggestion_agent": "Lomake-ehdotusagentti",
    "completeness_validator": "Täydellisyystarkistus",
    "consistency_validator": "Ristiriitatarkistus",
    "criteria_assessor": "Kriteeriarviointi",
    "caseworker_summary_agent": "Käsittelijän yhteenvetoagentti",
    "additional_info_request_agent": "Täydennyspyyntöagentti",
    "authority_memo_draft_agent": "Valmistelumuistioagentti",
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
    field_id: str | None = None
    attachment_id: str | None = None
    suggested_fix: str | None = None
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


def load_criteria(permit_type: str) -> dict[str, Any]:
    filename = CRITERIA_FILES.get(permit_type, CRITERIA_FILES["building_residential"])
    if filename == "__event_public__":
        return {
            "criteria_set_id": "vantaa_event_public_demo_v1",
            "tenant_id": "vantaa_demo",
            "permit_type": "event_public",
            "version": "2026-06-demo-1",
            "status": "active",
            "sources": ["Vantaan tapahtumaohjeet demo", "Pelastuslaitoksen tapahtumaturvallisuuden demo-ohje"],
            "rules": [
                {"id": "landowner_permission", "field_id": "landowner_permission", "attachment_type": "landowner_permission", "severity": "blocking", "instruction": "Julkisen alueen tapahtuma tarvitsee maanomistajan tai alueen haltijan luvan."},
                {"id": "rescue_plan", "field_id": "rescue_plan", "attachment_type": "rescue_plan", "severity": "major", "instruction": "Tapahtuman turvallisuusjärjestelyt ja pelastussuunnitelma tulee kuvata."},
                {"id": "traffic_arrangements", "field_id": "traffic_arrangements", "severity": "major", "instruction": "Liikennejärjestelyt tulee kuvata, jos tapahtuma vaikuttaa katualueeseen tai kulkureitteihin."},
            ],
        }
    return load_json(SPEC_ROOT / "criteria_sets" / filename, {})


def effective_criteria(db: Any, case_id: str, permit_type: str) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    art = artifact_json(db, case_id, "criteria_set", permit_type, {})
    if art:
        return art
    criteria = load_criteria(permit_type)
    save_artifact(db, case_id, "criteria_set", permit_type, criteria)
    return criteria


def save_criteria_version(db: Any, case_id: str, permit_type: str, criteria: dict[str, Any], editor: str = "admin") -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    current = effective_criteria(db, case_id, permit_type)
    history = artifact_json(db, case_id, "criteria_history", permit_type, {"versions": []})
    history.setdefault("versions", []).append({"ts": time.time(), "editor": editor, "action": "replaced", "criteria": current})
    criteria["version"] = str(criteria.get("version") or f"edited-{int(time.time())}")
    criteria["status"] = str(criteria.get("status") or "active")
    criteria["source_files"] = criteria.get("source_files") or criteria.get("sources") or []
    criteria["updated_at"] = time.time()
    criteria["updated_by"] = editor
    save_artifact(db, case_id, "criteria_history", permit_type, history)
    save_artifact(db, case_id, "criteria_set", permit_type, criteria)
    event(db, case_id, "criteria.updated", "Criteria set updated", {"permit_type": permit_type, "version": criteria.get("version")})
    return criteria


def criteria_history(db: Any, case_id: str, permit_type: str) -> dict[str, Any]:
    return artifact_json(db, case_id, "criteria_history", application_id_for(permit_type), {"versions": []})


def criteria_history_rows(history: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    versions = history.get("versions", []) if isinstance(history, dict) else []
    if not isinstance(versions, list):
        return rows
    for index, item in enumerate(versions):
        criteria = item.get("criteria", {}) if isinstance(item, dict) else {}
        rows.append(
            {
                "history_index": index,
                "ts": item.get("ts", "") if isinstance(item, dict) else "",
                "editor": item.get("editor", "") if isinstance(item, dict) else "",
                "action": item.get("action", "replaced") if isinstance(item, dict) else "replaced",
                "version": criteria.get("version", "") if isinstance(criteria, dict) else "",
                "status": criteria.get("status", "") if isinstance(criteria, dict) else "",
                "rule_count": len(criteria.get("rules", [])) if isinstance(criteria, dict) and isinstance(criteria.get("rules"), list) else 0,
            }
        )
    return list(reversed(rows))


def criteria_rule_rows(criteria: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    rules = criteria.get("rules", []) if isinstance(criteria, dict) else []
    if not isinstance(rules, list):
        return rows
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        rows.append(
            {
                "index": index,
                "id": rule.get("id", ""),
                "field_id": rule.get("field_id", ""),
                "attachment_type": rule.get("attachment_type", ""),
                "severity": rule.get("severity", "major"),
                "instruction": rule.get("instruction", ""),
            }
        )
    return rows


def restore_criteria_version(db: Any, case_id: str, permit_type: str, history_index: int, editor: str = "admin") -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    history = criteria_history(db, case_id, permit_type)
    versions = history.get("versions", [])
    if not isinstance(versions, list) or history_index < 0 or history_index >= len(versions):
        raise ValueError("criteria_history_index_not_found")
    selected = versions[history_index].get("criteria", {})
    if not isinstance(selected, dict) or not selected:
        raise ValueError("criteria_history_entry_missing_criteria")
    restored = json.loads(json.dumps(selected, ensure_ascii=False))
    restored["status"] = "active"
    restored["restored_from_version"] = selected.get("version", "")
    restored["restored_at"] = time.time()
    restored["version"] = f"{selected.get('version', 'criteria')}-restored-{int(restored['restored_at'])}"
    saved = save_criteria_version(db, case_id, permit_type, restored, editor=editor)
    event(db, case_id, "criteria.restored", "Criteria version restored", {"permit_type": permit_type, "history_index": history_index, "version": saved.get("version")})
    return saved


def upsert_criteria_rule(db: Any, case_id: str, permit_type: str, rule: dict[str, Any], editor: str = "admin") -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    rule_id = str(rule.get("id") or "").strip()
    if not rule_id:
        raise ValueError("criteria_rule_id_required")
    updated = json.loads(json.dumps(effective_criteria(db, case_id, permit_type), ensure_ascii=False))
    rules = updated.setdefault("rules", [])
    if not isinstance(rules, list):
        rules = []
        updated["rules"] = rules
    clean_rule = {
        "id": rule_id,
        "severity": str(rule.get("severity") or "major"),
        "instruction": str(rule.get("instruction") or "").strip(),
    }
    field_id = str(rule.get("field_id") or "").strip()
    attachment_type = str(rule.get("attachment_type") or "").strip()
    if field_id:
        clean_rule["field_id"] = field_id
    if attachment_type:
        clean_rule["attachment_type"] = attachment_type
    replaced = False
    for index, existing in enumerate(rules):
        if isinstance(existing, dict) and existing.get("id") == rule_id:
            rules[index] = {**existing, **clean_rule}
            replaced = True
            break
    if not replaced:
        rules.append(clean_rule)
    updated["version"] = f"{str(updated.get('version') or 'criteria')}-rule-{int(time.time())}"
    saved = save_criteria_version(db, case_id, permit_type, updated, editor=editor)
    event(db, case_id, "criteria.rule.upserted", "Criteria rule upserted", {"permit_type": permit_type, "rule_id": rule_id, "mode": "updated" if replaced else "added"})
    return saved


def delete_criteria_rule(db: Any, case_id: str, permit_type: str, rule_id: str, editor: str = "admin") -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    rule_id = str(rule_id or "").strip()
    if not rule_id:
        raise ValueError("criteria_rule_id_required")
    updated = json.loads(json.dumps(effective_criteria(db, case_id, permit_type), ensure_ascii=False))
    rules = updated.get("rules", [])
    if not isinstance(rules, list):
        rules = []
    next_rules = [rule for rule in rules if not (isinstance(rule, dict) and rule.get("id") == rule_id)]
    if len(next_rules) == len(rules):
        raise ValueError("criteria_rule_not_found")
    updated["rules"] = next_rules
    updated["version"] = f"{str(updated.get('version') or 'criteria')}-delete-{int(time.time())}"
    saved = save_criteria_version(db, case_id, permit_type, updated, editor=editor)
    event(db, case_id, "criteria.rule.deleted", "Criteria rule deleted", {"permit_type": permit_type, "rule_id": rule_id})
    return saved


def run_criteria_test(db: Any, settings: Any, case_id: str, permit_type: str) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    schema = load_form_schema(permit_type)
    field = next((f for f in schema.get("fields", []) if f.get("ai_suggest")), {"id": "project_description", "label": "Hankkeen kuvaus"})
    payload = {
        "request_id": f"criteria_test_{uuid.uuid4().hex[:8]}",
        "tenant_id": "vantaa_demo",
        "permit_type": permit_type,
        "form_schema": schema,
        "field_id": field["id"],
        "form_data": {
            "project_description": "Testipyyntö kriteeristön toimivuuden arviointiin.",
            "activity_description": "Teollinen testihanke, jossa tarkistetaan kriteeriversion vaikutus.",
            "event_description": "Julkinen testitapahtuma kaupunkitilassa.",
        },
        "attachments": [],
    }
    result = suggestion_service_field(payload, db=db, settings=settings, case_id=case_id)
    save_artifact(db, case_id, "criteria_test_result", permit_type, {"permit_type": permit_type, "result": result, "tested_at": time.time()})
    event(db, case_id, "criteria.tested", "Criteria test request completed", {"permit_type": permit_type, "criteria_version": result.get("criteria_version")})
    return result


def load_prompt(agent: str) -> str:
    filename = f"{agent}.prompt.md"
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


def _job_store(db: Any, case_id: str, permit_type: str) -> dict[str, Any]:
    return artifact_json(db, case_id, "ai_jobs", application_id_for(permit_type), {"jobs": []})


def list_ai_jobs(db: Any, case_id: str, permit_type: str, limit: int = 8) -> list[dict[str, Any]]:
    _refresh_ai_jobs_for_case_permit(db, case_id, permit_type)
    store = _job_store(db, case_id, permit_type)
    jobs = _list(store.get("jobs"))
    return sorted(jobs or [], key=lambda item: float(item.get("updated_at") or item.get("created_at") or 0), reverse=True)[:limit]


def find_ai_job(db: Any, job_id: str) -> dict[str, Any]:
    for case in db.list_cases():
        case_id = str(case.get("id") or "")
        for permit_type in PERMIT_SCHEMAS:
            for job in artifact_json(db, case_id, "ai_jobs", permit_type, {"jobs": []}).get("jobs", []):
                if isinstance(job, dict) and job.get("id") == job_id:
                    return {"case_id": case_id, "application_id": permit_type, "job": job}
    raise KeyError(job_id)


def ai_job_status_payload(job: dict[str, Any], *, case_id: str = "", application_id: str = "") -> dict[str, Any]:
    status_map = {"completed": "succeeded", "pending": "queued"}
    status = status_map.get(str(job.get("status") or ""), str(job.get("status") or "running"))
    updated_at = float(job.get("updated_at") or job.get("created_at") or time.time())
    events_payload = []
    for index, agent in enumerate(job.get("agents") or []):
        if not isinstance(agent, dict):
            continue
        events_payload.append(
            {
                "event_id": f"{job.get('id')}_agent_{index}",
                "timestamp": float(agent.get("finished_at") or agent.get("started_at") or updated_at),
                "level": "error" if status == "failed" else "info",
                "agent": agent.get("name"),
                "step": agent.get("label") or agent.get("name") or "agent",
                "message": agent.get("summary") or job.get("summary") or job.get("title") or "AI job step",
                "progress_percent": int(job.get("progress_pct") or 0),
                "estimated_seconds_remaining": int(job.get("eta_seconds") or 0),
                "artifact_ref": job.get("result_artifact") or None,
            }
        )
    if not events_payload:
        events_payload.append(
            {
                "event_id": f"{job.get('id')}_status",
                "timestamp": updated_at,
                "level": "info" if status != "failed" else "error",
                "agent": None,
                "step": str(job.get("kind") or "job"),
                "message": str(job.get("summary") or job.get("title") or "AI job"),
                "progress_percent": int(job.get("progress_pct") or 0),
                "estimated_seconds_remaining": int(job.get("eta_seconds") or 0),
                "artifact_ref": job.get("result_artifact") or None,
            }
        )
    return {
        "job_id": job.get("id"),
        "job_type": job.get("kind"),
        "title": job.get("title") or job.get("kind") or "AI job",
        "status": status if status in {"queued", "running", "needs_user_input", "succeeded", "failed", "cancelled"} else "running",
        "progress_percent": int(job.get("progress_pct") or 0),
        "estimated_seconds_total": int(job.get("estimated_seconds_total") or job.get("eta_seconds") or 0),
        "estimated_seconds_remaining": int(job.get("eta_seconds") or 0),
        "current_agent": next((agent.get("name") for agent in job.get("agents", []) if isinstance(agent, dict) and agent.get("status") not in {"completed", "failed"}), None),
        "current_step_label": next((agent.get("label") for agent in job.get("agents", []) if isinstance(agent, dict) and agent.get("status") not in {"completed", "failed"}), None),
        "events": events_payload,
        "project_id": case_id or None,
        "case_id": case_id or None,
        "application_id": application_id or job.get("permit_type") or None,
        "result_artifact_type": str(job.get("result_artifact") or "").split(":", 1)[0] or None,
        "result_artifact_name": str(job.get("result_artifact") or "").split(":", 1)[1] if ":" in str(job.get("result_artifact") or "") else None,
        "error_code": "job_failed" if status == "failed" else None,
        "error_message": str(job.get("summary") or "") if status == "failed" else None,
        "created_at": float(job.get("created_at") or updated_at),
        "started_at": float(job.get("created_at") or updated_at),
        "updated_at": updated_at,
        "completed_at": float(job.get("finished_at") or updated_at) if status in {"succeeded", "failed", "cancelled"} else None,
    }


def cancel_ai_job(db: Any, job_id: str) -> dict[str, Any]:
    found = find_ai_job(db, job_id)
    case_id = found["case_id"]
    permit_type = found["application_id"]
    store = _job_store(db, case_id, permit_type)
    now = time.time()
    selected: dict[str, Any] = {}
    for job in store.get("jobs", []):
        if job.get("id") == job_id:
            job["status"] = "cancelled"
            job["eta_seconds"] = 0
            job["updated_at"] = now
            job["finished_at"] = now
            job["summary"] = job.get("summary") or "AI job cancelled."
            selected = job
            break
    save_artifact(db, case_id, "ai_jobs", permit_type, store)
    event(db, case_id, "ai.job.cancelled", "AI job cancelled", {"job_id": job_id, "permit_type": permit_type})
    return selected


def retry_ai_job(db: Any, job_id: str) -> dict[str, Any]:
    found = find_ai_job(db, job_id)
    original = found["job"]
    case_id = found["case_id"]
    permit_type = found["application_id"]
    agent_names = [str(agent.get("name")) for agent in original.get("agents", []) if isinstance(agent, dict) and agent.get("name")]
    if not agent_names:
        agent_names = ["request_router"]
    retry = start_ai_job(
        db,
        case_id,
        permit_type,
        str(original.get("kind") or "retry"),
        f"Uusinta: {original.get('title') or original.get('kind') or 'AI job'}",
        agent_names,
        eta_seconds=int(original.get("estimated_seconds_total") or original.get("eta_seconds") or 10),
    )
    retry["retry_of"] = job_id
    store = _job_store(db, case_id, permit_type)
    for job in store.get("jobs", []):
        if job.get("id") == retry["id"]:
            job["retry_of"] = job_id
            retry = job
            break
    save_artifact(db, case_id, "ai_jobs", permit_type, store)
    event(db, case_id, "ai.job.retry_started", "AI job retry started", {"job_id": retry["id"], "retry_of": job_id, "permit_type": permit_type})
    return retry


def _job_agent_steps(agent_names: list[str], status: str = "pending") -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "label": AI_JOB_AGENT_LABELS.get(name, name),
            "status": status,
            "started_at": None,
            "finished_at": None,
            "summary": "",
        }
        for name in agent_names
    ]


def start_ai_job(
    db: Any,
    case_id: str,
    permit_type: str,
    kind: str,
    title: str,
    agent_names: list[str],
    eta_seconds: int = 20,
    *,
    auto_finalize: bool = False,
    managed_background: bool = False,
) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    now = time.time()
    job = {
        "id": f"job_{uuid.uuid4().hex[:10]}",
        "kind": kind,
        "title": title,
        "permit_type": permit_type,
        "status": "running",
        "progress_current": 0,
        "progress_total": max(1, len(agent_names)),
        "progress_pct": 0,
        "eta_seconds": eta_seconds,
        "estimated_seconds_total": eta_seconds,
        "auto_finalize": auto_finalize,
        "managed_background": managed_background,
        "agents": _job_agent_steps(agent_names),
        "created_at": now,
        "updated_at": now,
        "finished_at": None,
        "result_artifact": "",
        "summary": "",
    }
    store = _job_store(db, case_id, permit_type)
    store.setdefault("jobs", []).append(job)
    store["jobs"] = store["jobs"][-20:]
    save_artifact(db, case_id, "ai_jobs", permit_type, store)
    event(db, case_id, "ai.job.started", "AI job started", {"job_id": job["id"], "kind": kind, "permit_type": permit_type})
    return job


def _job_simulation_seconds() -> float:
    raw = os.getenv("LUPADEMO_AI_JOB_TOTAL_SECONDS", "4")
    try:
        return max(0.2, float(raw))
    except ValueError:
        return 4.0


def _refresh_ai_jobs_for_case_permit(db: Any, case_id: str, permit_type: str) -> None:
    permit_type = application_id_for(permit_type)
    store = _job_store(db, case_id, permit_type)
    changed = False
    for job in store.get("jobs", []):
        if isinstance(job, dict) and job.get("status") == "running":
            _advance_ai_job_in_store(db, case_id, permit_type, job)
            changed = True
    if changed:
        save_artifact(db, case_id, "ai_jobs", permit_type, store)


def refresh_ai_job(db: Any, job_id: str) -> dict[str, Any]:
    found = find_ai_job(db, job_id)
    case_id = found["case_id"]
    permit_type = found["application_id"]
    store = _job_store(db, case_id, permit_type)
    selected: dict[str, Any] = {}
    changed = False
    for job in store.get("jobs", []):
        if isinstance(job, dict) and job.get("id") == job_id:
            selected = job
            if job.get("status") == "running":
                _advance_ai_job_in_store(db, case_id, permit_type, job)
                changed = True
            break
    if changed:
        save_artifact(db, case_id, "ai_jobs", permit_type, store)
    return selected or found["job"]


def _advance_ai_job_in_store(db: Any, case_id: str, permit_type: str, job: dict[str, Any]) -> None:
    now = time.time()
    created_at = float(job.get("created_at") or now)
    total_seconds = float(job.get("estimated_seconds_total") or 20) if job.get("managed_background") else _job_simulation_seconds()
    elapsed = max(0.0, now - created_at)
    progress = max(1, min(99, int((elapsed / total_seconds) * 100)))
    agent_count = max(1, len(job.get("agents") or []))
    active_index = min(agent_count - 1, int((progress / 100) * agent_count))
    job["progress_pct"] = progress
    job["progress_current"] = min(agent_count, active_index + 1)
    job["eta_seconds"] = max(1, int(round(total_seconds - elapsed))) if progress < 99 else 1
    job["updated_at"] = now
    for index, agent in enumerate(job.get("agents") or []):
        if not isinstance(agent, dict):
            continue
        if index < active_index:
            agent["status"] = "completed"
            agent["started_at"] = agent.get("started_at") or created_at
            agent["finished_at"] = agent.get("finished_at") or now
            agent["summary"] = agent.get("summary") or f"{agent.get('label') or agent.get('name')} valmis."
        elif index == active_index:
            agent["status"] = "running"
            agent["started_at"] = agent.get("started_at") or now
            agent["finished_at"] = None
            agent["summary"] = agent.get("summary") or f"{agent.get('label') or agent.get('name')} käynnissä."
        else:
            agent["status"] = "pending"
            agent["started_at"] = None
            agent["finished_at"] = None
    if elapsed >= total_seconds and job.get("managed_background"):
        job["progress_pct"] = 95
        job["progress_current"] = max(1, agent_count)
        job["eta_seconds"] = 0
    elif elapsed >= total_seconds:
        if job.get("auto_finalize") and job.get("kind") == "intake_analysis":
            _finalize_intake_analysis_job_in_store(db, case_id, permit_type, job)
        else:
            job["status"] = "completed"
            job["progress_current"] = agent_count
            job["progress_pct"] = 100
            job["eta_seconds"] = 0
            job["finished_at"] = now
            job["summary"] = job.get("summary") or "AI-ajo valmis."
            for agent in job.get("agents") or []:
                if isinstance(agent, dict):
                    agent["status"] = "completed"
                    agent["started_at"] = agent.get("started_at") or created_at
                    agent["finished_at"] = now
                    agent["summary"] = agent.get("summary") or "Valmis."


def complete_ai_job(
    db: Any,
    case_id: str,
    permit_type: str,
    job_id: str,
    *,
    summary: str,
    result_artifact: str = "",
    failed: bool = False,
) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    store = _job_store(db, case_id, permit_type)
    now = time.time()
    selected: dict[str, Any] = {}
    for job in store.get("jobs", []):
        if job.get("id") != job_id:
            continue
        selected = job
        job["status"] = "failed" if failed else "completed"
        job["progress_current"] = job.get("progress_total") or len(job.get("agents") or []) or 1
        job["progress_pct"] = 100
        job["eta_seconds"] = 0
        job["updated_at"] = now
        job["finished_at"] = now
        job["summary"] = summary
        job["result_artifact"] = result_artifact
        for agent in job.get("agents", []):
            agent["status"] = "failed" if failed else "completed"
            agent["started_at"] = agent.get("started_at") or job.get("created_at")
            agent["finished_at"] = now
            agent["summary"] = summary if not agent.get("summary") else agent.get("summary")
        break
    save_artifact(db, case_id, "ai_jobs", permit_type, store)
    event(db, case_id, "ai.job.completed" if not failed else "ai.job.failed", "AI job finished", {"job_id": job_id, "permit_type": permit_type, "status": selected.get("status")})
    return selected


ATTACHMENT_REQUIREMENT_ALIASES = {
    "environmental_application": {"environmental_application"},
    "site_map": {"site_map", "site_plan"},
    "zoning_extract": {"zoning_extract", "site_plan"},
    "public_summary": {"public_summary"},
}

ATTACHMENT_REQUIREMENT_TERMS = {
    "environmental_application": ("ympäristölupahakemus", "ymparistolupahakemus"),
    "site_map": ("sijaintikart", "sijaintiperiaate", "asemapiirros", "tonttimuistio"),
    "zoning_extract": ("kaavatilanne", "kaavamuistio", "asemakaava", "kaavoitus"),
    "public_summary": ("yleistajuinen yhteenveto", "julkinen tiivistelmä", "tiivistelmä yleisölle"),
}


def attachment_requirement_covered(required: str, attachments: list[dict[str, Any]]) -> bool:
    aliases = ATTACHMENT_REQUIREMENT_ALIASES.get(required, {required})
    terms = ATTACHMENT_REQUIREMENT_TERMS.get(required, ())
    for attachment in attachments:
        if attachment.get("delivery_scope") != "official_submission":
            continue
        if str(attachment.get("primary_type") or "") in aliases:
            return True
        haystack = f"{attachment.get('filename', '')}\n{attachment.get('summary', '')}\n{attachment.get('text_excerpt', '')}".lower()
        if any(term in haystack for term in terms):
            return True
    return False


def _real_llm_enabled(settings: Any) -> bool:
    if settings is None:
        return False
    if str(getattr(settings, "llm_provider", "mock") or "mock").lower() == "mock":
        return False
    return str(os.getenv("KEHA_LV_FORCE_MOCK_LLM", "")).strip().lower() not in {"1", "true", "yes", "on"}


INCOMPLETE_EXTRACTION_PREFIXES = (
    "ei ole",
    "ei selvitystä",
    "ei ilmoitettu",
    "ei määritelty",
    "ei esitetty",
    "ei saatavilla",
    "puuttuu",
    "hakemuksessa ei",
    "aineistossa ei",
    "tarkentuu myöhemmin",
)


def _extraction_has_substantive_value(item: dict[str, Any]) -> bool:
    value = str(item.get("extracted_value") or "").strip().lower()
    return bool(value) and not (len(value) < 300 and value.startswith(INCOMPLETE_EXTRACTION_PREFIXES))


PACKAGE_FINDING_SEVERITY = {"info": 0, "minor": 1, "major": 2, "blocking": 3}


def _package_finding_key(item: PackageFinding) -> str:
    if item.field_id:
        return f"field:{item.field_id}"
    if item.attachment_id:
        return f"attachment:{item.attachment_id}"
    normalized = re.sub(r"[^a-z0-9åäö]+", " ", item.title.lower()).strip()
    return f"title:{normalized}"


def _finding_tokens(item: PackageFinding) -> set[str]:
    stopwords = {"ja", "tai", "on", "ei", "eri", "tieto", "tiedot", "puuttuu", "ristiriitainen", "ristiriidassa"}
    return {token for token in re.findall(r"[a-z0-9åäö]+", item.title.lower()) if len(token) >= 4 and token not in stopwords}


def _findings_are_duplicates(left: PackageFinding, right: PackageFinding) -> bool:
    if _package_finding_key(left) == _package_finding_key(right):
        return True
    left_tokens = _finding_tokens(left)
    right_tokens = _finding_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    left_sources = {ref.source_id for ref in left.evidence_refs if ref.source_id}
    right_sources = {ref.source_id for ref in right.evidence_refs if ref.source_id}
    return overlap >= 0.6 or (overlap >= 0.35 and bool(left_sources & right_sources))


def dedupe_package_findings(findings: list[PackageFinding]) -> list[PackageFinding]:
    selected: list[PackageFinding] = []
    for item in findings:
        duplicate_index = next((index for index, current in enumerate(selected) if _findings_are_duplicates(current, item)), None)
        if duplicate_index is None:
            selected.append(item)
            continue
        current = selected[duplicate_index]
        current_rank = (PACKAGE_FINDING_SEVERITY[current.severity], len(current.evidence_refs), current.confidence)
        candidate_rank = (PACKAGE_FINDING_SEVERITY[item.severity], len(item.evidence_refs), item.confidence)
        if candidate_rank > current_rank:
            selected[duplicate_index] = item
    return selected


def explicit_contradiction_findings(attachments: list[dict[str, Any]]) -> list[PackageFinding]:
    markers = ("ristiriit", "eri asiakirjoissa", "vaihtelee asiakirjoissa", "poikkeaa hakemuksesta")
    findings: list[PackageFinding] = []
    seen: set[str] = set()
    for attachment in attachments:
        text = str(attachment.get("text_excerpt") or "")
        low = text.lower()
        for marker in markers:
            for match in re.finditer(re.escape(marker), low):
                start = max(0, match.start() - 180)
                end = min(len(text), match.end() + 240)
                quote = re.sub(r"\s+", " ", text[start:end]).strip()
                quote_low = quote.lower()
                if "mw" in quote_low or "teho" in quote_low:
                    title = "Laitoksen tehotiedot ovat ristiriidassa"
                elif "piipun" in quote_low:
                    title = "Piipun korkeus vaihtelee asiakirjoissa"
                elif "purkup" in quote_low or "p1" in quote_low or "p3" in quote_low:
                    title = "Vesien purkupiste on ristiriitainen"
                elif "etäis" in quote_low or "metri" in quote_low:
                    title = "Vaikutuskohteen etäisyys on ristiriitainen"
                elif "öljy" in quote_low or "polttoaine" in quote_low:
                    title = "Polttoaineen määrä vaihtelee asiakirjoissa"
                else:
                    title = "Aineiston tiedot ovat ristiriidassa"
                key = f"{title}:{attachment.get('id')}"
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    PackageFinding(
                        id=f"explicit_contradiction_{len(findings) + 1}",
                        severity="major",
                        title=title,
                        detail=quote[:420],
                        suggested_fix="Vahvista oikea arvo ja yhtenäistä hakemus sekä sitä koskevat liitteet.",
                        confidence=0.92,
                        evidence_refs=[
                            EvidenceRef(
                                source_id=str(attachment.get("id") or "document"),
                                title=str(attachment.get("filename") or "Hakemusasiakirja"),
                                locator="tekstipoiminta",
                                quote=quote[:500],
                                confidence=0.92,
                            )
                        ],
                    )
                )
                if len(findings) >= 8:
                    return findings
    return findings


def intake_analysis_questions(schema: dict[str, Any], app: dict[str, Any], attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    form_data = app.get("form_data", {})
    questions: list[dict[str, Any]] = []
    for schema_field in schema.get("fields", []):
        if schema_field.get("required") and not str(form_data.get(schema_field["id"], "") or "").strip():
            questions.append(
                {
                    "field_id": schema_field["id"],
                    "question": f"Täydennä kenttä: {schema_field.get('label', schema_field['id'])}.",
                    "reason": "Pakollinen tieto puuttuu hakemusluonnoksesta.",
                    "source": "gap_analyzer",
                }
            )
    for required in schema.get("required_attachments", []):
        if not attachment_requirement_covered(required, attachments):
            questions.append(
                {
                    "field_id": "",
                    "attachment_type": required,
                    "question": f"Lisätäänkö vaadittu liite: {required}?",
                    "reason": "Vaadittua liitettä ei ole merkitty viranomaiselle toimitettavaksi.",
                    "source": "gap_analyzer",
                }
            )
    return questions[:12]


def package_analysis_from_current_state(
    db: Any,
    case_id: str,
    permit_type: str,
    *,
    job_id: str = "",
    structure: Optional[dict[str, Any]] = None,
    settings: Any = None,
) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    package = current_material_package(db, case_id, permit_type)
    app = ensure_application(db, case_id, permit_type)
    schema = load_form_schema(permit_type)
    attachments = list_attachments(db, case_id, permit_type)
    wizard = wizard_step_status(db, case_id, permit_type)
    field_extractions = list((structure or artifact_json(db, case_id, "structure_extraction_result", permit_type, {"field_extractions": []})).get("field_extractions", []))
    extracted_field_ids = {str(item.get("field_id")) for item in field_extractions if item.get("field_id") and _extraction_has_substantive_value(item)}
    effective_form_data = dict(app.get("form_data", {}))
    for item in field_extractions:
        if item.get("field_id") and _extraction_has_substantive_value(item) and not effective_form_data.get(item["field_id"]):
            effective_form_data[item["field_id"]] = item["extracted_value"]
    issues = [
        issue
        for issue in deterministic_issues(permit_type, effective_form_data, attachments)
        if not (
            issue.id.startswith("missing_attachment_")
            and attachment_requirement_covered(str(issue.attachment_id or issue.id.removeprefix("missing_attachment_")), attachments)
        )
    ]
    question_app = dict(app)
    question_app["form_data"] = effective_form_data
    questions = intake_analysis_questions(schema, question_app, attachments)
    if _real_llm_enabled(settings):
        criteria = effective_criteria(db, case_id, permit_type)
        retrieval = build_retrieval_context(
            db,
            case_id,
            permit_type,
            "package_review",
            ["current_application_material", "guidance_reference", "municipal_rule"],
            max_chars=30000,
        )
        common = artifact_json(db, case_id, "lv_common_data", "main", {"data": DEFAULT_COMMON_DATA.copy()})
        compact_attachments = [
            {
                "id": item.get("id"),
                "filename": item.get("filename"),
                "primary_type": item.get("primary_type"),
                "delivery_scope": item.get("delivery_scope"),
                "summary": item.get("summary"),
                "text_excerpt": str(item.get("text_excerpt") or "")[:5000],
            }
            for item in attachments
        ]
        base_values = {
            "form_schema": json.dumps(schema, ensure_ascii=False),
            "form_data": json.dumps(effective_form_data, ensure_ascii=False),
            "attachments": json.dumps(compact_attachments, ensure_ascii=False),
            "deterministic_issues": json.dumps(_plain(issues), ensure_ascii=False),
            "criteria_and_context": json.dumps({"criteria": criteria, "retrieval": retrieval}, ensure_ascii=False),
            "common_data": json.dumps(common.get("data", {}), ensure_ascii=False),
            "attachment_context": json.dumps(retrieval, ensure_ascii=False),
        }
        agent_issues: list[Issue] = []
        vector_ids = azure_vector_store_ids(db, settings, case_id)
        for agent_name in ("completeness_validator", "consistency_validator"):
            response = run_json_agent(
                db,
                settings,
                case_id,
                agent_name,
                base_values,
                IssueListResponse,
                vector_ids=vector_ids,
                fallback={"issues": []},
                max_output_tokens=6000,
            )
            if isinstance(response, IssueListResponse):
                agent_issues.extend(response.issues)
            elif isinstance(response, dict):
                agent_issues.extend(response.get("issues", []))
        issues = merge_issues(issues, agent_issues)
    joined_text = "\n".join(str(att.get("text_excerpt") or att.get("summary") or "") for att in attachments).lower()
    specific_contradictions = [issue for issue in issues if issue.type == "inconsistent"]
    contradictory = bool(specific_contradictions) or any(needle in joined_text for needle in ["ristiriita", "ristiriitainen", "eri asiakirjoissa", "poikkeaa hakemuksesta"])
    findings: list[PackageFinding] = []
    for issue in issues:
        if issue.id.startswith("missing_") and issue.field_id in extracted_field_ids:
            continue
        findings.append(
            PackageFinding(
                id=issue.id,
                severity="minor" if issue.severity == "recommendation" else issue.severity,
                title=issue.title,
                detail=issue.detail,
                field_id=issue.field_id,
                attachment_id=issue.attachment_id,
                suggested_fix=issue.suggested_fix,
                confidence=issue.confidence,
                evidence_refs=issue.evidence_refs,
            )
        )
    fallback_contradictions = [] if specific_contradictions else explicit_contradiction_findings(attachments)
    findings.extend(fallback_contradictions)
    for field_id in wizard.get("missing_required_fields", []):
        if field_id in extracted_field_ids:
            continue
        field = next((item for item in schema.get("fields", []) if item.get("id") == field_id), {"label": field_id})
        findings.append(
            PackageFinding(
                id=f"missing_field_{field_id}",
                severity="major",
                title=f"Pakollinen tieto puuttuu: {field.get('label', field_id)}",
                detail="Hakemuspaketin analyysi ei löytänyt tälle kentälle riittävää tietoa.",
                field_id=field_id,
                suggested_fix="Hyväksy AI-poiminta, lisää tieto käsin tai täydennä aineistoa.",
                confidence=0.82,
                evidence_refs=[EvidenceRef(source_id="form_schema", title="Lomakevaatimus", locator=field_id, quote=str(field.get("label", field_id)), confidence=0.85)],
            )
        )
    if contradictory and not specific_contradictions and not fallback_contradictions:
        findings.append(
            PackageFinding(
                id="package_contradiction",
                severity="major",
                title="Aineistossa on ristiriitaisia tietoja",
                detail="Eri dokumentit näyttävät kuvaavan olennaisen tiedon eri tavoin. Hakijan kannattaa varmistaa oikea arvo ennen lähettämistä.",
                suggested_fix="Tarkista ristiriitaiset luvut ja päivitä hakemusteksti sekä liitteet samaan versioon.",
                confidence=0.78,
                evidence_refs=[EvidenceRef(source_id="document_inventory", title="Dokumenttien tekstipoiminta", locator="package", quote="Ristiriitainen tieto havaittu aineistoteksteissä.", confidence=0.78)],
            )
        )
    findings = dedupe_package_findings(findings)
    blocking = sum(1 for item in findings if item.severity == "blocking")
    major = sum(1 for item in findings if item.severity == "major")
    minor = sum(1 for item in findings if item.severity in {"minor", "info"})
    score = max(0, min(100, 100 - min(45, blocking * 18) - min(40, major * 6) - min(10, minor * 2)))
    if contradictory:
        readiness = "contradictory"
    elif score >= 86 and blocking == 0 and major <= 1:
        readiness = "ready"
    elif score >= 62 and blocking == 0:
        readiness = "almost_ready"
    else:
        readiness = "needs_completion"
    extraction_queue = [
        {
            "field_id": item.get("field_id"),
            "suggested_value": item.get("extracted_value"),
            "source_document": item.get("source_document"),
            "confidence": item.get("confidence", 0.5),
            "evidence_refs": item.get("evidence_refs", []),
            "status": "ready_for_review" if float(item.get("confidence") or 0) >= 0.7 else "needs_review",
        }
        for item in field_extractions
    ]
    suggestions = [
        {
            "field_id": item["field_id"],
            "suggested_value": item.get("suggested_value") or "",
            "source_document": item.get("source_document") or "",
            "confidence": item.get("confidence", 0.5),
            "status": item.get("status", "needs_review"),
        }
        for item in extraction_queue
        if item.get("field_id") and item.get("suggested_value")
    ]
    inventory = [
        {
            "document_id": att.get("id"),
            "filename": att.get("filename"),
            "document_role": att.get("primary_type"),
            "delivery_scope": att.get("delivery_scope"),
            "extraction_status": att.get("extraction_status"),
            "text_chars": att.get("text_chars") or len(str(att.get("text_excerpt") or "")),
            "confidence": att.get("confidence", 0.5),
        }
        for att in attachments
    ]
    summary = {
        "ready": "Paketti näyttää lähes lähetysvalmiilta. Tarkista vielä yksittäiset huomiot ennen lähetystä.",
        "almost_ready": "Paketti on täydennettävissä lähetysvalmiiksi muutamalla korjauksella.",
        "needs_completion": "Paketista puuttuu olennaisia tietoja tai liitteitä ennen lähetystä.",
        "contradictory": "Paketti sisältää ristiriitaisia tietoja ja vaatii hakijan tarkistuksen.",
    }[readiness]
    result = asdict(PackageAnalysisResult(
        package_id=str(package.get("package_id") or f"pkg_current_{permit_type}"),
        permit_type=permit_type,
        readiness=readiness,
        readiness_score=score,
        summary=summary,
        findings=findings[:30],
        questions=questions,
        suggestions=suggestions[:30],
        document_inventory=inventory,
        field_extractions=field_extractions,
        created_at=time.time(),
    ))
    result["job_id"] = job_id
    result["extraction_queue"] = extraction_queue
    save_artifact(db, case_id, "package_analysis_result", permit_type, result)
    save_artifact(db, case_id, "field_extraction_queue", permit_type, {"items": extraction_queue, "updated_at": time.time()})
    event(db, case_id, "materials.package.analyzed", "Material package analyzed", {"permit_type": permit_type, "readiness": readiness, "score": score})
    return result


def _build_intake_analysis_result(db: Any, case_id: str, permit_type: str, job_id: str, settings: Any = None) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    if permit_type == "environmental_industrial":
        structure = structure_environmental_application_from_attachments(db, case_id, settings=settings, track_job=False)
    else:
        structure = artifact_json(db, case_id, "structure_extraction_result", permit_type, {"field_extractions": [], "updated_field_count": 0})
    app = ensure_application(db, case_id, permit_type)
    schema = load_form_schema(permit_type)
    attachments = list_attachments(db, case_id, permit_type)
    wizard = wizard_step_status(db, case_id, permit_type)
    questions = intake_analysis_questions(schema, app, attachments)
    result = {
        "permit_type": permit_type,
        "job_id": job_id,
        "structured_field_count": len(structure.get("field_extractions", [])),
        "field_extractions": structure.get("field_extractions", []),
        "missing_required_fields": wizard.get("missing_required_fields", []),
        "missing_attachments": wizard.get("missing_attachments", []),
        "questions": questions,
        "attachment_count": len(attachments),
        "official_attachment_count": sum(1 for item in attachments if item.get("delivery_scope") == "official_submission"),
        "ai_context_attachment_count": sum(1 for item in attachments if item.get("delivery_scope") == "ai_context_only"),
        "created_at": time.time(),
    }
    package_result = package_analysis_from_current_state(db, case_id, permit_type, job_id=job_id, structure=structure, settings=settings)
    result["readiness"] = package_result["readiness"]
    result["readiness_score"] = package_result["readiness_score"]
    result["findings"] = package_result["findings"]
    result["suggestions"] = package_result["suggestions"]
    result["document_inventory"] = package_result["document_inventory"]
    result["extraction_queue"] = package_result["extraction_queue"]
    save_artifact(db, case_id, "intake_analysis", permit_type, result)
    event(db, case_id, "ai.intake_analysis.completed", "Intake analysis completed", {"permit_type": permit_type, "questions": len(questions)})
    return result


def _finalize_intake_analysis_job_in_store(db: Any, case_id: str, permit_type: str, job: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    try:
        result = _build_intake_analysis_result(db, case_id, permit_type, str(job.get("id") or ""))
        summary = f"Analyysi valmis: {len(result.get('questions', []))} kysymystä, {len(result.get('missing_required_fields', []))} kenttäpuutetta."
        failed = False
    except Exception as exc:
        summary = f"Aineiston analyysi epäonnistui: {exc}"
        failed = True
    agent_count = max(1, len(job.get("agents") or []))
    job["status"] = "failed" if failed else "completed"
    job["progress_current"] = agent_count
    job["progress_pct"] = 100
    job["eta_seconds"] = 0
    job["updated_at"] = now
    job["finished_at"] = now
    job["summary"] = summary
    job["result_artifact"] = f"intake_analysis:{application_id_for(permit_type)}"
    for agent in job.get("agents") or []:
        if isinstance(agent, dict):
            agent["status"] = "failed" if failed else "completed"
            agent["started_at"] = agent.get("started_at") or job.get("created_at") or now
            agent["finished_at"] = now
            agent["summary"] = agent.get("summary") or summary
    event(db, case_id, "ai.job.failed" if failed else "ai.job.completed", "AI job finished", {"job_id": job.get("id"), "permit_type": permit_type, "status": job["status"]})
    return job


def start_intake_analysis_job(
    db: Any,
    case_id: str,
    permit_type: str,
    settings: Any = None,
    *,
    managed_background: bool = False,
) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    return start_ai_job(
        db,
        case_id,
        permit_type,
        "intake_analysis",
        "Aineiston analyysi ja kysymyslista",
        ["material_classifier", "structure_extractor", "gap_analyzer", "question_generator"],
        eta_seconds=45 if settings is not None and str(getattr(settings, "llm_provider", "mock")).lower() != "mock" else 12,
        auto_finalize=not managed_background,
        managed_background=managed_background,
    )


def execute_intake_analysis_job(db: Any, case_id: str, permit_type: str, job_id: str, settings: Any = None) -> None:
    permit_type = application_id_for(permit_type)
    try:
        result = _build_intake_analysis_result(db, case_id, permit_type, job_id, settings=settings)
        complete_ai_job(
            db,
            case_id,
            permit_type,
            job_id,
            summary=f"Analyysi valmis: {len(result.get('findings', []))} havaintoa, valmius {result.get('readiness_score', 0)}/100.",
            result_artifact=f"intake_analysis:{permit_type}",
        )
    except Exception as exc:
        complete_ai_job(
            db,
            case_id,
            permit_type,
            job_id,
            summary=f"Aineiston analyysi epäonnistui: {str(exc)[:240]}",
            result_artifact=f"intake_analysis:{permit_type}",
            failed=True,
        )


def contextual_chat_context(db: Any, case_id: str, permit_type: str) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    app = ensure_application(db, case_id, permit_type)
    attachments = list_attachments(db, case_id, permit_type)
    usable = [a for a in attachments if a.get("delivery_scope") in {"official_submission", "ai_context_only"}]
    excluded = [a for a in attachments if a.get("delivery_scope") == "internal_authority_only"]
    package_analysis = artifact_json(db, case_id, "package_analysis_result", permit_type, {})
    return {
        "permit_type": permit_type,
        "form_data": app.get("form_data", {}),
        "field_sources": app.get("field_sources", {}),
        "package_analysis": {
            "readiness": package_analysis.get("readiness"),
            "readiness_score": package_analysis.get("readiness_score"),
            "summary": package_analysis.get("summary"),
            "findings": _list(package_analysis.get("findings"))[:8],
            "questions": _list(package_analysis.get("questions"))[:8],
        },
        "attachments": [
            {
                "filename": a.get("filename"),
                "primary_type": a.get("primary_type"),
                "delivery_scope": a.get("delivery_scope"),
                "excerpt": str(a.get("text_excerpt") or a.get("summary") or "")[:900],
            }
            for a in usable[:8]
        ],
        "used_attachment_filenames": [str(a.get("filename")) for a in usable],
        "excluded_attachment_count": len(excluded),
        "intake_questions": artifact_json(db, case_id, "intake_analysis", permit_type, {"questions": []}).get("questions", []),
    }


def _chat_proposed_changes(db: Any, case_id: str, permit_type: str, message: str) -> list[dict[str, Any]]:
    low = (message or "").lower()
    if not any(word in low for word in ["ehdota", "paranna", "täydennä", "taydenna", "korjaa", "muutos"]):
        return []
    app = ensure_application(db, case_id, permit_type)
    schema = load_form_schema(permit_type)
    target = next((f for f in schema.get("fields", []) if f.get("id") in low), None)
    if target is None:
        target = next((f for f in schema.get("fields", []) if f.get("ai_suggest") and not str(app.get("form_data", {}).get(f.get("id"), "") or "").strip()), None)
    if target is None:
        target = next((f for f in schema.get("fields", []) if f.get("ai_suggest")), None)
    if not target:
        return []
    suggestion = deterministic_suggestion(permit_type, target["id"], app.get("form_data", {}), list_attachments(db, case_id, permit_type))
    return [
        {
            "field_id": target["id"],
            "label": target.get("label", target["id"]),
            "suggested_value": suggestion.suggested_value,
            "rationale": "Chat ehdottaa muutosta käyttäjän hyväksyttäväksi. Lomakedataa ei muutettu.",
            "confidence": suggestion.confidence,
        }
    ]


def contextual_chat_answer(db: Any, case_id: str, permit_type: str, message: str, settings: Any = None) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    context = contextual_chat_context(db, case_id, permit_type)
    history = artifact_json(db, case_id, "contextual_chat", permit_type, {"messages": []})
    prompt = (
        "Olet lupahakemuksen contextual chat -avustaja. Vastaa suomeksi hakijan kysymykseen. "
        "Käytä vain annettua lomake- ja liitekontekstia. Älä käytä benchmark- tai viranomaisesimerkkiaineistoa. "
        "Älä muuta hakemuksen tietoja; jos ehdotat muutosta, kerro että käyttäjän pitää hyväksyä se erikseen.\n\n"
        f"Kysymys: {message}\n\n"
        f"Konteksti JSON: {json.dumps(context, ensure_ascii=False)}"
    )
    if settings is not None:
        client = create_llm_client(settings, purpose="chat")
        resp = client.responses(
            input_text=prompt,
            vector_store_ids=[],
            response_format=None,
            max_output_tokens=900,
            reasoning_effort="low",
            text_verbosity="medium",
        )
        answer = client.extract_output_text(resp) or ""
    else:
        analysis = context.get("package_analysis") or {}
        readiness = analysis.get("readiness") or "ei vielä analysoitu"
        finding_count = len(analysis.get("findings") or [])
        question_count = len(analysis.get("questions") or [])
        answer = (
            "Voin auttaa tarkistamaan hakemuksen nykyisiä tietoja, liitteitä ja viimeisintä pakettianalyysiä. "
            f"Paketin tila on {readiness}; analyysissä on {finding_count} havaintoa ja {question_count} täydennyskysymystä. "
            "En muuta lomaketta ilman erillistä hyväksyntää."
        )
    proposed_changes = _chat_proposed_changes(db, case_id, permit_type, message)
    if proposed_changes:
        pending = artifact_json(db, case_id, "ai_suggestions", permit_type, {"suggestions": []})
        for change in proposed_changes:
            evidence = [
                EvidenceRef(
                    source_id="contextual_chat",
                    title="Contextual chat",
                    locator="chat",
                    quote="Chat ehdotti muutosta käyttäjän hyväksyttäväksi.",
                    confidence=0.7,
                )
            ]
            if context["used_attachment_filenames"]:
                evidence.append(
                    EvidenceRef(
                        source_id="attachments",
                        title=", ".join(context["used_attachment_filenames"][:3]),
                        locator="document_context",
                        quote="Ehdotus perustuu normaaliin hakemus- ja AI-taustakontekstiin.",
                        confidence=0.68,
                    )
                )
            suggestion = Suggestion(
                field_id=change["field_id"],
                current_value=str(context["form_data"].get(change["field_id"], "") or ""),
                suggested_value=change["suggested_value"],
                diff_summary="Chatin ehdottama muutos, vaatii käyttäjän hyväksynnän.",
                rationale=change["rationale"],
                confidence=float(change.get("confidence") or 0.65),
                evidence_refs=evidence,
                criteria_version=criteria_version(permit_type, db, case_id),
                agent="contextual_chat_assistant",
            )
            pending.setdefault("suggestions", []).append(suggestion)
            change["suggestion_id"] = suggestion.id
        save_artifact(db, case_id, "ai_suggestions", permit_type, pending)
    if proposed_changes:
        answer = answer.rstrip() + "\n\nEhdotin alla muutosta hyväksyttäväksi. Se ei ole muuttanut hakemuksen tietoja."
    turn = {
        "id": f"chat_{uuid.uuid4().hex[:10]}",
        "message": message,
        "answer": answer,
        "proposed_changes": proposed_changes,
        "used_attachment_filenames": context["used_attachment_filenames"],
        "excluded_attachment_count": context["excluded_attachment_count"],
        "created_at": time.time(),
    }
    history.setdefault("messages", []).append(turn)
    history["messages"] = history["messages"][-30:]
    save_artifact(db, case_id, "contextual_chat", permit_type, history)
    event(db, case_id, "ai.chat.answered", "Contextual chat answered", {"permit_type": permit_type, "proposed_changes": len(proposed_changes)})
    return turn


def voice_feature_state(settings: Any = None) -> dict[str, Any]:
    mode = str(os.getenv("LUPADEMO_VOICE_MODE", "") or "").strip().lower()
    enabled_flag = str(os.getenv("LUPADEMO_ENABLE_VOICE", "") or os.getenv("ENABLE_VOICE_CHAT", "") or "").strip().lower() in {"1", "true", "yes", "on"}
    if mode not in {"", "disabled", "mock"}:
        mode = "disabled"
    if mode == "mock":
        enabled = True
        status = "mock"
        reason = "Mock-puhetila on käytössä ilman ulkoista realtime-yhteyttä."
    elif enabled_flag:
        enabled = False
        status = "disabled"
        reason = "Live-puhetta ei ole kytketty Lupademoon; käytä LUPADEMO_VOICE_MODE=mock testaukseen."
    else:
        enabled = False
        status = "disabled"
        reason = "Puheavustin on oletuksena poissa käytöstä."
    return {
        "enabled": enabled,
        "status": status,
        "mode": mode or "disabled",
        "reason": reason,
        "mock_available": True,
        "provider": str(getattr(settings, "llm_provider", "") or os.getenv("KEHA_LV_LLM_PROVIDER", "")),
    }


def voice_mock_turn(db: Any, case_id: str, permit_type: str, transcript: str, settings: Any = None) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    state = voice_feature_state(settings)
    history = artifact_json(db, case_id, "voice_chat", permit_type, {"turns": []})
    if not state.get("enabled"):
        result = {
            "id": f"voice_{uuid.uuid4().hex[:10]}",
            "status": "disabled",
            "transcript": transcript,
            "answer": state["reason"],
            "used_attachment_filenames": [],
            "excluded_attachment_count": 0,
            "created_at": time.time(),
        }
    else:
        context = contextual_chat_context(db, case_id, permit_type)
        answer = (
            "Mock-puheavustin kuuli kysymyksen ja käyttää samaa rajattua hakemus- ja liitekontekstia kuin tekstichat. "
            f"Kontekstissa on {len(context['used_attachment_filenames'])} sallittua liitettä ja "
            f"{context['excluded_attachment_count']} benchmark-/viranomaisliitettä rajattiin pois."
        )
        result = {
            "id": f"voice_{uuid.uuid4().hex[:10]}",
            "status": "mock_completed",
            "transcript": transcript,
            "answer": answer,
            "used_attachment_filenames": context["used_attachment_filenames"],
            "excluded_attachment_count": context["excluded_attachment_count"],
            "created_at": time.time(),
        }
    history.setdefault("turns", []).append(result)
    history["turns"] = history["turns"][-20:]
    save_artifact(db, case_id, "voice_chat", permit_type, history)
    event(db, case_id, "ai.voice.mock_turn", "Voice assistant mock turn recorded", {"permit_type": permit_type, "status": result["status"]})
    return result


def ensure_demo_case(db: Any) -> str:
    cases = db.list_cases()
    if cases:
        return cases[0]["id"]
    case_id = db.create_case("Lupademo: Koivutien pientalohanke", language="fi")
    db.set_case_status(case_id, "application_draft")
    ensure_seed_state(db, case_id)
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


def classify_attachment(filename: str) -> str:
    name = filename.lower()
    rules = [
        ("asemapiir", "site_plan"),
        ("sijaintikart", "site_map"),
        ("kartta", "site_map"),
        ("site", "site_plan"),
        ("pääpiir", "main_drawings"),
        ("paapiir", "main_drawings"),
        ("julkisivu", "facade_drawings"),
        ("huleves", "stormwater_plan"),
        ("ympäristölupahakemus", "environmental_application"),
        ("ymparistolupahakemus", "environmental_application"),
        ("lupahakemus", "environmental_application"),
        ("tiivistel", "public_summary"),
        ("perustila", "baseline_report"),
        ("asemakaava", "zoning_extract"),
        ("kaavaote", "zoning_extract"),
        ("kaavoitus", "zoning_extract"),
        ("kemikaali", "chemical_inventory"),
        ("ilmanlaatu", "air_quality_model"),
        ("savukaasu", "air_quality_model"),
        ("melu", "noise_model"),
        ("yva", "yva_statement"),
        ("bat", "bat_assessment"),
        ("tarkkailu", "monitoring_plan"),
        ("varautum", "risk_preparedness_plan"),
        ("maanomist", "landowner_permission"),
        ("alueen_haltija", "landowner_permission"),
        ("pelastussuunnitel", "rescue_plan"),
        ("liikenne", "traffic_plan"),
        ("meluilmo", "noise_notice"),
    ]
    for needle, type_ in rules:
        if needle in name:
            return type_
    return "other"


def safe_filename(filename: str) -> str:
    name = os.path.basename(filename or "liite")
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name or f"liite-{uuid.uuid4().hex[:8]}"


def validate_upload_bytes(filename: str, content: bytes, *, allow_zip: bool = False) -> None:
    clean_name = safe_filename(filename or "liite")
    ext = Path(clean_name).suffix.lower()
    allowed = set(ALLOWED_UPLOAD_EXTENSIONS)
    if allow_zip:
        allowed.add(".zip")
    if ext not in allowed:
        raise ValueError("unsupported_file_type")
    limit = MAX_UPLOAD_ZIP_BYTES if ext == ".zip" else MAX_UPLOAD_FILE_BYTES
    if len(content or b"") > limit:
        raise ValueError("file_too_large")


def _is_system_zip_member(filename: str) -> bool:
    parts = [part for part in Path(filename).parts if part not in {"", "."}]
    return any(part == "__MACOSX" or part.startswith(".") for part in parts)


def validate_zip_archive(zf: zipfile.ZipFile) -> None:
    members = [member for member in zf.infolist() if not member.is_dir()]
    if len(members) > MAX_UPLOAD_ZIP_FILE_COUNT:
        raise ValueError("zip_too_many_files")
    total_size = 0
    for member in members:
        filename = member.filename or ""
        if _is_system_zip_member(filename):
            continue
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            raise ValueError("unsupported_zip_member_type")
        if int(member.file_size or 0) > MAX_UPLOAD_FILE_BYTES:
            raise ValueError("zip_member_too_large")
        total_size += int(member.file_size or 0)
        if total_size > MAX_UPLOAD_ZIP_TOTAL_BYTES:
            raise ValueError("zip_uncompressed_too_large")


def extract_text_from_path(path: str, filename: str, *, max_chars: int = 24000) -> str:
    ext = os.path.splitext(filename or path)[1].lower()
    try:
        if ext == ".pdf":
            parts = []
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages[:20]:
                    text = page.extract_text() or ""
                    if text:
                        parts.append(text)
                    if sum(len(p) for p in parts) >= max_chars:
                        break
            return "\n\n".join(parts)[:max_chars]
        if ext == ".docx":
            doc = DocxDocument(path)
            return "\n".join([p.text for p in doc.paragraphs if p.text])[:max_chars]
        if ext == ".xlsx":
            wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
            rows = []
            for sheet in wb.sheetnames[:8]:
                ws = wb[sheet]
                rows.append(f"[Sheet] {sheet}")
                for row in ws.iter_rows(min_row=1, max_row=80, max_col=20, values_only=True):
                    line = "\t".join(str(c) for c in row if c is not None)
                    if line:
                        rows.append(line)
                    if sum(len(r) for r in rows) >= max_chars:
                        return "\n".join(rows)[:max_chars]
            return "\n".join(rows)[:max_chars]
        if ext in {".txt", ".md", ".csv", ".tsv"}:
            return Path(path).read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except Exception as exc:
        return f"[tekstinpoiminta epäonnistui: {exc}]"
    return ""


def extract_text_from_bytes(filename: str, content: bytes, *, max_chars: int = 24000) -> str:
    if not content:
        return ""
    suffix = Path(safe_filename(filename)).suffix or ".txt"
    with tempfile.NamedTemporaryFile(prefix="lupademo-upload-", suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        return extract_text_from_path(tmp_path, filename, max_chars=max_chars)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def extraction_metadata(text_excerpt: str = "", *, local_path: str = "") -> dict[str, Any]:
    text = text_excerpt or ""
    if text.startswith("[tekstinpoiminta epäonnistui:"):
        return {"extraction_status": "error", "text_chars": 0, "extraction_error": text.strip("[]")[:500]}
    if text:
        return {"extraction_status": "ready", "text_chars": len(text), "extraction_error": ""}
    if local_path:
        return {"extraction_status": "empty", "text_chars": 0, "extraction_error": ""}
    return {"extraction_status": "not_extracted", "text_chars": 0, "extraction_error": ""}


def safe_extract_zip(zf: zipfile.ZipFile, target_dir: Path) -> None:
    target_root = target_dir.resolve()
    for member in zf.infolist():
        member_path = (target_dir / member.filename).resolve()
        if target_root != member_path and target_root not in member_path.parents:
            raise ValueError(f"Unsafe zip member path: {member.filename}")
    zf.extractall(target_dir)


def save_upload_file(settings: Any, case_id: str, filename: str, content: bytes) -> str:
    base = Path(settings.uploads_dir) / "lupademo" / case_id
    base.mkdir(parents=True, exist_ok=True)
    target = base / f"{uuid.uuid4().hex[:8]}-{safe_filename(filename)}"
    target.write_bytes(content)
    return str(target)


def source_ref(title: str, source_id: str = "local_excerpt", quote: str = "", confidence: float = 0.65) -> dict[str, Any]:
    return {"source_id": source_id, "title": title, "locator": "local_excerpt", "quote": quote[:260], "confidence": confidence}


def retrieval_context(db: Any, case_id: str, permit_type: str, *, max_chars: int = 16000) -> dict[str, Any]:
    attachments = list_attachments(db, case_id, permit_type)
    excerpts = []
    used = 0
    for att in attachments:
        text = str(att.get("text_excerpt") or "")
        if not text:
            continue
        chunk = text[: max(0, max_chars - used)]
        if not chunk:
            break
        excerpts.append(
            {
                "source_id": att.get("id"),
                "title": att.get("filename"),
                "delivery_scope": att.get("delivery_scope"),
                "primary_type": att.get("primary_type"),
                "text": chunk,
            }
        )
        used += len(chunk)
    return {"mode": "local_excerpt", "excerpts": excerpts}


V3_SCOPE_TO_DELIVERY = {
    "current_application_material": {"official_submission", "ai_context_only"},
    "applicant_background_only": {"ai_context_only"},
    "guidance_reference": set(),
    "municipal_rule": set(),
    "semantic_form_model": set(),
    "example_case_benchmark": {"internal_authority_only"},
    "authority_example": {"internal_authority_only"},
}


def build_retrieval_context(
    db: Any,
    project_id: str,
    application_id: str,
    operation: str,
    requested_scopes: list[str] | tuple[str, ...] | None,
    tenant_id: str = "",
    *,
    include_benchmark_cases: bool = False,
    user_role: str = "applicant",
    max_chars: int = 16000,
) -> dict[str, Any]:
    requested = [str(scope) for scope in (requested_scopes or ["current_application_material"])]
    benchmark_requested = any(scope in {"example_case_benchmark", "authority_example"} for scope in requested)
    if benchmark_requested and not (include_benchmark_cases and user_role in {"admin", "eval"}):
        raise PermissionError("benchmark_scope_forbidden")
    allowed_delivery: set[str] = set()
    for scope in requested:
        allowed_delivery.update(V3_SCOPE_TO_DELIVERY.get(scope, set()))
    context_payload = retrieval_context(db, project_id, application_id, max_chars=max_chars)
    excerpts = [
        item
        for item in context_payload.get("excerpts", [])
        if not allowed_delivery or str(item.get("delivery_scope") or "") in allowed_delivery
    ]
    return {
        "project_id": project_id,
        "application_id": application_id_for(application_id),
        "operation": operation,
        "tenant_id": tenant_id,
        "requested_scopes": requested,
        "mode": "local_excerpt",
        "excerpts": excerpts,
        "excluded_benchmark": not benchmark_requested,
    }


def azure_vector_store_ids(db: Any, settings: Any, case_id: str) -> list[str]:
    case = db.get_case(case_id) or {}
    ids = vector_store_ids(case.get("case_vector_store_id"), settings, db, include_company=False, max_ids=0)
    research_vs = str(case.get("research_vector_store_id") or "").strip()
    if research_vs and not research_vs.startswith("ingest_failed:"):
        ids.append(research_vs)
    include_company = str(os.getenv("LUPADEMO_INCLUDE_COMPANY_VECTOR_STORES", "")).strip().lower() in {"1", "true", "yes", "on"}
    if include_company:
        ids.extend(settings.company_vector_store_ids)
    deduped: list[str] = []
    seen: set[str] = set()
    for vs_id in ids:
        if not vs_id or vs_id in seen:
            continue
        seen.add(vs_id)
        deduped.append(vs_id)
    return deduped[:3]


def azure_vector_store_manifest(db: Any, settings: Any, case_id: str, selected_ids: list[str]) -> dict[str, Any]:
    case = db.get_case(case_id) or {}
    case_store = str(case.get("case_vector_store_id") or "").strip()
    knowledge_store = str(case.get("research_vector_store_id") or "").strip()
    stores: list[dict[str, str]] = []
    for store_id in selected_ids:
        if store_id == case_store:
            stores.append(
                {
                    "vector_store_id": store_id,
                    "scope": "current_application_material",
                    "purpose": "Hakemuksen faktat ja hakijan sallitut liitteet",
                    "prohibited_use": "Ei normi- tai oikeuslähteenä",
                }
            )
        elif store_id == knowledge_store:
            stores.append(
                {
                    "vector_store_id": store_id,
                    "scope": "guidance_and_rules",
                    "purpose": "Ohjeet, lait, kriteerit ja kuntakohtaiset säännöt",
                    "prohibited_use": "Ei hakemuksen toteutuneina faktoina",
                }
            )
        else:
            stores.append(
                {
                    "vector_store_id": store_id,
                    "scope": "configured_knowledge_source",
                    "purpose": "Erikseen sallittu knowledge-lähde",
                    "prohibited_use": "Ei benchmark- tai viranomaisesimerkkiaineistoa",
                }
            )
    return {
        "stores": stores,
        "comparison_mode": "case_against_knowledge",
        "evidence_policy": "Säilytä case- ja knowledge-lähteet erillisinä evidence_refs-viitteissä.",
    }


def get_or_create_case_vector_store(db: Any, settings: Any, case_id: str, *, name_suffix: str = "attachments") -> str:
    case = db.get_case(case_id) or {}
    existing = str(case.get("case_vector_store_id") or "").strip()
    if existing and not existing.startswith("ingest_failed:"):
        return existing
    _backend = create_llm_client(settings, purpose="core")
    vector_store_id = _backend.create_vector_store(f"lupademo-{case_id[:8]}-{name_suffix}")
    db.set_case_vector_store(case_id, vector_store_id)
    return vector_store_id


def ingest_file_row_to_case_vector_store(db: Any, settings: Any, case_id: str, file_row_id: int) -> dict[str, Any]:
    row = db.get_file(int(file_row_id))
    if not row:
        raise KeyError(file_row_id)
    if int(row.get("vectorize") or 0) != 1:
        db.update_file(int(file_row_id), status="skipped", error=None)
        return {"file_row_id": int(file_row_id), "status": "skipped", "reason": "vectorize disabled"}
    local_path = str(row.get("local_path") or "")
    if not local_path or not os.path.exists(local_path):
        db.update_file(int(file_row_id), status="error", error=f"Missing local file: {local_path}")
        return {"file_row_id": int(file_row_id), "status": "error", "error": "missing local file"}
    vector_store_id = get_or_create_case_vector_store(db, settings, case_id)
    _backend = create_llm_client(settings, purpose="core")
    try:
        db.update_file(int(file_row_id), status="uploading", error=None)
        upload_id, azure_file_id = _backend.upload_file_from_path(local_path)
        db.update_file(int(file_row_id), azure_upload_id=upload_id, azure_file_id=azure_file_id, status="uploaded")
        vs_file_id = _backend.attach_file_to_vector_store(vector_store_id, azure_file_id)
        db.update_file(int(file_row_id), azure_vs_file_id=vs_file_id, status="ingesting")
        _backend.wait_vector_store_file_completed(vector_store_id, vs_file_id)
        db.update_file(int(file_row_id), status="ready", error=None)
        return {
            "file_row_id": int(file_row_id),
            "status": "ready",
            "vector_store_id": vector_store_id,
            "azure_file_id": azure_file_id,
            "azure_vs_file_id": vs_file_id,
        }
    except Exception as exc:
        db.update_file(int(file_row_id), status="error", error=str(exc))
        return {"file_row_id": int(file_row_id), "status": "error", "vector_store_id": vector_store_id, "error": str(exc)}


def run_json_agent(
    db: Any,
    settings: Any,
    case_id: str,
    agent: str,
    prompt_values: dict[str, Any],
    response_model: type[Any],
    *,
    vector_ids: Optional[list[str]] = None,
    fallback: Any = None,
    max_output_tokens: int = 4000,
    reasoning_effort: str = "medium",
) -> Any:
    prompt = render_agent_prompt(agent, prompt_values)
    retrieval_manifest = azure_vector_store_manifest(db, settings, case_id, vector_ids or [])
    if retrieval_manifest["stores"]:
        prompt += (
            "\n\nHAKUKONTEKSTIN SOPIMUS (pakollinen):\n"
            + json.dumps(retrieval_manifest, ensure_ascii=False, indent=2)
            + "\nVertaa case-aineiston väitteitä knowledge-aineiston vaatimuksiin. "
            "Älä käytä knowledge-aineistoa case-faktana tai case-aineistoa normilähteenä. "
            "Säilytä evidence_refs-viitteissä lähteen scope ja merkitse ristiriita tai puuttuva lähdetuki epävarmaksi."
        )
    client = create_llm_client(settings, purpose="core")
    try:
        payload = call_llm_json(
            client,
            prompt,
            db=db,
            case_id=case_id,
            task_id=None,
            agent=f"lupademo.{agent}",
            vector_store_ids_list=vector_ids,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            text_verbosity="low",
        )
        payload = normalize_agent_payload(response_model, payload)
        model = response_model(**payload)
        save_artifact(db, case_id, "agent_output", agent, {"agent": agent, "output": model, "used_vector_store_ids": vector_ids or [], "vector_store_scope_manifest": retrieval_manifest, "fallback_used": False})
        return model
    except Exception as exc:
        if vector_ids and "vector store" in str(exc).lower():
            try:
                payload = call_llm_json(
                    client,
                    prompt,
                    db=db,
                    case_id=case_id,
                    task_id=None,
                    agent=f"lupademo.{agent}",
                    vector_store_ids_list=[],
                    max_output_tokens=max_output_tokens,
                    reasoning_effort=reasoning_effort,
                    text_verbosity="low",
                )
                payload = normalize_agent_payload(response_model, payload)
                model = response_model(**payload)
                save_artifact(
                    db,
                    case_id,
                    "agent_output",
                    agent,
                    {
                        "agent": agent,
                        "output": model,
                        "used_vector_store_ids": [],
                        "vector_store_scope_manifest": retrieval_manifest,
                        "vector_store_retry_reason": str(exc)[:500],
                        "fallback_used": False,
                    },
                )
                return model
            except Exception as retry_exc:
                exc = retry_exc
        if fallback is None:
            raise
        model = fallback if isinstance(fallback, response_model) else response_model(**fallback)
        save_artifact(db, case_id, "agent_output", agent, {"agent": agent, "output": model, "used_vector_store_ids": vector_ids or [], "vector_store_scope_manifest": retrieval_manifest, "fallback_used": True, "fallback_reason": str(exc)[:500]})
        return model


def _coerce_confidence(value: Any, default: float = 0.6) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(float(value), 1.0))
    lowered = str(value or "").strip().lower()
    if lowered in {"high", "korkea", "strong"}:
        return 0.85
    if lowered in {"medium", "moderate", "keskitaso", "keski"}:
        return 0.6
    if lowered in {"low", "matala", "weak"}:
        return 0.35
    try:
        return max(0.0, min(float(lowered), 1.0))
    except Exception:
        return default


def _normalize_evidence_ref(ref: Any, ref_index: int) -> dict[str, Any] | None:
    if isinstance(ref, str):
        value = ref.strip()
        if not value:
            return None
        return {
            "source_id": value[:120],
            "title": "Agentin lähdehavainto",
            "locator": value[:240],
            "quote": "",
            "confidence": 0.5,
        }
    if not isinstance(ref, dict):
        return None
    return {
        "source_id": str(ref.get("source_id") or ref.get("id") or f"agent_ref_{ref_index}"),
        "title": str(ref.get("title") or ref.get("source") or "Agentin lähdehavainto"),
        "locator": str(ref.get("locator") or ref.get("path") or ref.get("field") or "agent_output"),
        "quote": str(ref.get("quote") or "")[:500],
        "confidence": _coerce_confidence(ref.get("confidence"), 0.5),
    }


def _normalize_evidence_refs(value: Any, *, limit: int = 4) -> list[dict[str, Any]]:
    refs = value if isinstance(value, list) else [value] if isinstance(value, (dict, str)) else []
    clean_refs = []
    for ref_index, ref in enumerate(refs[:limit]):
        clean_ref = _normalize_evidence_ref(ref, ref_index)
        if clean_ref is not None:
            clean_refs.append(clean_ref)
    return clean_refs


def normalize_agent_payload(response_model: type[Any], payload: dict[str, Any]) -> dict[str, Any]:
    if response_model is StructureExtractionResponse:
        extracted_fields = payload.get("extracted_fields", []) if isinstance(payload, dict) else []
        if not isinstance(extracted_fields, list):
            extracted_fields = []
        normalized_fields = []
        for item in extracted_fields[:80]:
            if not isinstance(item, dict):
                continue
            field_id = str(item.get("field_id") or item.get("id") or "").strip()
            if not field_id:
                continue
            normalized_fields.append(
                {
                    "field_id": field_id,
                    "value": str(item.get("value") or item.get("text") or ""),
                    "confidence": _coerce_confidence(item.get("confidence"), 0.55),
                    "evidence_refs": _normalize_evidence_refs(item.get("evidence_refs", []), limit=6),
                }
            )
        return {"request_id": str(payload.get("request_id") or ""), "extracted_fields": normalized_fields}
    if response_model in {AttachmentClassificationResponse, CaseworkerSummaryResponse, AuthorityMemoDraft}:
        normalized = dict(payload) if isinstance(payload, dict) else {}
        normalized["evidence_refs"] = _normalize_evidence_refs(normalized.get("evidence_refs", []), limit=8)
        return normalized
    if response_model is FieldSuggestionResponse:
        normalized = dict(payload) if isinstance(payload, dict) else {}
        suggestions = normalized.get("suggestions", [])
        if isinstance(suggestions, list):
            clean_suggestions = []
            for suggestion in suggestions:
                if not isinstance(suggestion, dict):
                    continue
                clean_suggestion = dict(suggestion)
                clean_suggestion["evidence_refs"] = _normalize_evidence_refs(clean_suggestion.get("evidence_refs", []), limit=6)
                clean_suggestions.append(clean_suggestion)
            normalized["suggestions"] = clean_suggestions
        return normalized
    if response_model is CriteriaAssessmentResponse:
        normalized = dict(payload) if isinstance(payload, dict) else {}
        results = normalized.get("criteria_results", [])
        if isinstance(results, list):
            clean_results = []
            for result in results:
                if not isinstance(result, dict):
                    continue
                clean_result = dict(result)
                clean_result["evidence_refs"] = _normalize_evidence_refs(clean_result.get("evidence_refs", []), limit=6)
                clean_results.append(clean_result)
            normalized["criteria_results"] = clean_results
        return normalized
    if response_model is not IssueListResponse:
        return payload
    issues = payload.get("issues", []) if isinstance(payload, dict) else []
    if not isinstance(issues, list):
        return {"issues": []}
    type_map = {
        "contradiction": "inconsistent",
        "conflict": "inconsistent",
        "inconsistency": "inconsistent",
        "warning": "risk",
        "uncertainty": "risk",
    }
    allowed_types = {"missing", "incomplete", "inconsistent", "risk", "recommendation"}
    allowed_severities = {"blocking", "major", "minor", "recommendation"}
    normalized = []
    for index, issue in enumerate(issues[:12]):
        if not isinstance(issue, dict):
            continue
        issue_type = type_map.get(str(issue.get("type") or "").strip().lower(), str(issue.get("type") or "risk").strip().lower())
        if issue_type not in allowed_types:
            issue_type = "risk"
        severity = str(issue.get("severity") or "major").strip().lower()
        if severity not in allowed_severities:
            severity = "major"
        normalized.append(
            {
                "id": str(issue.get("id") or f"agent_issue_{index + 1}"),
                "type": issue_type,
                "severity": severity,
                "title": str(issue.get("title") or "Agentin havainto"),
                "detail": str(issue.get("detail") or issue.get("description") or issue.get("rationale") or "Agentti tunnisti tarkistettavan kohdan."),
                "confidence": _coerce_confidence(issue.get("confidence"), 0.6),
                "field_id": issue.get("field_id") or None,
                "attachment_id": issue.get("attachment_id") or None,
                "suggested_fix": issue.get("suggested_fix") or issue.get("recommendation") or None,
                "evidence_refs": _normalize_evidence_refs(issue.get("evidence_refs", []), limit=4),
                "resolved": bool(issue.get("resolved", False)),
            }
        )
    return {"issues": normalized}


def classify_attachment_with_agent(db: Any, settings: Any, case_id: str, file_id: str, filename: str, delivery_scope: str, text_excerpt: str) -> AttachmentClassificationResponse:
    heuristic_type = classify_attachment(filename)
    fallback = {
        "file_id": file_id,
        "primary_type": heuristic_type,
        "type_tags": [],
        "summary": text_excerpt[:420] or f"Luokiteltu tiedostonimen perusteella: {filename}",
        "suggested_delivery_scope": delivery_scope,
        "evidence_refs": [source_ref(filename, file_id, filename, 0.7)],
        "confidence": 0.74,
    }
    response = run_json_agent(
        db,
        settings,
        case_id,
        "attachment_classifier",
        {"file_id": file_id, "filename": filename, "delivery_scope": delivery_scope, "text_excerpt": text_excerpt[:4000]},
        AttachmentClassificationResponse,
        fallback=fallback,
        max_output_tokens=2000,
        reasoning_effort="low",
    )
    if heuristic_type != "other" and response.primary_type in {"", "other", "unknown", "general_attachment"}:
        response.primary_type = heuristic_type
        response.type_tags = sorted(set([*response.type_tags, heuristic_type]))
        response.summary = response.summary or f"Luokiteltu tiedostonimen perusteella: {filename}"
    return response


def add_attachment(db: Any, case_id: str, permit_type: str, filename: str, size: int, delivery_scope: str, text_excerpt: str = "", local_path: str = "", settings: Any = None) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    attachments = artifact_json(db, case_id, "permit_attachments", permit_type, {"attachments": []})
    file_id = f"att_{uuid.uuid4().hex[:10]}"
    classification = None
    extraction = extraction_metadata(text_excerpt, local_path=local_path)
    if settings is not None:
        classification = classify_attachment_with_agent(db, settings, case_id, file_id, filename, delivery_scope, text_excerpt)
    attachment = {
        "id": file_id,
        "filename": filename,
        "size_bytes": size,
        "delivery_scope": delivery_scope if delivery_scope in {"official_submission", "ai_context_only", "internal_authority_only"} else "official_submission",
        "primary_type": classification.primary_type if classification else classify_attachment(filename),
        "type_tags": classification.type_tags if classification else [],
        "summary": classification.summary if classification else (text_excerpt[:500] or f"Luokiteltu tiedostonimen perusteella: {filename}"),
        "local_path": local_path,
        "text_excerpt": text_excerpt[:24000],
        "evidence_refs": _plain(classification.evidence_refs) if classification else [source_ref(filename, filename, filename, 0.7)],
        "confidence": classification.confidence if classification else 0.74,
        "created_at": time.time(),
        "extraction_status": extraction["extraction_status"],
        "text_chars": extraction["text_chars"],
        "extraction_error": extraction["extraction_error"],
    }
    attachments.setdefault("attachments", []).append(attachment)
    save_artifact(db, case_id, "permit_attachments", permit_type, attachments)
    if local_path and attachment["delivery_scope"] != "internal_authority_only":
        try:
            mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            file_row_id = db.add_file(
                case_id,
                filename,
                local_path,
                mime_type,
                size,
                kind="application",
                source_group=permit_type,
                type_primary=attachment["primary_type"],
                type_tags=json.dumps(attachment.get("type_tags") or [], ensure_ascii=False),
                vectorize=1,
            )
            attachment["file_row_id"] = file_row_id
            attachment["vector_store_status"] = "queued"
            if settings is not None:
                ingest_result = ingest_file_row_to_case_vector_store(db, settings, case_id, int(file_row_id))
                attachment["vector_store_status"] = ingest_result.get("status")
                attachment["vector_store_id"] = ingest_result.get("vector_store_id", "")
                attachment["azure_file_id"] = ingest_result.get("azure_file_id", "")
                attachment["azure_vs_file_id"] = ingest_result.get("azure_vs_file_id", "")
                if ingest_result.get("error"):
                    attachment["vector_store_error"] = str(ingest_result.get("error"))[:300]
        except Exception:
            attachment["vector_store_status"] = "error"
    elif attachment["delivery_scope"] == "internal_authority_only":
        attachment["vector_store_status"] = "excluded_by_policy"
    event(db, case_id, "attachment.uploaded", "Attachment uploaded", {"application_id": permit_type, "attachment_id": attachment["id"], "delivery_scope": attachment["delivery_scope"]})
    save_artifact(db, case_id, "permit_attachments", permit_type, attachments)
    return attachment


def document_asset_from_attachment(attachment: dict[str, Any], package_id: str) -> dict[str, Any]:
    refs = []
    for ref in attachment.get("evidence_refs") or []:
        try:
            refs.append(EvidenceRef(**ref))
        except Exception:
            continue
    asset = DocumentAsset(
        document_id=str(attachment.get("id") or f"doc_{uuid.uuid4().hex[:10]}"),
        package_id=package_id,
        filename=str(attachment.get("filename") or ""),
        mime_type=mimetypes.guess_type(str(attachment.get("filename") or ""))[0] or "application/octet-stream",
        size_bytes=int(attachment.get("size_bytes") or 0),
        document_role=str(attachment.get("primary_type") or "unknown"),
        delivery_scope=str(attachment.get("delivery_scope") or "official_submission"),
        extraction_status=str(attachment.get("extraction_status") or "not_started"),
        text_excerpt=str(attachment.get("text_excerpt") or attachment.get("summary") or "")[:1000],
        text_chars=int(attachment.get("text_chars") or len(str(attachment.get("text_excerpt") or ""))),
        confidence=_coerce_confidence(attachment.get("confidence"), 0.6),
        evidence_refs=refs,
    )
    return asdict(asset)


def save_material_package(
    db: Any,
    case_id: str,
    permit_type: str,
    display_name: str,
    attachments: list[dict[str, Any]],
    *,
    source_channel: str = "browser_upload",
    package_scope: str = "current_application_material",
    package_id: str = "",
    status: str = "ready",
    expected_document_count: int = 0,
) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    package_id = package_id or f"pkg_{uuid.uuid4().hex[:10]}"
    existing_package = artifact_json(db, case_id, "material_package", permit_type, {})
    expected_document_count = expected_document_count or int(existing_package.get("expected_document_count") or 0)
    package = asdict(MaterialPackage(
        package_id=package_id,
        project_id=case_id,
        application_id=permit_type,
        display_name=display_name or "Hakemusaineistopaketti",
        source_channel=source_channel,
        package_scope=package_scope,
        documents=[DocumentAsset(**document_asset_from_attachment(att, package_id)) for att in attachments],
        expected_document_count=expected_document_count or len(attachments),
        status=status,
        created_at=time.time(),
    ))
    store = artifact_json(db, case_id, "material_packages", permit_type, {"packages": []})
    store["packages"] = [item for item in store.get("packages", []) if item.get("package_id") != package_id]
    store.setdefault("packages", []).append(package)
    store["packages"] = store["packages"][-12:]
    save_artifact(db, case_id, "material_packages", permit_type, store)
    save_artifact(db, case_id, "material_package", permit_type, package)
    event(db, case_id, "materials.package.ready", "Material package ready", {"permit_type": permit_type, "package_id": package_id, "documents": len(attachments)})
    return package


def current_material_package(db: Any, case_id: str, permit_type: str) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    package = artifact_json(db, case_id, "material_package", permit_type, {})
    if package:
        return package
    attachments = list_attachments(db, case_id, permit_type)
    if not attachments:
        return {}
    return save_material_package(db, case_id, permit_type, "Nykyinen hakemusaineisto", attachments, source_channel="seed")


def find_material_package(db: Any, package_id: str) -> dict[str, Any]:
    for case in db.list_cases():
        case_id = str(case.get("id") or "")
        for permit_type in PERMIT_SCHEMAS:
            package = artifact_json(db, case_id, "material_package", permit_type, {})
            if isinstance(package, dict) and package.get("package_id") == package_id:
                return {"case_id": case_id, "permit_type": permit_type, "package": package}
    raise KeyError(package_id)


def suggested_scope_for_attachment(primary_type: str, filename: str = "") -> str:
    name = (filename or "").lower()
    authority_types = {"authority_notice", "authority_additional_info_request", "decision_or_notice"}
    if primary_type in authority_types or any(needle in name for needle in ["päätös", "paatos", "kuulutus", "lausuntopyyntö", "lausuntopyynto", "vireilletulo"]):
        return "internal_authority_only"
    if any(needle in name for needle in ["muistiinpano", "tyoversio", "työversio", "tyoaineisto", "työaineisto", "tausta-aineisto", "taustaa"]):
        return "ai_context_only"
    if primary_type in {"chemical_inventory"} and any(needle in name for needle in ["tyoversio", "työversio", "tausta-aineisto"]):
        return "ai_context_only"
    return "official_submission"


def ingest_application_zip(
    db: Any,
    settings: Any,
    case_id: str,
    permit_type: str,
    filename: str,
    content: bytes,
    *,
    package_id: str = "",
    display_name: str = "",
) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    archive_name = safe_filename(filename or "aineisto.zip")
    validate_upload_bytes(archive_name, content, allow_zip=True)
    archive_path = save_upload_file(settings, case_id, archive_name, content)
    target_dir = Path(settings.uploads_dir) / "lupademo" / case_id / f"zip-{uuid.uuid4().hex[:8]}"
    target_dir.mkdir(parents=True, exist_ok=True)
    added: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    with zipfile.ZipFile(archive_path) as zf:
        validate_zip_archive(zf)
        safe_extract_zip(zf, target_dir)
    for path in sorted(target_dir.rglob("*")):
        if not path.is_file():
            continue
        rel_name = str(path.relative_to(target_dir))
        if path.name.startswith(".") or "/__MACOSX/" in f"/{rel_name}":
            skipped.append({"filename": rel_name, "reason": "system_file"})
            continue
        extracted_text = extract_text_from_path(str(path), path.name)
        primary_type = classify_attachment(rel_name)
        scope = suggested_scope_for_attachment(primary_type, rel_name)
        if scope == "internal_authority_only":
            skipped.append({
                "filename": rel_name,
                "reason": "authority_document_not_ingested",
            })
            continue
        attachment = add_attachment(
            db,
            case_id,
            permit_type,
            rel_name,
            path.stat().st_size,
            scope,
            extracted_text,
            local_path=str(path),
            settings=settings,
        )
        attachment["primary_type"] = primary_type if attachment.get("primary_type") in {"", "other", "unknown"} else attachment.get("primary_type", primary_type)
        attachment["zip_source"] = archive_name
        added.append(attachment)
    result = {
        "archive": archive_name,
        "permit_type": permit_type,
        "added_count": len(added),
        "skipped_count": len(skipped),
        "added": added,
        "skipped": skipped,
        "created_at": time.time(),
    }
    result["material_package"] = save_material_package(
        db,
        case_id,
        permit_type,
        display_name or archive_name,
        added,
        source_channel="browser_upload",
        package_id=package_id,
    )
    save_artifact(db, case_id, "material_intake", permit_type, result)
    event(db, case_id, "materials.zip.ingested", "Application material zip ingested", {"permit_type": permit_type, "archive": archive_name, "added_count": len(added)})
    return result


def ingest_application_files(
    db: Any,
    settings: Any,
    case_id: str,
    permit_type: str,
    files: list[tuple[str, bytes]],
    *,
    display_name: str = "",
    package_id: str = "",
) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    added: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for filename, content in files:
        clean_name = safe_filename(filename or "dokumentti")
        if not content:
            skipped.append({"filename": clean_name, "reason": "empty_file"})
            continue
        validate_upload_bytes(clean_name, content)
        local_path = save_upload_file(settings, case_id, clean_name, content)
        text = extract_text_from_path(local_path, clean_name)
        primary_type = classify_attachment(clean_name)
        scope = suggested_scope_for_attachment(primary_type, clean_name)
        if scope == "internal_authority_only":
            skipped.append({
                "filename": clean_name,
                "reason": "authority_document_not_ingested",
            })
            continue
        attachment = add_attachment(
            db,
            case_id,
            permit_type,
            clean_name,
            len(content),
            scope,
            text,
            local_path=local_path,
            settings=settings,
        )
        attachment["primary_type"] = primary_type if attachment.get("primary_type") in {"", "other", "unknown"} else attachment.get("primary_type", primary_type)
        added.append(attachment)
    package = save_material_package(
        db,
        case_id,
        permit_type,
        display_name or f"Hakemusaineisto {time.strftime('%Y-%m-%d %H:%M')}",
        added,
        source_channel="browser_upload",
        package_id=package_id,
    )
    result = {
        "permit_type": permit_type,
        "added_count": len(added),
        "skipped_count": len(skipped),
        "added": added,
        "skipped": skipped,
        "material_package": package,
        "created_at": time.time(),
    }
    save_artifact(db, case_id, "material_intake", permit_type, result)
    event(db, case_id, "materials.files.ingested", "Application material files ingested", {"permit_type": permit_type, "package_id": package["package_id"], "added_count": len(added)})
    return result


def ingest_application_text(
    db: Any,
    settings: Any,
    case_id: str,
    permit_type: str,
    title: str,
    text: str,
    *,
    delivery_scope: str = "ai_context_only",
    package_id: str = "",
    display_name: str = "",
) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    clean_text = str(text or "").strip()
    if not clean_text:
        raise ValueError("empty_text_material")
    scope = delivery_scope if delivery_scope in {"official_submission", "ai_context_only"} else "ai_context_only"
    base_name = safe_filename(title or "vapaamuotoinen_kuvaus")
    if not base_name.lower().endswith(".txt"):
        base_name = f"{base_name}.txt"
    content = clean_text.encode("utf-8")
    local_path = save_upload_file(settings, case_id, base_name, content)
    attachment = add_attachment(
        db,
        case_id,
        permit_type,
        base_name,
        len(content),
        scope,
        clean_text,
        local_path=local_path,
        settings=settings,
    )
    if attachment.get("primary_type") in {"", "other", "unknown", "general_attachment"}:
        attachment["primary_type"] = "free_text_description"
    attachments = list_attachments(db, case_id, permit_type)
    package = save_material_package(
        db,
        case_id,
        permit_type,
        display_name or f"Hakemusaineisto ja vapaateksti {time.strftime('%Y-%m-%d %H:%M')}",
        attachments,
        source_channel="browser_text",
        package_id=package_id,
    )
    result = {
        "permit_type": permit_type,
        "added_count": 1,
        "skipped_count": 0,
        "added": [attachment],
        "skipped": [],
        "material_package": package,
        "created_at": time.time(),
    }
    save_artifact(db, case_id, "material_intake", permit_type, result)
    event(db, case_id, "materials.text.ingested", "Application material free text ingested", {"permit_type": permit_type, "attachment_id": attachment["id"], "delivery_scope": scope})
    return result


def create_pending_material_package(db: Any, case_id: str, permit_type: str, display_name: str, expected_document_count: int = 0) -> dict[str, Any]:
    return save_material_package(
        db,
        case_id,
        permit_type,
        display_name,
        [],
        source_channel="browser_upload",
        status="processing",
        expected_document_count=expected_document_count,
    )


def start_material_ingest_job(db: Any, case_id: str, permit_type: str, document_count: int, *, azure_enabled: bool) -> dict[str, Any]:
    per_document = 12 if azure_enabled else 4
    eta_seconds = max(15, 8 + max(1, document_count) * per_document)
    return start_ai_job(
        db,
        case_id,
        permit_type,
        "material_ingest",
        "Aineiston vastaanotto ja valmistelu",
        ["archive_validator", "text_extractor", "material_classifier", "vector_indexer"],
        eta_seconds=eta_seconds,
        managed_background=True,
    )


def execute_material_ingest_job(
    db: Any,
    settings: Any,
    case_id: str,
    permit_type: str,
    package_id: str,
    job_id: str,
    display_name: str,
    *,
    zip_payload: Optional[tuple[str, bytes]] = None,
    files_payload: Optional[list[tuple[str, bytes]]] = None,
    text_body: str = "",
) -> None:
    permit_type = application_id_for(permit_type)
    try:
        if zip_payload:
            ingest_application_zip(
                db,
                settings,
                case_id,
                permit_type,
                zip_payload[0],
                zip_payload[1],
                package_id=package_id,
                display_name=display_name,
            )
        if files_payload:
            ingest_application_files(
                db,
                settings,
                case_id,
                permit_type,
                files_payload,
                display_name=display_name,
                package_id=package_id,
            )
        if text_body.strip():
            ingest_application_text(
                db,
                settings,
                case_id,
                permit_type,
                "Hakijan kuvaus",
                text_body,
                delivery_scope="ai_context_only",
                package_id=package_id,
                display_name=display_name,
            )
        attachments = list_attachments(db, case_id, permit_type)
        save_material_package(
            db,
            case_id,
            permit_type,
            display_name,
            attachments,
            source_channel="browser_upload",
            package_id=package_id,
            status="ready",
        )
        complete_ai_job(
            db,
            case_id,
            permit_type,
            job_id,
            summary=f"Aineisto valmis: {len(attachments)} asiakirjaa käsitelty.",
            result_artifact=f"material_package:{package_id}",
        )
    except Exception as exc:
        attachments = list_attachments(db, case_id, permit_type)
        save_material_package(
            db,
            case_id,
            permit_type,
            display_name,
            attachments,
            source_channel="browser_upload",
            package_id=package_id,
            status="failed",
        )
        complete_ai_job(
            db,
            case_id,
            permit_type,
            job_id,
            summary=f"Aineiston käsittely epäonnistui: {str(exc)[:240]}",
            result_artifact=f"material_package:{package_id}",
            failed=True,
        )


def list_attachments(db: Any, case_id: str, permit_type: str) -> list[dict[str, Any]]:
    return artifact_json(db, case_id, "permit_attachments", application_id_for(permit_type), {"attachments": []}).get("attachments", [])


def attachment_extraction_summary(db: Any, case_id: str) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_permit_type: dict[str, dict[str, int]] = {}
    samples: list[dict[str, Any]] = []
    attachment_count = 0
    extracted_count = 0
    total_text_chars = 0
    for permit_type in PERMIT_SCHEMAS:
        permit_stats = {"attachment_count": 0, "ready_count": 0, "error_count": 0, "text_chars": 0}
        for attachment in list_attachments(db, case_id, permit_type):
            attachment_count += 1
            permit_stats["attachment_count"] += 1
            status = str(attachment.get("extraction_status") or ("ready" if attachment.get("text_excerpt") else "not_extracted"))
            by_status[status] = by_status.get(status, 0) + 1
            text_chars = int(attachment.get("text_chars") or len(str(attachment.get("text_excerpt") or "")))
            total_text_chars += text_chars
            permit_stats["text_chars"] += text_chars
            if status == "ready":
                extracted_count += 1
                permit_stats["ready_count"] += 1
            if status == "error":
                permit_stats["error_count"] += 1
            if len(samples) < 8:
                samples.append(
                    {
                        "permit_type": permit_type,
                        "filename": attachment.get("filename", ""),
                        "delivery_scope": attachment.get("delivery_scope", ""),
                        "primary_type": attachment.get("primary_type", ""),
                        "extraction_status": status,
                        "text_chars": text_chars,
                        "vector_store_status": attachment.get("vector_store_status", ""),
                    }
                )
        by_permit_type[permit_type] = permit_stats
    return {
        "attachment_count": attachment_count,
        "extracted_text_attachment_count": extracted_count,
        "failed_extraction_attachment_count": by_status.get("error", 0),
        "total_text_chars": total_text_chars,
        "by_status": by_status,
        "by_permit_type": by_permit_type,
        "sample_attachments": samples,
    }


def set_attachment_scope(db: Any, case_id: str, permit_type: str, attachment_id: str, delivery_scope: str) -> list[dict[str, Any]]:
    art = artifact_json(db, case_id, "permit_attachments", application_id_for(permit_type), {"attachments": []})
    for att in art.get("attachments", []):
        if att.get("id") == attachment_id:
            att["delivery_scope"] = delivery_scope
            att["updated_at"] = time.time()
    save_artifact(db, case_id, "permit_attachments", application_id_for(permit_type), art)
    event(db, case_id, "attachment.scope.changed", "Attachment scope changed", {"attachment_id": attachment_id, "delivery_scope": delivery_scope})
    return art.get("attachments", [])


def update_attachment_metadata(
    db: Any,
    case_id: str,
    permit_type: str,
    attachment_id: str,
    *,
    primary_type: str = "",
    delivery_scope: str = "",
) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    art = artifact_json(db, case_id, "permit_attachments", permit_type, {"attachments": []})
    updated = None
    for att in art.get("attachments", []):
        if att.get("id") == attachment_id:
            if primary_type:
                att["primary_type"] = primary_type
            if delivery_scope:
                att["delivery_scope"] = delivery_scope if delivery_scope in {"official_submission", "ai_context_only", "internal_authority_only"} else att.get("delivery_scope", "official_submission")
            att["updated_at"] = time.time()
            updated = att
            break
    if updated is None:
        raise KeyError(attachment_id)
    save_artifact(db, case_id, "permit_attachments", permit_type, art)
    event(db, case_id, "attachment.metadata.updated", "Attachment metadata updated", {"attachment_id": attachment_id, "primary_type": primary_type, "delivery_scope": delivery_scope})
    return updated


def delete_attachment(db: Any, case_id: str, permit_type: str, attachment_id: str) -> bool:
    permit_type = application_id_for(permit_type)
    art = artifact_json(db, case_id, "permit_attachments", permit_type, {"attachments": []})
    kept = []
    removed = None
    for att in art.get("attachments", []):
        if att.get("id") == attachment_id:
            removed = att
        else:
            kept.append(att)
    if not removed:
        return False
    art["attachments"] = kept
    save_artifact(db, case_id, "permit_attachments", permit_type, art)
    file_row_id = removed.get("file_row_id")
    if file_row_id:
        try:
            db.delete_file(int(file_row_id))
        except Exception:
            pass
    event(db, case_id, "attachment.deleted", "Attachment deleted", {"attachment_id": attachment_id, "application_id": permit_type})
    return True


def environmental_material_story(story_id: str = "generic_industrial_environmental_demo") -> dict[str, Any]:
    for story in ENVIRONMENTAL_MATERIAL_STORIES:
        if story["id"] == story_id:
            return story
    return ENVIRONMENTAL_MATERIAL_STORIES[0]


def seed_environmental_material_story(db: Any, case_id: str, story_id: str = "generic_industrial_environmental_demo") -> dict[str, Any]:
    story = environmental_material_story(story_id)
    existing = list_attachments(db, case_id, "environmental_industrial")
    if existing:
        save_artifact(db, case_id, "environmental_material_story", "main", {"story": story, "seeded": False, "reason": "attachments_already_present"})
        return {"story": story, "seeded_count": 0, "attachments": existing}
    seeded = []
    for doc in story.get("documents", []):
        attachment = add_attachment(
            db,
            case_id,
            "environmental_industrial",
            doc["filename"],
            len(doc.get("text", "").encode("utf-8")),
            doc.get("delivery_scope", "official_submission"),
            doc.get("text", ""),
        )
        attachment["primary_type"] = doc.get("primary_type", attachment.get("primary_type"))
        attachment["summary"] = doc.get("text", "")[:400]
        attachment["document_story_id"] = story["id"]
        seeded.append(attachment)
    save_artifact(db, case_id, "permit_attachments", "environmental_industrial", {"attachments": seeded})
    save_artifact(db, case_id, "environmental_material_story", "main", {"story": story, "seeded": True, "seeded_count": len(seeded)})
    event(db, case_id, "environmental.materials.seeded", "Environmental material story seeded", {"story_id": story["id"], "count": len(seeded)})
    return {"story": story, "seeded_count": len(seeded), "attachments": seeded}


def _keyword_snippet(text: str, keywords: list[str], fallback: str = "") -> str:
    normalized = text.replace("\r", "\n")
    parts = [part.strip() for part in re.split(r"[\n.]", normalized) if part.strip()]
    for keyword in keywords:
        for part in parts:
            if keyword in part.lower():
                return part[:520]
    return fallback[:520]


def structure_environmental_application_from_attachments(
    db: Any,
    case_id: str,
    settings: Any = None,
    *,
    track_job: bool = True,
) -> dict[str, Any]:
    permit_type = "environmental_industrial"
    job = start_ai_job(db, case_id, permit_type, "structure_extraction", "Hakemuksen rakenteistus liitteistä", ["structure_extractor", "gap_analyzer"], eta_seconds=20) if track_job else None
    ensure_application(db, case_id, permit_type)
    attachments = list_attachments(db, case_id, permit_type)
    official_or_ai = [a for a in attachments if a.get("delivery_scope") in {"official_submission", "ai_context_only"}]
    joined = "\n".join(str(a.get("text_excerpt") or a.get("summary") or "") for a in official_or_ai)
    low_joined = joined.lower()
    extracted: dict[str, str] = {}
    field_keywords = {
        "activity_name": ["toiminnan nimi", "hankkeen nimi", "laitoksen nimi"],
        "activity_description": ["toiminta", "laitos", "hanke", "biokaasu", "biohiili", "lämpö", "prosessi"],
        "permit_basis": ["ympäristölupa", "luvanvar", "ympäristönsuojelulain"],
        "competent_authority": ["aluehallintovirasto", "avi", "kunnan ympäristönsuojelu"],
        "site_location": ["sijaitsee", "sijainti", "kiinteistö", "laitostie", "teollisuusalue"],
        "zoning_context": ["kaava", "kaavoitus", "teollisuusalue", "asemakaava"],
        "sensitive_receptors": ["asutus", "häiriinty", "pohjaves", "natura", "suojelukoh"],
        "production_capacity": ["kapasiteetti", "tonnia", "mw", "tuotanto", "vuodessa"],
        "process_description": ["prosessi", "vastaanotto", "murskaus", "kuivaus", "mädätys", "pyrolyysi"],
        "raw_materials_chemicals": ["kemikaali", "varastointi", "raaka-aine", "syöte", "lipeä", "happo"],
        "water_and_wastewater": ["jäteves", "vesi", "huleves", "viemäri"],
        "air_emissions": ["päästöt ilmaan", "haju", "pöly", "savukaasu", "ilmapäästö"],
        "waste": ["jäte", "sivutuote", "tuhka", "rejekti"],
        "environmental_impacts": ["ympäristövaikut", "melu", "liikenne", "vaikutus"],
        "risk_preparedness": ["riski", "onnettomuus", "häiriö", "varautuminen", "paloturvallisuus"],
        "bat_assessment": ["bat", "paras käyttökelpoinen", "energiatehokkuus"],
        "monitoring_plan": ["tarkkailu", "raportointi", "mittaus", "seuranta"],
        "yva_context": ["yva", "perusteltu päätelmä"],
    }
    for field_id, keywords in field_keywords.items():
        snippet = _keyword_snippet(joined, keywords)
        if snippet:
            extracted[field_id] = snippet
    if extracted.get("activity_description") and "teollinen tuotantolaitos" not in extracted["activity_description"].lower():
        extracted["activity_description"] = f"Teollinen tuotantolaitos: {extracted['activity_description']}"
    if "aluehallintovirasto" in low_joined:
        extracted["competent_authority"] = "Aluehallintovirasto"
    elif re.search(r"\bavi\b", low_joined):
        extracted["competent_authority"] = "AVI"
    if "ympäristölupa" in low_joined and "permit_basis" not in extracted:
        extracted["permit_basis"] = "Aineisto viittaa ympäristöluvanvaraiseen toimintaan; säädösperusta on tarkistettava ennen lähettämistä."
    source_by_field = []
    for field_id, value in extracted.items():
        source = next((a for a in official_or_ai if field_id.split("_")[0].lower() in str(a.get("text_excerpt", "")).lower()), None) or (official_or_ai[0] if official_or_ai else {})
        source_by_field.append(
            {
                "field_id": field_id,
                "extracted_value": value,
                "source_document": source.get("filename", "synthetic_environmental_story"),
                "confidence": 0.78 if source else 0.55,
                "evidence_refs": [source_ref(source.get("filename", "Hakemusaineisto"), source.get("id", "local_excerpt"), value, 0.72)] if source else [],
            }
        )
    if _real_llm_enabled(settings):
        allowed_fields = {str(field.get("id")) for field in load_form_schema(permit_type).get("fields", [])}
        fallback_fields = [
            {
                "field_id": item["field_id"],
                "value": item["extracted_value"],
                "confidence": item["confidence"],
                "evidence_refs": item.get("evidence_refs", []),
            }
            for item in source_by_field
        ]
        response = run_json_agent(
            db,
            settings,
            case_id,
            "structure_extractor",
            {
                "request_id": f"structure_{uuid.uuid4().hex[:8]}",
                "form_schema": json.dumps(load_form_schema(permit_type), ensure_ascii=False),
                "source_text": joined[:32000],
            },
            StructureExtractionResponse,
            vector_ids=azure_vector_store_ids(db, settings, case_id),
            fallback={"request_id": f"structure_{uuid.uuid4().hex[:8]}", "extracted_fields": fallback_fields},
            max_output_tokens=8000,
        )
        source_by_field = []
        if isinstance(response, StructureExtractionResponse):
            fields_iter = response.extracted_fields
        else:
            fields_iter = []
        for schema_field in fields_iter:
            if schema_field.field_id not in allowed_fields or not schema_field.value.strip():
                continue
            refs = [ref for ref in schema_field.evidence_refs if isinstance(ref, dict)]
            source_by_field.append(
                {
                    "field_id": schema_field.field_id,
                    "extracted_value": schema_field.value.strip(),
                    "source_document": str((refs[0].get("title") if refs else "Hakemusaineisto") or "Hakemusaineisto"),
                    "confidence": schema_field.confidence,
                    "evidence_refs": refs,
                }
            )
    result = {
        "permit_type": permit_type,
        "field_extractions": source_by_field,
        "updated_field_count": 0,
        "drafted_field_count": len(source_by_field),
        "requires_user_acceptance": True,
        "created_at": time.time(),
    }
    save_artifact(db, case_id, "structure_extraction_result", permit_type, result)
    extraction_queue = [
        {
            "field_id": item.get("field_id"),
            "suggested_value": item.get("extracted_value"),
            "source_document": item.get("source_document"),
            "confidence": item.get("confidence", 0.5),
            "evidence_refs": item.get("evidence_refs", []),
            "status": "ready_for_review" if float(item.get("confidence") or 0) >= 0.7 else "needs_review",
        }
        for item in source_by_field
        if item.get("field_id") and item.get("extracted_value")
    ]
    save_artifact(db, case_id, "field_extraction_queue", permit_type, {"items": extraction_queue, "updated_at": time.time()})
    if job is not None:
        complete_ai_job(db, case_id, permit_type, job["id"], summary=f"Rakenteistus valmis: {len(source_by_field)} kenttää odottaa hyväksyntää.", result_artifact=f"structure_extraction_result:{permit_type}")
    event(db, case_id, "environmental.structure_extracted", "Environmental application structured from attachments", {"fields": len(source_by_field), "requires_user_acceptance": True})
    return result


def accept_extraction_queue_item(
    db: Any,
    case_id: str,
    permit_type: str,
    field_id: str,
    edited_value: str = "",
) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    queue_payload = artifact_json(db, case_id, "field_extraction_queue", permit_type, {"items": []})
    items = _list(queue_payload.get("items"))
    selected = next((item for item in items if isinstance(item, dict) and item.get("field_id") == field_id), None)
    if not selected:
        raise KeyError(field_id)
    value = edited_value if edited_value is not None and edited_value != "" else str(selected.get("suggested_value") or selected.get("extracted_value") or "")
    if not value.strip():
        raise ValueError("empty_extraction_value")
    app = update_application_data(db, case_id, permit_type, {field_id: value})
    app.setdefault("field_sources", {})[field_id] = "accepted_ai_extraction"
    save_artifact(db, case_id, "permit_application", permit_type, app)
    now = time.time()
    for item in items:
        if isinstance(item, dict) and item.get("field_id") == field_id:
            item["status"] = "accepted"
            item["accepted_value"] = value
            item["edited_on_accept"] = value != str(item.get("suggested_value") or "")
            item["accepted_at"] = now
    queue_payload["items"] = items
    queue_payload["updated_at"] = now
    save_artifact(db, case_id, "field_extraction_queue", permit_type, queue_payload)
    package_result = artifact_json(db, case_id, "package_analysis_result", permit_type, {})
    if isinstance(package_result.get("extraction_queue"), list):
        for item in package_result["extraction_queue"]:
            if isinstance(item, dict) and item.get("field_id") == field_id:
                item["status"] = "accepted"
                item["accepted_value"] = value
                item["edited_on_accept"] = value != str(item.get("suggested_value") or "")
                item["accepted_at"] = now
        save_artifact(db, case_id, "package_analysis_result", permit_type, package_result)
    event(db, case_id, "ai.extraction.accepted", "AI extraction accepted", {"field_id": field_id, "permit_type": permit_type})
    return app


def deterministic_suggestion(permit_type: str, field_id: str, form_data: dict[str, Any], attachments: Optional[list[dict[str, Any]]] = None) -> Suggestion:
    criteria = load_criteria(permit_type)
    source_title = (criteria.get("sources") or ["Lupademon demokriteeristö"])[0]
    current_value = str(form_data.get(field_id, "") or "")
    values = {
        "stormwater_management": "Hulevedet hallitaan ensisijaisesti tontilla imeyttämällä ja viivyttämällä. Ylivirtaama johdetaan hallitusti kunnan hulevesiverkostoon asemapiirroksen ja hulevesisuunnitelman mukaisesti.",
        "wastewater_handling": "Jätevedet johdetaan kunnalliseen viemäriverkostoon. Rakentamisen aikaiset vedet käsitellään niin, ettei kiintoainesta johdu ympäristöön.",
        "project_description": "Hankkeessa rakennetaan yksikerroksinen asuinrakennus ja siihen liittyvät pihajärjestelyt Vantaalle. Kuvaus on luonnos ja hakijan tulee tarkistaa luvut suunnitelmista.",
        "permit_basis": "Toiminta voi olla ympäristöluvanvaraista teollisen toiminnan, kemikaalien käsittelyn ja päästövaikutusten vuoksi. Tarkka säädösperusta tulee varmistaa toimivaltaiselta viranomaiselta.",
        "bat_assessment": "Hakemuksessa tulee kuvata sovellettavat BAT-päätelmät, valitut tekniikat, päästöjen ehkäisy sekä perustelut mahdollisille poikkeamille.",
        "monitoring_plan": "Tarkkailusuunnitelmassa kuvataan käyttötarkkailu, päästötarkkailu, vaikutustarkkailu, mittausvälit, raportointi ja poikkeamatilanteiden käsittely.",
        "activity_description": "Toiminta koostuu uusiutuvan energian tuotantoon liittyvästä prosessista, raaka-aineiden vastaanotosta, varastoinnista, tuotannosta ja päästöjen hallinnasta.",
        "event_description": "Tapahtuma on yleisölle avoin paikallinen kaupunkitapahtuma. Kuvaus tulee täydentää ohjelmalla, aikataululla ja järjestäjän vastuuhenkilöillä.",
        "landowner_permission": "Tapahtuma-alueen käyttö perustuu maanomistajan tai alueen haltijan lupaan, joka liitetään hakemukseen.",
        "rescue_plan": "Pelastussuunnitelmassa kuvataan osallistujamäärä, poistumisreitit, ensiapu, järjestyksenvalvonta, alkusammutus ja häiriötilanteiden toimintamalli.",
        "traffic_arrangements": "Liikennejärjestelyissä kuvataan suljettavat alueet, opastus, esteettömät kulkureitit, huoltoajo ja vaikutukset joukkoliikenteeseen.",
        "waste_management": "Jätehuolto järjestetään lajitteluastioilla, tyhjennyksillä ja tapahtuman jälkisiivouksella.",
    }
    suggested_value = values.get(field_id) or f"Täydennä kenttä '{field_id}' hakemuksen, liitteiden ja kriteeristön perusteella. Tämä on tarkistettava ehdotus."
    if attachments:
        official = [a["filename"] for a in attachments if a.get("delivery_scope") == "official_submission"]
        if official:
            suggested_value += f" Ehdotus huomioi liitteet: {', '.join(official[:3])}."
    suggestion = Suggestion(
        field_id=field_id,
        current_value=current_value,
        suggested_value=suggested_value,
        diff_summary="Korvaa tyhjän kentän ehdotuksella." if not current_value else "Täydentää nykyistä arvoa AI-ehdotuksella.",
        rationale="Ehdotus perustuu lomakedataan, demokriteeristöön ja liitteiden luokitteluun.",
        confidence=0.78,
        evidence_refs=[EvidenceRef(source_id=criteria.get("criteria_set_id", "demo"), title=source_title, locator="demo criteria", quote="Tekoälyavusteinen ehdotus, tarkista ennen käyttöä.", confidence=0.72)],
        criteria_version=str(criteria.get("version") or "demo"),
        agent="field_suggestion_agent",
        fallback_used=False,
        warnings=[] if field_id in values else ["Lähdetuki on rajallinen, tarkista sisältö ennen hyväksyntää."],
    )
    return suggestion


def create_field_suggestion(db: Any, case_id: str, permit_type: str, field_id: str, settings: Any = None) -> Suggestion:
    app = ensure_application(db, case_id, permit_type)
    job = start_ai_job(db, case_id, app["permit_type"], "field_suggestion", f"Kenttäehdotus: {field_id}", ["field_suggestion_agent"], eta_seconds=15)
    fallback_suggestion = deterministic_suggestion(app["permit_type"], field_id, app.get("form_data", {}), list_attachments(db, case_id, app["permit_type"]))
    if settings is not None:
        schema = load_form_schema(app["permit_type"])
        field_def = next((f for f in schema.get("fields", []) if f.get("id") == field_id), {"id": field_id})
        criteria = effective_criteria(db, case_id, app["permit_type"])
        context = retrieval_context(db, case_id, app["permit_type"])
        common = artifact_json(db, case_id, "lv_common_data", "main", {"data": DEFAULT_COMMON_DATA.copy()})
        response = run_json_agent(
            db,
            settings,
            case_id,
            "field_suggestion_agent",
            {
                "request_id": f"ui_{uuid.uuid4().hex[:8]}",
                "criteria_version": criteria_version(app["permit_type"], db, case_id),
                "field_id": field_id,
                "field_definition": json.dumps(field_def, ensure_ascii=False),
                "form_data": json.dumps(app.get("form_data", {}), ensure_ascii=False),
                "common_data": json.dumps(common.get("data", {}), ensure_ascii=False),
                "criteria": json.dumps(criteria, ensure_ascii=False),
                "retrieved_context": json.dumps(context, ensure_ascii=False),
            },
            FieldSuggestionResponse,
            vector_ids=azure_vector_store_ids(db, settings, case_id),
            fallback={"request_id": f"fallback_{uuid.uuid4().hex[:8]}", "criteria_version": criteria_version(app["permit_type"], db, case_id), "suggestions": [fallback_suggestion], "warnings": []},
            max_output_tokens=3000,
        )
        if isinstance(response, FieldSuggestionResponse) and response.suggestions:
            suggestion = response.suggestions[0]
        else:
            suggestion = fallback_suggestion
    else:
        suggestion = fallback_suggestion
    current_value = str(app.get("form_data", {}).get(field_id, "") or "")
    suggestion.current_value = suggestion.current_value or current_value
    suggestion.criteria_version = suggestion.criteria_version or criteria_version(app["permit_type"], db, case_id)
    suggestion.agent = suggestion.agent or "field_suggestion_agent"
    suggestion.fallback_used = bool(suggestion.fallback_used)
    if not suggestion.diff_summary:
        suggestion.diff_summary = "Korvaa tyhjän kentän ehdotuksella." if not current_value else "Täydentää nykyistä arvoa AI-ehdotuksella."
    FieldSuggestionResponse(request_id=f"ui_{uuid.uuid4().hex[:8]}", criteria_version=criteria_version(app["permit_type"], db, case_id), suggestions=[suggestion])
    pending = artifact_json(db, case_id, "ai_suggestions", app["permit_type"], {"suggestions": []})
    pending.setdefault("suggestions", []).append(suggestion)
    save_artifact(db, case_id, "ai_suggestions", app["permit_type"], pending)
    complete_ai_job(db, case_id, app["permit_type"], job["id"], summary=f"Ehdotus luotu kentälle {field_id}.", result_artifact=f"ai_suggestions:{app['permit_type']}")
    event(db, case_id, "ai.suggestion.created", "AI field suggestion created", {"application_id": app["permit_type"], "field_id": field_id, "suggestion_id": suggestion.id})
    return suggestion


def accept_suggestion(db: Any, case_id: str, permit_type: str, suggestion_id: str, edited_value: Optional[str] = None) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    pending = artifact_json(db, case_id, "ai_suggestions", permit_type, {"suggestions": []})
    selected = None
    for item in pending.get("suggestions", []):
        if item.get("id") == suggestion_id:
            item["status"] = "accepted"
            selected = item
            break
    if not selected:
        raise KeyError(suggestion_id)
    value = edited_value if edited_value is not None and edited_value != "" else selected["suggested_value"]
    selected["accepted_value"] = value
    selected["edited_on_accept"] = value != selected.get("suggested_value")
    app = update_application_data(db, case_id, permit_type, {selected["field_id"]: value})
    app.setdefault("field_sources", {})[selected["field_id"]] = "accepted_ai_suggestion"
    save_artifact(db, case_id, "permit_application", permit_type, app)
    save_artifact(db, case_id, "ai_suggestions", permit_type, pending)
    event(db, case_id, "ai.suggestion.accepted", "AI suggestion accepted", {"suggestion_id": suggestion_id, "field_id": selected["field_id"]})
    return app


def accept_selected_suggestions(db: Any, case_id: str, permit_type: str, suggestion_ids: list[str]) -> dict[str, Any]:
    app = ensure_application(db, case_id, permit_type)
    for suggestion_id in suggestion_ids:
        app = accept_suggestion(db, case_id, permit_type, suggestion_id)
    return app


def reject_suggestion(db: Any, case_id: str, permit_type: str, suggestion_id: str) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    pending = artifact_json(db, case_id, "ai_suggestions", permit_type, {"suggestions": []})
    for item in pending.get("suggestions", []):
        if item.get("id") == suggestion_id:
            item["status"] = "rejected"
    save_artifact(db, case_id, "ai_suggestions", permit_type, pending)
    event(db, case_id, "ai.suggestion.rejected", "AI suggestion rejected", {"suggestion_id": suggestion_id})
    return pending


def criteria_version(permit_type: str, db: Any = None, case_id: str = "") -> str:
    criteria = effective_criteria(db, case_id, permit_type) if db is not None and case_id else load_criteria(permit_type)
    return str(criteria.get("version") or "demo")


def deterministic_issues(permit_type: str, form_data: dict[str, Any], attachments: list[dict[str, Any]]) -> list[Issue]:
    schema = load_form_schema(permit_type)
    criteria = load_criteria(permit_type)
    criteria_source = EvidenceRef(source_id=criteria.get("criteria_set_id", "demo"), title=(criteria.get("sources") or ["Demokriteeristö"])[0], locator="criteria", confidence=0.75)
    issues: list[Issue] = []
    for schema_field in schema.get("fields", []):
        if schema_field.get("required") and str(form_data.get(schema_field["id"], "")).strip() == "":
            severity = "blocking" if schema_field["id"] in {"building_height_m", "stormwater_management", "permit_basis", "competent_authority", "landowner_permission"} else "major"
            issues.append(Issue(id=f"missing_{schema_field['id']}", type="missing", severity=severity, field_id=schema_field["id"], title=f"{schema_field['label']} puuttuu", detail="Pakollinen kenttä on tyhjä.", suggested_fix=f"Täytä kenttä {schema_field['label']}.", evidence_refs=[criteria_source], confidence=0.95))
    official_types = {a.get("primary_type") for a in attachments if a.get("delivery_scope") == "official_submission"}
    for required in schema.get("required_attachments", []):
        if required not in official_types:
            sev = "blocking" if permit_type == "building_residential" or required == "landowner_permission" else "major"
            issues.append(Issue(id=f"missing_attachment_{required}", type="missing", severity=sev, attachment_id=required, title=f"Liite {required} puuttuu lähetyspaketista", detail="Vaadittua liitettä ei ole merkitty viranomaiselle toimitettavaksi.", suggested_fix="Lisää liite tai muuta sen käyttötarkoitus viranomaiselle toimitettavaksi.", evidence_refs=[criteria_source], confidence=0.9))
    if permit_type == "building_residential":
        for num_field in ["floor_area_m2", "building_height_m", "distance_to_boundary_m"]:
            raw = str(form_data.get(num_field, "")).strip()
            if raw:
                try:
                    if float(raw.replace(",", ".")) <= 0:
                        issues.append(Issue(id=f"invalid_{num_field}", type="incomplete", severity="blocking", field_id=num_field, title="Numeerinen arvo ei voi olla nolla tai negatiivinen", detail=f"Kentän {num_field} arvo on {raw}.", suggested_fix="Anna positiivinen arvo.", evidence_refs=[criteria_source], confidence=0.95))
                except ValueError:
                    issues.append(Issue(id=f"invalid_{num_field}", type="incomplete", severity="blocking", field_id=num_field, title="Numeerinen arvo on virheellinen", detail=f"Kentän {num_field} arvo ei ole numero.", suggested_fix="Anna arvo numerona.", evidence_refs=[criteria_source], confidence=0.95))
    if permit_type == "environmental_industrial":
        mentions_chemicals = "kemikaal" in str(form_data.get("raw_materials_chemicals", "")).lower()
        chemical_ai_only = any(a.get("primary_type") == "chemical_inventory" and a.get("delivery_scope") == "ai_context_only" for a in attachments)
        chemical_official = any(a.get("primary_type") == "chemical_inventory" and a.get("delivery_scope") == "official_submission" for a in attachments)
        if mentions_chemicals and chemical_ai_only and not chemical_official:
            issues.append(Issue(id="chemical_inventory_scope", type="inconsistent", severity="major", attachment_id="chemical_inventory", title="Kemikaaliluettelo on vain AI-tausta-aineistona", detail="Hakemus viittaa kemikaaliluetteloon, mutta liite ei kuulu viranomaiselle lähtevään pakettiin.", suggested_fix="Merkitse kemikaaliluettelo viranomaiselle toimitettavaksi tai poista viittaus.", evidence_refs=[criteria_source], confidence=0.88))
    return issues


def merge_issues(*groups: list[Issue]) -> list[Issue]:
    seen: dict[str, Issue] = {}
    for group in groups:
        for issue in group:
            if issue.id not in seen or SEVERITY_ORDER[issue.severity] > SEVERITY_ORDER[seen[issue.id].severity]:
                seen[issue.id] = issue
    return list(seen.values())


def run_application_check(db: Any, case_id: str, permit_type: str, settings: Any = None) -> ApplicationCheckResponse:
    app = ensure_application(db, case_id, permit_type)
    job = start_ai_job(
        db,
        case_id,
        app["permit_type"],
        "application_check",
        "AI- ja sääntötarkistus",
        ["completeness_validator", "consistency_validator", "criteria_assessor", "applicant_precheck_report"],
        eta_seconds=35,
    )
    attachments = list_attachments(db, case_id, app["permit_type"])
    deterministic = deterministic_issues(app["permit_type"], app.get("form_data", {}), attachments)
    agent_issues: list[Issue] = []
    criteria_results: list[dict[str, Any]] = []
    if settings is not None:
        schema = load_form_schema(app["permit_type"])
        criteria = effective_criteria(db, case_id, app["permit_type"])
        context = retrieval_context(db, case_id, app["permit_type"])
        common = artifact_json(db, case_id, "lv_common_data", "main", {"data": DEFAULT_COMMON_DATA.copy()})
        base_values = {
            "form_schema": json.dumps(schema, ensure_ascii=False),
            "form_data": json.dumps(app.get("form_data", {}), ensure_ascii=False),
            "attachments": json.dumps(attachments, ensure_ascii=False),
            "deterministic_issues": json.dumps(_plain(deterministic), ensure_ascii=False),
            "criteria_and_context": json.dumps({"criteria": criteria, "retrieval": context}, ensure_ascii=False),
            "common_data": json.dumps(common.get("data", {}), ensure_ascii=False),
            "attachment_context": json.dumps(context, ensure_ascii=False),
            "criteria": json.dumps(criteria, ensure_ascii=False),
            "retrieved_context": json.dumps(context, ensure_ascii=False),
        }
        vector_ids = azure_vector_store_ids(db, settings, case_id)
        for agent in ("completeness_validator", "consistency_validator"):
            response = run_json_agent(
                db,
                settings,
                case_id,
                agent,
                base_values,
                IssueListResponse,
                vector_ids=vector_ids,
                fallback={"issues": []},
                max_output_tokens=5000,
            )
            if isinstance(response, IssueListResponse):
                agent_issues.extend(response.issues)
            elif isinstance(response, dict):
                agent_issues.extend(response.get("issues", []))
        criteria_response = run_json_agent(
            db,
            settings,
            case_id,
            "criteria_assessor",
            base_values,
            CriteriaAssessmentResponse,
            vector_ids=vector_ids,
            fallback={"criteria_results": []},
            max_output_tokens=6000,
        )
        if isinstance(criteria_response, CriteriaAssessmentResponse):
            criteria_results = _plain(criteria_response.criteria_results)
        elif isinstance(criteria_response, dict):
            criteria_results = criteria_response.get("criteria_results", [])
    issues = merge_issues(deterministic, agent_issues)
    blockers = sum(1 for i in issues if i.severity == "blocking" and not i.resolved)
    major = sum(1 for i in issues if i.severity == "major" and not i.resolved)
    score = max(0, 100 - blockers * 25 - major * 10 - (len(issues) - blockers - major) * 4)
    checked_at = time.time()
    current_criteria_version = criteria_version(app["permit_type"], db, case_id)
    response = ApplicationCheckResponse(
        request_id=f"check_{uuid.uuid4().hex[:8]}",
        criteria_version=current_criteria_version,
        score=score,
        summary="Hakemus on lähetysvalmis." if blockers == 0 else "Hakemuksessa on lähettämisen estäviä puutteita.",
        issues=issues,
    )
    report_payload = asdict(response)
    report_payload.update(
        {
            "checked_at": checked_at,
            "form_updated_at": float(app.get("updated_at") or 0),
            "attachments_updated_at": attachments_updated_at(db, case_id, app["permit_type"]),
            "criteria_version": current_criteria_version,
        }
    )
    save_artifact(db, case_id, "check_report", app["permit_type"], report_payload)
    if criteria_results:
        save_artifact(db, case_id, "criteria_assessment", app["permit_type"], {"criteria_results": criteria_results})
    if settings is not None:
        precheck = run_json_agent(
            db,
            settings,
            case_id,
            "applicant_precheck_report",
            {"issues": json.dumps(_plain(issues), ensure_ascii=False)},
            ApplicantPrecheckReport,
            fallback={
                "summary": response.summary,
                "readiness": "not_ready" if blockers else ("almost_ready" if major else "ready"),
                "score": response.score,
                "sections": [
                    {"title": "Tärkeimmät korjattavat", "items": [i.title for i in issues if i.severity == "blocking"]},
                    {"title": "Ristiriidat", "items": [i.title for i in issues if i.type == "inconsistent"]},
                    {"title": "Suositukset", "items": [i.title for i in issues if i.severity in {"minor", "recommendation"}]},
                ],
                "blocking_issue_count": blockers,
                "major_issue_count": major,
            },
            max_output_tokens=5000,
            reasoning_effort="low",
        )
        save_artifact(db, case_id, "applicant_precheck_report", app["permit_type"], precheck)
    event(db, case_id, "ai.check.completed", "Application check completed", {"application_id": app["permit_type"], "issues": len(issues), "blocking": blockers, "major": major, "score": response.score})
    save_artifact(db, case_id, "precheck_gate", app["permit_type"], precheck_gate_status(db, case_id, app["permit_type"]))
    complete_ai_job(db, case_id, app["permit_type"], job["id"], summary=f"Tarkistus valmis: {len(issues)} havaintoa, pisteet {response.score}.", result_artifact=f"check_report:{app['permit_type']}")
    return response


def attachments_updated_at(db: Any, case_id: str, permit_type: str) -> float:
    timestamps: list[float] = []
    for attachment in list_attachments(db, case_id, permit_type):
        for key in ("updated_at", "created_at"):
            try:
                value = float(attachment.get(key) or 0)
            except Exception:
                value = 0
            if value > 0:
                timestamps.append(value)
    return max(timestamps) if timestamps else 0.0


def precheck_gate_status(db: Any, case_id: str, permit_type: str, *, max_age_seconds: int = PRECHECK_MAX_AGE_SECONDS) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    app = ensure_application(db, case_id, permit_type)
    report = artifact_json(db, case_id, "check_report", permit_type, {})
    now = time.time()
    stale_reasons: list[str] = []
    try:
        checked_at = float(report.get("checked_at") or 0)
    except Exception:
        checked_at = 0.0
    form_updated_at = float(app.get("updated_at") or 0)
    current_attachments_updated_at = attachments_updated_at(db, case_id, permit_type)
    current_criteria_version = criteria_version(permit_type, db, case_id)
    report_criteria_version = str(report.get("criteria_version") or report.get("criteria_version_id") or "")

    if not report:
        stale_reasons.append("missing_report")
    if report and checked_at <= 0:
        stale_reasons.append("missing_checked_at")
    if checked_at > 0 and form_updated_at > checked_at + 0.001:
        stale_reasons.append("form_changed_after_check")
    if checked_at > 0 and current_attachments_updated_at > checked_at + 0.001:
        stale_reasons.append("attachments_changed_after_check")
    if report and report_criteria_version and report_criteria_version != current_criteria_version:
        stale_reasons.append("criteria_version_changed")
    if checked_at > 0 and now - checked_at > max_age_seconds:
        stale_reasons.append("report_too_old")

    issues = report.get("issues") or []
    blocking_count = sum(1 for issue in issues if issue.get("severity") == "blocking" and not issue.get("resolved"))
    major_count = sum(1 for issue in issues if issue.get("severity") == "major" and not issue.get("resolved"))
    is_fresh = bool(report) and not stale_reasons
    return {
        "application_id": permit_type,
        "can_submit": is_fresh and blocking_count == 0,
        "is_fresh": is_fresh,
        "blocking_count": blocking_count,
        "major_count": major_count,
        "stale_reasons": stale_reasons,
        "checked_at": checked_at,
        "form_updated_at": form_updated_at,
        "attachments_updated_at": current_attachments_updated_at,
        "criteria_version": current_criteria_version,
        "report_criteria_version": report_criteria_version or current_criteria_version,
        "max_age_seconds": max_age_seconds,
        "updated_at": now,
    }


def ensure_fresh_precheck_or_raise(db: Any, case_id: str, permit_type: str, settings: Any = None) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    gate = precheck_gate_status(db, case_id, permit_type)
    if not gate["is_fresh"]:
        run_application_check(db, case_id, permit_type, settings=settings)
        gate = precheck_gate_status(db, case_id, permit_type)
    save_artifact(db, case_id, "precheck_gate", permit_type, gate)
    if not gate["can_submit"]:
        if gate["blocking_count"] > 0:
            raise RuntimeError("blocking_issues")
        raise RuntimeError("precheck_not_fresh")
    return gate


def wizard_step_status(db: Any, case_id: str, permit_type: str) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    app = ensure_application(db, case_id, permit_type)
    schema = load_form_schema(permit_type)
    attachments = list_attachments(db, case_id, permit_type)
    suggestions = artifact_json(db, case_id, "ai_suggestions", permit_type, {"suggestions": []}).get("suggestions", [])
    check_report = artifact_json(db, case_id, "check_report", permit_type, {})
    gate = precheck_gate_status(db, case_id, permit_type)
    form_data = app.get("form_data") or {}
    required_fields = [f for f in schema.get("fields", []) if f.get("required")]
    missing_required = [f.get("id") for f in required_fields if not str(form_data.get(f.get("id"), "")).strip()]
    official_types = {a.get("primary_type") for a in attachments if a.get("delivery_scope") == "official_submission"}
    missing_attachments = [item for item in schema.get("required_attachments", []) if item not in official_types]
    pending_suggestions = [s for s in suggestions if s.get("status") == "pending"]
    issues = check_report.get("issues") or []
    blocking_count = sum(1 for issue in issues if issue.get("severity") == "blocking" and not issue.get("resolved"))
    steps = [
        {"step": 1, "label": "Valmistelu ja perustiedot", "status": "done" if not missing_required[:3] else "attention"},
        {"step": 2, "label": "Lomake ja liitteet", "status": "done" if not missing_required and not missing_attachments else "attention"},
        {"step": 3, "label": "AI-ehdotusten tarkastelu", "status": "attention" if pending_suggestions else "done"},
        {"step": 4, "label": "AI- ja sääntötarkistus", "status": "done" if gate["is_fresh"] and blocking_count == 0 else "attention"},
        {"step": 5, "label": "Asiakirjaesitys", "status": "done" if gate["can_submit"] else "blocked"},
        {"step": 6, "label": "Lähetys ja kuitti", "status": "done" if app.get("status") == "submitted" else "pending"},
    ]
    return {
        "steps": steps,
        "missing_required_fields": missing_required,
        "missing_attachments": missing_attachments,
        "pending_suggestion_count": len(pending_suggestions),
        "blocking_count": blocking_count,
        "can_submit": gate["can_submit"],
        "gate": gate,
    }


def compose_submission_package(db: Any, case_id: str, permit_type: str) -> dict[str, Any]:
    app = ensure_application(db, case_id, permit_type)
    schema = load_form_schema(app["permit_type"])
    attachments = list_attachments(db, case_id, app["permit_type"])
    official = [a for a in attachments if a.get("delivery_scope") == "official_submission"]
    excluded = [a for a in attachments if a.get("delivery_scope") != "official_submission"]
    form_data = app.get("form_data", {})
    form_fields = []
    for schema_field in schema.get("fields", []):
        field_id = schema_field.get("id")
        if not field_id:
            continue
        value = form_data.get(field_id, "")
        form_fields.append(
            {
                "field_id": field_id,
                "label": schema_field.get("label") or field_id,
                "value": value,
                "required": bool(schema_field.get("required")),
                "source": (app.get("field_sources") or {}).get(field_id, "käyttäjän syöttämä"),
            }
        )
    check_report = artifact_json(db, case_id, "check_report", app["permit_type"], {"issues": []})
    non_blocking_issues = [
        issue
        for issue in check_report.get("issues", [])
        if issue.get("severity") != "blocking" and not issue.get("resolved")
    ]
    package = {
        "package_id": f"pkg_{uuid.uuid4().hex[:10]}",
        "application_id": app["permit_type"],
        "application_title": schema.get("title") or app["permit_type"],
        "authority": schema.get("authority_default", "Viranomainen"),
        "form_data": form_data,
        "form_fields": form_fields,
        "official_attachments": official,
        "excluded_ai_context_attachments": excluded,
        "non_blocking_issues": non_blocking_issues,
        "disclaimer": "Tekoälyavusteinen ehdotus, tarkista ennen käyttöä.",
        "created_at": time.time(),
    }
    save_artifact(db, case_id, "submission_package", app["permit_type"], package)
    event(db, case_id, "submission.package.composed", "Submission package composed", {"application_id": app["permit_type"], "official_attachments": len(official), "excluded": len(excluded)})
    return package


def metrics_snapshot(db: Any, case_id: str) -> dict[str, Any]:
    events_payload = artifact_json(db, case_id, "lupademo_events", "main", {"events": []})
    events = events_payload.get("events", [])
    source_ingest = artifact_json(db, case_id, "source_material_ingest", "main", {})
    authority_benchmark = source_ingest.get("authority_benchmark") if isinstance(source_ingest, dict) else {}
    agent_audit = agent_chain_audit(db, case_id)
    agent_audit_run = artifact_json(db, case_id, "agent_chain_audit_run", "main", {})
    attachment_extraction = attachment_extraction_summary(db, case_id)
    applications = [ensure_application(db, case_id, p) for p in PERMIT_SCHEMAS]
    checks = [artifact_json(db, case_id, "check_report", p, {}) for p in PERMIT_SCHEMAS]
    suggestions = []
    for permit_type in PERMIT_SCHEMAS:
        suggestions.extend(artifact_json(db, case_id, "ai_suggestions", permit_type, {"suggestions": []}).get("suggestions", []))
    blocking = sum(1 for check in checks for issue in check.get("issues", []) if issue.get("severity") == "blocking")
    accepted = sum(1 for suggestion in suggestions if suggestion.get("status") == "accepted")
    rejected = sum(1 for suggestion in suggestions if suggestion.get("status") == "rejected")
    edited_accepted = sum(1 for suggestion in suggestions if suggestion.get("status") == "accepted" and suggestion.get("edited_on_accept"))
    source_backed = sum(1 for suggestion in suggestions if suggestion.get("evidence_refs"))
    submitted = sum(1 for app in applications if app.get("status") == "submitted")
    checks_run = sum(1 for event_row in events if event_row.get("action") == "ai.check.completed")
    detected_issues = sum(int((event_row.get("data") or {}).get("issues") or 0) for event_row in events if event_row.get("action") == "ai.check.completed")
    score_values = [int(check.get("score")) for check in checks if check.get("score") is not None]
    avg_score = round(sum(score_values) / len(score_values), 1) if score_values else 0
    check_scores = [int((event_row.get("data") or {}).get("score") or 0) for event_row in events if event_row.get("action") == "ai.check.completed"]
    quality_before_ai = check_scores[0] if check_scores else max(0, int(avg_score) - accepted * 3)
    quality_after_ai = check_scores[-1] if check_scores else int(avg_score)
    issue_count_by_check = [int((event_row.get("data") or {}).get("issues") or 0) for event_row in events if event_row.get("action") == "ai.check.completed"]
    issue_count_before = issue_count_by_check[0] if issue_count_by_check else detected_issues
    issue_count_after = issue_count_by_check[-1] if issue_count_by_check else detected_issues
    severity_before = {"blocking": 0, "major": 0, "minor": 0}
    severity_after = {"blocking": 0, "major": 0, "minor": 0}
    if checks:
        first_issues = checks[0].get("issues", []) if isinstance(checks[0], dict) else []
        last_issues = checks[-1].get("issues", []) if isinstance(checks[-1], dict) else []
        for issue in first_issues:
            sev = issue.get("severity")
            if sev in severity_before:
                severity_before[sev] += 1
        for issue in last_issues:
            sev = issue.get("severity")
            if sev in severity_after:
                severity_after[sev] += 1
    info_request_drafts = sum(1 for event_row in events if event_row.get("action") == "authority.additional_info_draft.generated")
    event_counts: dict[str, int] = {}
    for event_row in events:
        action = str(event_row.get("action") or "")
        event_counts[action] = event_counts.get(action, 0) + 1
    queue_items = artifact_json(db, case_id, "authority_queue", "main", {"items": []}).get("items", [])
    queue_by_status: dict[str, int] = {}
    for item in queue_items:
        status = str(item.get("status") or "unknown")
        queue_by_status[status] = queue_by_status.get(status, 0) + 1
    baseline = {
        "baseline_info_request_probability": 0.65,
        "ai_assisted_info_request_probability": 0.25,
        "baseline_caseworker_minutes": 120,
        "ai_assisted_caseworker_minutes": 75,
        "simulated": True,
    }
    save_artifact(db, case_id, "demo_metrics_baseline", "main", baseline)
    all_ai_jobs: list[dict[str, Any]] = []
    for permit_type in PERMIT_SCHEMAS:
        all_ai_jobs.extend(artifact_json(db, case_id, "ai_jobs", permit_type, {"jobs": []}).get("jobs", []))
    completed_ai_jobs = sum(1 for job in all_ai_jobs if job.get("status") == "completed")
    contextual_chat_suggestions = sum(1 for suggestion in suggestions if suggestion.get("agent") == "contextual_chat_assistant")
    api_demo = artifact_json(db, case_id, "suggestion_service_demo", "api", {})
    api_trace_count = len(api_demo.get("trace_stream") or []) if isinstance(api_demo, dict) else 0
    quality_delta = quality_after_ai - quality_before_ai
    minutes_saved = baseline["baseline_caseworker_minutes"] - baseline["ai_assisted_caseworker_minutes"]
    hours_saved = round(minutes_saved / 60.0 + (accepted * 0.25) + (checks_run * 0.5), 1)
    source_ratio = round(source_backed / len(suggestions), 2) if suggestions else 0
    proof_cards = [
        {
            "title": "Laatu parani",
            "value": f"{quality_delta:+d}",
            "detail": f"{quality_before_ai} -> {quality_after_ai} laatupistettä",
            "status": "good" if quality_delta >= 0 else "warn",
        },
        {
            "title": "AI-työ näkyy",
            "value": str(completed_ai_jobs),
            "detail": f"{len(all_ai_jobs)} työtä, {checks_run} tarkistusta, {accepted} hyväksyntää",
            "status": "good" if completed_ai_jobs or checks_run or accepted else "warn",
        },
        {
            "title": "Käsittelijäaika",
            "value": f"{hours_saved} h",
            "detail": f"Simuloitu säästö {minutes_saved} min + hyväksyntöjen ja tarkistusten vaikutus",
            "status": "good",
        },
        {
            "title": "Lähteistetyt ehdotukset",
            "value": f"{int(source_ratio * 100)}%",
            "detail": f"{source_backed} / {len(suggestions)} ehdotusta sisältää lähdeviitteet",
            "status": "good" if source_ratio >= 0.5 or not suggestions else "warn",
        },
    ]
    ai_work_impact = {
        "jobs_total": len(all_ai_jobs),
        "jobs_completed": completed_ai_jobs,
        "checks_run": checks_run,
        "accepted_suggestions": accepted,
        "contextual_chat_suggestions": contextual_chat_suggestions,
        "api_trace_events": api_trace_count,
        "quality_score_delta": quality_delta,
        "estimated_caseworker_minutes_saved": minutes_saved,
        "estimated_caseworker_hours_saved": hours_saved,
    }
    snapshot = {
        "application_count": len(applications),
        "submitted_count": submitted,
        "ai_checks_run": checks_run,
        "accepted_suggestions": accepted,
        "edited_accepted_suggestions": edited_accepted,
        "rejected_suggestions": rejected,
        "source_backed_suggestion_ratio": source_ratio,
        "blocking_issues_detected": blocking,
        "issue_count_before_ai": issue_count_before,
        "issue_count_after_ai": issue_count_after,
        "issue_count_delta": issue_count_after - issue_count_before,
        "issue_severity_before": severity_before,
        "issue_severity_after": severity_after,
        "corrected_deficiencies": max(issue_count_before - issue_count_after, accepted, 0),
        "detected_issue_count": detected_issues,
        "additional_info_drafts": info_request_drafts,
        "demo_metrics_baseline": baseline,
        "baseline_info_request_probability": baseline["baseline_info_request_probability"],
        "ai_assisted_info_request_probability": baseline["ai_assisted_info_request_probability"],
        "info_request_probability_delta": round(baseline["ai_assisted_info_request_probability"] - baseline["baseline_info_request_probability"], 2),
        "simulated_avoided_info_requests": max(accepted + blocking // 2, 0),
        "estimated_caseworker_minutes_saved": minutes_saved,
        "estimated_caseworker_hours_saved": hours_saved,
        "agent_call_count": agent_audit.get("call_count", 0),
        "file_search_call_count": agent_audit.get("file_search_call_count", 0),
        "fallback_count": agent_audit.get("fallback_count", 0),
        "api_calls_by_tenant": {},
        "quality_score_before_ai": quality_before_ai,
        "quality_score_after_ai": quality_after_ai,
        "quality_score_delta": quality_delta,
        "average_quality_score": avg_score,
        "proof_cards": proof_cards,
        "ai_work_impact": ai_work_impact,
        "presenter_mode": {
            "query_param": "present=1",
            "surfaces": ["hakemusstudio", "viranomaisen työjono", "mittarit", "API-demo"],
        },
        "authority_queue_count": len(queue_items),
        "authority_queue_by_status": queue_by_status,
        "event_counts": event_counts,
        "agent_chain_audit": agent_audit,
        "agent_chain_audit_run": agent_audit_run,
        "attachment_extraction": attachment_extraction,
        "authority_benchmark": authority_benchmark or {},
        "readiness": readiness_snapshot(db, case_id, source_ingest, agent_audit, attachment_extraction),
        "recent_events": events[-12:],
    }
    save_artifact(db, case_id, "metrics", "main", snapshot)
    return snapshot


SOURCE_MATERIAL_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".txt", ".md"}
EXPECTED_SOURCE_ARCHIVES = ["P2X.zip", "Ren-Gas.zip", "Sharpcell.zip"]
DEFAULT_SOURCE_MATERIAL_DIRS = ["testiaineisto/esimerkkihakemukset", "source_materials", "esimerkkihakemukset"]


def _source_case_name(name: str) -> str:
    lowered = name.lower()
    if "ren" in lowered and "gas" in lowered:
        return "Ren-Gas"
    if "sharp" in lowered or "sharpcell" in lowered:
        return "SharpCell"
    if "p2x" in lowered or "px2" in lowered:
        return "P2X"
    return "unknown"


def resolve_source_material_dir(source_dir: str = "source_materials") -> Path:
    requested = Path(source_dir)
    if requested.is_absolute():
        return requested
    candidates = [ROOT.parent / source_dir, ROOT / source_dir]
    if source_dir == "source_materials":
        candidates.extend(ROOT.parent / name for name in DEFAULT_SOURCE_MATERIAL_DIRS if name != source_dir)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def external_real_case_upload_allowed(base: Path, settings: Any) -> tuple[bool, str]:
    provider = str(getattr(settings, "llm_provider", "azure") or "azure").strip().lower()
    if provider != "azure":
        return True, ""
    expected_present = all((base / name).exists() for name in EXPECTED_SOURCE_ARCHIVES)
    looks_like_real_examples = base.name == "esimerkkihakemukset" or expected_present
    if not looks_like_real_examples:
        return True, ""
    allowed = str(os.getenv("LUPADEMO_ALLOW_EXTERNAL_REAL_CASE_UPLOAD", "")).strip().lower() in {"1", "true", "yes", "on"}
    if allowed:
        return True, ""
    return False, "external_upload_blocked: set LUPADEMO_ALLOW_EXTERNAL_REAL_CASE_UPLOAD=1 to upload real application documents to Azure"


def _case_inventory_index() -> dict[tuple[str, str], dict[str, Any]]:
    rows = load_json(SPEC_ROOT / "case_inventory.json", [])
    index: dict[tuple[str, str], dict[str, Any]] = {}
    if not isinstance(rows, list):
        return index
    for row in rows:
        if not isinstance(row, dict):
            continue
        case = str(row.get("case") or "").strip()
        filename = str(row.get("filename") or row.get("path") or "").strip().lower()
        path = str(row.get("path") or "").strip().lower()
        if case and filename:
            index[(case, filename)] = row
        if case and path:
            index[(case, path)] = row
    return index


def _source_inventory_match(index: dict[tuple[str, str], dict[str, Any]], case_name: str, rel_path: str, filename: str) -> dict[str, Any]:
    for key in (rel_path.lower(), filename.lower()):
        row = index.get((case_name, key))
        if row:
            return row
    return {}


def classify_source_material(filename: str, inventory_category: str = "") -> str:
    name = filename.lower()
    category = inventory_category.lower()
    guidance_needles = [
        "ohje",
        "opas",
        "laki",
        "asetus",
        "säädös",
        "saados",
        "määräys",
        "maaray",
        "ymparisto.fi",
        "ympäristö.fi",
        "rakentamislaki",
        "rakl",
    ]
    if "täydennyspyy" in name or "taydennyspyy" in name or "täydennyspyy" in category:
        return "additional_info_request"
    if "täydenn" in name or "taydenn" in name or "täydenn" in category:
        return "additional_info_response"
    if "päätös" in name or "paatos" in name or "kuulutus" in name or "vireille" in name or "päätös" in category:
        return "decision_or_notice"
    if any(needle in name or needle in category for needle in guidance_needles):
        return "guidance_reference"
    if "hakemus" in name and "kuulutus" not in name:
        return "environmental_application"
    primary = classify_attachment(filename)
    if primary != "other":
        return primary
    if "liite" in category or "taustaselvitys" in category:
        return "background_study"
    return "source_document"


def source_material_role(filename: str, primary_type: str, inventory_category: str = "") -> str:
    name = filename.lower()
    category = inventory_category.lower()
    authority_needles = [
        "päätös",
        "paatos",
        "kuulutus",
        "vireilletulo",
        "täydennyspyy",
        "taydennyspyy",
        "ei_julkaistavat",
        "ei julkaistavat",
        "sähköpostisaate",
        "sahkopostisaate",
        "kirjeenvaihto",
    ]
    guidance_needles = [
        "ohje",
        "opas",
        "laki",
        "asetus",
        "säädös",
        "saados",
        "määräys",
        "maaray",
        "ymparisto.fi",
        "ympäristö.fi",
        "rakentamislaki",
        "rakl",
    ]
    authority_types = {"additional_info_request", "decision_or_notice"}
    if primary_type in authority_types:
        return "authority_example"
    if any(needle in name or needle in category for needle in authority_needles):
        return "authority_example"
    if primary_type == "guidance_reference" or any(needle in name or needle in category for needle in guidance_needles):
        return "guidance_reference"
    return "application_case"


def analyze_source_material_file(path: Path, *, base_dir: Path, case_name: str, source_archive: str, inventory_index: dict[tuple[str, str], dict[str, Any]], max_chars: int = 12000) -> dict[str, Any]:
    rel_path = str(path.relative_to(base_dir)) if path.is_relative_to(base_dir) else path.name
    inventory = _source_inventory_match(inventory_index, case_name, rel_path, path.name)
    text_excerpt = extract_text_from_path(str(path), path.name, max_chars=max_chars)
    extraction_status = "ready" if text_excerpt and not text_excerpt.startswith("[tekstinpoiminta epäonnistui:") else ("error" if text_excerpt else "empty")
    primary_type = classify_source_material(path.name, str(inventory.get("category") or ""))
    role = source_material_role(path.name, primary_type, str(inventory.get("category") or ""))
    evidence = []
    if text_excerpt:
        evidence.append(source_ref(path.name, f"{case_name}:{rel_path}", text_excerpt[:260], 0.7 if extraction_status == "ready" else 0.4))
    return {
        "case": case_name,
        "source_archive": source_archive,
        "relative_path": rel_path,
        "filename": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
        "inventory_category": inventory.get("category", ""),
        "inventory_pages": inventory.get("pages"),
        "primary_type": primary_type,
        "document_role": role,
        "ingest_to_system": role in {"application_case", "guidance_reference"},
        "text_excerpt": text_excerpt[:max_chars],
        "text_chars": len(text_excerpt or ""),
        "extraction_status": extraction_status,
        "evidence_refs": evidence,
    }


def source_material_summary(documents: list[dict[str, Any]], missing_expected: list[str]) -> dict[str, Any]:
    by_case: dict[str, int] = {}
    by_type: dict[str, int] = {}
    by_extension: dict[str, int] = {}
    by_role: dict[str, int] = {}
    extracted_count = 0
    ingested_document_count = 0
    total_text_chars = 0
    total_bytes = 0
    for doc in documents:
        case = str(doc.get("case") or "unknown")
        primary_type = str(doc.get("primary_type") or "source_document")
        extension = str(doc.get("extension") or "")
        role = str(doc.get("document_role") or "application_case")
        by_case[case] = by_case.get(case, 0) + 1
        by_type[primary_type] = by_type.get(primary_type, 0) + 1
        by_extension[extension] = by_extension.get(extension, 0) + 1
        by_role[role] = by_role.get(role, 0) + 1
        if doc.get("extraction_status") == "ready":
            extracted_count += 1
        if doc.get("ingest_to_system"):
            ingested_document_count += 1
        total_text_chars += int(doc.get("text_chars") or 0)
        total_bytes += int(doc.get("size_bytes") or 0)
    return {
        "document_count": len(documents),
        "ingested_document_count": ingested_document_count,
        "guidance_reference_count": by_role.get("guidance_reference", 0),
        "excluded_authority_example_count": by_role.get("authority_example", 0),
        "extracted_text_document_count": extracted_count,
        "total_text_chars": total_text_chars,
        "total_size_bytes": total_bytes,
        "by_case": by_case,
        "by_type": by_type,
        "by_extension": by_extension,
        "by_role": by_role,
        "missing_expected": missing_expected,
        "sample_documents": documents[:12],
        "sample_ingested_documents": [doc for doc in documents if doc.get("ingest_to_system")][:12],
        "sample_guidance_references": [doc for doc in documents if doc.get("document_role") == "guidance_reference"][:12],
        "sample_authority_examples": [doc for doc in documents if doc.get("document_role") == "authority_example"][:12],
    }


def authority_example_benchmark(documents: list[dict[str, Any]]) -> dict[str, Any]:
    examples = [doc for doc in documents if doc.get("document_role") == "authority_example"]
    by_case: dict[str, int] = {}
    by_type: dict[str, int] = {}
    targets: dict[str, dict[str, Any]] = {
        "additional_info_request_agent": {
            "agent": "additional_info_request_agent",
            "document_types": ["additional_info_request"],
            "example_count": 0,
            "cases": [],
            "rubric": [
                "Pyytää vain ratkaisevat täydennykset eikä sekoita hakijan liitteitä viranomaisaineistoon.",
                "Viittaa hakemuksen puutteisiin ja perustelee pyynnön kriteereillä tai säädöksillä.",
                "Erottaa määräajan, puutteen ja pyydetyn toimitettavan aineiston toisistaan.",
            ],
        },
        "authority_memo_draft_agent": {
            "agent": "authority_memo_draft_agent",
            "document_types": ["decision_or_notice", "additional_info_request", "additional_info_response"],
            "example_count": 0,
            "cases": [],
            "rubric": [
                "Tiivistää käsittelyvaiheen olennaiset perustelut päätöstuen näkökulmasta.",
                "Nostaa lupaehtojen, kuulemisen ja täydennysten vaikutuksen ratkaisusuuntaan.",
                "Pitää viranomaisdokumentit vertailuesimerkkeinä, ei hakijan toimittamana aineistona.",
            ],
        },
        "caseworker_summary_agent": {
            "agent": "caseworker_summary_agent",
            "document_types": ["decision_or_notice", "additional_info_request", "additional_info_response"],
            "example_count": 0,
            "cases": [],
            "rubric": [
                "Tuottaa käsittelijälle nopean tilannekuvan hakemuksesta ja käsittelyvaiheesta.",
                "Erottelee hakemuksen sisällön, viranomaisen havainnot ja seuraavat toimet.",
                "Säilyttää epävarmuudet eikä muunna esimerkkipäätöksiä automaattisiksi johtopäätöksiksi.",
            ],
        },
    }
    sample_examples: list[dict[str, Any]] = []
    for doc in examples:
        case = str(doc.get("case") or "unknown")
        primary_type = str(doc.get("primary_type") or "source_document")
        by_case[case] = by_case.get(case, 0) + 1
        by_type[primary_type] = by_type.get(primary_type, 0) + 1
        for target in targets.values():
            if primary_type in target["document_types"]:
                target["example_count"] += 1
                if case not in target["cases"]:
                    target["cases"].append(case)
        if len(sample_examples) < 10:
            sample_examples.append(
                {
                    "case": case,
                    "filename": doc.get("filename", ""),
                    "primary_type": primary_type,
                    "text_chars": doc.get("text_chars", 0),
                    "excerpt": str(doc.get("text_excerpt") or "")[:500],
                }
            )
    target_rows = [target for target in targets.values() if target["example_count"] > 0]
    return {
        "authority_example_count": len(examples),
        "by_case": by_case,
        "by_type": by_type,
        "agent_comparison_targets": target_rows,
        "sample_examples": sample_examples,
        "usage_policy": "Viranomaisdokumentteja käytetään vain agenttitulosten vertailu- ja evaluaatioaineistona. Niitä ei ingestoida järjestelmän hakemus-/ohjeaineistoksi eikä lähetetä vector storeen.",
    }


def benchmark_lab_snapshot(db: Any, case_id: str) -> dict[str, Any]:
    source_ingest = artifact_json(db, case_id, "source_material_ingest", "main", {})
    benchmark = _dict(source_ingest.get("authority_benchmark"))
    documents = _list(source_ingest.get("authority_example_documents"))
    targets = _list(benchmark.get("agent_comparison_targets"))
    result = {
        "scope": "admin_eval_only",
        "normal_path_available": False,
        "authority_example_count": int(benchmark.get("authority_example_count") or len(documents) or 0),
        "target_count": len(targets),
        "agent_comparison_targets": targets,
        "sample_examples": benchmark.get("sample_examples") or documents[:10],
        "usage_policy": benchmark.get("usage_policy")
        or "Viranomaisdokumentteja käytetään vain admin/eval-benchmarkissa eikä normaalin hakemuksen knowledge basessa.",
        "vector_store_policy": "Benchmark-dokumentteja ei lähetetä normaalin arvioinnin vector storeen.",
        "eval_runner": "scripts/run_lupademo_story_evals.py",
        "mock_llm_supported": True,
    }
    save_artifact(db, case_id, "benchmark_lab", "main", result)
    return result


def production_hardening_snapshot(db: Any, case_id: str) -> dict[str, Any]:
    voice = voice_feature_state()
    benchmark = benchmark_lab_snapshot(db, case_id)
    metrics = artifact_json(db, case_id, "metrics", "main", {})
    items = [
        {
            "id": "mock_tests_without_azure",
            "status": "ready",
            "evidence": "scripts/verify_lupademo.sh pakottaa mock-LLM-polun perusverifiointiin.",
        },
        {
            "id": "voice_default_disabled",
            "status": "ready" if voice.get("status") == "disabled" else "partial",
            "evidence": voice.get("reason", ""),
        },
        {
            "id": "benchmark_admin_eval_only",
            "status": "ready" if benchmark.get("scope") == "admin_eval_only" and not benchmark.get("normal_path_available") else "partial",
            "evidence": benchmark.get("usage_policy", ""),
        },
        {
            "id": "file_upload_limits",
            "status": "ready",
            "evidence": (
                f"Sallitut päätteet {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}; "
                f"yksittäinen tiedosto enintään {MAX_UPLOAD_FILE_BYTES // (1024 * 1024)} Mt, "
                f"zip enintään {MAX_UPLOAD_ZIP_BYTES // (1024 * 1024)} Mt."
            ),
        },
        {
            "id": "metrics_audit_trail",
            "status": "ready" if metrics.get("proof_cards") or metrics.get("ai_work_impact") else "partial",
            "evidence": "Mittaristo sisältää proof_cards- ja ai_work_impact-rakenteet." if metrics else "Aja mittaristo vähintään kerran.",
        },
        {
            "id": "error_pages",
            "status": "ready",
            "evidence": "HTML-käyttöliittymäreiteille on oma virhesivu; /api-polut säilyttävät JSON-virhevastaukset.",
        },
        {
            "id": "prompt_version_display",
            "status": "ready",
            "evidence": "/admin/prompts näyttää promptin sha256-version ja viimeisimmän päivitysajan.",
        },
    ]
    status_counts: dict[str, int] = {}
    for item in items:
        status = item["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    result = {"items": items, "status_counts": status_counts, "ready_count": status_counts.get("ready", 0), "total_count": len(items)}
    save_artifact(db, case_id, "production_hardening", "main", result)
    return result


LUPADEMO_AGENT_EXPECTED = [
    "attachment_classifier",
    "structure_extractor",
    "field_suggestion_agent",
    "form_suggestion_agent",
    "completeness_validator",
    "consistency_validator",
    "criteria_assessor",
    "applicant_precheck_report",
    "caseworker_summary_agent",
    "additional_info_request_agent",
    "authority_memo_draft_agent",
]


def _parse_jsonish(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def agent_chain_audit(db: Any, case_id: str) -> dict[str, Any]:
    logs = [row for row in db.list_ai_logs(limit=200, case_id=case_id) if str(row.get("agent") or "").startswith("lupademo.")]
    artifacts = {
        str(row.get("name") or ""): row.get("data_json") or {}
        for row in db.list_artifacts(case_id)
        if row.get("type") == "agent_output"
    }
    calls: list[dict[str, Any]] = []
    called_agents: set[str] = set()
    file_search_call_count = 0
    json_response_call_count = 0
    fallback_count = 0
    vector_store_ids_seen: list[str] = []
    for row in logs:
        request = _parse_jsonish(row.get("request_json"))
        response = _parse_jsonish(row.get("response_json"))
        full_agent = str(row.get("agent") or "")
        agent = full_agent.removeprefix("lupademo.")
        called_agents.add(agent)
        tools = _list(request.get("tools"))
        file_search_tools = [tool for tool in tools if isinstance(tool, dict) and tool.get("type") == "file_search"]
        request_vector_ids: list[str] = []
        for tool in file_search_tools:
            request_vector_ids.extend([str(v) for v in tool.get("vector_store_ids") or [] if v])
        if file_search_tools:
            file_search_call_count += 1
        text_settings = _dict(request.get("text"))
        format_settings = _dict(text_settings.get("format"))
        response_format = str(format_settings.get("type") or "")
        if response_format == "json_object":
            json_response_call_count += 1
        agent_artifact = artifacts.get(agent, {})
        fallback_used = bool(agent_artifact.get("fallback_used"))
        if fallback_used:
            fallback_count += 1
        artifact_vector_ids = [str(v) for v in agent_artifact.get("used_vector_store_ids") or [] if v]
        for vs_id in [*request_vector_ids, *artifact_vector_ids]:
            if vs_id not in vector_store_ids_seen:
                vector_store_ids_seen.append(vs_id)
        calls.append(
            {
                "agent": agent,
                "model": row.get("model") or request.get("model") or "",
                "created_at": row.get("created_at"),
                "duration_ms": row.get("duration_ms"),
                "error": row.get("error") or "",
                "response_id": response.get("id") or response.get("response_id") or "",
                "response_format": response_format or "-",
                "file_search": bool(file_search_tools),
                "request_vector_store_ids": request_vector_ids,
                "artifact_vector_store_ids": artifact_vector_ids,
                "fallback_used": fallback_used,
                "fallback_reason": agent_artifact.get("fallback_reason") or "",
                "vector_store_retry_reason": agent_artifact.get("vector_store_retry_reason") or "",
            }
        )
    missing_expected = [agent for agent in LUPADEMO_AGENT_EXPECTED if agent not in called_agents]
    return {
        "call_count": len(calls),
        "agent_count": len(called_agents),
        "file_search_call_count": file_search_call_count,
        "json_response_call_count": json_response_call_count,
        "fallback_count": fallback_count,
        "vector_store_ids_seen": vector_store_ids_seen,
        "called_agents": sorted(called_agents),
        "missing_expected_agents": missing_expected,
        "recent_calls": calls[:20],
    }


def _agent_audit_settings_summary(settings: Any) -> dict[str, Any]:
    return {
        "llm_provider": str(getattr(settings, "llm_provider", "") or ""),
        "demo_mode": bool(getattr(settings, "demo_mode", False)),
        "model_core": str(getattr(settings, "azure_openai_model_core", "") or ""),
    }


def _write_agent_audit_attachment(settings: Any, case_id: str) -> tuple[str, str, int]:
    upload_root = Path(getattr(settings, "uploads_dir", "/tmp")) / "lupademo_agent_audit"
    upload_root.mkdir(parents=True, exist_ok=True)
    path = upload_root / f"{case_id}_tarkkailusuunnitelma.txt"
    text = (
        "Hakijan liite: tarkkailusuunnitelman ote.\n"
        "Varastoitavat aineet ovat vety, typpi ja pienet määrät huoltokemikaaleja. "
        "BAT-arviointi ja häiriötilanteiden hallinta kuvataan hakemuksessa. "
        "Liite kuuluu hakijan viranomaiselle toimitettavaan hakemuspakettiin."
    )
    path.write_text(text, encoding="utf-8")
    return str(path), text, path.stat().st_size


def run_agent_chain_audit(db: Any, settings: Any, case_id: str) -> dict[str, Any]:
    ensure_seed_state(db, case_id)
    started_at = time.time()
    environmental_data = {
        "activity_name": "Synteettinen vetylaitoksen auditointihakemus",
        "activity_description": "Vedyn tuotanto, elektrolyysi ja varastointi teollisuusalueella. Hakija kuvaa päästöt, häiriötilanteet ja naapureihin kohdistuvat vaikutukset.",
        "permit_basis": "Ympäristönsuojelulain mukainen ympäristölupa uudelle teolliselle toiminnalle.",
        "competent_authority": "Aluehallintovirasto",
        "raw_materials_chemicals": "Kemikaaliluettelo sisältää vedyn, typen ja huoltokemikaalit. Varastointi on kuvattu liitteessä.",
        "bat_assessment": "BAT-päätelmien mukainen energiatehokas elektrolyysi, päästöjen ehkäisy ja häiriötilanteiden seuranta.",
        "monitoring_plan": "Jatkuva käyttötarkkailu, poikkeamien raportointi ja vuosiyhteenveto viranomaiselle.",
    }
    building_data = {
        "project_description": "Auditointia varten täydennetty pientalohanke, jossa hulevedet viivytetään tontilla.",
        "building_height_m": "7.2",
        "stormwater_management": "Hulevedet viivytetään painanteessa ja johdetaan hallitusti kunnan verkostoon.",
        "wastewater_handling": "Liitytään kunnan vesi- ja viemäriverkostoon.",
        "designer": "Pääsuunnittelija Paula Pro",
    }
    event_data = {
        "event_name": "Auditointitapahtuma",
        "event_description": "Avoin kaupunkitapahtuma rajatulla torialueella.",
        "event_location": "Vantaan tori",
        "expected_attendees": "500",
        "landowner_permission": "Alueen haltijan lupa on saatu ja liitetään hakemukseen.",
        "rescue_plan": "Pelastussuunnitelmassa kuvataan poistumisreitit, ensiapu ja alkusammutus.",
        "traffic_arrangements": "Huoltoajo ja jalankulkureitit erotellaan tilapäisillä opasteilla.",
        "waste_management": "Jätehuolto järjestetään lajitteluastioilla ja loppusiivouksella.",
    }
    update_application_data(db, case_id, "environmental_industrial", environmental_data)
    update_application_data(db, case_id, "building_residential", building_data)
    update_application_data(db, case_id, "event_public", event_data)

    attachment_path, attachment_text, attachment_size = _write_agent_audit_attachment(settings, case_id)
    attachment = add_attachment(
        db,
        case_id,
        "environmental_industrial",
        "agenttiauditointi_tarkkailusuunnitelma.txt",
        attachment_size,
        "official_submission",
        attachment_text,
        local_path=attachment_path,
        settings=settings,
    )

    schema = load_form_schema("environmental_industrial")
    attachments = list_attachments(db, case_id, "environmental_industrial")
    common_data = artifact_json(db, case_id, "lv_common_data", "main", {"data": DEFAULT_COMMON_DATA.copy()}).get("data", {})
    audit_runs: list[dict[str, Any]] = []

    def record(name: str, fn: Any) -> None:
        item_started = time.time()
        try:
            output = fn()
            audit_runs.append({"step": name, "status": "ok", "duration_ms": int((time.time() - item_started) * 1000), "output_keys": sorted(output.keys()) if isinstance(output, dict) else []})
        except Exception as exc:
            audit_runs.append({"step": name, "status": "error", "duration_ms": int((time.time() - item_started) * 1000), "error": str(exc)[:500]})

    record("structure_extractor", lambda: structure_extract({"request_id": f"audit_{uuid.uuid4().hex[:8]}", "permit_type": "environmental_industrial", "form_schema": schema, "source_text": json.dumps(environmental_data, ensure_ascii=False)}, db=db, settings=settings, case_id=case_id))
    record("field_suggestion_agent", lambda: create_field_suggestion(db, case_id, "environmental_industrial", "activity_description", settings=settings))
    record(
        "form_suggestion_agent",
        lambda: suggestion_service_form(
            {
                "request_id": f"audit_{uuid.uuid4().hex[:8]}",
                "tenant_id": "lupademo",
                "permit_type": "environmental_industrial",
                "field_id": "activity_description",
                "target_field_ids": ["activity_description", "monitoring_plan"],
                "form_schema": schema,
                "form_data": environmental_data,
                "attachments": attachments,
                "common_data": common_data,
            },
            db=db,
            settings=settings,
            case_id=case_id,
        ),
    )
    record("application_check", lambda: run_application_check(db, case_id, "environmental_industrial", settings=settings))
    record("caseworker_summary_agent", lambda: authority_summary(db, case_id, "environmental_industrial", settings=settings))
    record("additional_info_request_agent", lambda: additional_info_draft(db, case_id, "environmental_industrial", settings=settings))
    record("authority_memo_draft_agent", lambda: authority_memo(db, case_id, "environmental_industrial", settings=settings))

    audit = agent_chain_audit(db, case_id)
    completed_at = time.time()
    result = {
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": int((completed_at - started_at) * 1000),
        "settings": _agent_audit_settings_summary(settings),
        "synthetic_attachment": {
            "id": attachment.get("id"),
            "filename": attachment.get("filename"),
            "delivery_scope": attachment.get("delivery_scope"),
            "primary_type": attachment.get("primary_type"),
            "vector_store_status": attachment.get("vector_store_status"),
            "vector_store_id": attachment.get("vector_store_id"),
        },
        "steps": audit_runs,
        "agent_chain_audit": audit,
    }
    save_artifact(db, case_id, "agent_chain_audit_run", "main", result)
    event(
        db,
        case_id,
        "agent_chain.audit_run.completed",
        "Full lupademo agent chain audit completed",
        {
            "call_count": audit.get("call_count", 0),
            "missing_expected_agents": audit.get("missing_expected_agents", []),
            "fallback_count": audit.get("fallback_count", 0),
            "file_search_call_count": audit.get("file_search_call_count", 0),
        },
    )
    return result


def _readiness_item(item_id: str, title: str, status: str, evidence: list[str], next_action: str = "") -> dict[str, Any]:
    return {
        "id": item_id,
        "title": title,
        "status": status,
        "evidence": [item for item in evidence if item],
        "next_action": next_action,
    }


def _read_text_if_exists(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except Exception:
        return ""


def readiness_snapshot(
    db: Any,
    case_id: str,
    source_ingest: dict[str, Any] | None = None,
    agent_audit: dict[str, Any] | None = None,
    attachment_extraction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_ingest = _dict(source_ingest or artifact_json(db, case_id, "source_material_ingest", "main", {}))
    agent_audit = _dict(agent_audit or agent_chain_audit(db, case_id))
    attachment_extraction = _dict(attachment_extraction or attachment_extraction_summary(db, case_id))
    analysis = _dict(source_ingest.get("analysis"))
    by_case = _dict(analysis.get("by_case"))
    prompt_files = sorted((SPEC_ROOT / "prompts").glob("*.prompt.md"))
    e2e_path = ROOT / "tests" / "e2e" / "lupademo-full-flows.spec.js"
    e2e_text = _read_text_if_exists(e2e_path)
    e2e_named_flows = len(re.findall(r"E2E-00[1-5]", e2e_text))
    e2e_total_tests = len(re.findall(r"(?m)^\s*test\(", e2e_text))
    case_names = {str(key).lower().replace("-", "").replace(" ", "") for key in by_case}
    has_real_case_set = {"p2x", "rengas"}.issubset(case_names) and any("sharp" in key for key in case_names)
    source_blocked = bool(source_ingest.get("external_upload_blocked"))
    extracted_count = int(analysis.get("extracted_text_document_count") or 0)
    document_count = int(analysis.get("document_count") or 0)
    attachment_count = int(attachment_extraction.get("attachment_count") or 0)
    extracted_attachment_count = int(attachment_extraction.get("extracted_text_attachment_count") or 0)
    ingested_count = int(analysis.get("ingested_document_count") or source_ingest.get("file_count") or 0)
    ready_file_count = int(source_ingest.get("ready_file_count") or 0)
    authority_count = int(analysis.get("excluded_authority_example_count") or 0)
    guidance_count = int(analysis.get("guidance_reference_count") or 0)
    called_agents = set(agent_audit.get("called_agents") or [])
    missing_agents = agent_audit.get("missing_expected_agents") or []
    criteria_sets = [effective_criteria(db, case_id, permit_type) for permit_type in PERMIT_SCHEMAS]
    criteria_ready = len(criteria_sets) == len(PERMIT_SCHEMAS) and all(item.get("rules") for item in criteria_sets)
    events = artifact_json(db, case_id, "lupademo_events", "main", {"events": []}).get("events", [])
    checks_run = sum(1 for event_row in events if event_row.get("action") == "ai.check.completed")
    accepted_suggestions = 0
    for permit_type in PERMIT_SCHEMAS:
        accepted_suggestions += sum(
            1
            for suggestion in artifact_json(db, case_id, "ai_suggestions", permit_type, {"suggestions": []}).get("suggestions", [])
            if suggestion.get("status") == "accepted"
        )

    items: list[dict[str, Any]] = []
    agent_status = "ready" if agent_audit.get("call_count", 0) > 0 and not missing_agents and int(agent_audit.get("fallback_count") or 0) == 0 else ("partial" if agent_audit.get("call_count", 0) else "missing")
    items.append(
        _readiness_item(
            "azure_responses_agent_chain",
            "Azure OpenAI / Responses API -agenttiketju",
            agent_status,
            [
                f"LLM-kutsuja: {agent_audit.get('call_count', 0)}",
                f"JSON Responses: {agent_audit.get('json_response_call_count', 0)}",
                f"Fallbackeja: {agent_audit.get('fallback_count', 0)}",
                "Puuttuvat agentit: " + ", ".join(missing_agents) if missing_agents else "Kaikki odotetut agentit ajettu tässä casessa",
            ],
            "Aja koko agenttiketju Azure-moodissa samalla casella." if agent_status != "ready" else "",
        )
    )
    rag_ready = int(agent_audit.get("file_search_call_count") or 0) > 0 and bool(agent_audit.get("vector_store_ids_seen"))
    items.append(
        _readiness_item(
            "rag_file_search",
            "RAG / file_search / vector store -haku",
            "ready" if rag_ready and not source_blocked else ("blocked" if source_blocked else ("partial" if rag_ready else "missing")),
            [
                f"file_search-kutsuja: {agent_audit.get('file_search_call_count', 0)}",
                f"Vector storet auditissa: {len(agent_audit.get('vector_store_ids_seen') or [])}",
                f"Lähdeaineiston valmiit tiedostot: {ready_file_count}",
                source_ingest.get("external_upload_block_reason", "") if source_blocked else "",
            ],
            "Hyväksy todellisen hakemusaineiston ulkoinen Azure-upload ja aja ingest uudelleen." if source_blocked else ("Aja agenttiketju file_search-työkalulla." if not rag_ready else ""),
        )
    )
    extraction_total = document_count + attachment_count
    extraction_ready_count = extracted_count + extracted_attachment_count
    extraction_status = "ready" if extraction_total > 0 and extraction_ready_count == extraction_total else ("partial" if extraction_ready_count > 0 else "missing")
    items.append(
        _readiness_item(
            "document_extraction",
            "PDF/DOCX/XLSX-tekstinpoiminta liitteissä ja aineistossa",
            extraction_status,
            [
                f"Teksti poimittu: {extracted_count} / {document_count} dokumenttia",
                f"Ladattujen liitteiden tekstipoiminta: {extracted_attachment_count} / {attachment_count}",
                f"Järjestelmään valitut hakemus-/liitedokumentit: {ingested_count}",
                f"Ohje-/lakiaineistoa: {guidance_count}",
            ],
            "Tarkista ne tiedostot, joiden extraction_status ei ole ready." if extraction_status != "ready" else "",
        )
    )
    items.append(
        _readiness_item(
            "prompt_orchestration",
            "Prompttien orkestrointi LLM-abstraktion kautta",
            "ready" if len(prompt_files) >= len(LUPADEMO_AGENT_EXPECTED) and agent_audit.get("json_response_call_count", 0) > 0 else "partial",
            [
                f"Prompt-tiedostoja: {len(prompt_files)}",
                f"Ajetut agentit: {len(called_agents)}",
                f"JSON Responses -kutsuja: {agent_audit.get('json_response_call_count', 0)}",
            ],
            "Aja kaikki odotetut agentit samalla casella ja tarkista puuttuvat agentit auditista." if missing_agents else "",
        )
    )
    items.append(
        _readiness_item(
            "playwright_e2e",
            "Playwright-testit viidelle speksin e2e-virralle",
            "ready" if e2e_named_flows >= 5 and e2e_total_tests >= 5 else "missing",
            [
                f"Nimettyjä E2E-001...005-virtoja: {e2e_named_flows}",
                f"Playwright-testejä tiedostossa: {e2e_total_tests}",
                str(e2e_path),
            ],
            "Lisää puuttuvat E2E-001...005-virrat." if e2e_named_flows < 5 else "",
        )
    )
    items.append(
        _readiness_item(
            "criteria_maintenance",
            "Kriteerien ylläpito, muokkaus ja versionhallinta",
            "ready" if criteria_ready else "partial",
            [
                f"Kriteeristöjä: {len(criteria_sets)} / {len(PERMIT_SCHEMAS)}",
                "Sääntömäärät: " + ", ".join(f"{item.get('permit_type')}={len(item.get('rules') or [])}" for item in criteria_sets),
            ],
            "Täydennä kriteeristöt, joilla ei ole aktiivisia sääntöjä." if not criteria_ready else "",
        )
    )
    items.append(
        _readiness_item(
            "epic8_metrics",
            "EPIC 8 -mittaristo ja statistiikka",
            "ready",
            [
                f"AI-tarkistuksia: {checks_run}",
                f"Hyväksyttyjä ehdotuksia: {accepted_suggestions}",
                "Laatupiste ennen/jälkeen ja tapahtumalokin kooste sisältyvät mittaristoon",
            ],
        )
    )
    event_schema = load_form_schema("event_public")
    items.append(
        _readiness_item(
            "event_permit_minidemo",
            "Tapahtumalupaminidemo",
            "ready" if event_schema.get("required_attachments") and event_schema.get("fields") else "missing",
            [
                f"Kenttiä: {len(event_schema.get('fields') or [])}",
                f"Pakollisia liitteitä: {len(event_schema.get('required_attachments') or [])}",
            ],
        )
    )
    real_status = "ready" if has_real_case_set and not source_blocked and ready_file_count >= ingested_count > 0 else ("blocked" if has_real_case_set and source_blocked else ("partial" if document_count > 0 else "missing"))
    items.append(
        _readiness_item(
            "real_zip_ingest",
            "Todellisten P2X/Ren-Gas/SharpCell-zippien ingest ja analyysi",
            real_status,
            [
                "Case-jakauma: " + ", ".join(f"{key}={value}" for key, value in by_case.items()) if by_case else "Case-jakaumaa ei ole",
                f"Hakemus-/liitedokumentteja: {ingested_count}",
                f"Viranomaisesimerkkejä eroteltu: {authority_count}",
                f"Upload estetty: {source_ingest.get('blocked_file_count', 0)}",
                "Puuttuvat zipit: " + ", ".join(source_ingest.get("missing_expected") or []) if source_ingest.get("missing_expected") else "Odotetut zipit löytyivät tai lähdettä ei ole ajettu",
            ],
            "Aja ulkoinen Azure-upload vain nimenomaisella luvalla." if source_blocked else ("Aja ingest todellisella `esimerkkihakemukset`-aineistolla." if real_status != "ready" else ""),
        )
    )
    status_counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "items": items,
        "status_counts": status_counts,
        "ready_count": status_counts.get("ready", 0),
        "blocked_count": status_counts.get("blocked", 0),
        "total_count": len(items),
        "all_ready": len(items) > 0 and status_counts.get("ready", 0) == len(items),
    }


def ingest_source_materials(db: Any, settings: Any, case_id: str, source_dir: str = "source_materials") -> dict[str, Any]:
    base = resolve_source_material_dir(source_dir)
    extracted_root = Path(settings.uploads_dir) / "lupademo_source_materials"
    extracted_root.mkdir(parents=True, exist_ok=True)
    discovered: list[dict[str, Any]] = []
    missing_expected = []
    for name in EXPECTED_SOURCE_ARCHIVES:
        if not (base / name).exists():
            missing_expected.append(name)
    if base.exists():
        for path in base.iterdir():
            if path.suffix.lower() == ".zip":
                target_dir = extracted_root / path.stem
                target_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(path) as zf:
                    safe_extract_zip(zf, target_dir)
                case_name = _source_case_name(path.name)
                for doc_path in target_dir.rglob("*"):
                    if doc_path.is_file() and doc_path.suffix.lower() in SOURCE_MATERIAL_EXTENSIONS:
                        discovered.append({"path": doc_path, "case": case_name, "source_archive": path.name, "analysis_base": target_dir})
            elif path.is_file() and path.suffix.lower() in SOURCE_MATERIAL_EXTENSIONS:
                discovered.append({"path": path, "case": _source_case_name(path.name), "source_archive": "", "analysis_base": base})
    file_rows = []
    inventory_index = _case_inventory_index()
    documents = []
    for item in discovered:
        path = Path(item["path"])
        filename = path.name
        case_name = str(item.get("case") or "unknown")
        analysis_base = Path(item.get("analysis_base") or base)
        source_archive = str(item.get("source_archive") or "")
        analysis = analyze_source_material_file(
            path,
            base_dir=analysis_base,
            case_name=case_name,
            source_archive=source_archive,
            inventory_index=inventory_index,
        )
        documents.append(analysis)
        if not analysis.get("ingest_to_system"):
            continue
        try:
            mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            file_id = db.add_file(
                case_id,
                filename,
                str(path),
                mime_type,
                path.stat().st_size,
                kind="source_material",
                source_group=case_name if case_name != "unknown" else "lupademo_source_materials",
                type_primary=analysis["primary_type"],
                type_tags=json.dumps(
                    {
                        "source_archive": source_archive,
                        "inventory_category": analysis.get("inventory_category", ""),
                        "extraction_status": analysis.get("extraction_status", ""),
                        "document_role": analysis.get("document_role", ""),
                    },
                    ensure_ascii=False,
                ),
                vectorize=1,
            )
            file_rows.append(
                {
                    "file_id": file_id,
                    "filename": filename,
                    "path": str(path),
                    "case": case_name,
                    "source_archive": source_archive,
                    "primary_type": analysis["primary_type"],
                    "document_role": analysis.get("document_role", ""),
                    "extraction_status": analysis["extraction_status"],
                }
            )
        except Exception:
            pass
    vector_store_id = ""
    _ingest_backend: Any = None
    file_errors: list[dict[str, Any]] = []
    blocked_file_count = 0
    upload_allowed, upload_block_reason = external_real_case_upload_allowed(base, settings)
    if file_rows:
        if not upload_allowed:
            for row in file_rows:
                if row.get("document_role") != "guidance_reference":
                    row["status"] = "blocked_external_upload"
                    row["error"] = upload_block_reason
                    blocked_file_count += 1
                    try:
                        db.update_file(int(row["file_id"]), status="blocked_external_upload", error=upload_block_reason)
                    except Exception:
                        pass
        uploadable_rows = [row for row in file_rows if row.get("status") != "blocked_external_upload"]
        if not uploadable_rows and blocked_file_count:
            vector_store_id = upload_block_reason
        elif uploadable_rows:
            try:
                _ingest_backend = create_llm_client(settings, purpose="core")
                vector_store_id = _ingest_backend.create_vector_store(f"lupademo-{case_id[:8]}-source-materials")
                db.set_research_vector_store(case_id, vector_store_id)
            except Exception as exc:
                vector_store_id = f"ingest_failed:{str(exc)[:180]}"
        if vector_store_id and not vector_store_id.startswith("ingest_failed:") and uploadable_rows and _ingest_backend:
            for row in uploadable_rows:
                try:
                    db.update_file(int(row["file_id"]), status="uploading", error=None)
                    _, azure_file_id = _ingest_backend.upload_file_from_path(row["path"])
                    row["azure_file_id"] = azure_file_id
                    db.update_file(int(row["file_id"]), azure_file_id=azure_file_id, status="uploaded")
                    vs_file_id = _ingest_backend.attach_file_to_vector_store(vector_store_id, azure_file_id)
                    row["azure_vs_file_id"] = vs_file_id
                    db.update_file(int(row["file_id"]), azure_vs_file_id=vs_file_id, status="ingesting")
                    _ingest_backend.wait_vector_store_file_completed(vector_store_id, vs_file_id)
                    row["status"] = "ready"
                    db.update_file(int(row["file_id"]), status="ready", error=None)
                except Exception as exc:
                    row["status"] = "error"
                    row["error"] = str(exc)[:500]
                    file_errors.append({"file_id": row.get("file_id"), "filename": row.get("filename"), "error": str(exc)[:500]})
                    try:
                        db.update_file(int(row["file_id"]), status="error", error=str(exc))
                    except Exception:
                        pass
    analysis_summary = source_material_summary(documents, missing_expected)
    authority_benchmark = authority_example_benchmark(documents)
    ready_file_count = sum(1 for row in file_rows if row.get("status") == "ready")
    result = {
        "source_dir": str(base),
        "files": file_rows,
        "file_count": len(file_rows),
        "ready_file_count": ready_file_count,
        "failed_file_count": len(file_errors),
        "blocked_file_count": blocked_file_count,
        "external_upload_blocked": blocked_file_count > 0,
        "external_upload_block_reason": upload_block_reason,
        "file_errors": file_errors,
        "missing_expected": missing_expected,
        "vector_store_id": vector_store_id,
        "analysis": analysis_summary,
        "documents": documents,
        "case_documents": [doc for doc in documents if doc.get("ingest_to_system")],
        "guidance_reference_documents": [doc for doc in documents if doc.get("document_role") == "guidance_reference"],
        "authority_example_documents": [doc for doc in documents if doc.get("document_role") == "authority_example"],
        "authority_benchmark": authority_benchmark,
    }
    save_artifact(db, case_id, "source_material_ingest", "main", result)
    event(
        db,
        case_id,
        "source_materials.ingested",
        "Source materials ingest attempted",
        {
            "file_count": len(file_rows),
            "ready_file_count": ready_file_count,
            "failed_file_count": len(file_errors),
            "blocked_file_count": blocked_file_count,
            "external_upload_blocked": blocked_file_count > 0,
            "missing_expected": missing_expected,
            "vector_store_id": vector_store_id,
            "by_case": analysis_summary.get("by_case", {}),
            "by_type": analysis_summary.get("by_type", {}),
            "by_role": analysis_summary.get("by_role", {}),
        },
    )
    return result


def submit_application(db: Any, case_id: str, permit_type: str, settings: Any = None) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    ensure_fresh_precheck_or_raise(db, case_id, permit_type, settings=settings)
    package = compose_submission_package(db, case_id, permit_type)
    receipt = {
        "receipt_id": f"LV-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}",
        "application_id": permit_type,
        "received_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "authority": load_form_schema(permit_type).get("authority_default", "Viranomainen"),
        "package_id": package["package_id"],
        "official_attachment_count": len(package["official_attachments"]),
    }
    save_artifact(db, case_id, "receipt", permit_type, receipt)
    app = ensure_application(db, case_id, permit_type)
    app["status"] = "submitted"
    save_artifact(db, case_id, "permit_application", permit_type, app)
    project = artifact_json(db, case_id, "lv_project", "main", {"statuses": {}})
    project.setdefault("statuses", {})[permit_type] = "submitted"
    save_artifact(db, case_id, "lv_project", "main", project)
    queue = artifact_json(db, case_id, "authority_queue", "main", {"items": []})
    if not any(i.get("application_id") == permit_type for i in queue.get("items", [])):
        check_report = artifact_json(db, case_id, "check_report", permit_type, {})
        issues = check_report.get("issues") or []
        major_count = sum(1 for issue in issues if issue.get("severity") == "major" and not issue.get("resolved"))
        issue_count = sum(1 for issue in issues if not issue.get("resolved"))
        form_data = app.get("form_data") or {}
        common = artifact_json(db, case_id, "lv_common_data", "main", {"data": DEFAULT_COMMON_DATA.copy()}).get("data", {})
        queue.setdefault("items", []).append(
            {
                "application_id": permit_type,
                "application_title": load_form_schema(permit_type).get("title", permit_type),
                "receipt_id": receipt["receipt_id"],
                "applicant_name": form_data.get("applicant_name") or common.get("applicant_name", ""),
                "municipality": form_data.get("municipality") or common.get("municipality", ""),
                "status": "käsittelyssä",
                "submitted_at": receipt["received_at"],
                "quality_score": check_report.get("score", 75),
                "open_issue_count": issue_count,
                "info_request_risk": "korkea" if major_count >= 2 else ("kohonnut" if major_count == 1 else "matala"),
                "permit_type": permit_type,
                "processing_stage": "vastaanotettu",
                "sla_due_at": time.strftime("%Y-%m-%d", time.localtime(time.time() + 30 * 24 * 60 * 60)),
                "urgency": "kiireellinen" if major_count >= 2 or check_report.get("score", 100) < 60 else "normaali",
            }
        )
    save_artifact(db, case_id, "authority_queue", "main", queue)
    event(db, case_id, "application.submitted", "Application submitted", {"application_id": permit_type, "receipt_id": receipt["receipt_id"]})
    return receipt


def _caseworker_observation_id(kind: str, raw_id: Any, index: int) -> str:
    token = re.sub(r"[^A-Za-z0-9_-]+", "_", str(raw_id or "")).strip("_").lower()[:64]
    return f"{kind}_{token or index}"


def caseworker_observation_panel(db: Any, case_id: str, permit_type: str) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    app = ensure_application(db, case_id, permit_type)
    form_data = _dict(app.get("form_data"))
    attachments = list_attachments(db, case_id, permit_type)
    check_report = artifact_json(db, case_id, "check_report", permit_type, {"issues": []})
    authority_summary_data = artifact_json(db, case_id, "authority_summary", permit_type, {})
    memo = artifact_json(db, case_id, "authority_memo", permit_type, {})
    suggestions = artifact_json(db, case_id, "ai_suggestions", permit_type, {"suggestions": []}).get("suggestions", [])
    issues = _list(check_report.get("issues"))
    unresolved_issues = [issue for issue in issues if not issue.get("resolved")]
    blocking_issues = [issue for issue in unresolved_issues if issue.get("severity") == "blocking"]
    major_issues = [issue for issue in unresolved_issues if issue.get("severity") == "major"]
    official_attachments = [att for att in attachments if att.get("delivery_scope") == "official_submission"]
    ai_context_attachments = [att for att in attachments if att.get("delivery_scope") == "ai_context_only"]
    authority_only_attachments = [att for att in attachments if att.get("delivery_scope") == "internal_authority_only"]
    pending_suggestions = [suggestion for suggestion in suggestions if suggestion.get("status") == "pending"]
    accepted_suggestions = [suggestion for suggestion in suggestions if suggestion.get("status") == "accepted"]

    observations: list[dict[str, Any]] = []
    for index, issue in enumerate((blocking_issues + major_issues)[:4], start=1):
        observations.append(
            {
                "id": _caseworker_observation_id("issue", issue.get("id") or issue.get("title"), index),
                "title": issue.get("title") or issue.get("id") or "Avoin havainto",
                "detail": issue.get("detail") or issue.get("suggested_fix") or "Tarkista hakemuksen tieto käsittelyssä.",
                "severity": issue.get("severity") or "major",
                "source": "AI-tarkistus",
            }
        )
    for index, question in enumerate((authority_summary_data.get("open_questions") or [])[:3], start=1):
        text = str(question or "").strip()
        if text:
            observations.append({"id": _caseworker_observation_id("summary_question", text, index), "title": "Avoin kysymys", "detail": text, "severity": "major", "source": "Yhteenvetoagentti"})
    for index, observation in enumerate((memo.get("ai_observations") or [])[:3], start=1):
        text = str(observation or "").strip()
        if text:
            observations.append({"id": _caseworker_observation_id("memo_observation", text, index), "title": "Päätöstuen havainto", "detail": text, "severity": "minor", "source": "Muistioagentti"})
    if not observations:
        observations.append(
            {
                "id": "observation_no_critical_ai_findings",
                "title": "Ei kriittisiä AI-havaintoja",
                "detail": "Tarkistus ei nostanut blocking- tai major-puutteita. Käsittelijän tulee silti varmistaa toimivalta, liitteet ja säädösperuste.",
                "severity": "good",
                "source": "Koostepaneeli",
            }
        )
    decisions = artifact_json(db, case_id, "caseworker_observation_decisions", permit_type, {"items": {}})
    decision_items = _dict(decisions.get("items"))
    for observation in observations:
        decision = decision_items.get(observation["id"], {}) if isinstance(decision_items, dict) else {}
        observation["review_status"] = decision.get("status", "new")
        observation["review_note"] = decision.get("note", "")
        observation["reviewed_at"] = decision.get("updated_at", "")

    def checklist_item(title: str, ready: bool, detail: str, source: str, needs_action: bool = False) -> dict[str, Any]:
        return {
            "title": title,
            "status": "needs_action" if needs_action else ("ready" if ready else "needs_review"),
            "detail": detail,
            "source": source,
        }

    checklist = [
        checklist_item(
            "Toimivalta ja lupaperuste",
            bool(str(form_data.get("competent_authority") or "").strip() and str(form_data.get("permit_basis") or "").strip()),
            "Toimivaltainen viranomainen ja lupaperuste löytyvät lomakedatasta." if str(form_data.get("competent_authority") or "").strip() else "Varmista toimivaltainen viranomainen ja lupaperuste ennen päätösvalmistelua.",
            "Lomakedata",
        ),
        checklist_item(
            "Viranomaiselle lähtevät liitteet",
            bool(official_attachments),
            f"Viranomaispaketissa on {len(official_attachments)} liitettä.",
            "Liitteiden scope",
        ),
        checklist_item(
            "Blocking-puutteet",
            not blocking_issues,
            "Ei avoimia blocking-puutteita." if not blocking_issues else f"Avoimia blocking-puutteita: {len(blocking_issues)}.",
            "AI-tarkistus",
            needs_action=bool(blocking_issues),
        ),
        checklist_item(
            "AI-ehdotusten hyväksyntä",
            not pending_suggestions,
            "Kaikki luodut ehdotukset on käsitelty." if not pending_suggestions else f"Käsittelemättömiä AI-ehdotuksia: {len(pending_suggestions)}.",
            "Ehdotusloki",
        ),
    ]
    score = check_report.get("score") if isinstance(check_report, dict) else None
    risk_level = "korkea" if blocking_issues else ("kohonnut" if major_issues else "matala")
    latest_jobs = list_ai_jobs(db, case_id, permit_type, limit=3)
    panel = {
        "risk_level": risk_level,
        "quality_score": score if score is not None else "",
        "top_ai_observations": observations[:6],
        "caseworker_checklist": checklist,
        "evidence_summary": {
            "official_attachment_count": len(official_attachments),
            "ai_context_attachment_count": len(ai_context_attachments),
            "authority_only_attachment_count": len(authority_only_attachments),
            "accepted_suggestions": len(accepted_suggestions),
            "pending_suggestions": len(pending_suggestions),
            "source_backed_suggestions": sum(1 for suggestion in suggestions if suggestion.get("evidence_refs")),
            "latest_job_status": latest_jobs[0].get("status", "") if latest_jobs else "",
            "latest_job_title": latest_jobs[0].get("title", "") if latest_jobs else "",
            "reviewed_observations": sum(1 for observation in observations if observation.get("review_status") in {"accepted", "rejected"}),
            "human_review_observations": sum(1 for observation in observations if observation.get("review_status") == "needs_human_review"),
        },
        "source_boundaries": [
            "Hakemus ja official_submission-liitteet kuuluvat viranomaiselle lähtevään aineistoon.",
            "ai_context_only-liitteet tukevat valmistelua, mutta eivät lähde viranomaispakettiin ilman erillistä hyväksyntää.",
            "internal_authority_only- ja viranomaisesimerkkiaineisto ovat benchmark/eval-aineistoa eivätkä normaalin arvioinnin knowledge basea.",
        ],
    }
    save_artifact(db, case_id, "caseworker_observations", permit_type, panel)
    return panel


def update_caseworker_observation_status(
    db: Any,
    case_id: str,
    permit_type: str,
    observation_id: str,
    status: Literal["accepted", "rejected", "needs_human_review"],
    note: str = "",
) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    if status not in {"accepted", "rejected", "needs_human_review"}:
        raise ValueError("invalid_observation_status")
    panel = caseworker_observation_panel(db, case_id, permit_type)
    observation_ids = {str(item.get("id")) for item in panel.get("top_ai_observations", []) if item.get("id")}
    if observation_id not in observation_ids:
        raise KeyError(observation_id)
    decisions = artifact_json(db, case_id, "caseworker_observation_decisions", permit_type, {"items": {}})
    items = _dict(decisions.get("items"))
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    items[observation_id] = {"observation_id": observation_id, "status": status, "note": note, "updated_at": now}
    decisions["items"] = items
    decisions["updated_at"] = time.time()
    save_artifact(db, case_id, "caseworker_observation_decisions", permit_type, decisions)
    caseworker_observation_panel(db, case_id, permit_type)
    event(db, case_id, "authority.observation.reviewed", "Caseworker reviewed AI observation", {"application_id": permit_type, "observation_id": observation_id, "status": status})
    return items[observation_id]


def authority_summary(db: Any, case_id: str, permit_type: str, settings: Any = None) -> dict[str, Any]:
    app = ensure_application(db, case_id, permit_type)
    report = artifact_json(db, case_id, "check_report", app["permit_type"], {"issues": []})
    fallback = {
        "summary_md": f"## Hakemuksen yhteenveto\n\nHakemus koskee lupatyyppiä `{app['permit_type']}`. AI-havainnot ovat valmistelua tukevia ehdotuksia, eivät päätöksiä.",
        "key_facts": [f"Hakija: {app.get('form_data', {}).get('applicant_name', '')}", f"Hanke: {app.get('form_data', {}).get('project_description') or app.get('form_data', {}).get('activity_name', '')}"],
        "open_questions": [i.get("title", "") for i in report.get("issues", []) if i.get("severity") in {"blocking", "major"}],
        "evidence_refs": [{"source_id": "check_report", "title": "Lupademo tarkistusraportti", "locator": "artifact", "confidence": 0.8}],
        "confidence": 0.78,
    }
    if settings is not None:
        summary_model = run_json_agent(
            db,
            settings,
            case_id,
            "caseworker_summary_agent",
            {"form_data": json.dumps(app.get("form_data", {}), ensure_ascii=False), "context": json.dumps({"attachments": list_attachments(db, case_id, app["permit_type"]), "check_report": report}, ensure_ascii=False)},
            CaseworkerSummaryResponse,
            vector_ids=azure_vector_store_ids(db, settings, case_id),
            fallback=fallback,
            max_output_tokens=5000,
        )
        summary = _as_dict(summary_model)
    else:
        summary = fallback
    save_artifact(db, case_id, "authority_summary", app["permit_type"], summary)
    event(db, case_id, "authority.summary.generated", "Authority summary generated", {"application_id": app["permit_type"]})
    return summary


def additional_info_draft(db: Any, case_id: str, permit_type: str, settings: Any = None) -> dict[str, Any]:
    app = ensure_application(db, case_id, permit_type)
    report = artifact_json(db, case_id, "check_report", app["permit_type"], {"issues": []})
    items = [i for i in report.get("issues", []) if i.get("severity") in {"blocking", "major"}]
    fallback = {
        "subject": "Täydennyspyyntöluonnos lupahakemukseen",
        "body_md": "Hyvä hakija,\n\nhakemuksen käsittelyn jatkamiseksi pyydämme täydentämään alla kuvatut tiedot. Tämä on käsittelijän muokattava luonnos.",
        "requested_items": [{"title": i.get("title"), "reason": i.get("detail"), "related_issue_id": i.get("id"), "evidence_refs": i.get("evidence_refs", [])} for i in items],
        "review_notes_for_caseworker": ["Tarkista määräpäivä ja säädösviittaukset ennen lähettämistä."],
    }
    if settings is not None:
        draft_model = run_json_agent(
            db,
            settings,
            case_id,
            "additional_info_request_agent",
            {"check_report": json.dumps(report, ensure_ascii=False), "form_data": json.dumps(app.get("form_data", {}), ensure_ascii=False)},
            AdditionalInfoRequestDraft,
            vector_ids=azure_vector_store_ids(db, settings, case_id),
            fallback=fallback,
            max_output_tokens=5000,
            reasoning_effort="low",
        )
        draft = draft_model
    else:
        draft = fallback
    save_artifact(db, case_id, "additional_info_draft", app["permit_type"], draft)
    event(db, case_id, "authority.additional_info_draft.generated", "Additional info draft generated", {"application_id": app["permit_type"], "items": len(items)})
    return draft


def _compact_authority_context(db: Any, case_id: str, app: dict[str, Any]) -> dict[str, Any]:
    check_report = artifact_json(db, case_id, "check_report", app["permit_type"], {})
    form_data = _dict(app.get("form_data", {}))
    compact_form_data = {}
    for key, value in form_data.items():
        text = str(value or "").strip()
        if text:
            compact_form_data[str(key)] = text[:320]
    issues = check_report.get("issues") if isinstance(check_report, dict) else []
    compact_issues = []
    for issue in issues[:8] if isinstance(issues, list) else []:
        if not isinstance(issue, dict):
            continue
        compact_issues.append(
            {
                "id": issue.get("id", ""),
                "type": issue.get("type", ""),
                "severity": issue.get("severity", ""),
                "title": issue.get("title", ""),
                "detail": str(issue.get("detail") or "")[:500],
                "suggested_fix": str(issue.get("suggested_fix") or "")[:300],
            }
        )
    compact_attachments = []
    official_attachments = [a for a in list_attachments(db, case_id, app["permit_type"]) if a.get("delivery_scope") == "official_submission"]
    excluded_attachments = [a for a in list_attachments(db, case_id, app["permit_type"]) if a.get("delivery_scope") != "official_submission"]
    for attachment in official_attachments[:10]:
        compact_attachments.append(
            {
                "id": attachment.get("id", ""),
                "filename": attachment.get("filename", ""),
                "delivery_scope": attachment.get("delivery_scope", ""),
                "primary_type": attachment.get("primary_type", ""),
                "summary": str(attachment.get("summary") or "")[:240],
                "vector_store_status": attachment.get("vector_store_status", ""),
            }
        )
    return {
        "application": {
            "permit_type": app.get("permit_type", ""),
            "status": app.get("status", ""),
            "form_data": compact_form_data,
        },
        "attachments": compact_attachments,
        "official_attachment_count": len(official_attachments),
        "excluded_attachment_count": len(excluded_attachments),
        "check_report": {
            "score": check_report.get("score", "") if isinstance(check_report, dict) else "",
            "summary": check_report.get("summary", "") if isinstance(check_report, dict) else "",
            "issues": compact_issues,
        },
    }


def authority_memo(db: Any, case_id: str, permit_type: str, settings: Any = None) -> dict[str, Any]:
    app = ensure_application(db, case_id, permit_type)
    fallback = {
        "memo_md": "## Päätöstuen muistio\n\nMuistio kokoaa hakemuksen faktat ja AI-havainnot käsittelijän tarkistettavaksi. Tämä ei ole päätös.",
        "facts": [f"Lupatyyppi: {app['permit_type']}", f"Tila: {app.get('status', 'draft')}"],
        "ai_observations": ["AI-havainnot perustuvat lomakedataan, liitteiden metadataan ja demokriteeristöön."],
        "caseworker_checks": ["Tarkista toimivalta, liitteiden riittävyys ja mahdolliset täydennyspyynnöt."],
        "evidence_refs": [{"source_id": "submission_package", "title": "Hakemuspaketin manifesti", "locator": "artifact", "confidence": 0.75}],
    }
    if settings is not None:
        memo_model = run_json_agent(
            db,
            settings,
            case_id,
            "authority_memo_draft_agent",
            {"context": json.dumps(_compact_authority_context(db, case_id, app), ensure_ascii=False)},
            AuthorityMemoDraft,
            vector_ids=azure_vector_store_ids(db, settings, case_id),
            fallback=fallback,
            max_output_tokens=2500,
            reasoning_effort="low",
        )
        memo = memo_model
    else:
        memo = fallback
    save_artifact(db, case_id, "authority_memo", app["permit_type"], memo)
    event(db, case_id, "authority.memo.generated", "Authority memo generated", {"application_id": app["permit_type"]})
    return memo


def _api_case_id(db: Any, case_id: str = "") -> str:
    if case_id:
        ensure_seed_state(db, case_id)
        return case_id
    return ensure_demo_case(db)


def append_suggestion_service_log(
    db: Any,
    case_id: str,
    mode: str,
    tenant_id: str,
    permit_type: str,
    criteria_version_value: str,
    *,
    endpoint: str = "",
    request_id: str = "",
    status: str = "ok",
    fallback_used: bool = False,
) -> dict[str, Any]:
    payload = artifact_json(db, case_id, "suggestion_service_request_log", "main", {"items": []})
    items = list(payload.get("items") or [])
    items.append(
        {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode,
            "tenant_id": tenant_id,
            "permit_type": permit_type,
            "criteria_version": criteria_version_value,
            "endpoint": endpoint,
            "request_id": request_id,
            "status": status,
            "fallback_used": fallback_used,
        }
    )
    payload["items"] = items[-25:]
    save_artifact(db, case_id, "suggestion_service_request_log", "main", payload)
    return payload


def _field_defs(form_schema: dict[str, Any], field_ids: list[str]) -> list[dict[str, Any]]:
    fields = form_schema.get("fields", [])
    out = []
    for field_id in field_ids:
        out.append(next((f for f in fields if f.get("id") == field_id), {"id": field_id}))
    return out


def _request_from_payload(model: type[Any], payload: dict[str, Any]) -> Any:
    """Build a request dataclass from untrusted JSON: drop unknown keys, 422 on missing ones."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="request body must be a JSON object")
    known = {f.name for f in dataclass_fields(model)}
    try:
        return model(**{key: value for key, value in payload.items() if key in known})
    except TypeError as exc:
        raise HTTPException(status_code=422, detail=f"invalid request payload: {exc}") from exc


def suggestion_service_field(payload: dict[str, Any], db: Any = None, settings: Any = None, case_id: str = "") -> dict[str, Any]:
    req = _request_from_payload(FieldSuggestionRequest, payload)
    fallback_suggestion = deterministic_suggestion(req.permit_type, req.field_id, req.form_data, req.attachments)
    response = FieldSuggestionResponse(request_id=req.request_id, criteria_version=criteria_version(req.permit_type), suggestions=[fallback_suggestion], warnings=[])
    if db is not None and settings is not None:
        api_case = _api_case_id(db, case_id)
        criteria = effective_criteria(db, api_case, req.permit_type)
        agent_response = run_json_agent(
            db,
            settings,
            api_case,
            "field_suggestion_agent",
            {
                "request_id": req.request_id,
                "criteria_version": criteria_version(req.permit_type, db, api_case),
                "field_id": req.field_id,
                "field_definition": json.dumps(_field_defs(req.form_schema, [req.field_id])[0], ensure_ascii=False),
                "form_data": json.dumps(req.form_data, ensure_ascii=False),
                "common_data": json.dumps(req.common_data, ensure_ascii=False),
                "criteria": json.dumps(criteria, ensure_ascii=False),
                "retrieved_context": json.dumps({"mode": "api_payload", "attachments": req.attachments}, ensure_ascii=False),
            },
            FieldSuggestionResponse,
            vector_ids=azure_vector_store_ids(db, settings, api_case),
            fallback=response,
            max_output_tokens=3000,
        )
        if isinstance(agent_response, FieldSuggestionResponse):
            suggestions = agent_response.suggestions
            warnings = agent_response.warnings
        else:
            suggestions = [fallback_suggestion]
            warnings = []
        response = FieldSuggestionResponse(
            request_id=req.request_id,
            criteria_version=criteria_version(req.permit_type, db, api_case),
            suggestions=suggestions,
            warnings=warnings,
        )
    return asdict(response)


def suggestion_service_form(payload: dict[str, Any], db: Any = None, settings: Any = None, case_id: str = "") -> dict[str, Any]:
    base = _request_from_payload(FieldSuggestionRequest, payload)
    target_fields = payload.get("target_field_ids") or [base.field_id]
    fallback_suggestions = [deterministic_suggestion(base.permit_type, field_id, base.form_data, base.attachments) for field_id in target_fields]
    response = FieldSuggestionResponse(request_id=base.request_id, criteria_version=criteria_version(base.permit_type), suggestions=fallback_suggestions, warnings=[])
    if db is not None and settings is not None:
        api_case = _api_case_id(db, case_id)
        criteria = effective_criteria(db, api_case, base.permit_type)
        agent_response = run_json_agent(
            db,
            settings,
            api_case,
            "form_suggestion_agent",
            {
                "request_id": base.request_id,
                "criteria_version": criteria_version(base.permit_type, db, api_case),
                "target_fields": json.dumps(_field_defs(base.form_schema, target_fields), ensure_ascii=False),
                "form_data": json.dumps(base.form_data, ensure_ascii=False),
                "criteria": json.dumps(criteria, ensure_ascii=False),
                "retrieved_context": json.dumps({"mode": "api_payload", "attachments": base.attachments}, ensure_ascii=False),
            },
            FieldSuggestionResponse,
            vector_ids=azure_vector_store_ids(db, settings, api_case),
            fallback=response,
            max_output_tokens=5000,
        )
        if isinstance(agent_response, FieldSuggestionResponse):
            suggestions = agent_response.suggestions
            warnings = agent_response.warnings
        else:
            suggestions = fallback_suggestions
            warnings = []
        response = FieldSuggestionResponse(
            request_id=base.request_id,
            criteria_version=criteria_version(base.permit_type, db, api_case),
            suggestions=suggestions,
            warnings=warnings,
        )
    return asdict(response)


def suggestion_service_check(payload: dict[str, Any], db: Any = None, settings: Any = None, case_id: str = "") -> dict[str, Any]:
    req = _request_from_payload(ApplicationCheckRequest, payload)
    issues = deterministic_issues(req.permit_type, req.form_data, req.attachments)
    api_case: str = ""  # ponytail: avoids "possibly unbound" false-positive from type checker
    if db is not None and settings is not None:
        api_case = _api_case_id(db, case_id)
        criteria = effective_criteria(db, api_case, req.permit_type)
        base_values = {
            "form_schema": json.dumps(req.form_schema, ensure_ascii=False),
            "form_data": json.dumps(req.form_data, ensure_ascii=False),
            "attachments": json.dumps(req.attachments, ensure_ascii=False),
            "deterministic_issues": json.dumps(_plain(issues), ensure_ascii=False),
            "criteria_and_context": json.dumps({"criteria": criteria, "retrieval": {"mode": "api_payload", "attachments": req.attachments}}, ensure_ascii=False),
            "common_data": json.dumps(req.common_data, ensure_ascii=False),
            "attachment_context": json.dumps({"mode": "api_payload", "attachments": req.attachments}, ensure_ascii=False),
            "criteria": json.dumps(criteria, ensure_ascii=False),
            "retrieved_context": json.dumps({"mode": "api_payload", "attachments": req.attachments}, ensure_ascii=False),
        }
        vector_ids = azure_vector_store_ids(db, settings, api_case)
        agent_issues: list[Issue] = []
        for agent in ("completeness_validator", "consistency_validator"):
            response = run_json_agent(
                db,
                settings,
                api_case,
                agent,
                base_values,
                IssueListResponse,
                vector_ids=vector_ids,
                fallback={"issues": []},
                max_output_tokens=5000,
            )
            if isinstance(response, IssueListResponse):
                agent_issues.extend(response.issues)
            elif isinstance(response, dict):
                agent_issues.extend(response.get("issues", []))
        criteria_response = run_json_agent(
            db,
            settings,
            api_case,
            "criteria_assessor",
            base_values,
            CriteriaAssessmentResponse,
            vector_ids=vector_ids,
            fallback={"criteria_results": []},
            max_output_tokens=6000,
        )
        if isinstance(criteria_response, CriteriaAssessmentResponse) and criteria_response.criteria_results:
            save_artifact(db, api_case, "criteria_assessment", f"api_{req.permit_type}", {"criteria_results": _plain(criteria_response.criteria_results)})
        elif isinstance(criteria_response, dict):
            cr = criteria_response.get("criteria_results", [])
            if cr:
                save_artifact(db, api_case, "criteria_assessment", f"api_{req.permit_type}", {"criteria_results": _plain(cr)})
        issues = merge_issues(issues, agent_issues)
    blockers = sum(1 for i in issues if i.severity == "blocking")
    majors = sum(1 for i in issues if i.severity == "major")
    response = ApplicationCheckResponse(
        request_id=req.request_id,
        criteria_version=criteria_version(req.permit_type, db, api_case) if db is not None and settings is not None else criteria_version(req.permit_type),
        score=max(0, 100 - blockers * 25 - majors * 10),
        summary="API-tarkistus valmis.",
        issues=issues,
    )
    return asdict(response)


def structure_extract(payload: dict[str, Any], db: Any = None, settings: Any = None, case_id: str = "") -> dict[str, Any]:
    req = _request_from_payload(StructureExtractionRequest, payload)
    extracted = []
    text = req.source_text.lower()
    for schema_field in req.form_schema.get("fields", []):
        fid = schema_field.get("id")
        if fid and fid.replace("_", " ") in text:
            extracted.append({"field_id": fid, "value": req.source_text[:240], "confidence": 0.55, "evidence_refs": req.source_refs})
    fallback = StructureExtractionResponse(request_id=req.request_id, extracted_fields=extracted)
    if db is not None and settings is not None:
        api_case = _api_case_id(db, case_id)
        response = run_json_agent(
            db,
            settings,
            api_case,
            "structure_extractor",
            {
                "request_id": req.request_id,
                "form_schema": json.dumps(req.form_schema, ensure_ascii=False),
                "source_text": req.source_text[:12000],
            },
            StructureExtractionResponse,
            vector_ids=azure_vector_store_ids(db, settings, api_case),
            fallback=fallback,
            max_output_tokens=5000,
            reasoning_effort="low",
        )
        if isinstance(response, StructureExtractionResponse):
            return asdict(StructureExtractionResponse(request_id=req.request_id, extracted_fields=response.extracted_fields))
    return asdict(fallback)
