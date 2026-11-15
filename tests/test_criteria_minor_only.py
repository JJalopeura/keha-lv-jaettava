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
    # Only the CURRENT Ren-Gas package is used. The earlier, "complete" Ren-Gas
    # package (46 MB, with melumallinnus, perustilaselvitys and the neighbourhood
    # maps) is retired: the demo material is the 2026-11-04 application package.
    "Ren-Gas": {
        "zip": "testiaineisto/esimerkkihakemukset/Ren-Gas pelkkä hakemus 20261104.zip",
        "form_data": {
            "municipality": "Kerava",
            "applicant_name": "Nordic Ren-Gas Oy",
            "business_id": "2347291-4",  # demo-stand-in
            "activity_name": "Puhtaiden P2X-kaasupolttoaineiden ja CO2-vapaan kaukolämmön yhteistuotantolaitos",
            "permit_basis": "Laitos sijoittuu Alikeravan teollisuuskortteliin Keravan Energian biovoimalaitoksen tontille. Hanke tuottaa uusiutuvaa e-metaania 15 000 t/a ja kaukolämpöä noin 32 MW; YVA-menettely on käyty ja perusteltu päätelmä on liitteenä.",
            "site_location": "Kiinteistö 245-9-348-1, Alikeravan teollisuuskortteli, Kerava; omistaja- ja haltijatiedot erillisellä liitteellä.",
            "zoning_context": "Alue on asemakaavassa teollisuuskorttelialuetta; kaavamerkinnät ja kaavatilanne hakemuksen kohdassa 2.3.",
            "activity_description": "Power-to-Gas-laitos: vedyn tuotanto, hiilidioksidin talteenotto savukaasuista, metaanin tuotanto ja kaukolämmön tuotanto Keravalla.",
            "production_capacity": "Metaania 15 000 t/a, metaanin tuotantoteho 25 MW ja kaukolämpöä noin 32 MW.",
            "byproducts": "Sivutuoteluokitteluа ei haeta; prosessin jätteet ja sivuvirrat kuvataan hakemuksen kohdassa 3.8.",
            "process_description": "Vedyn tuotanto, CO₂:n talteenotto, metaanisynteesi, metaanin syöttö siirtoverkkoon sekä apujäähdytys ja kaukolämmön tuotanto (hakemus 3.3).",
            "risk_preparedness": "Liite 12 Ennaltavarautumissuunnitelma Ren-Gas Kerava; tunnistetut onnettomuus- ja häiriötilanteet hakemuksen kohdassa 5.",
            "land_use_environment": "Alueen itäpuolella valtatie 4, eteläpuolella metsäalue ja Keravanjoki; lähimmät asuinalueet ja herkät kohteet hakemuksen kohdassa 2.4.",
            "land_use_impacts": "YVA-selostuksen mukaiset vaikutukset maankäyttöön ja lähialueen asutukseen (Liite 4 a).",
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
    """The current Ren-Gas application package, ingested via the product's zip path and
    checked as a prepared application: vähäinen findings only. Earlier packages are
    retired and must not be used as fixtures."""

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
