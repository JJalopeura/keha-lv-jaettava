"""Seed two deterministic Ren-Gas applications for the customer demo."""

from __future__ import annotations

import argparse
import json
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domains.lupademo import scenarios
from app.domains.lupademo import services as svc
from app.domains.lupademo.services.intake import (
    add_attachment,
    classify_attachment,
    extract_text_from_path,
    save_upload_file,
    save_material_package,
    suggested_scope_for_attachment,
)
from app.infra.config import load_settings
from app.infra.db import DB

PERMIT = "environmental_industrial"
CUSTOMER_ID = "ren-gas"
SEED_KEY = "ren_gas_customer_demo_2026_09"
ZIP_PATH = ROOT / "testiaineisto" / "esimerkkihakemukset" / "Ren-Gas.zip"
DEMO_SUGGESTION_FIELDS = {
    "activity_name",
    "permit_basis",
    "competent_authority",
    "site_location",
    "zoning_context",
    "sensitive_receptors",
    "activity_description",
    "production_capacity",
    "process_description",
    "raw_materials_chemicals",
    "water_and_wastewater",
    "air_emissions",
    "waste",
    "environmental_impacts",
    "risk_preparedness",
    "bat_assessment",
    "monitoring_plan",
    "yva_context",
}

COMMON = {
    "applicant_name": "Ren-Gas Oy",
    "business_id": "0000000-2",
    "contact_person": "Mikko Mäki",
    "phone": "+358 40 000 2002",
    "email": "mikko.maki@ren-gas.example",
    "municipality": "Kerava",
    "address": "Biokaasuntie 8",
    "property_id": "245-8-42-1",
    "project_name": "Ren-Gas biokaasulaitos",
    "permit_basis": "Keravalle suunniteltu Power-to-Gas-laitos valmistaa biogeenisestä hiilidioksidista synteettistä metaania ja tuottaa samalla CO2-vapaata kaukolämpöä. Kokonaisuuteen kuuluvat vedyn tuotanto, hiilidioksidin talteenotto, metaanin tuotanto ja jakelu, kaasujen puhdistus, kemikaalien ja kaasujen varastointi, prosessin apujäähdytys sekä paineelliset putkistot. Hakemuksessa arvioidaan toiminnan päästöt ilmaan ja veteen, melu, jätteet, liikenne, hulevedet ja poikkeustilanteiden ympäristöriskit ympäristönsuojelulain 27 §:n mukaisen luvantarpeen yhteydessä.",
}

FORM_DATA = {
    **COMMON,
    "activity_name": "Ren-Gas biokaasulaitos",
    "competent_authority": "Etelä-Suomen aluehallintovirasto (ESAVI-14858-2025)",
    "site_location": "Kerava, Biokaasuntie 8, kiinteistö 245-8-42-1. Vaikutusalueella on Keravanjoki ja sen pohjaeliöstö.",
    "zoning_context": "Toiminta sijoittuu teollisuusalueelle. Kaavatilanne ja vaikutusalueen etäisyydet on kuvattu hakemuksen pääasiakirjassa ja YVA-aineistossa.",
    "sensitive_receptors": "Keravanjoki, lähialueen asutus ja luonnonsuojelun kannalta huomioitavat kohteet. Natura-arvioinnin tarpeellisuus on hyvä varmistaa erikseen.",
    "activity_description": "Laitos vastaanottaa biogeenistä hiilidioksidia ja tuottaa biokaasupohjaista energiaa. Kokonaisuuteen liittyvät syötteiden käsittely, varastointi, putkistot, kaasun puhdistus ja häiriötilanteiden hallinta.",
    "production_capacity": "Kapasiteetti ja tuotannon vaihteluvälit esitetään hakemuksen pääasiakirjassa. Tarkkailussa erotellaan normaalikäyttö, alasajo ja poikkeustilanteet.",
    "process_description": "Prosessiin kuuluvat vastaanotto, käsittely, kaasun tuotanto ja puhdistus, varastointi sekä paineelliset putkistot. Prosessivaiheiden väliset päästölähteet on tunnistettu mallinnuksessa.",
    "raw_materials_chemicals": "Syötteet, kemikaalit ja varastointimäärät on koottu Liite 7:n kemikaaliluetteloon. Käyttöturvallisuustiedotteiden ja Tukesin toimivaltarajan yhteys kannattaa kuvata samassa taulukossa.",
    "water_and_wastewater": "Veden käyttö, jätevedet ja hulevedet kuvataan hakemuksessa. Liite 10 arvioi hulevesien vaikutusta Keravanjoen pohjaeliöstöön.",
    "air_emissions": "Ilmapäästöt, hajut ja häiriötilanteet on mallinnettu Liitteessä 8 ja täydennetty asiakirjalla Ren-Gas Kerava - Täydennys ilmapäästöihin_2025_0527.pdf.",
    "waste": "Jätteet, sivuvirrat ja niiden toimitus hyväksyttyyn käsittelyyn kuvataan hakemuksessa ja perustilaselvityksessä. Seurantaan lisätään poikkeamien kuukausittainen koonti.",
    "environmental_impacts": "Keskeiset vaikutuspolut ovat ilmapäästöt, hajut, melu, hulevedet, liikenne ja poikkeustilanteet. Vaikutusarviot yhdistetään tarkkailupisteisiin ja raportointikynnyksiin.",
    "risk_preparedness": "Liite 12 käsittelee vedyn, metaanin ja paineellisten putkistojen poikkeustilanteet. Ilmoitus- ja alasajopolku ympäristö- ja kemikaaliturvallisuusviranomaisille täsmennetään.",
    "bat_assessment": "Liite 13 vertailee toimintaa BAT-periaatteisiin ja kuvaa energiatehokkuuden, päästöjen minimoinnin sekä prosessiturvallisuuden ratkaisut.",
    "monitoring_plan": "Tarkkailusuunnitelma kattaa päästöjä, jätevesiä, melua ja vuosiraportointia. Ilmapäästöjen jatkuvan ja jaksottaisen tarkkailun rajat sekä poikkeamatilanteiden näytteenotto täsmennetään.",
    "yva_context": "Liite 4 a sisältää YVA-selostuksen ja perustellun päätelmän, Liite 4 b yhteenvetonsa ja Liite 4 c UUDELY:n lausunnon ajantasaisuudesta.",
}


def _artifact(db: DB, case_id: str, type_: str, name: str, value: dict[str, Any]) -> None:
    svc.save_artifact(db, case_id, type_, name, value)


def _load_uploaded_attachments(db: DB, settings: Any, case_id: str) -> list[dict[str, Any]]:
    if not ZIP_PATH.exists():
        raise FileNotFoundError(f"Ren-Gas archive not found: {ZIP_PATH}")
    added: list[dict[str, Any]] = []
    with zipfile.ZipFile(ZIP_PATH) as archive:
        for member in sorted(archive.infolist(), key=lambda item: item.filename):
            if member.is_dir() or Path(member.filename).name.startswith(".") or "__MACOSX" in member.filename:
                continue
            filename = Path(member.filename).name
            content = archive.read(member)
            local_path = save_upload_file(settings, case_id, filename, content)
            text = extract_text_from_path(local_path, filename)
            primary_type = classify_attachment(filename)
            attachment = add_attachment(
                db,
                case_id,
                PERMIT,
                filename,
                len(content),
                suggested_scope_for_attachment(primary_type, filename),
                text,
                local_path=local_path,
            )
            attachment["primary_type"] = primary_type
            attachment["zip_source"] = ZIP_PATH.name
            added.append(attachment)
    _artifact(db, case_id, "permit_attachments", PERMIT, {"attachments": added})
    return added


def _source(attachment: dict[str, Any], quote: str, locator: str = "document") -> dict[str, Any]:
    return svc.source_ref(
        str(attachment.get("filename") or "Ren-Gas hakemusaineisto"),
        str(attachment.get("id") or "local_excerpt"),
        quote,
        0.96,
    ) | {"locator": locator}


def _find(attachments: list[dict[str, Any]], needle: str) -> dict[str, Any]:
    return next(item for item in attachments if needle.casefold() in str(item.get("filename") or "").casefold())


def _seed_step_three(db: DB, case_id: str, attachments: list[dict[str, Any]]) -> None:
    main = _find(attachments, "Ympäristölupahakemus")
    yva = _find(attachments, "YVA-selostus")
    chemical = _find(attachments, "Kemikaaliluettelo")
    field_sources = {
        "yva_context": (yva, "YVA-selostus ja perusteltu päätelmä muodostavat hankkeen vaikutusarvioinnin perustan."),
        "raw_materials_chemicals": (chemical, "Kemikaaliluettelo kokoaa prosessin kemikaalit ja varastoitavat aineet."),
    }
    items = []
    suggestions = []
    for field in svc.load_form_schema(PERMIT).get("fields", []):
        field_id = str(field.get("id") or "")
        if field_id not in DEMO_SUGGESTION_FIELDS or not FORM_DATA.get(field_id):
            continue
        attachment, quote = field_sources.get(
            field_id,
            (main, "Hakemuksen pääasiakirja kuvaa Ren-Gas biokaasulaitoksen toimintaa ja ympäristövaikutuksia."),
        )
        reference = _source(attachment, quote)
        items.append({
            "id": f"extraction_{field_id}",
            "field_id": field_id,
            "suggested_value": FORM_DATA[field_id],
            "source_document": attachment["filename"],
            "confidence": 0.96,
            "low_confidence": False,
            "evidence_refs": [reference],
            "status": "pending",
        })
        suggestions.append({
            "id": f"suggestion_{field_id}",
            "field_id": field_id,
            "suggested_value": FORM_DATA[field_id],
            "current_value": "",
            "diff_summary": "Täydentää kentän aineistoon perustuvalla AI-ehdotuksella.",
            "rationale": "Ehdotus perustuu Ren-Gasin ladattuun hakemusaineistoon.",
            "confidence": 0.96,
            "evidence_refs": [reference],
            "criteria_version": "2026-08-demo-2",
            "agent": "field_suggestion_agent",
            "fallback_used": False,
            "warnings": [],
            "status": "pending",
            "created_at": time.time() - 20,
        })
    _artifact(db, case_id, "field_extraction_queue", PERMIT, {"items": [], "updated_at": time.time() - 20})
    _artifact(db, case_id, "ai_suggestions", PERMIT, {"suggestions": suggestions, "updated_at": time.time() - 20})
    _artifact(db, case_id, "ai_jobs", PERMIT, {"jobs": []})
    for kind, title, summary in (
        ("intake_analysis", "Aineiston analyysi", "Aineisto analysoitu: Ren-Gasin 19 ladattua liitettä käsitelty."),
        ("form_suggestions", "Hakemuksen ehdotukset", "Hakemuksen ehdotukset muodostettu 16 kentälle."),
    ):
        job = svc.start_ai_job(db, case_id, PERMIT, kind, title, ["demo_seed"], eta_seconds=20)
        svc.complete_ai_job(db, case_id, PERMIT, job["id"], summary=summary)
    app = svc.ensure_application(db, case_id, PERMIT)
    app["form_data"] = {
        str(field.get("id")): COMMON.get(str(field.get("id")), "")
        if field.get("common_data")
        else ""
        for field in svc.load_form_schema(PERMIT).get("fields", [])
    }
    app["updated_at"] = time.time() - 60
    svc.save_artifact(db, case_id, "permit_application", PERMIT, app)


def _seed_kevyt_case(db: DB, settings: Any) -> str:
    for case in db.list_cases_for_customer(CUSTOMER_ID):
        project = svc.artifact_json(db, case["id"], "lv_project", "main", {})
        if project.get("demo_seed_key") == SEED_KEY and project.get("demo_stage") == "kevyt":
            case_id = str(case["id"])
            attachments = svc.list_attachments(db, case_id, PERMIT)
            if attachments:
                _seed_kevyt_artifacts(db, case_id, attachments)
            return case_id

    created = scenarios.create_generic_project(
        db,
        project_name=COMMON["project_name"],
        permit_type=PERMIT,
        applicant_name=COMMON["applicant_name"],
        municipality=COMMON["municipality"],
        address=COMMON["address"],
        customer_id=CUSTOMER_ID,
        customer_profile=COMMON,
        created_from="kevytasiointi",
    )
    case_id = str(created["case_id"])
    project = svc.artifact_json(db, case_id, "lv_project", "main", {})
    project.update({
        "name": COMMON["project_name"],
        "presenter_mode": True,
        "demo_seed_key": SEED_KEY,
        "demo_stage": "kevyt",
        "demo_hidden_home": True,
        "created_from": "kevytasiointi",
        "service_mode": "kevytasiointi",
    })
    svc.save_artifact(db, case_id, "lv_project", "main", project)
    svc.save_artifact(db, case_id, "lv_common_data", "main", {"data": {**COMMON}, "version": 1})
    svc.save_artifact(db, case_id, "common_data_sync_state", "main", {"base": {**COMMON}, "changes": []})
    attachments = _load_uploaded_attachments(db, settings, case_id)
    _seed_kevyt_artifacts(db, case_id, attachments)
    svc.event(db, case_id, "demo.ren_gas.kevyt_seeded", "Deterministic Ren-Gas kevytasiointi demo seeded", {"attachment_count": len(attachments)})
    return case_id


def _seed_kevyt_artifacts(db: DB, case_id: str, attachments: list[dict[str, Any]]) -> None:
    existing_package = svc.artifact_json(db, case_id, "material_package", PERMIT, {})
    package = save_material_package(
        db,
        case_id,
        PERMIT,
        COMMON["project_name"],
        attachments,
        source_channel="browser_upload",
        package_id=str(existing_package.get("package_id") or ""),
        status="ready",
        expected_document_count=len(attachments),
    )
    schema = svc.load_form_schema(PERMIT)
    prose_fields = []
    prose_lines = ["## Tekoälyapurin ehdotus — ympäristölupa", ""]
    for field in schema.get("fields", []):
        field_id = str(field.get("id") or "")
        value = str(FORM_DATA.get(field_id) or "").strip()
        if not value:
            continue
        label = str(field.get("label") or field_id)
        prose_fields.append({"field_id": field_id, "label": label, "value": value, "source": "Ren-Gas-hakemusaineisto"})
        prose_lines.extend([f"**{label}**", value, ""])
    suggestions = [
        {"field_id": item["field_id"], "suggested_value": item["value"], "source_document": item["source"]}
        for item in prose_fields
    ]
    analysis = {
        "package_id": package["package_id"],
        "permit_type": PERMIT,
        "readiness": "ready",
        "readiness_score": 96,
        "summary": "Aineisto on vastaanotettu ja analysoitu. Tekoälyapurin suositukset ovat valmiina tarkasteltaviksi.",
        "findings": [],
        "questions": [],
        "suggestions": suggestions,
        "document_inventory": [{"filename": item.get("filename"), "document_role": item.get("primary_type"), "delivery_scope": item.get("delivery_scope")} for item in attachments],
        "field_extractions": suggestions,
        "extraction_queue": [],
        "missing_attachments": [],
        "criteria_set_id": "national_environmental_industrial_demo_v1",
        "criteria_municipality": "",
        "criteria_version": "2026-08-demo-2",
        "created_at": time.time(),
    }
    _artifact(db, case_id, "package_analysis_result", PERMIT, analysis)
    _artifact(db, case_id, "field_extraction_queue", PERMIT, {"items": [], "updated_at": time.time()})
    _artifact(db, case_id, "material_intake", PERMIT, {"added_count": len(attachments), "skipped_count": 0, "package_id": package["package_id"], "created_at": time.time()})
    _artifact(db, case_id, "prose_suggestion", PERMIT, {
        "request_id": "demo_kevyt_prose_ren_gas",
        "criteria_version": "2026-08-demo-2",
        "prose_md": "\n".join(prose_lines).strip(),
        "fields": prose_fields,
        "provider": "demo_seed",
        "fallback_used": False,
    })
    _artifact(db, case_id, "ai_jobs", PERMIT, {"jobs": []})
    for kind, title, summary, result_artifact in (
        ("material_ingest", "Aineiston vastaanotto ja valmistelu", "19 asiakirjaa vastaanotettu ja valmisteltu.", f"material_intake:{PERMIT}"),
        ("intake_analysis", "Aineiston analyysi", "Aineisto analysoitu; tekoälyapurin suositukset ovat valmiina.", f"package_analysis_result:{PERMIT}"),
    ):
        job = svc.start_ai_job(db, case_id, PERMIT, kind, title, ["demo_seed"], eta_seconds=20)
        svc.complete_ai_job(db, case_id, PERMIT, job["id"], summary=summary, result_artifact=result_artifact)


def _criterion_results(criteria: dict[str, Any], attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    yellow = {
        "monitoring_plan": "Tarkkailusuunnitelma on mukana, mutta ilmapäästöjen tarkkailun päätöspisteet ja poikkeamatilanteiden näytteenotto kannattaa kytkeä näkyvästi mallinnuksen päästölähteisiin.",
        "site_map": "Sijainti hahmottuu aineistosta, mutta erillinen kartta- ja kaavaote nopeuttaisi vaikutusalueen ja asianosaisten tarkastelua.",
        "natura_assessment": "Keravanjoki on tunnistettu vaikutuspoluksi. Natura- ja muiden suojelukohteiden arviointitarve kannattaa dokumentoida nimenomaisena johtopäätöksenä.",
    }
    results = []
    for rule in criteria.get("rules", []):
        rule_id = str(rule.get("id") or "")
        status = "yellow" if rule_id in yellow else "green"
        results.append(
            {
                "criteria_id": rule_id,
                "status": status,
                "rationale": yellow.get(rule_id, "Aineistosta löytyi tähän kriteeriin liittyvä toimitettava näyttö."),
                "confidence": 0.94,
                "evidence_refs": [],
            }
        )
    return results


def _seed_step_four(db: DB, case_id: str, attachments: list[dict[str, Any]], app: dict[str, Any]) -> None:
    criteria = svc.effective_criteria(db, case_id, PERMIT, COMMON["municipality"])
    main = _find(attachments, "Ympäristölupahakemus")
    air = _find(attachments, "Savukaasumallinnus")
    supplement = _find(attachments, "Täydennys ilmapäästöihin")
    noise = _find(attachments, "Melumallinnus")
    stormwater = _find(attachments, "hulevesien vaikutuksista")
    risk = _find(attachments, "Ennaltavarautumissuunnitelma")
    chemical = _find(attachments, "Kemikaaliluettelo")
    yva = _find(attachments, "YVA-selostus")
    now = time.time()
    criteria_results = _criterion_results(criteria, attachments)
    strengths = [
        {
            "title": "YVA-aineisto muodostaa jäljitettävän vaikutusarvioinnin ketjun",
            "detail": "YVA-selostus, perustellun päätelmän yhteenveto ja UUDELY:n ajantasaisuuslausunto ovat samassa paketissa. Viranomainen saa näin sekä vaikutusarvion että tiedon sen suhteesta päivitettyyn hankesuunnitelmaan.",
            "action": "Pidä Liitteet 4 a–c yhtenä kokonaisuutena myös asiakirjaluettelossa.",
            "priority": "normal",
            "evidence_refs": [_source(yva, "YVA-selostus ja perusteltu päätelmä on toimitettu osana Ren-Gas Keravan hakemusaineistoa.")],
        },
        {
            "title": "Päästöjen ja melun arviointi kattaa eri vaikutusmekanismit",
            "detail": "Ilmapäästömallinnus ja myöhemmin päivitetty melumallinnus kattavat eri vaikutusmekanismit. Aineisto antaa hyvän perustan tarkastella sekä normaalikäyttöä että lähialueen herkkiä kohteita.",
            "action": "Säilytä mallinnusten päiväykset ja lähtötiedot näkyvästi, jotta niiden keskinäinen ajantasaisuus on helppo tarkistaa.",
            "priority": "normal",
            "evidence_refs": [_source(air, "Ilmapäästömallinnus arvioi typen oksidit, hajut ja häiriötilanteiden vaikutukset lähialueella."), _source(noise, "Päivitetty melumallinnus täydentää vaikutusten arviointia.")],
        },
        {
            "title": "Poikkeustilanteiden hallinta on kuvattu toiminnan kannalta olennaisilla aineistoilla",
            "detail": "Ennaltavarautumissuunnitelma ja kemikaaliluettelo kattavat paineelliset putkistot, metaanin ja prosessikemikaalit. Ne muodostavat jo hyvän perustan riskien arvioinnille ja häiriötilanteiden käsittelylle.",
            "action": "Pidä kemikaalien määrät ja suojausratkaisut yhdenmukaisina varautumissuunnitelman kanssa.",
            "priority": "normal",
            "evidence_refs": [_source(risk, "Varautumissuunnitelma käsittelee vedyn, metaanin ja paineellisten putkistojen poikkeustilanteet."), _source(chemical, "Kemikaaliluettelo kokoaa prosessin kemikaalit ja varastoitavat aineet.")],
        },
    ]
    improvements = [
        {
            "title": "Tee YVA:n jälkeen muuttuneesta kapasiteetista läpileikkaava muutosjälki",
            "detail": "UUDELY:n lausunnossa kapasiteetti kasvaa 20 MW:sta 25 MW:iin ja tuotanto 12 000:sta 15 000 tonniin vuodessa. Samalla veden, kemikaalien ja varastoinnin määrä muuttuu. Hakemuksesta ei näe nopeasti, missä päivitys on viety kaikkiin vaikutusasiakirjoihin.",
            "action": "Lisää muutosjälkitaulukko: vanha arvo, päivitetty arvo, vaikutus ja asiakirjakohta. Käsittelijä näkee heti, mitkä arviot perustuvat uuteen mitoitukseen.",
            "priority": "normal",
            "evidence_refs": [_source(yva, "Hankkeen suunniteltu metaanin tuotantokapasiteetti on kasvanut 20 MW:sta 25 MW:iin eli noin 12 000 tonnista vuodessa noin 15 000 tonniin vuodessa; muutos kasvattaa veden ja kemikaalien käyttöä."), _source(chemical, "Kemikaaliluettelossa esitetään aineiden käyttötarkoitus, vuotuinen käyttö, maksimivarasto, laitteistossa oleva määrä ja suojausjärjestelyt.")],
        },
        {
            "title": "Kytke mallien epävarmuudet tarkkailun hälytyslogiikkaan",
            "detail": "Savukaasumallinnus tuo esiin päästölähteet ja epävarmuudet. Melumallinnus on päivitetty myöhemmin, ja muutos herkissä kohteissa on 0–2 dB. Seuraava puuttuva linkki on toimintatapa tilanteeseen, jossa toteutunut käyttö poikkeaa mallin lähtöarvosta.",
            "action": "Lisää matriisi: lähtöoletus, mittaus, hälytysraja, uusintamallinnuksen tarve ja raportointi. Näin malli ohjaa myös käytännön valvontaa.",
            "priority": "normal",
            "evidence_refs": [_source(air, "Raportti sisältää päästölähteet, lähimmät rakennukset, pitoisuudet maanpinnalla ja lähtötiedoista aiheutuvat epävarmuustekijät."), _source(noise, "Melumallinnuksen päivitys on päivätty 12.03.2025."), _source(yva, "Päivitetyn suunnitelman mukainen melu kasvaa jonkin verran, mutta muutos lähimmissä herkissä kohteissa on 0–2 dB.")],
        },
        {
            "title": "Tee kemikaaliluettelosta ympäristöriskien priorisointikartta",
            "detail": "Kemikaaliluettelossa MEA:n ulkovarasto on 90 m³, ja mukana ovat vuotoallas ja ylitäytönesto. Ammoniakille kuvataan paineenalainen käyttö ja vuotojen seuranta. Varautumissuunnitelma ei vielä yhdistä näitä ratkaisuja hulevesien suojaamiseen ja ilmoituskynnyksiin.",
            "action": "Lisää riskipolku: määrä, suojaus, havainto, ensimmäinen sulku, hulevesisuojaus, ilmoituskynnys ja palautumisen todentaminen.",
            "priority": "normal",
            "evidence_refs": [_source(chemical, "Kemikaaliluettelossa monoetanolamiinin maksimivarasto on 90 m3 ja suojausratkaisuna vuotoallas sekä ylitäytönesto; ammoniakille kuvataan paineenalainen käyttö ja vuotojen seurantaa."), _source(risk, "Ennaltavarautumissuunnitelman sisällysluettelo käsittelee kemikaaleja, vedenottoa, jätevesiä, sammutusvesiä ja ilmapäästöjä erillisinä kokonaisuuksina.")],
        },
    ]
    issues = [
        {
            "id": "demo_monitoring_link",
            "type": "inconsistent",
            "severity": "minor",
            "field_id": "",
            "title": "Tarkkailusuunnitelman ja päästömallinnuksen välinen seurantalinkki",
            "detail": "Päästömallinnus ja tarkkailusuunnitelma ovat mukana, mutta niiden päästölähde- ja mittauskohdistus kannattaa tehdä eksplisiittiseksi.",
            "confidence": 0.96,
            "criteria_id": "monitoring_plan",
            "attachment_id": air.get("id"),
            "suggested_fix": "Lisää lähdekohtainen seuranta- ja raportointimatriisi.",
            "evidence_refs": [_source(air, "Ilmapäästömallinnus arvioi typen oksidit, hajut ja häiriötilanteet.")],
            "resolved": False,
        },
        {
            "id": "demo_stormwater_thresholds",
            "type": "recommendation",
            "severity": "minor",
            "field_id": "",
            "title": "Hulevesitarkkailun toimenpiderajat",
            "detail": "Vaikutus Keravanjokeen on arvioitu, mutta toimenpiderajat ja näytteenottorytmi kannattaa kirjata samaan paikkaan.",
            "confidence": 0.94,
            "criteria_id": "stormwater_management",
            "attachment_id": stormwater.get("id"),
            "suggested_fix": "Täydennä vaikutusarvio taulukolla, joka sitoo mittaustuloksen korjaavaan toimeen.",
            "evidence_refs": [_source(stormwater, "Arvio hulevesien vaikutuksista Keravanjoen pohjaeliöstöön.")],
            "resolved": False,
        },
        {
            "id": "demo_public_summary",
            "type": "recommendation",
            "severity": "minor",
            "field_id": "",
            "title": "Yleisölle suunnattu vaikutuspolkujen yhteenveto",
            "detail": "Hakemuksen tekninen aineisto on laaja. Erillinen selkokielinen yhteenveto vähentäisi viranomaisen tarvetta muodostaa vaikutuskuvaa useista liitteistä kuulutusta varten.",
            "confidence": 0.91,
            "criteria_id": "public_summary",
            "attachment_id": main.get("id"),
            "suggested_fix": "Laadi yhden sivun yhteenveto toiminnasta, päästöistä, riskeistä, tarkkailusta ja vaikutusalueesta.",
            "evidence_refs": [_source(main, "Hakemuksen pääasiakirja kuvaa toiminnan ja ympäristövaikutusten kokonaisuuden.")],
            "resolved": False,
        },
    ]
    counts = {"blocking": 0, "major": 0, "minor": len(issues), "red": 0, "yellow": 3, "green": max(0, len(criteria_results) - 3)}
    report = {
        "request_id": "demo_check_ren_gas_stable",
        "criteria_version": str(criteria.get("version") or "demo"),
        "criteria_set_id": str(criteria.get("criteria_set_id") or ""),
        "criteria_municipality": COMMON["municipality"],
        "checked_at": now,
        "form_updated_at": now - 30,
        "attachments_updated_at": now - 10,
        "score": 91,
        "summary": "Hakemus on vahva ja aineisto on poikkeuksellisen hyvin jäljitettävissä. Seuraavat ehdotukset parantavat erityisesti valvottavuutta, vaikutusketjujen luettavuutta ja viranomaiskäsittelyn nopeutta.",
        "readiness": "ready",
        "issues": issues,
        "criteria_results": criteria_results,
        "counts": counts,
        "degraded": {"fallback_used_agents": [], "reconciliation_notes": []},
        "review": {
            "summary": "Ren-Gasin aineisto antaa jo hyvän päätöksenteon perustan. Parhaat seuraavat parannukset eivät ole uusia perusliitteitä, vaan olemassa olevan näytön yhdistämistä tarkkailuun, raja-arvoihin ja viranomaisen tapahtumapolkuihin.",
            "strengths": strengths,
            "improvements": improvements,
            "sections": [
                {"title": "Vahvuudet", "items": [item["title"] for item in strengths]},
                {"title": "Parannusehdotukset", "items": [item["title"] for item in improvements]},
            ],
            "source_count": len(attachments),
        },
        "provider": "demo_seed",
    }
    _artifact(db, case_id, "check_report", PERMIT, report)
    _artifact(db, case_id, "precheck_gate", PERMIT, {
        "application_id": PERMIT,
        "can_submit": True,
        "is_fresh": True,
        "blocking_count": 0,
        "major_count": 0,
        "red_count": 0,
        "stale_reasons": [],
        "checked_at": now,
        "form_updated_at": now - 30,
        "attachments_updated_at": now - 10,
        "criteria_version": str(criteria.get("version") or "demo"),
        "report_criteria_version": str(criteria.get("version") or "demo"),
        "max_age_seconds": 86400,
        "updated_at": now,
    })
    _artifact(db, case_id, "ai_jobs", PERMIT, {"jobs": []})
    for kind, title, summary in (
        ("intake_analysis", "Aineiston analyysi", "Aineisto analysoitu: Ren-Gasin 19 ladattua liitettä käsitelty."),
        ("form_suggestions", "Hakemuksen ehdotukset", "Hakemuksen ehdotukset muodostettu 16 kentälle."),
        ("application_check", "Hakemuksen tarkistus", "Tarkistus valmis: aineisto on vahva ja kolme täsmennystä suositellaan."),
    ):
        agents = ["demo_seed"] if kind != "application_check" else [
            "deterministic_checks", "completeness_validator", "consistency_validator", "criteria_assessor", "applicant_precheck_report",
        ]
        job = svc.start_ai_job(db, case_id, PERMIT, kind, title, agents, eta_seconds=35)
        svc.complete_ai_job(
            db,
            case_id,
            PERMIT,
            job["id"],
            summary=summary,
            result_artifact=f"check_report:{PERMIT}" if kind == "application_check" else "",
        )
    app["updated_at"] = now - 30
    svc.save_artifact(db, case_id, "permit_application", PERMIT, app)


def _seed_case(db: DB, settings: Any, stage: int) -> str:
    existing = []
    for case in db.list_cases_for_customer(CUSTOMER_ID):
        project = svc.artifact_json(db, case["id"], "lv_project", "main", {})
        if project.get("demo_seed_key") == SEED_KEY and project.get("demo_stage") == stage:
            case_id = str(case["id"])
            svc.save_artifact(db, case_id, "lv_common_data", "main", {"data": {**COMMON}, "version": 1})
            svc.save_artifact(db, case_id, "common_data_sync_state", "main", {"base": {**COMMON}, "changes": []})
            attachments = svc.list_attachments(db, case_id, PERMIT)
            if stage == 3:
                _seed_step_three(db, case_id, attachments)
            else:
                app = svc.ensure_application(db, case_id, PERMIT)
                app.setdefault("form_data", {})["permit_basis"] = COMMON["permit_basis"]
                _seed_step_four(db, case_id, attachments, app)
            existing.append(case_id)
    if existing:
        return str(existing[0])
    created = scenarios.create_generic_project(
        db,
        project_name=COMMON["project_name"],
        permit_type=PERMIT,
        applicant_name=COMMON["applicant_name"],
        municipality=COMMON["municipality"],
        address=COMMON["address"],
        customer_id=CUSTOMER_ID,
        customer_profile=COMMON,
        created_from="direct_demo",
    )
    case_id = str(created["case_id"])
    project = svc.artifact_json(db, case_id, "lv_project", "main", {})
    project.update({"name": COMMON["project_name"], "presenter_mode": True, "demo_seed_key": SEED_KEY, "demo_stage": stage, "demo_hidden_home": True, "created_from": "lv"})
    svc.save_artifact(db, case_id, "lv_project", "main", project)
    common = {**COMMON}
    svc.save_artifact(db, case_id, "lv_common_data", "main", {"data": common, "version": 1})
    svc.save_artifact(db, case_id, "common_data_sync_state", "main", {"base": common.copy(), "changes": []})
    app = svc.ensure_application(db, case_id, PERMIT)
    app["form_data"] = dict(FORM_DATA)
    app["status"] = "draft"
    app["updated_at"] = time.time() - 60
    svc.save_artifact(db, case_id, "permit_application", PERMIT, app)
    attachments = _load_uploaded_attachments(db, settings, case_id)
    if stage == 3:
        _seed_step_three(db, case_id, attachments)
    else:
        _seed_step_four(db, case_id, attachments, app)
    svc.event(db, case_id, "demo.ren_gas.seeded", "Deterministic Ren-Gas customer demo seeded", {"stage": stage, "attachment_count": len(attachments)})
    return case_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="", help="Override KEHA_LV_DB_PATH")
    args = parser.parse_args()
    settings = load_settings()
    db = DB(args.db or settings.db_path)
    first = _seed_case(db, settings, 3)
    second = _seed_case(db, settings, 4)
    kevyt = _seed_kevyt_case(db, settings)
    kevyt_package = svc.artifact_json(db, kevyt, "material_package", PERMIT, {}).get("package_id", "")
    print(json.dumps({
        "wizard_3": f"/cases/{first}/lupademo/application/{PERMIT}/wizard/3",
        "wizard_4": f"/cases/{second}/lupademo/application/{PERMIT}/wizard/4",
        "kevyt": f"/applicant/packages/{kevyt_package}",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
