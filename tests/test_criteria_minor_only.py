"""Minor-only criteria gate (vähäinen-policy) for the check pipeline.

Locks the demo promise: a PREPARED application built from the real testiaineisto
material (P2X / Ren-Gas / Sharpcell demo packages — officially approved applications)
must pass the AI application check with ZERO blocking/major findings, and every
remaining finding must carry non-empty, actionable Finnish suggestion text.

Empty wizard drafts still trip blocking/major via deterministic rules — that is the
teaching moment and stays untouched. This gate pins the prepared-application path.

Stdlib unittest on purpose: no pytest dependency.
    .venv/bin/python -m unittest tests.test_criteria_minor_only
"""

import atexit
import sys
import os
import shutil
import tempfile
import unittest
from pathlib import Path

_tmp_db_dir = tempfile.mkdtemp(prefix="keha-minor-")
_tmp_uploads_dir = tempfile.mkdtemp(prefix="keha-minor-uploads-")
# Self-clean: each run otherwise leaks ~400M of ingested package uploads in /tmp.
atexit.register(shutil.rmtree, _tmp_db_dir, True)
atexit.register(shutil.rmtree, _tmp_uploads_dir, True)
os.environ["KEHA_LV_DB_PATH"] = os.path.join(_tmp_db_dir, "test.db")
os.environ["KEHA_LV_UPLOADS_DIR"] = _tmp_uploads_dir

import app.domains.lupademo.services as svc  # noqa: E402
from app.domains.lupademo.routes import create_lupademo_router  # noqa: E402
from app.domains.lupademo.scenarios import create_case_from_scenario  # noqa: E402
from app.infra.config import load_settings  # noqa: E402
from app.infra.db import DB  # noqa: E402
from app.infra.session import DemoSessionMiddleware  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_llm import install_fake_llm  # noqa: E402

install_fake_llm()


_settings = load_settings()
_db = DB(_settings.db_path)

_app = FastAPI(title="KEHA LV AI-demo (minor-only gate)")
_app.add_middleware(
    DemoSessionMiddleware,
    secret_key=_settings.session_secret,
    session_cookie="lv_session",
    same_site="lax",
)
_app.include_router(create_lupademo_router(lambda: _db, lambda: _settings))

MUNICIPALITY = "Vantaa"
GENERIC_FIX = "Täydennä tieto tai lisää vaatimus täyttävä viranomaisliite."

# Real-package ground truth (testiaineisto/*.md — produced by the approved criteria
# rework goal). The packages are ingested with their REAL bytes via the product's
# zip-ingestion path; form values derive from the package documents.
REAL_PACKAGES = {
    "P2X": {
        "zip": "testiaineisto/esimerkkihakemukset/P2X.zip",
        "form_data": {
            "municipality": "Joensuu",
            "applicant_name": "P2X Solutions Oy",
            "business_id": "2483724-7",  # demo-stand-in (Y-tunnusta ei julkaista md:ssä)
            "activity_name": "Power-to-X-laitos (CO₂-talteenotto ja metanolisynteesi)",
            "permit_basis": "Ympäristönsuojelulain 27 § mukainen luvanvarainen toiminta (ISAVI-12315-2024).",
            "competent_authority": "Etelä-Suomen aluehallintovirasto",
            "site_location": "Joensuu, teollisuusalue (toiminnansijaintikartta liitteenä).",
            "zoning_context": "Teollisuus- ja varastoaluetta; kaavaote (Liite 6 Asemakaavaote) liitteenä.",
            "sensitive_receptors": "Lähimmät asuinalueet etäisyydellä; luonnonyleiskuvaus liitteenä (Karttakuva 721).",
            "activity_description": "CO₂:n talteenotto ja metanolisynteesi Power-to-X-laitoksella Joensuussa; vihreän siirtymän etusijamenettely.",
            "production_capacity": "Metanolituotanto laitoksen mitoituksen mukaisesti (ks. hakemus).",
            "process_description": "CO₂-talteenotto, elektrolyysi ja metanolisynteesi (Liite 1 Hiilidioksidin talteenotto).",
            "raw_materials_chemicals": "Kemikaalit ja SDS-turvatiedotteet liitteenä (8 safety data sheet -liitettä hyväksyttiin).",
            "water_and_wastewater": "Jätevedet kunnalliseen verkostoon esikäsittelyn jälkeen.",
            "air_emissions": "Päästöt ja ilmapäästöselvitys hakemusasiakirjojen mukaan.",
            "waste": "Jätteiden käsittely kuvattu hakemuksessa (erillistä jateliitettä ei ole — demo-käytäntö).",
            "environmental_impacts": "Merkittävimmät vaikutukset meluun ja liikenteeseen; kokonaisvaikutus vähäinen.",
            "risk_preparedness": "Ennaltavarautumissuunnitelma hakemusasiakirjojen mukaan.",
            "bat_assessment": "BAT-arviointi hakemusasiakirjojen mukaan.",
            "monitoring_plan": "Tarkkailu- ja raportointisuunnitelma hakemusasiakirjojen mukaan.",
        },
    },
    "Ren-Gas": {
        "zip": "testiaineisto/esimerkkihakemukset/Ren-Gas.zip",
        "form_data": {
            "municipality": "Kerava",
            "applicant_name": "Ren-Gas Oy",
            "business_id": "2347291-4",  # demo-stand-in
            "activity_name": "Biokaasu- ja energiantuotantolaitos (ESAVI-14858-2025)",
            "permit_basis": "Ympäristönsuojelulain 27 § mukainen luvanvaraisuus hakemuksessa; aiemmat AVI-päätökset 2019/2020 liitteinä.",
            "competent_authority": "Etelä-Suomen aluehallintovirasto",
            "site_location": "Kerava; sijainti kuvattu hakemuksessa ja YVA-selostuksessa (ei erillistä sijaintikarttaa — ⚠️ yellow ground truth).",
            "zoning_context": "Kaavoitus ja maankäyttö kuvattu hakemuksessa.",
            "sensitive_receptors": "Keravanjoki (Liite 10 pohjaeliöstöarvio); erillistä Natura-arviota ei ole (⚠️ yellow).",
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
            "monitoring_plan": "Vesipuolen tarkkailu Liite 10; erillistä ilman tarkkailuohjelmaa ei (❌ ground truth → advisory).",
        },
    },
    "Sharpcell": {
        "zip": "testiaineisto/esimerkkihakemukset/Sharpcell.zip",
        "form_data": {
            "municipality": "Valkeakoski",
            "applicant_name": "SharpCell Oy",
            "business_id": "3102847-6",  # demo-stand-in
            "activity_name": "Paperitehtaan jätevesien käsittely, Projekti Aurora (LSSAVI-8122-2025)",
            "permit_basis": "Ympäristönsuojelulain mukainen luvanvarainen toiminta; LSSAVI-käsittely.",
            "competent_authority": "Länsi- ja Sisä-Suomen aluehallintovirasto",
            "site_location": "UPM Tervasaaren tontti, Valkeakoski.",
            "zoning_context": "Teollisuusympäristö, Valkeakoski.",
            "sensitive_receptors": "Vesistö ja lähialueen käyttäjät kuvattuna hakemuksessa.",
            "activity_description": "Paperitehtaalle kohdistuvat jätevedet ja niihin liittyvä toiminta (Projekti Aurora).",
            "production_capacity": "Tuotanto hakemusasiakirjojen mukaan.",
            "process_description": "Jätevesien käsittelyprosessi hakemusasiakirjojen mukaan.",
            "raw_materials_chemicals": "Kemikaaliluettelo Kemikaaliluettelo_www.xlsx (Excel-käytäntö, ei SDS-tiedotteita).",
            "water_and_wastewater": "Jätevesien käsittely on hankkeen ydinaihe; kuvaus hakemuksessa.",
            "air_emissions": "Ilmapäästöt kuvattu hakemuksessa; erillistä savukaasumallinnusta ei (❌ ground truth → advisory).",
            "waste": "Jätteiden käsittely kuvattu hakemuksessa.",
            "environmental_impacts": "Ympäristövaikutukset hakemusasiakirjojen mukaan.",
            "risk_preparedness": "Varautumissuunnitelma hakemusasiakirjojen mukaan.",
            "bat_assessment": "BAT-arviota ei ole liitteenä (❌ ground truth → advisory); paras käytettävissä oleva tekniikka käsitelty hakemustekstissä.",
            "monitoring_plan": "Tarkkailusuunnitelma 4_2025 painottuu tehtaan tarkkailuun (⚠️ yellow).",
        },
    },
}

# Prepared-application fixtures per permit type: complete required form data
# (values mirror the real testiaineisto demo packages) + every required
# attachment present as official_submission. Filenames hit the deterministic
# classifier needles so primary_type maps without an LLM call.
PREPARED_FORM_DATA = {
    "building_residential": {
        "municipality": MUNICIPALITY,
        "applicant_name": "P2X Vantaa Oy",
        "business_id": "2483724-7",
        "contact_person": "Päivi Rask",
        "email": "paivi.rask@p2xdemo.fi",
        "address": "Rakentajantie 12, 01510 Vantaa",
        "property_id": "092-4031-0034-0018",
        "intended_use": "Asuinrakennus",
        "project_description": "Uuden kaksiohtaisen asuinrakennuksen (P2X-kotikonsepti) rakentaminen Vantaan Koivukylään.",
        "floor_area_m2": "145",
        "building_height_m": "8.5",
        "distance_to_boundary_m": "4.5",
        "wastewater_handling": "Kiinteistö liittyy Vantaan Vesian jätevesiverkostoon.",
        "stormwater_management": "Hulevedet johdetaan viivästysaltaan kautta sadevesiverkostoon.",
        "designer": "Arkkitehti SVI, Kortex Arkkitehdit Oy",
    },
    "event_public": {
        "municipality": MUNICIPALITY,
        "applicant_name": "Sharpcell Events Oy",
        "business_id": "3102847-6",
        "event_name": "Koivutien korttelijuhla",
        "event_description": "Kaksipäiväinen yleisötapahtuma paikallisille toimijoille; esiintyjiä, ruokatarjontaa ja musisointia.",
        "event_location": "Koivutienpuisto, Vantaa",
        "expected_attendees": "250",
        "landowner_permission": "Alueen haltijan (Vantaan kaupunki) kirjallinen lupa liitteenä.",
        "rescue_plan": "Pelastussuunnitelma: kaksi pelastusreittiä, sammutusvälineet ja ensiapupiste.",
        "traffic_arrangements": "Tilapäinen pysäköintijärjestely ja jalankulkureittien opastus.",
        "waste_management": "Kierrätyspisteet kahdessa kohteessa, jätehuolto hoidetaan sopimusyrityksellä.",
    },
}

PREPARED_ATTACHMENTS = {
    "building_residential": ["asemapiirros_aluerakennus.pdf", "paapiirustukset_rymitys.pdf", "julkisivupiirustukset_etela.pdf"],
    "event_public": ["maanomistajan_lupa_kaupunki.pdf", "pelastussuunnitelma_koivutie.pdf"],
}

# Real packages cover environmental_industrial; scenario fixtures cover building/event.
SCENARIO_BY_PERMIT = {
    "building_residential": "building_vantaa_residential",
    "event_public": "event_public_vantaa",
}


def _unresolved(report: dict) -> list[dict]:
    return [i for i in report.get("issues", []) if not i.get("resolved")]


def _severity(issue: dict) -> str:
    return str(issue.get("severity") or "")


def _assert_minor_only(self, permit_label: str, report: dict) -> None:
    """Shared gate: zero blocking/major, minor/recommendation severities only,
    non-empty actionable suggestions, no red criteria (yellow = advisory is OK)."""
    unresolved = _unresolved(report)
    offenders = [f"{i.get('id')}:{i.get('severity')}" for i in unresolved if _severity(i) in ("blocking", "major")]
    self.assertEqual(offenders, [], f"{permit_label}: prepared application must not trip blocking/major")
    for issue in unresolved:
        self.assertIn(_severity(issue), {"minor", "recommendation"},
                      f"{permit_label}: issue {issue.get('id')} has severity {_severity(issue)}")
        fix = str(issue.get("suggested_fix") or "").strip()
        self.assertTrue(fix, f"{permit_label}: issue {issue.get('id')} lacks suggested_fix")
        self.assertGreater(len(fix), 8, f"{permit_label}: issue {issue.get('id')} suggestion is not actionable: {fix!r}")
    criteria = report.get("criteria_results", [])
    self.assertTrue(criteria, f"{permit_label}: criteria_results must be populated")
    reds = [c.get("criteria_id") for c in criteria if c.get("status") == "red"]
    self.assertEqual(reds, [], f"{permit_label}: prepared application must have no red criteria")


class RealPackageMinorOnlyTest(unittest.TestCase):
    """The three REAL testiaineisto packages, ingested via the product's zip path,
    checked as prepared applications: vähäinen findings only."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.reports: dict[str, dict] = {}
        for name, spec in REAL_PACKAGES.items():
            zip_path = Path(spec["zip"])
            case_id = create_case_from_scenario(_db, "environmental_industrial_generic")["case_id"]
            svc.ingest_application_zip(
                _db, _settings, case_id, "environmental_industrial",
                zip_path.name, zip_path.read_bytes(),
            )
            svc.update_application_data(_db, case_id, "environmental_industrial", spec["form_data"])
            response = svc.run_application_check(_db, case_id, "environmental_industrial", _settings)
            report = svc.artifact_json(_db, case_id, "check_report", "environmental_industrial", {})
            cls.reports[name] = report or response.__dict__

    def test_real_packages_zero_blocking_or_major(self) -> None:
        for name, report in self.reports.items():
            _assert_minor_only(self, f"real:{name}", report)

    def test_criteria_rule_findings_carry_rule_instruction(self) -> None:
        for name, report in self.reports.items():
            criteria_findings = [i for i in _unresolved(report) if str(i.get("id", "")).startswith("criteria_")]
            for issue in criteria_findings:
                self.assertNotEqual(str(issue.get("suggested_fix")), GENERIC_FIX,
                                    f"real:{name}: {issue.get('id')} carries boilerplate instead of its rule instruction")
            # Pairwise-distinct suggested_fix among criteria findings (if ≥2 exist).
            # Deduplicate by criteria_ prefix first — deterministic_issues and
            # reconcile_verdict may both emit the same criteria_id (pre-existing).
            if len(criteria_findings) >= 2:
                seen = {}
                for f in criteria_findings:
                    cid = str(f.get("id", "")).split("_", 1)[-1] if str(f.get("id", "")).startswith("criteria_") else f.get("id")
                    seen.setdefault(cid, []).append(str(f.get("suggested_fix")))
                # Each criterion's instruction must be unique among criteria.
                fixes = [vals[0] for vals in seen.values()]
                self.assertEqual(len(set(fixes)), len(fixes),
                                 f"real:{name}: criteria findings must carry pairwise-distinct suggested_fix — duplicates: {fixes}")

    def test_real_packages_readiness(self) -> None:
        for name, report in self.reports.items():
            self.assertEqual(report.get("counts", {}).get("blocking"), 0, f"real:{name}: no blocking counts")
            self.assertIn(report.get("readiness"), {"ready", "almost_ready"},
                          f"real:{name}: advisory-only package must not be not_ready")


class CriteriaMinorOnlyTest(unittest.TestCase):
    """Per prepared scenario package (building/event; real packages cover env above):
    check must emit vähäinen/suositus findings only."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.reports: dict[str, dict] = {}
        for permit_type, scenario_id in SCENARIO_BY_PERMIT.items():
            case_id = create_case_from_scenario(_db, scenario_id)["case_id"]
            svc.update_application_data(_db, case_id, permit_type, PREPARED_FORM_DATA[permit_type])
            for filename in PREPARED_ATTACHMENTS[permit_type]:
                svc.add_attachment(_db, case_id, permit_type, filename, 2048, "official_submission")
            response = svc.run_application_check(_db, case_id, permit_type, _settings)
            report = svc.artifact_json(_db, case_id, "check_report", permit_type, {})
            cls.reports[permit_type] = report or response.__dict__

    def test_zero_blocking_or_major(self) -> None:
        for permit_type, report in self.reports.items():
            _assert_minor_only(self, permit_type, report)

    def test_suggestions_are_distinctive(self) -> None:
        """Criteria-rule findings must carry their own instruction text, not one
        shared boilerplate string — the 'clever vähäinen suggestions' promise."""
        for permit_type, report in self.reports.items():
            criteria_findings = [i for i in _unresolved(report) if str(i.get("id", "")).startswith("criteria_")]
            # Deduplicate by criteria_ prefix to handle pre-existing duplicate
            # emissions from deterministic_issues + reconcile_verdict.
            seen = {}
            for f in criteria_findings:
                cid = str(f.get("id", "")).split("_", 1)[-1] if str(f.get("id", "")).startswith("criteria_") else f.get("id")
                seen.setdefault(cid, []).append(str(f.get("suggested_fix")))
            fixes = [vals[0] for vals in seen.values()]
            if len(fixes) >= 2:
                self.assertGreater(len(set(fixes)), 1,
                                   f"{permit_type}: all criteria findings share one generic suggestion — not actionable")
            for issue in criteria_findings:
                self.assertNotEqual(str(issue.get("suggested_fix")), GENERIC_FIX,
                                    f"{permit_type}: {issue.get('id')} carries the generic boilerplate fix")

    def test_readiness_is_ready(self) -> None:
        for permit_type, report in self.reports.items():
            counts = report.get("counts", {})
            self.assertEqual(counts.get("blocking"), 0, f"{permit_type}: no blocking counts")
            self.assertEqual(report.get("readiness"), "ready", f"{permit_type}: prepared demo application should read as ready")


class CriteriaAdminCoherenceTest(unittest.TestCase):
    """Demo-lab coherence: /admin/criteria renders with check-system severity labels
    and states the minor-only policy for prepared demo applications."""

    def test_admin_criteria_renders_minor_only_policy(self) -> None:
        case_id = create_case_from_scenario(_db, "building_vantaa_residential")["case_id"]
        client = TestClient(_app, follow_redirects=False)
        response = client.get("/admin/criteria")
        self.assertEqual(response.status_code, 200)
        body = response.text
        for expected in ("Vähäinen", "vain vähäisiä havaintoja"):
            self.assertIn(expected, body, f"/admin/criteria must surface {expected!r}")


if __name__ == "__main__":
    unittest.main()
