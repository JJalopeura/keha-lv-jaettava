from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from . import services as svc


ROOT = Path(__file__).resolve().parents[3]
DOMAIN_ROOT = Path(__file__).resolve().parent
SPEC_SCENARIOS = DOMAIN_ROOT / "specs" / "scenarios" / "demo_scenarios.json"

ACTIVE_CASE_KEY = "lupademo_active_case_id"
ACTIVE_SCENARIO_KEY = "lupademo_active_scenario_id"


def list_scenarios() -> list[dict[str, Any]]:
    try:
        payload = json.loads(SPEC_SCENARIOS.read_text(encoding="utf-8"))
        scenarios = payload.get("scenarios")
        if isinstance(scenarios, list) and scenarios:
            return [s for s in scenarios if isinstance(s, dict) and s.get("id")]
    except Exception:
        pass
    return []


def get_scenario(scenario_id: str) -> dict[str, Any]:
    for scenario in list_scenarios():
        if scenario.get("id") == scenario_id:
            return scenario
    raise KeyError(scenario_id)


def active_case_id(db: Any) -> str:
    case_id = ""
    try:
        case_id = str(db.get_app_setting(ACTIVE_CASE_KEY) or "").strip()
    except Exception:
        case_id = ""
    if case_id and db.get_case(case_id):
        return case_id
    cases = db.list_cases()
    return cases[0]["id"] if cases else ""


def set_active_scenario(db: Any, case_id: str, scenario_id: str) -> None:
    db.set_app_setting(ACTIVE_CASE_KEY, case_id)
    db.set_app_setting(ACTIVE_SCENARIO_KEY, scenario_id)


def scenario_common_data(scenario: dict[str, Any]) -> dict[str, str]:
    common = dict(svc.DEFAULT_COMMON_DATA)
    scenario_id = str(scenario.get("id") or "")
    if scenario_id == "environmental_industrial_generic":
        common.update(
            {
                "applicant_name": "Teollisuus Demo Oy",
                "business_id": "2876543-2",
                "contact_person": "Eero Hakija",
                "email": "eero.hakija@example.fi",
                "municipality": "Lahti",
                "address": "Teollisuustie 4",
                "property_id": "398-401-7-22",
                "project_name": "Teollisen tuotannon ympäristölupahanke",
            }
        )
    elif scenario_id == "event_public_vantaa":
        common.update(
            {
                "applicant_name": "Kaupunkiyhdistys Vantaa ry",
                "business_id": "1122334-5",
                "contact_person": "Veera Tapahtuma",
                "email": "veera.tapahtuma@example.fi",
                "municipality": "Vantaa",
                "address": "Tikkurilan tori",
                "property_id": "092-001-9903-1",
                "project_name": "Tikkurilan kesätapahtuma",
            }
        )
    elif scenario_id == "national_suggestion_service":
        common.update(
            {
                "applicant_name": "Kansallinen asiointipalvelu",
                "business_id": "0000000-0",
                "contact_person": "Tuoteomistaja",
                "email": "tuoteomistaja@example.fi",
                "municipality": "Useita kuntia",
                "address": "API-demo",
                "property_id": "",
                "project_name": "Kansallinen AI-ehdotuspalvelu",
            }
        )
    return common


def seed_scenario_attachments(db: Any, case_id: str, scenario: dict[str, Any]) -> None:
    if "environmental_industrial" in (scenario.get("permit_types") or []):
        result = svc.seed_environmental_material_story(db, case_id, "generic_industrial_environmental_demo")
        svc.save_artifact(
            db,
            case_id,
            "scenario_attachment_seed",
            "main",
            {"status": "seeded", "scenario_id": scenario.get("id"), "seeded_at": time.time(), "result": result},
        )
        return
    svc.save_artifact(
        db,
        case_id,
        "scenario_attachment_seed",
        "main",
        {"status": "not_seeded_in_phase_1", "scenario_id": scenario.get("id"), "seeded_at": time.time()},
    )


def create_case_from_scenario(db: Any, scenario_id: str) -> dict[str, Any]:
    scenario = get_scenario(scenario_id)
    case_id = db.create_case(f"Lupademo: {scenario.get('label', scenario_id)}", language="fi")
    db.set_case_status(case_id, "application_draft")
    common = scenario_common_data(scenario)
    svc.save_artifact(db, case_id, "lv_common_data", "main", {"data": common, "version": 1})
    svc.save_artifact(db, case_id, "common_data_sync_state", "main", {"base": common.copy(), "changes": []})
    permits = list(scenario.get("permit_types") or ["building_residential"])
    statuses = {permit: ("draft" if index == 0 else "not_started") for index, permit in enumerate(permits)}
    for permit_type in svc.PERMIT_SCHEMAS:
        statuses.setdefault(permit_type, "not_started")
    now = time.time()
    project = {
        "project_id": f"lv-demo-{case_id[:8]}",
        "name": common["project_name"],
        "created_from": "lv",
        "applicant_role": "company",
        "presenter_mode": True,
        "common_data": common.copy(),
        "location": f"{common.get('municipality', '')}, {common.get('address', '')}".strip(", "),
        "tenant_id": scenario.get("tenant_id", "vantaa_demo"),
        "scenario_id": scenario_id,
        "scenario_label": scenario.get("label", scenario_id),
        "persona": scenario.get("persona", ""),
        "permit_candidates": [{"permit_type": permit, "confidence": 0.9 if index == 0 else 0.6} for index, permit in enumerate(permits)],
        "applications": permits,
        "permits": permits,
        "statuses": statuses,
        "integration_events": [
            {"id": "R1", "label": "Hanke luotu LV:ssä", "status": "done"},
            {"id": "R2", "label": "Yhteiset tiedot välitetty kevytasiointiin", "status": "done"},
            {"id": "R3", "label": "Hakemusluonnos avoinna", "status": "active"},
            {"id": "R4", "label": "Lähetyspaketti odottaa hyväksyttyä tarkistusta", "status": "waiting"},
        ],
        "created_at": now,
        "updated_at": now,
    }
    svc.save_artifact(db, case_id, "lv_project", "main", project)
    svc.save_artifact(
        db,
        case_id,
        "demo_scenario",
        "main",
        {"scenario": scenario, "started_at": time.time(), "seed_version": "phase1"},
    )
    svc.ensure_seed_state(db, case_id)
    seed_scenario_attachments(db, case_id, scenario)
    set_active_scenario(db, case_id, scenario_id)
    svc.event(db, case_id, "demo.scenario.started", "Demo scenario started", {"scenario_id": scenario_id})
    return {"case_id": case_id, "scenario": scenario, "next_url": str(scenario.get("default_next") or "/lv/projects/{case_id}").format(case_id=case_id)}


def create_generic_project(
    db: Any,
    *,
    project_name: str,
    permit_type: str,
    applicant_name: str = "",
    municipality: str = "",
    address: str = "",
    tenant_id: str = "",
    applicant_role: str = "company",
    created_from: str = "direct_demo",
) -> dict[str, Any]:
    requested_permit_type = str(permit_type or "building_residential")
    ai_decides = requested_permit_type in {"ai_decides", "unknown", "ai_arvioi"}
    permit = "environmental_industrial" if ai_decides else svc.application_id_for(requested_permit_type)
    if permit not in svc.PERMIT_SCHEMAS:
        permit = "building_residential"
    allowed_roles = {"person", "company", "consultant", "authority", "integrator"}
    if applicant_role not in allowed_roles:
        applicant_role = "company"
    allowed_sources = {"lv", "municipality_site", "direct_demo", "api_import", "package_workbench"}
    if created_from not in allowed_sources:
        created_from = "direct_demo"
    if not tenant_id:
        tenant_id = "avi_demo" if permit == "environmental_industrial" else "vantaa_demo"
    # Skip hardcoded demo defaults when real LLM (Azure) is active.
    _real_llm = str(os.getenv("KEHA_LV_LLM_PROVIDER", "mock") or "mock").strip().lower() != "mock"
    if _real_llm:
        _real_llm = str(os.getenv("KEHA_LV_FORCE_MOCK_LLM", "")).strip().lower() not in {"1", "true", "yes", "on"}
    common = (
        {key: "" for key in svc.DEFAULT_COMMON_DATA}
        if created_from == "package_workbench" or _real_llm
        else dict(svc.DEFAULT_COMMON_DATA)
    )
    if project_name:
        common["project_name"] = project_name
    if applicant_name:
        common["applicant_name"] = applicant_name
    if municipality:
        common["municipality"] = municipality
    if address:
        common["address"] = address
    case_id = db.create_case(f"Lupademo: {common['project_name']}", language="fi")
    db.set_case_status(case_id, "application_draft")
    svc.save_artifact(db, case_id, "lv_common_data", "main", {"data": common, "version": 1})
    svc.save_artifact(db, case_id, "common_data_sync_state", "main", {"base": common.copy(), "changes": []})
    statuses = {key: ("draft" if key == permit else "not_started") for key in svc.PERMIT_SCHEMAS}
    now = time.time()
    project = {
        "project_id": f"lv-generic-{case_id[:8]}",
        "name": common["project_name"],
        "created_from": created_from,
        "applicant_role": applicant_role,
        "presenter_mode": False,
        "common_data": common.copy(),
        "location": f"{common.get('municipality', '')}, {common.get('address', '')}".strip(", "),
        "tenant_id": tenant_id,
        "requested_permit_type": requested_permit_type,
        "scenario_id": "generic_project",
        "scenario_label": "Uusi hanke",
        "persona": "Hakija",
        "permit_candidates": (
            [
                {"permit_type": "environmental_industrial", "confidence": 0.72},
                {"permit_type": "building_residential", "confidence": 0.46},
                {"permit_type": "event_public", "confidence": 0.32},
            ]
            if ai_decides
            else [{"permit_type": permit, "confidence": 0.85}]
        ),
        "applications": [permit],
        "permits": [permit],
        "statuses": statuses,
        "integration_events": [
            {"id": "G1", "label": "Hanke luotu", "status": "done"},
            {"id": "G2", "label": "Aineisto odottaa latausta", "status": "active"},
            {"id": "G3", "label": "AI-analyysi käynnistetään hakemusstudiosta", "status": "waiting"},
        ],
        "created_at": now,
        "updated_at": now,
    }
    svc.save_artifact(db, case_id, "lv_project", "main", project)
    svc.save_artifact(
        db,
        case_id,
        "demo_scenario",
        "main",
        {"scenario": {"id": "generic_project", "label": "Uusi hanke", "permit_types": [permit]}, "started_at": time.time(), "seed_version": "generic"},
    )
    svc.ensure_seed_state(db, case_id)
    if created_from == "package_workbench":
        schema = svc.load_form_schema(permit)
        form_data = {field["id"]: common.get(field["id"], "") for field in schema.get("fields", [])}
        app = svc.ensure_application(db, case_id, permit)
        app["form_data"] = form_data
        app["field_sources"] = {key: "lv_common_data" for key in svc.COMMON_FIELDS if form_data.get(key)}
        app["updated_at"] = time.time()
        svc.save_artifact(db, case_id, "permit_application", permit, app)
    set_active_scenario(db, case_id, "generic_project")
    svc.event(db, case_id, "project.generic.created", "Generic project created", {"permit_type": permit})
    return {"case_id": case_id, "next_url": f"/lv/projects/{case_id}", "permit_type": permit}
