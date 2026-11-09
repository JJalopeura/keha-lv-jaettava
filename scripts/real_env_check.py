"""Real-model sanity run for the Ympäristölupa lane (not a unit test).

Ingests the real Ren-Gas package, applies the approved-application form fixture,
runs the full check against Azure AI Foundry, and prints the verdict shape:
criteria statuses, finding titles/severities, score. No secrets are printed.

    set -a; source .env; set +a
    KEHA_LV_DATA_DIR=/tmp/keha-real .venv/bin/python /tmp/real_env_check.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.domains.lupademo.services as svc  # noqa: E402
from app.domains.lupademo.scenarios import create_case_from_scenario  # noqa: E402
from app.infra.config import load_settings  # noqa: E402
from app.infra.db import DB  # noqa: E402

FORM_DATA = {
    "municipality": "Kerava",
    "applicant_name": "Ren-Gas Oy",
    "business_id": "2347291-4",
    "activity_name": "Biokaasu- ja energiantuotantolaitos (ESAVI-14858-2025)",
    "permit_basis": "Ympäristönsuojelulain 27 § mukainen luvanvaraisuus hakemuksessa; aiemmat AVI-päätökset 2019/2020 liitteinä.",
    "competent_authority": "Etelä-Suomen aluehallintovirasto",
    "site_location": "Kerava; sijainti kuvattu hakemuksessa ja YVA-selostuksessa.",
    "zoning_context": "Kaavoitus ja maankäyttö kuvattu hakemuksessa.",
    "sensitive_receptors": "Keravanjoki (Liite 10 pohjaeliöstöarvio); erillistä Natura-arviota ei ole.",
    "activity_description": "Biokaasu- ja energiantuotantolaitos Keravalla; YVA-menettely käyty (Liitteet 4 a–c).",
    "production_capacity": "Tuotantokapasiteetti hakemusasiakirjojen mukaan.",
    "process_description": "Biokaasun ja energian tuotantoprosessi; perusteltu päätelmä Liite 4 a.",
    "raw_materials_chemicals": "Kemikaalien käsittely hakemuksessa; Liite 7 Kemikaaliluettelo.",
    "water_and_wastewater": "Jätevesien kuvaus hakemuksessa; pohjaeliöstöarvio Liite 10.",
    "air_emissions": "Savukaasumallinnus Liite 8 + täydennys ilmapäästöihin 2025_0527.",
    "waste": "Jätteiden käsittely kuvattu hakemuksessa ja perustilaselvityksessä.",
    "environmental_impacts": "YVA-selostuksen mukaiset vaikutukset (Liite 4 a).",
    "risk_preparedness": "Liite 12 Ennaltavarautumissuunnitelma Ren-Gas Kerava.",
    "bat_assessment": "Liite 13 BAT-vertailu Ren-Gas Kerava.",
    "monitoring_plan": "Vesipuolen tarkkailu Liite 10; erillistä ilman tarkkailuohjelmaa ei ole.",
}

PERMIT = "environmental_industrial"


def main() -> None:
    settings = load_settings()
    if not settings.azure_openai_host_core:
        raise SystemExit("AZURE_OPENAI_HOST_CORE missing — source .env first")
    db = DB(settings.db_path)
    started = time.time()
    case_id = create_case_from_scenario(db, "environmental_industrial_generic")["case_id"]
    zip_path = Path("testiaineisto/esimerkkihakemukset/Ren-Gas.zip")
    svc.ingest_application_zip(db, settings, case_id, PERMIT, zip_path.name, zip_path.read_bytes())
    attachments = svc.list_attachments(db, case_id, PERMIT)
    print(f"ingested {len(attachments)} attachments in {time.time() - started:.0f}s")
    for att in attachments:
        print(f"  {att.get('delivery_scope'):18s} {att.get('primary_type'):24s} {att.get('filename')[:60]}")
    svc.update_application_data(db, case_id, PERMIT, FORM_DATA)
    # Keep the shared applicant data coherent with the application. A scenario case
    # seeds "Teollisuus Demo Oy / Lahti", and leaving that in place makes the
    # consistency validator report real name/Y-tunnus/municipality contradictions
    # that belong to the fixture, not to the pipeline.
    svc.update_lv_common_data(
        db,
        case_id,
        {
            "applicant_name": FORM_DATA["applicant_name"],
            "business_id": FORM_DATA["business_id"],
            "municipality": FORM_DATA["municipality"],
        },
    )
    check_started = time.time()
    svc.run_application_check(db, case_id, PERMIT, settings)
    report = svc.artifact_json(db, case_id, "check_report", PERMIT, {})
    print(f"\ncheck took {time.time() - check_started:.0f}s · provider={report.get('provider')}")
    print("counts:", json.dumps(report.get("counts")), "score:", report.get("score"), "readiness:", report.get("readiness"))
    print("summary:", report.get("summary"))
    print("gate:", json.dumps(svc.precheck_gate_status(db, case_id, PERMIT)))
    degraded = report.get("degraded") or {}
    print("fallback agents:", degraded.get("fallback_used_agents"))
    print("\ncriteria:")
    for c in report.get("criteria_results", []):
        print(f"  {c.get('criteria_id'):24s} {c.get('status'):7s} {str(c.get('rationale'))[:110]}")
    print("\nissues:")
    for i in report.get("issues", []):
        if i.get("resolved"):
            continue
        print(f"  {i.get('severity'):10s} crit={str(i.get('criteria_id')):20s} {str(i.get('title'))[:90]}")
    print("\nprecheck summary:", str((report.get("precheck") or {}).get("summary"))[:300])


if __name__ == "__main__":
    main()
