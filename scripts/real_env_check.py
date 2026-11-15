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
    "applicant_name": "Nordic Ren-Gas Oy",
    "business_id": "2347291-4",
    "activity_name": "Puhtaiden P2X-kaasupolttoaineiden ja CO2-vapaan kaukolämmön yhteistuotantolaitos",
    "permit_basis": "Laitos sijoittuu Alikeravan teollisuuskortteliin Keravan Energian biovoimalaitoksen tontille. Hanke tuottaa uusiutuvaa e-metaania 15 000 t/a ja kaukolämpöä noin 32 MW; YVA-menettely on käyty ja perusteltu päätelmä on liitteenä.",
    "site_location": "Kiinteistö 245-9-348-1, Alikeravan teollisuuskortteli, Kerava; omistaja- ja haltijatiedot erillisellä liitteellä.",
    "zoning_context": "Alue on asemakaavassa teollisuuskorttelialuetta; kaavamerkinnät ja kaavatilanne hakemuksen kohdassa 2.3.",
    "activity_description": "Power-to-Gas-laitos: vedyn tuotanto, hiilidioksidin talteenotto savukaasuista, metaanin tuotanto ja kaukolämmön tuotanto Keravalla.",
    "production_capacity": "Metaania 15 000 t/a, metaanin tuotantoteho 25 MW ja kaukolämpöä noin 32 MW.",
    "byproducts": "Sivutuoteluokittelua ei haeta; prosessin jätteet ja sivuvirrat kuvataan hakemuksen kohdassa 3.8.",
    "process_description": "Vedyn tuotanto, CO₂:n talteenotto, metaanisynteesi, metaanin syöttö siirtoverkkoon sekä apujäähdytys ja kaukolämmön tuotanto (hakemus 3.3).",
    "risk_preparedness": "Liite 12 Ennaltavarautumissuunnitelma Ren-Gas Kerava; tunnistetut onnettomuus- ja häiriötilanteet hakemuksen kohdassa 5.",
    "land_use_environment": "Alueen itäpuolella valtatie 4, eteläpuolella metsäalue ja Keravanjoki; lähimmät asuinalueet ja herkät kohteet hakemuksen kohdassa 2.4.",
    "land_use_impacts": "YVA-selostuksen mukaiset vaikutukset maankäyttöön ja lähialueen asutukseen (Liite 4 a).",
}

PERMIT = "environmental_industrial"


def main() -> None:
    settings = load_settings()
    if not settings.azure_openai_host_core:
        raise SystemExit("AZURE_OPENAI_HOST_CORE missing — source .env first")
    db = DB(settings.db_path)
    started = time.time()
    case_id = create_case_from_scenario(db, "environmental_industrial_generic")["case_id"]
    zip_path = Path("testiaineisto/esimerkkihakemukset/Ren-Gas pelkkä hakemus 20261104.zip")
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
