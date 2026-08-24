"""Attachments, material packages, ingestion, extraction, classification,
retrieval/vector-store context. Depends on core + jobs.
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
from typing import Any, Optional

import openpyxl
import pdfplumber
from dataclasses import asdict
from dataclasses import fields as dataclass_fields
from docx import Document as DocxDocument

from app.infra.llm_factory import LLMUnavailableError, create_llm_client
from app.infra.llm_utils import call_llm_json, vector_store_ids

from .core import ApplicantPrecheckReport, AttachmentClassificationResponse, AuthorityMemoDraft, CaseworkerSummaryResponse, CriteriaAssessmentResponse, DocumentAsset, EvidenceRef, ExtractedField, FieldSuggestionResponse, Issue, IssueListResponse, MaterialPackage, PERMIT_SCHEMAS, StructureExtractionResponse, Suggestion, _extraction_queue_item, _is_presenter_case, _list, _plain, _real_llm_enabled_for_case, application_id_for, artifact_json, ensure_application, event, load_form_schema, render_agent_prompt, save_artifact, source_ref, update_application_data
from .jobs import complete_ai_job, mark_agent_complete, start_ai_job


ENVIRONMENTAL_MATERIAL_STORIES_PATH = Path(__file__).resolve().parent.parent / "specs" / "scenarios" / "environmental_material_stories.json"

with open(ENVIRONMENTAL_MATERIAL_STORIES_PATH, encoding="utf-8") as _f:
    ENVIRONMENTAL_MATERIAL_STORIES: list[dict[str, Any]] = json.load(_f)


MAX_UPLOAD_FILE_BYTES = 50 * 1024 * 1024


MAX_UPLOAD_ZIP_BYTES = 200 * 1024 * 1024


MAX_UPLOAD_ZIP_TOTAL_BYTES = 300 * 1024 * 1024


MAX_UPLOAD_ZIP_FILE_COUNT = 200


ALLOWED_UPLOAD_EXTENSIONS = {".txt", ".md", ".csv", ".tsv", ".pdf", ".docx", ".xlsx"}


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
        try:
            file_size = int(member.file_size or 0)
        except (TypeError, ValueError):
            file_size = 0
        if file_size > MAX_UPLOAD_FILE_BYTES:
            raise ValueError("zip_member_too_large")
        total_size += file_size
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
            try:
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
            finally:
                wb.close()
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


def retrieval_context(db: Any, case_id: str, permit_type: str, *, max_chars: int = 64000) -> dict[str, Any]:
    attachments = list_attachments(db, case_id, permit_type)
    excerpts = []
    used = 0
    per_doc = max(800, max_chars // max(1, len(attachments)))  # 800-char floor, even spread otherwise
    for att in attachments:
        text = str(att.get("text_excerpt") or "")
        if not text:
            continue
        budget = min(per_doc, max(0, max_chars - used))
        if budget < 200:
            break
        chunk = text[:budget]
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
    # F21: an empty allowed_delivery means the scopes authorize nothing — return
    # zero excerpts, never the unfiltered set.
    excerpts = [
        item
        for item in context_payload.get("excerpts", [])
        if allowed_delivery and str(item.get("delivery_scope") or "") in allowed_delivery
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
    return deduped[:2]


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
    _backend = create_llm_client(settings, purpose="core", db=db, case_id=case_id)
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
    _backend = create_llm_client(settings, purpose="core", db=db, case_id=case_id)
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
    timeout_s: float | None = None,
    task_id: Optional[str] = None,
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
    client = create_llm_client(settings, purpose="core", db=db, case_id=case_id, timeout_s=timeout_s)
    # Log the actual provider used, not just the config setting.
    provider = "mock" if _is_presenter_case(db, case_id) else str(getattr(settings, "llm_provider", "mock") or "mock").lower()

    def _invoke(vids: Optional[list[str]], mode: str, **extra: Any) -> Any:
        payload = call_llm_json(
            client,
            prompt,
            db=db,
            case_id=case_id,
            task_id=task_id,
            agent=f"lupademo.{agent}",
            vector_store_ids_list=vids,
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
                "provider": provider,
                "execution_mode": mode,
                "used_vector_store_ids": vids or [],
                "vector_store_scope_manifest": retrieval_manifest,
                "fallback_used": False,
                "error": None,
                **extra,
            },
        )
        return model

    try:
        return _invoke(vector_ids, "retrieval" if vector_ids else "direct")
    except Exception as exc:
        failure = exc
        if vector_ids and any(
            marker in str(exc).lower()
            for marker in ("vector store", "readtimeout", "read operation timed out", "timed out")
        ):
            try:
                return _invoke([], "direct_after_retrieval_error", vector_store_retry_reason=str(exc)[:500])
            except Exception as retry_exc:
                failure = RuntimeError(
                    f"Initial retrieval request failed: {exc}; "
                    f"retry without retrieval also failed: {retry_exc}"
                )
        if "HTTP 429" in str(failure):
            time.sleep(2)
            try:
                return _invoke(vector_ids, "retry_after_rate_limit")
            except Exception as retry_exc:
                failure = RuntimeError(f"Rate-limited request failed on retry: {retry_exc}")
        allow_fallback = (
            provider == "mock"
            or _is_presenter_case(db, case_id)
            or str(os.getenv("KEHA_LV_FORCE_MOCK_LLM", "")).strip().lower() in {"1", "true", "yes", "on"}
        )
        if fallback is None or not allow_fallback:
            raise LLMUnavailableError("Azure AI ei ole käytettävissä. Mock-AI on sallittu vain esittäjätilassa.") from failure
        model = fallback if isinstance(fallback, response_model) else response_model(**fallback)
        save_artifact(db, case_id, "agent_output", agent, {"agent": agent, "output": model, "provider": provider, "execution_mode": "deterministic_fallback", "used_vector_store_ids": vector_ids or [], "vector_store_scope_manifest": retrieval_manifest, "fallback_used": True, "fallback_reason": str(failure)[:500], "error": str(failure)[:500]})
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
                ExtractedField(
                    field_id=field_id,
                    value=str(item.get("value") or item.get("text") or ""),
                    confidence=_coerce_confidence(item.get("confidence"), 0.55),
                    evidence_refs=_normalize_evidence_refs(item.get("evidence_refs", []), limit=6),
                )
            )
        return {"request_id": str(payload.get("request_id") or ""), "extracted_fields": normalized_fields}
    if response_model in {AttachmentClassificationResponse, CaseworkerSummaryResponse, AuthorityMemoDraft}:
        normalized = dict(payload) if isinstance(payload, dict) else {}
        normalized["evidence_refs"] = _normalize_evidence_refs(normalized.get("evidence_refs", []), limit=8)
        return normalized
    if response_model is FieldSuggestionResponse:
        normalized = dict(payload) if isinstance(payload, dict) else {}
        suggestions = normalized.get("suggestions", [])
        clean_suggestions: list[Any] = []
        if isinstance(suggestions, list):
            for suggestion in suggestions:
                if not isinstance(suggestion, dict):
                    continue
                clean_suggestion = dict(suggestion)
                clean_suggestion["evidence_refs"] = _normalize_evidence_refs(clean_suggestion.get("evidence_refs", []), limit=6)
                try:
                    clean_suggestions.append(Suggestion(**clean_suggestion))
                except Exception:
                    continue  # skip malformed items instead of failing entire response
        return {
            "request_id": str(normalized.get("request_id") or ""),
            "criteria_version": str(normalized.get("criteria_version") or ""),
            "suggestions": clean_suggestions,
            "warnings": [str(w) for w in (normalized.get("warnings") or []) if isinstance(w, str)],
        }
    if response_model is CriteriaAssessmentResponse:
        normalized = dict(payload) if isinstance(payload, dict) else {}
        results = normalized.get("criteria_results", [])
        clean_results: list[dict[str, Any]] = []
        if isinstance(results, list):
            for result in results:
                if not isinstance(result, dict):
                    continue
                clean_result = dict(result)
                clean_result["evidence_refs"] = _normalize_evidence_refs(clean_result.get("evidence_refs", []), limit=6)
                clean_results.append(clean_result)
        return {"criteria_results": clean_results}
    if response_model is ApplicantPrecheckReport:
        # score/readiness/counts are code-owned (03 D4); agent returns summary+sections only.
        # Azure responses omit them, so default instead of failing construction.
        normalized = dict(payload) if isinstance(payload, dict) else {}
        allowed = {f.name for f in dataclass_fields(ApplicantPrecheckReport)}
        normalized = {k: v for k, v in normalized.items() if k in allowed}
        normalized["readiness"] = str(normalized.get("readiness") or "needs_completion")
        try:
            normalized["score"] = int(normalized.get("score") or 0)
        except (TypeError, ValueError):
            normalized["score"] = 0
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
            Issue(
                id=str(issue.get("id") or f"agent_issue_{index + 1}"),
                type=issue_type,
                severity=severity,
                title=str(issue.get("title") or "Agentin havainto"),
                detail=str(issue.get("detail") or issue.get("description") or issue.get("rationale") or "Agentti tunnisti tarkistettavan kohdan."),
                confidence=_coerce_confidence(issue.get("confidence"), 0.6),
                field_id=issue.get("field_id") or None,
                attachment_id=issue.get("attachment_id") or None,
                suggested_fix=issue.get("suggested_fix") or issue.get("recommendation") or None,
                evidence_refs=_normalize_evidence_refs(issue.get("evidence_refs", []), limit=4),
                resolved=bool(issue.get("resolved", False)),
            )
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
        # F22: unknown scope defaults to ai_context_only, not the most sensitive scope.
        "delivery_scope": delivery_scope if delivery_scope in {"official_submission", "ai_context_only", "internal_authority_only"} else "ai_context_only",
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
        except Exception as exc:
            attachment["vector_store_status"] = "error"
            attachment["vector_store_error"] = str(exc)[:300]
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
    for review in db.list_package_reviews():
        review_id = str(review.get("id") or "")
        for permit_type in PERMIT_SCHEMAS:
            package = artifact_json(db, review_id, "material_package", permit_type, {})
            if isinstance(package, dict) and package.get("package_id") == package_id:
                return {
                    "case_id": review_id,
                    "permit_type": permit_type,
                    "package": package,
                    "customer_id": str(review.get("customer_id") or ""),
                    "review": review,
                }
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
    """F017: Create a new material package, canceling any existing pending/running ones first."""
    existing = artifact_json(db, case_id, "material_package", permit_type, {})
    if existing.get("status") in {"pending", "processing", "ready"}:
        # Cancel existing in-flight package and archive it
        store = artifact_json(db, case_id, "material_packages", permit_type, {"packages": []})
        existing["status"] = "cancelled_by_new_upload"
        existing["cancelled_at"] = time.time()
        store.setdefault("packages", []).append(existing)
        store["packages"] = store["packages"][-12:]
        save_artifact(db, case_id, "material_packages", permit_type, store)
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


def start_material_ingest_job(
    db: Any,
    case_id: str,
    permit_type: str,
    document_count: int,
    *,
    azure_enabled: bool,
    package_id: str = "",
) -> dict[str, Any]:
    per_document = 12 if azure_enabled else 4
    eta_seconds = max(15, 8 + max(1, document_count) * per_document)
    job = start_ai_job(
        db,
        case_id,
        permit_type,
        "material_ingest",
        "Aineiston vastaanotto ja valmistelu",
        ["archive_validator", "text_extractor", "attachment_classifier", "vector_indexer"],
        eta_seconds=eta_seconds,
        managed_background=True,
    )
    if package_id:
        job["package_id"] = package_id
        store = artifact_json(db, case_id, "ai_jobs", permit_type, {"jobs": []})
        for stored_job in store.get("jobs", []):
            if stored_job.get("id") == job["id"]:
                stored_job["package_id"] = package_id
                break
        save_artifact(db, case_id, "ai_jobs", permit_type, store)
    return job


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
        # Mark the ingest pipeline agents complete as their work finishes so the
        # wizard banner reflects real progress instead of sitting at 1%.
        mark_agent_complete(db, case_id, permit_type, job_id, "archive_validator", "Paketin tarkistus valmis.")
        mark_agent_complete(db, case_id, permit_type, job_id, "text_extractor", "Tekstin poiminta valmis.")
        mark_agent_complete(db, case_id, permit_type, job_id, "attachment_classifier", "Aineiston luokittelu valmis.")
        mark_agent_complete(db, case_id, permit_type, job_id, "vector_indexer", "Hakukontekstin valmistelu valmis.")
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
                # F22: reject unknown scopes instead of silently storing them.
                if delivery_scope not in {"official_submission", "ai_context_only", "internal_authority_only"}:
                    raise ValueError(f"unknown_delivery_scope:{delivery_scope}")
                att["delivery_scope"] = delivery_scope
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
    timeout_s: float | None = None,
) -> dict[str, Any]:
    permit_type = "environmental_industrial"
    job = start_ai_job(db, case_id, permit_type, "structure_extraction", "Hakemuksen rakenteistus liitteistä", ["structure_extractor", "gap_analyzer"], eta_seconds=20) if track_job else None
    ensure_application(db, case_id, permit_type)
    attachments = list_attachments(db, case_id, permit_type)
    official_or_ai = [a for a in attachments if a.get("delivery_scope") in {"official_submission", "ai_context_only"}]
    joined = "\n\n".join(
        f"[Dokumentti: {a.get('filename') or 'Hakemusaineisto'}]\n{str(a.get('text_excerpt') or a.get('summary') or '')}"
        for a in official_or_ai
    )
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
    if _real_llm_enabled_for_case(db, case_id, settings):
        allowed_fields = {str(field.get("id")) for field in load_form_schema(permit_type).get("fields", [])}
        source_fallbacks = {str(item.get("field_id")): item for item in source_by_field}
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
            timeout_s=timeout_s,
        )
        source_by_field = []
        if isinstance(response, StructureExtractionResponse):
            fields_iter = response.extracted_fields
        else:
            fields_iter = []
        for schema_field in fields_iter:
            # ExtractedField is a dataclass — attribute access only, no .get().
            fid = str(schema_field.field_id or "")
            val = str(schema_field.value or "")
            if fid not in allowed_fields or not val.strip():
                continue
            refs = [ref for ref in (schema_field.evidence_refs or []) if isinstance(ref, dict)]
            fallback_source = source_fallbacks.get(str(fid), {})
            if not refs:
                refs = [ref for ref in (fallback_source.get("evidence_refs") or []) if isinstance(ref, dict)]
            source_by_field.append(
                {
                    "field_id": fid,
                    "extracted_value": val.strip(),
                    "source_document": str((refs[0].get("title") if refs else "Hakemusaineisto") or "Hakemusaineisto"),
                    "confidence": float(schema_field.confidence),
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
        _extraction_queue_item(item)
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
            edited = value != str(item.get("suggested_value") or "")
            item["status"] = "edited" if edited else "accepted"
            item["accepted_value"] = value
            item["edited_on_accept"] = edited
            item["accepted_at"] = now
    queue_payload["items"] = items
    queue_payload["updated_at"] = now
    save_artifact(db, case_id, "field_extraction_queue", permit_type, queue_payload)
    package_result = artifact_json(db, case_id, "package_analysis_result", permit_type, {})
    if isinstance(package_result.get("extraction_queue"), list):
        for item in package_result["extraction_queue"]:
            if isinstance(item, dict) and item.get("field_id") == field_id:
                edited = value != str(item.get("suggested_value") or "")
                item["status"] = "edited" if edited else "accepted"
                item["accepted_value"] = value
                item["edited_on_accept"] = edited
                item["accepted_at"] = now
        save_artifact(db, case_id, "package_analysis_result", permit_type, package_result)
    event(db, case_id, "ai.extraction.accepted", "AI extraction accepted", {"field_id": field_id, "permit_type": permit_type})
    return app


def accept_all_extraction_queue_items(db: Any, case_id: str, permit_type: str) -> int:
    permit_type = application_id_for(permit_type)
    queue_payload = artifact_json(db, case_id, "field_extraction_queue", permit_type, {"items": []})
    pending_items = [
        item for item in _list(queue_payload.get("items"))
        if isinstance(item, dict) and item.get("status") == "pending" and str(item.get("suggested_value") or item.get("extracted_value") or "").strip()
    ]
    accepted_count = 0
    for item in pending_items:
        accept_extraction_queue_item(
            db,
            case_id,
            permit_type,
            str(item.get("field_id")),
            str(item.get("suggested_value") or item.get("extracted_value") or ""),
        )
        accepted_count += 1
    return accepted_count


def reject_extraction_queue_item(db: Any, case_id: str, permit_type: str, field_id: str) -> None:
    permit_type = application_id_for(permit_type)
    queue_payload = artifact_json(db, case_id, "field_extraction_queue", permit_type, {"items": []})
    items = _list(queue_payload.get("items"))
    selected = next((item for item in items if isinstance(item, dict) and item.get("field_id") == field_id), None)
    if not selected:
        raise KeyError(field_id)
    selected["status"] = "rejected"
    selected["rejected_at"] = time.time()
    queue_payload["updated_at"] = time.time()
    save_artifact(db, case_id, "field_extraction_queue", permit_type, queue_payload)
    package_result = artifact_json(db, case_id, "package_analysis_result", permit_type, {})
    for item in package_result.get("extraction_queue", []) if isinstance(package_result.get("extraction_queue"), list) else []:
        if isinstance(item, dict) and item.get("field_id") == field_id:
            item["status"] = "rejected"
            item["rejected_at"] = queue_payload["updated_at"]
    if package_result:
        save_artifact(db, case_id, "package_analysis_result", permit_type, package_result)
    event(db, case_id, "ai.extraction.rejected", "AI extraction rejected", {"field_id": field_id, "permit_type": permit_type})


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


EXPECTED_SOURCE_ARCHIVES = ["P2X.zip", "Ren-Gas.zip", "Sharpcell.zip"]


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
