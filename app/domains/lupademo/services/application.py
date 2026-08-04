"""Business logic: suggestions, checks, packaging, authority, chat, metrics, API.

Quick index (by section):
  Criteria test            ~40    run_criteria_test
  Package analysis         ~65    dedupe_package_findings, explicit_contradiction_findings,
                                  intake_analysis_questions, _run_check_agents,
                                  package_analysis_from_current_state
  Intake analysis jobs    ~420    _build_intake_analysis_result, start_intake_analysis_job,
                                  execute_intake_analysis_job
  Chat & voice            ~570    contextual_chat_answer, voice_feature_state, voice_mock_turn
  Field suggestions       ~780    deterministic_suggestion, create_field_suggestion,
                                  start_form_suggestions_job, accept_suggestion, reject_suggestion
  Application check       ~970    deterministic_issues, merge_issues, run_application_check,
                                  precheck_gate_status, wizard_step_status
  Submission packaging   ~1225    compose_submission_package
  Metrics & benchmarks   ~1270    metrics_snapshot, benchmark_lab_snapshot, production_hardening_snapshot
  Source materials       ~1435    resolve_source_material_dir, analyze_source_material_file,
                                  source_material_summary, authority_example_benchmark
  Agent audit            ~1775    agent_chain_audit, run_agent_chain_audit
  Readiness              ~1970    readiness_snapshot, ingest_source_materials
  Submission             ~2335    submit_application
  Authority views        ~2385    caseworker_observation_panel, authority_summary,
                                  additional_info_draft, authority_memo
  Suggestion service API ~2740    suggestion_service_field, suggestion_service_form,
                                  suggestion_service_check, structure_extract

Depends on core + jobs + intake + criteria.
"""

from __future__ import annotations

import copy
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
from dataclasses import asdict, dataclass, is_dataclass
from dataclasses import fields as dataclass_fields
from docx import Document as DocxDocument
from fastapi import HTTPException
from jinja2 import Template

from app.infra.llm_factory import create_llm_client
from app.infra.llm_utils import call_llm_json, vector_store_ids

from .core import AdditionalInfoRequestDraft, ApplicantPrecheckReport, ApplicationCheckRequest, ApplicationCheckResponse, AuthorityMemoDraft, CaseworkerSummaryResponse, CriteriaAssessmentResponse, DEFAULT_COMMON_DATA, EvidenceRef, FieldSuggestionRequest, FieldSuggestionResponse, Issue, IssueListResponse, PERMIT_SCHEMAS, PackageAnalysisResult, PackageFinding, ROOT, SPEC_ROOT, StructureExtractionRequest, StructureExtractionResponse, Suggestion, _dict, _extraction_queue_item, _list, _plain, _real_llm_enabled_for_case, application_id_for, artifact_json, ensure_application, ensure_demo_case, ensure_seed_state, event, load_form_schema, load_json, save_artifact, source_ref, update_application_data
from .criteria import *  # noqa: F401,F403
from .jobs import complete_ai_job, list_ai_jobs, mark_agent_complete, start_ai_job
from .intake import ALLOWED_UPLOAD_EXTENSIONS, EXPECTED_SOURCE_ARCHIVES, MAX_UPLOAD_FILE_BYTES, MAX_UPLOAD_ZIP_BYTES, _extraction_has_substantive_value, _write_agent_audit_attachment, add_attachment, attachment_extraction_summary, attachment_requirement_covered, attachments_updated_at, azure_vector_store_ids, build_retrieval_context, classify_source_material, current_material_package, execute_material_ingest_job, external_real_case_upload_allowed, extract_text_from_path, list_attachments, retrieval_context, run_json_agent, safe_extract_zip, structure_environmental_application_from_attachments


PRECHECK_MAX_AGE_SECONDS = 24 * 60 * 60


def run_criteria_test(db: Any, settings: Any, case_id: str, permit_type: str, municipality: Any = None) -> dict[str, Any]:
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
            "municipality": str(municipality or ""),
            "project_description": "Testipyyntö kriteeristön toimivuuden arviointiin.",
            "activity_description": "Teollinen testihanke, jossa tarkistetaan kriteeriversion vaikutus.",
            "event_description": "Julkinen testitapahtuma kaupunkitilassa.",
        },
        "attachments": [],
    }
    result = suggestion_service_field(payload, db=db, settings=settings, case_id=case_id)
    save_artifact(db, case_id, "criteria_test_result", permit_type, {"permit_type": permit_type, "result": result, "tested_at": time.time()})
    event(db, case_id, "criteria.tested", "Criteria test request completed", {"permit_type": permit_type, "municipality": str(municipality or ""), "criteria_version": result.get("criteria_version")})
    return result


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
    left_sources = {ref.get("source_id", "") if isinstance(ref, dict) else ref.source_id for ref in left.evidence_refs if (ref.get("source_id") if isinstance(ref, dict) else ref.source_id)}
    right_sources = {ref.get("source_id", "") if isinstance(ref, dict) else ref.source_id for ref in right.evidence_refs if (ref.get("source_id") if isinstance(ref, dict) else ref.source_id)}
    return overlap >= 0.6 or (overlap >= 0.35 and bool(left_sources & right_sources))


_ISSUE_TYPE_TO_FINDING_CATEGORY = {"missing": "missing_required_info", "incomplete": "weak_description", "inconsistent": "contradiction"}


def _issue_question(issue: Issue) -> str | None:
    if issue.type == "missing":
        return f"Täydennätkö tiedon: {issue.title}?"
    if issue.type == "inconsistent":
        return f"Kumpi tieto pitää paikkansa: {issue.title.lower()}?"
    return None


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
                        category="contradiction",
                        title=title,
                        detail=quote[:420],
                        suggested_fix="Vahvista oikea arvo ja yhtenäistä hakemus sekä sitä koskevat liitteet.",
                        question_to_applicant=f"Kumpi tieto pitää paikkansa: {title.lower()}?",
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


def _run_check_agents(
    db: Any,
    settings: Any,
    case_id: str,
    base_values: dict[str, Any],
    *,
    vector_ids: list[str] | None = None,
    max_output_tokens: int = 5000,
) -> list[Issue]:
    """Run completeness and consistency validator agents and return merged issues."""
    if vector_ids is None:
        vector_ids = azure_vector_store_ids(db, settings, case_id)
    agent_issues: list[Issue] = []
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
            max_output_tokens=max_output_tokens,
        )
        if isinstance(response, IssueListResponse):
            agent_issues.extend(response.issues)
        elif isinstance(response, dict):
            agent_issues.extend(response.get("issues", []))
    return agent_issues


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
    common = artifact_json(db, case_id, "lv_common_data", "main", {"data": DEFAULT_COMMON_DATA.copy()})
    municipality = effective_form_data.get("municipality") or common.get("data", {}).get("municipality", "")
    issues = [
        issue
        for issue in deterministic_issues(permit_type, effective_form_data, attachments, municipality=municipality)
        if not (
            issue.id.startswith("missing_attachment_")
            and attachment_requirement_covered(str(issue.attachment_id or issue.id.removeprefix("missing_attachment_")), attachments)
        )
    ]
    question_app = dict(app)
    question_app["form_data"] = effective_form_data
    questions = intake_analysis_questions(schema, question_app, attachments)
    if _real_llm_enabled_for_case(db, case_id, settings):
        criteria = effective_criteria(db, case_id, permit_type, municipality)
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
        agent_issues = _run_check_agents(db, settings, case_id, base_values, max_output_tokens=6000)
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
                category=_ISSUE_TYPE_TO_FINDING_CATEGORY.get(issue.type, "missing_required_info"),
                title=issue.title,
                detail=issue.detail,
                field_id=issue.field_id,
                attachment_id=issue.attachment_id,
                suggested_fix=issue.suggested_fix,
                question_to_applicant=_issue_question(issue),
                criteria_refs=[ref.get("source_id", "") if isinstance(ref, dict) else ref.source_id for ref in issue.evidence_refs if (ref.get("locator") if isinstance(ref, dict) else ref.locator) == "criteria"],
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
                category="missing_required_info",
                title=f"Pakollinen tieto puuttuu: {field.get('label', field_id)}",
                detail="Hakemuspaketin analyysi ei löytänyt tälle kentälle riittävää tietoa.",
                field_id=field_id,
                suggested_fix="Hyväksy AI-poiminta, lisää tieto käsin tai täydennä aineistoa.",
                question_to_applicant=f"Täydennätkö tiedon: {field.get('label', field_id)}?",
                confidence=0.82,
                evidence_refs=[EvidenceRef(source_id="form_schema", title="Lomakevaatimus", locator=field_id, quote=str(field.get("label", field_id)), confidence=0.85)],
            )
        )
    if contradictory and not specific_contradictions and not fallback_contradictions:
        findings.append(
            PackageFinding(
                id="package_contradiction",
                severity="major",
                category="contradiction",
                title="Aineistossa on ristiriitaisia tietoja",
                detail="Eri dokumentit näyttävät kuvaavan olennaisen tiedon eri tavoin. Hakijan kannattaa varmistaa oikea arvo ennen lähettämistä.",
                suggested_fix="Tarkista ristiriitaiset luvut ja päivitä hakemusteksti sekä liitteet samaan versioon.",
                question_to_applicant="Kumpi tieto pitää paikkansa aineistossa?",
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
        _extraction_queue_item(item)
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


def _build_intake_analysis_result(db: Any, case_id: str, permit_type: str, job_id: str, settings: Any = None, *, full_check: bool = True) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    if permit_type == "environmental_industrial":
        structure = structure_environmental_application_from_attachments(db, case_id, settings=settings, track_job=False)
    else:
        structure = artifact_json(db, case_id, "structure_extraction_result", permit_type, {"field_extractions": [], "updated_field_count": 0})
    if job_id:
        mark_agent_complete(db, case_id, permit_type, job_id, "material_classifier", "Aineiston luokittelu valmis.")
        mark_agent_complete(db, case_id, permit_type, job_id, "structure_extractor", "Rakenteistus valmis.")
    app = ensure_application(db, case_id, permit_type)
    schema = load_form_schema(permit_type)
    attachments = list_attachments(db, case_id, permit_type)
    wizard = wizard_step_status(db, case_id, permit_type)
    questions = intake_analysis_questions(schema, app, attachments) if full_check else []
    if job_id:
        mark_agent_complete(db, case_id, permit_type, job_id, "question_generator", "Kysymysten muodostus valmis.")
    field_extractions = list(structure.get("field_extractions", []))
    result: dict[str, Any] = {
        "permit_type": permit_type,
        "job_id": job_id,
        "structured_field_count": len(field_extractions),
        "field_extractions": field_extractions,
        "missing_required_fields": wizard.get("missing_required_fields", []),
        "missing_attachments": wizard.get("missing_attachments", []),
        "questions": questions,
        "attachment_count": len(attachments),
        "official_attachment_count": sum(1 for item in attachments if item.get("delivery_scope") == "official_submission"),
        "ai_context_attachment_count": sum(1 for item in attachments if item.get("delivery_scope") == "ai_context_only"),
        "created_at": time.time(),
    }

    # Build extraction queue from field extractions — used by both light and full paths
    extraction_queue = [
        _extraction_queue_item(item)
        for item in field_extractions
        if item.get("field_id") and _extraction_has_substantive_value(item)
    ]
    save_artifact(db, case_id, "field_extraction_queue", permit_type, {"items": extraction_queue, "updated_at": time.time()})
    result["extraction_queue"] = extraction_queue

    if full_check:
        package_result = package_analysis_from_current_state(db, case_id, permit_type, job_id=job_id, structure=structure, settings=settings)
        if job_id:
            mark_agent_complete(db, case_id, permit_type, job_id, "gap_analyzer", "Puutteiden tunnistus valmis.")
        result["readiness"] = package_result["readiness"]
        result["readiness_score"] = package_result["readiness_score"]
        result["summary"] = package_result["summary"]
        result["findings"] = package_result["findings"]
        result["suggestions"] = package_result["suggestions"]
        result["document_inventory"] = package_result["document_inventory"]
        # extraction_queue already set from package_result; overwrite with consistent copy
        result["extraction_queue"] = package_result.get("extraction_queue", extraction_queue)
    else:
        # Light path: material exploration only, no completeness/consistency/criteria check.
        # Saves field_extraction_queue for step 3 suggestion review.
        # Also auto-generates field suggestions so step 3 is pre-populated.
        result["readiness"] = "pending_review"
        result["readiness_score"] = 0
        result["summary"] = f"Aineistosta poimittu {len(field_extractions)} kenttäehdotusta."
        result["findings"] = []
        result["suggestions"] = [
            {"field_id": item["field_id"], "suggested_value": item.get("suggested_value") or "", "source_document": item.get("source_document") or "", "confidence": item.get("confidence", 0.5), "status": "pending"}
            for item in extraction_queue
        ]
        result["document_inventory"] = [
            {"document_id": att.get("id"), "filename": att.get("filename"), "document_role": att.get("primary_type"), "delivery_scope": att.get("delivery_scope"), "extraction_status": att.get("extraction_status"), "text_chars": att.get("text_chars") or len(str(att.get("text_excerpt") or "")), "confidence": att.get("confidence", 0.5)}
            for att in attachments
        ]

    save_artifact(db, case_id, "intake_analysis", permit_type, result)
    event(db, case_id, "ai.intake_analysis.completed", "Intake analysis completed", {"permit_type": permit_type, "questions": len(questions), "full_check": full_check})
    return result


def _finalize_intake_analysis_job_in_store(db: Any, case_id: str, permit_type: str, job: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    try:
        result = _build_intake_analysis_result(db, case_id, permit_type, str(job.get("id") or ""))
        summary = f"Analyysi valmis: {len(result.get('questions', []))} kysymystä, {len(result.get('missing_required_fields', []))} kenttäpuutetta."
        failed = False
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        summary = f"Aineiston analyysi epäonnistui: {exc}\n{tb[-500:]}"
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
    full_check: bool = True,
) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    # Light path (wizard/2 "Analysoi") only classifies, structures and asks
    # questions — no gap analysis. Full path adds gap_analyzer. Keep the
    # agent list in sync with what _build_intake_analysis_result marks.
    agent_names = (
        ["material_classifier", "structure_extractor", "gap_analyzer", "question_generator"]
        if full_check
        else ["material_classifier", "structure_extractor", "question_generator"]
    )
    return start_ai_job(
        db,
        case_id,
        permit_type,
        "intake_analysis",
        "Aineiston analyysi ja kysymyslista",
        agent_names,
        eta_seconds=45 if settings is not None and str(getattr(settings, "llm_provider", "mock")).lower() != "mock" else 12,
        auto_finalize=not managed_background,
        managed_background=managed_background,
    )


def execute_intake_analysis_job(db: Any, case_id: str, permit_type: str, job_id: str, settings: Any = None, *, full_check: bool = True) -> None:
    permit_type = application_id_for(permit_type)
    try:
        result = _build_intake_analysis_result(db, case_id, permit_type, job_id, settings=settings, full_check=full_check)
        summary = f"Analyysi valmis: {len(result.get('findings', []))} havaintoa, valmius {result.get('readiness_score', 0)}/100." if full_check else f"Aineisto käsitelty: {result.get('structured_field_count', 0)} kenttäehdotusta."
        complete_ai_job(
            db,
            case_id,
            permit_type,
            job_id,
            summary=summary,
            result_artifact=f"intake_analysis:{permit_type}",
        )
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        complete_ai_job(
            db,
            case_id,
            permit_type,
            job_id,
            summary=f"Aineiston analyysi epäonnistui: {str(exc)[:200]}\n{tb[-400:]}",
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
        client = create_llm_client(settings, purpose="chat", db=db, case_id=case_id)
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
        "environmental_impacts": "Toiminnan ympäristövaikutukset kohdistuvat pääasiassa ilmapäästöihin, meluun, liikenteeseen ja jätevesiin. Vaikutukset arvioidaan YVA-selostuksen ja lupahakemuksen liitteiden perusteella.",
        "risk_preparedness": "Laitoksella on ennaltavarautumissuunnitelma ja toimintaohjeet häiriö- ja poikkeustilanteita varten. Keskeiset riskit liittyvät kemikaalien käsittelyyn, tulipaloon ja vuotoihin.",
    }
    suggested_value = values.get(field_id, "")
    if suggested_value and attachments:
        official = [a["filename"] for a in attachments if a.get("delivery_scope") == "official_submission"]
        if official:
            suggested_value += f" Ehdotus huomioi liitteet: {', '.join(official[:3])}."
    is_supported = field_id in values
    suggestion = Suggestion(
        field_id=field_id,
        current_value=current_value,
        suggested_value=suggested_value,
        diff_summary="Korvaa tyhjän kentän ehdotuksella." if not current_value else "Täydentää nykyistä arvoa AI-ehdotuksella.",
        rationale="Ehdotus perustuu lomakedataan, demokriteeristöön ja liitteiden luokitteluun." if is_supported else "Luotettavaa ehdotusta ei voitu muodostaa.",
        confidence=0.78 if is_supported else 0.0,
        evidence_refs=[EvidenceRef(source_id=criteria.get("criteria_set_id", "demo"), title=source_title, locator="demo criteria", quote="Tekoälyavusteinen ehdotus, tarkista ennen käyttöä.", confidence=0.72)] if is_supported else [],
        criteria_version=str(criteria.get("version") or "demo"),
        agent="field_suggestion_agent",
        fallback_used=True,
        warnings=[] if is_supported else ["AI-ehdotusta ei voitu muodostaa luotettavasti. Täydennä kenttä lähdeaineiston perusteella."],
    )
    return suggestion


def create_field_suggestion(db: Any, case_id: str, permit_type: str, field_id: str, settings: Any = None, *, job_id: str = "", complete_job: bool = True) -> Suggestion:
    app = ensure_application(db, case_id, permit_type)
    if not job_id:
        job = start_ai_job(
            db,
            case_id,
            app["permit_type"],
            "field_suggestion",
            f"Kenttäehdotus: {field_id}",
            ["retrieval_context", "field_suggestion_agent", "validate_suggestion", "persist_pending_suggestion"],
            eta_seconds=15,
        )
        job_id = job["id"]
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
            reasoning_effort="low",
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
    pending["suggestions"] = [s for s in pending.get("suggestions", []) if s.get("field_id") != field_id]
    pending.setdefault("suggestions", []).append(suggestion)
    save_artifact(db, case_id, "ai_suggestions", app["permit_type"], pending)
    if complete_job:
        complete_ai_job(db, case_id, app["permit_type"], job_id, summary=f"Ehdotus luotu kentälle {field_id}.", result_artifact=f"ai_suggestions:{app['permit_type']}")
    event(db, case_id, "ai.suggestion.created", "AI field suggestion created", {"application_id": app["permit_type"], "field_id": field_id, "suggestion_id": suggestion.id})
    return suggestion


def start_form_suggestions_job(db: Any, case_id: str, permit_type: str, settings: Any = None) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    fields = [field["id"] for field in load_form_schema(permit_type).get("fields", []) if field.get("ai_suggest")]
    return start_ai_job(
        db,
        case_id,
        permit_type,
        "form_suggestions",
        "Hakemusluonnoksen AI-ehdotukset",
        fields,
        eta_seconds=max(10, len(fields) * (8 if settings is not None and str(getattr(settings, "llm_provider", "mock")).lower() != "mock" else 1)),
        managed_background=True,
    )


def execute_form_suggestions_job(db: Any, case_id: str, permit_type: str, job_id: str, settings: Any = None) -> None:
    permit_type = application_id_for(permit_type)
    fields = [field["id"] for field in load_form_schema(permit_type).get("fields", []) if field.get("ai_suggest")]
    try:
        for field_id in fields:
            create_field_suggestion(db, case_id, permit_type, field_id, settings=settings, job_id=job_id, complete_job=False)
            mark_agent_complete(db, case_id, permit_type, job_id, field_id, f"Ehdotus luotu kentälle {field_id}.")
        complete_ai_job(
            db,
            case_id,
            permit_type,
            job_id,
            summary=f"Hakemusluonnos valmis: {len(fields)} kenttäehdotusta muodostettu.",
            result_artifact=f"ai_suggestions:{permit_type}",
        )
    except Exception as exc:
        complete_ai_job(
            db,
            case_id,
            permit_type,
            job_id,
            summary=f"Hakemusluonnoksen ehdotukset epäonnistuivat: {str(exc)[:200]}",
            result_artifact=f"ai_suggestions:{permit_type}",
            failed=True,
        )


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


def deterministic_issues(permit_type: str, form_data: dict[str, Any], attachments: list[dict[str, Any]], municipality: Any = None) -> list[Issue]:
    schema = load_form_schema(permit_type)
    criteria = load_criteria(permit_type, municipality)
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


def _issue_id(issue: Issue | dict[str, Any]) -> str:
    return issue.id if isinstance(issue, Issue) else str(issue.get("id", ""))


def _issue_severity(issue: Issue | dict[str, Any]) -> str:
    return issue.severity if isinstance(issue, Issue) else str(issue.get("severity", "major"))


def _as_issue(issue: Issue | dict[str, Any]) -> Issue:
    """Normalize an Issue dataclass or a fallback dict into an Issue."""
    if isinstance(issue, Issue):
        return issue
    return Issue(
        id=str(issue.get("id", "")),
        type=str(issue.get("type", "unknown")),
        severity=str(issue.get("severity", "major")),
        title=str(issue.get("title", "")),
        detail=str(issue.get("detail", "")),
        confidence=float(issue.get("confidence", 0.5)),
        field_id=issue.get("field_id"),
        attachment_id=issue.get("attachment_id"),
        suggested_fix=issue.get("suggested_fix"),
        evidence_refs=issue.get("evidence_refs", []),
        resolved=bool(issue.get("resolved", False)),
    )


def merge_issues(*groups: list[Issue]) -> list[Issue]:
    """Merge issue groups, keeping the highest-severity entry per id.

    Groups may contain ``Issue`` dataclasses (from agents) or plain dicts
    (from fallback payloads); both are normalized to ``Issue``.
    """
    seen: dict[str, Issue] = {}
    for group in groups:
        for issue in group:
            iid = _issue_id(issue)
            isev = _issue_severity(issue)
            if iid not in seen or SEVERITY_ORDER.get(isev, 1) > SEVERITY_ORDER.get(_issue_severity(seen[iid]), 1):
                seen[iid] = _as_issue(issue)
    return list(seen.values())


def run_application_check(db: Any, case_id: str, permit_type: str, settings: Any = None, *, job_id: str = "") -> ApplicationCheckResponse:
    app = ensure_application(db, case_id, permit_type)
    if not job_id:
        job = start_ai_job(
            db,
            case_id,
            app["permit_type"],
            "application_check",
            "AI- ja sääntötarkistus",
            ["deterministic_checks", "completeness_validator", "consistency_validator", "criteria_assessor", "applicant_precheck_report"],
            eta_seconds=35,
        )
        job_id = job["id"]
    attachments = list_attachments(db, case_id, app["permit_type"])
    schema = load_form_schema(app["permit_type"])
    common = artifact_json(db, case_id, "lv_common_data", "main", {"data": DEFAULT_COMMON_DATA.copy()})
    municipality = app.get("form_data", {}).get("municipality") or common.get("data", {}).get("municipality", "")
    deterministic = deterministic_issues(app["permit_type"], app.get("form_data", {}), attachments, municipality=municipality)
    if job_id:
        mark_agent_complete(db, case_id, app["permit_type"], job_id, "deterministic_checks", "Sääntöpohjainen tarkistus valmis.")
    agent_issues: list[Issue] = []
    criteria_results: list[dict[str, Any]] = []
    if settings is not None:
        criteria = effective_criteria(db, case_id, app["permit_type"], municipality)
        context = retrieval_context(db, case_id, app["permit_type"])
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
        agent_issues = _run_check_agents(db, settings, case_id, base_values, vector_ids=vector_ids, max_output_tokens=5000)
        if job_id:
            mark_agent_complete(db, case_id, app["permit_type"], job_id, "completeness_validator", "Täydellisyystarkistus valmis.")
            mark_agent_complete(db, case_id, app["permit_type"], job_id, "consistency_validator", "Ristiriitatarkistus valmis.")
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
        if job_id:
            mark_agent_complete(db, case_id, app["permit_type"], job_id, "criteria_assessor", "Kriteeriarviointi valmis.")
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
    questions = intake_analysis_questions(schema, app, attachments)
    report_payload.update(
        {
            "checked_at": checked_at,
            "form_updated_at": float(app.get("updated_at") or 0),
            "attachments_updated_at": attachments_updated_at(db, case_id, app["permit_type"]),
            "criteria_version": current_criteria_version,
            "questions": questions,
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
        if job_id:
            mark_agent_complete(db, case_id, app["permit_type"], job_id, "applicant_precheck_report", "Esitarkistusraportti valmis.")
    event(db, case_id, "ai.check.completed", "Application check completed", {"application_id": app["permit_type"], "issues": len(issues), "blocking": blockers, "major": major, "score": response.score})
    save_artifact(db, case_id, "precheck_gate", app["permit_type"], precheck_gate_status(db, case_id, app["permit_type"]))
    complete_ai_job(db, case_id, app["permit_type"], job_id, summary=f"Tarkistus valmis: {len(issues)} havaintoa, pisteet {response.score}.", result_artifact=f"check_report:{app['permit_type']}")
    return response


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
    extraction_items = artifact_json(db, case_id, "field_extraction_queue", permit_type, {"items": []}).get("items", [])
    check_report = artifact_json(db, case_id, "check_report", permit_type, {})
    gate = precheck_gate_status(db, case_id, permit_type)
    form_data = app.get("form_data") or {}
    required_fields = [f for f in schema.get("fields", []) if f.get("required")]
    missing_required = [f.get("id") for f in required_fields if not str(form_data.get(f.get("id"), "")).strip()]
    official_types = {a.get("primary_type") for a in attachments if a.get("delivery_scope") == "official_submission"}
    missing_attachments = [item for item in schema.get("required_attachments", []) if item not in official_types]
    pending_suggestions = [s for s in suggestions if s.get("status") == "pending"]
    pending_extractions = [item for item in extraction_items if item.get("status") == "pending"]
    pending_suggestion_count = len(pending_suggestions) + len(pending_extractions)
    issues = check_report.get("issues") or []
    blocking_count = sum(1 for issue in issues if issue.get("severity") == "blocking" and not issue.get("resolved"))
    steps = [
        {"step": 1, "label": "Perustiedot", "status": "done" if not missing_required[:3] else "attention"},
        {"step": 2, "label": "Aineistot", "status": "done" if not missing_attachments else "attention"},
        {"step": 3, "label": "AI-ehdotukset", "status": "attention" if pending_suggestion_count or missing_required else "done"},
        {"step": 4, "label": "Tarkistus", "status": "done" if gate["is_fresh"] and blocking_count == 0 else "attention"},
        {"step": 5, "label": "Paketointi", "status": "done" if gate["can_submit"] else "blocked"},
        {"step": 6, "label": "Vastaanotto", "status": "done" if app.get("status") == "submitted" else "pending"},
    ]
    return {
        "steps": steps,
        "missing_required_fields": missing_required,
        "missing_attachments": missing_attachments,
        "pending_suggestion_count": pending_suggestion_count,
        "pending_extraction_count": len(pending_extractions),
        "pending_field_suggestion_count": len(pending_suggestions),
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
            return _dict(parsed)
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
    e2e_text = ""
    if e2e_path.exists():
        try:
            e2e_text = e2e_path.read_text(encoding="utf-8")
        except Exception:
            pass
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
                _ingest_backend = create_llm_client(settings, purpose="core", db=db, case_id=case_id)
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
        decision = _dict(decision_items.get(observation["id"]))
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
        summary = asdict(summary_model) if is_dataclass(summary_model) and not isinstance(summary_model, type) else _dict(summary_model)
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
    issues = _list(_dict(check_report).get("issues"))
    compact_issues = []
    for issue in issues[:8]:
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
        if isinstance(agent_response, FieldSuggestionResponse) and agent_response.suggestions:
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
        if isinstance(agent_response, FieldSuggestionResponse) and agent_response.suggestions:
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
    municipality = req.form_data.get("municipality") or (req.common_data or {}).get("municipality", "")
    issues = deterministic_issues(req.permit_type, req.form_data, req.attachments, municipality=municipality)
    api_case: str = ""
    if db is not None and settings is not None:
        api_case = _api_case_id(db, case_id)
        criteria = effective_criteria(db, api_case, req.permit_type, municipality)
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
        agent_issues = _run_check_agents(db, settings, api_case, base_values, vector_ids=vector_ids, max_output_tokens=5000)
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
