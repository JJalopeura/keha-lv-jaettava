"""End-to-end user journey tests for the applicant wizard and authority workflow.

Complements test_smoke.py (which focuses on agent/API contracts). These tests
walk the full user paths through the HTTP layer:

- Applicant: create project -> save common data (step 1) -> upload material
  (step 2) -> run check (step 4) -> submit (step 5) -> receipt (step 6).
- Authority: queue -> detail -> update observation status -> update status.

Stdlib unittest on purpose: no pytest dependency.
    .venv/bin/python -m unittest discover tests
"""

import io
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root

os.environ["KEHA_LV_FORCE_MOCK_LLM"] = "true"

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.domains.lupademo.services as svc  # noqa: E402
from app.domains.lupademo.routes import create_lupademo_router  # noqa: E402
from app.infra.session import DemoSessionMiddleware  # noqa: E402
from app.infra.config import load_settings  # noqa: E402
from app.infra.db import DB  # noqa: E402

# Build a fully isolated app + DB so these journey tests never touch the
# shared `app.main` state that test_smoke.py relies on (which asserts exact
# attachment counts on its demo case).
os.environ["KEHA_LV_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="keha-flows-"), "test.db")
os.environ["KEHA_LV_UPLOADS_DIR"] = tempfile.mkdtemp(prefix="keha-flows-uploads-")
_flow_settings = load_settings()
Path(_flow_settings.uploads_dir).mkdir(parents=True, exist_ok=True)

_flow_db = DB(_flow_settings.db_path)
app = FastAPI(title="KEHA LV AI-demo (flow tests)")
app.add_middleware(
    DemoSessionMiddleware,
    secret_key=_flow_settings.session_secret,
    session_cookie="lv_session",
    same_site="lax",
)
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parents[1] / "app" / "domains" / "lupademo" / "static")),
    name="static",
)
app.include_router(create_lupademo_router(lambda: _flow_db, lambda: _flow_settings))

PERMIT = "environmental_industrial"
SAMPLE = "Biokaasulaitos Vantaalla, kasittelymaara 18000 t/a, toiminnanharjoittaja Oy Testi Ab."

client = TestClient(app, follow_redirects=False)
db: DB
case: str
base: str


def setUpModule() -> None:
    """Create a fresh, dedicated case for the journey tests."""
    global db, case, base
    db = _flow_db
    case = db.create_case("Flow journey test", language="fi")
    svc.ensure_seed_state(db, case)
    base = f"/cases/{case}/lupademo/application/{PERMIT}"


class ApplicantJourney(unittest.TestCase):
    """Full applicant path: common data -> materials -> check -> submit -> receipt."""

    def setUp(self) -> None:
        client.post("/logout")

    def test_building_check_uses_target_municipality_criteria(self) -> None:
        """A municipality-specific rule changes the deterministic check result."""
        form_data = {"municipality": "Vantaa"}
        vantaa_issues = svc.deterministic_issues("building_residential", form_data, [], municipality="Vantaa")
        kerava_issues = svc.deterministic_issues("building_residential", form_data, [], municipality="Kerava")

        self.assertNotEqual(
            svc.effective_criteria(db, case, "building_residential", "Vantaa").get("version"),
            svc.effective_criteria(db, case, "building_residential", "Kerava").get("version"),
        )
        self.assertNotIn("criteria_required_energy_report", {issue.id for issue in vantaa_issues})
        self.assertIn("criteria_required_energy_report", {issue.id for issue in kerava_issues})
        self.assertEqual(
            len([issue for issue in vantaa_issues if issue.field_id == "stormwater_management"]),
            1,
        )

    def test_environmental_criteria_stay_global(self) -> None:
        """Environmental applications use one national set for every municipality."""
        environmental_case = db.create_case("Global environmental criteria", language="fi")
        svc.ensure_seed_state(db, environmental_case)
        svc.save_artifact(
            db,
            environmental_case,
            "criteria_set",
            "environmental_industrial:vantaa",
            {"municipality": "Vantaa", "criteria_set_id": "city_only", "version": "city-only"},
        )

        vantaa = svc.effective_criteria(db, environmental_case, PERMIT, "Vantaa")
        kerava = svc.effective_criteria(db, environmental_case, PERMIT, "Kerava")

        self.assertEqual(vantaa["criteria_set_id"], "national_environmental_industrial_demo_v1")
        self.assertEqual(vantaa["criteria_set_id"], kerava["criteria_set_id"])
        self.assertEqual(vantaa["municipality"], "")
        svc.create_city_criteria(db, environmental_case, "Espoo")
        self.assertEqual(
            svc.artifact_json(db, environmental_case, "criteria_set", "environmental_industrial:vantaa", {}),
            {"municipality": "Vantaa", "criteria_set_id": "city_only", "version": "city-only"},
        )
        self.assertEqual(
            svc.artifact_json(db, environmental_case, "criteria_set", "environmental_industrial:espoo", {}),
            {},
        )

    def test_wizard_displays_active_municipality_criteria(self) -> None:
        response = client.get(f"{base}/wizard/4")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Tarkistuksessa käytetty kriteeristö", response.text)
        self.assertIn("Kansallinen peruskriteeristö · 2026-08-demo-2", response.text)

    def test_idle_check_action_stays_in_ai_panel(self) -> None:
        fresh_case = db.create_case("Idle check action", language="fi")
        svc.ensure_seed_state(db, fresh_case)
        fresh_base = f"/cases/{fresh_case}/lupademo/application/building_residential"
        response = client.get(f"{fresh_base}/wizard/4")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.count(f'action="{fresh_base}/ai/check"'), 1)
        self.assertIn("Tekoälytoiminnot", response.text)
        self.assertNotIn("analysis-panel", response.text)

    def test_application_check_persists_target_municipality_criteria_version(self) -> None:
        kerava_case = db.create_case("Kerava criteria check", language="fi")
        svc.ensure_seed_state(db, kerava_case)
        svc.update_application_data(db, kerava_case, "building_residential", {"municipality": "Kerava"})

        response = svc.run_application_check(db, kerava_case, "building_residential", settings=_flow_settings)
        report = svc.artifact_json(db, kerava_case, "check_report", "building_residential", {})

        self.assertEqual(response.criteria_version, "2026-08-demo-1")
        self.assertEqual(report["criteria_version"], "2026-08-demo-1")

    def test_active_criteria_edit_changes_deterministic_findings(self) -> None:
        criteria = svc.effective_criteria(db, case, "building_residential", "Kerava")
        criteria["rules"] = [{
            "id": "custom_noise_context",
            "field_id": "noise_context",
            "severity": "blocking",
            "instruction": "Kunnan oma hankekuvausvaatimus.",
        }]
        svc.save_criteria_version(db, case, "building_residential", criteria, municipality="Kerava")

        issues = svc.deterministic_issues(
            "building_residential",
            {"municipality": "Kerava"},
            [],
            municipality="Kerava",
            criteria=svc.effective_criteria(db, case, "building_residential", "Kerava"),
        )

        self.assertIn("criteria_custom_noise_context", {issue.id for issue in issues})

    def test_conditional_environmental_criteria_waits_for_trigger(self) -> None:
        # Kriteeristön uudistuksen (2026-08) jälkeen ehdollisuus on kemikaalisäännössä:
        # chemical_inventory laukeaa vain, jos raaka-ainekenttä mainitsee kemikaalit.
        quiet_form = {"environmental_impacts": "Vaikutukset arvioitu.", "raw_materials_chemicals": "Vesi ja biomassa."}
        triggered_form = {
            "environmental_impacts": "Toiminnasta syntyy merkittävää melua.",
            "raw_materials_chemicals": "Kemikaalien varastointi: prosessikemikaalit.",
        }

        quiet_ids = {issue.id for issue in svc.deterministic_issues("environmental_industrial", quiet_form, [])}
        triggered_ids = {issue.id for issue in svc.deterministic_issues("environmental_industrial", triggered_form, [])}

        self.assertNotIn("criteria_chemical_inventory", quiet_ids)
        self.assertIn("criteria_chemical_inventory", triggered_ids)

    def test_criteria_edit_advances_version_and_deduplicates_shared_subject(self) -> None:
        criteria = svc.effective_criteria(db, case, "environmental_industrial")
        criteria["rules"] = [
            {"id": "first", "field_id": "yva_context", "attachment_type": "yva_statement", "severity": "major"},
            {"id": "second", "field_id": "yva_context", "attachment_type": "yva_statement", "severity": "minor"},
        ]
        original_version = criteria["version"]
        saved = svc.save_criteria_version(db, case, "environmental_industrial", criteria)
        issues = svc.deterministic_issues(
            "environmental_industrial",
            {"yva_context": ""},
            [],
            criteria=svc.effective_criteria(db, case, "environmental_industrial"),
        )

        self.assertNotEqual(saved["version"], original_version)
        self.assertEqual(
            len([issue for issue in issues if issue.field_id == "yva_context" and issue.attachment_id == "yva_statement"]),
            1,
        )

    def test_stricter_criteria_rule_wins_for_shared_subject(self) -> None:
        issues = svc.deterministic_issues(
            "building_residential",
            {"project_description": ""},
            [],
            criteria={
                "criteria_set_id": "severity-test",
                "sources": ["Severity test"],
                "rules": [
                    {"id": "minor_description", "field_id": "noise_context", "severity": "minor"},
                    {"id": "blocking_description", "field_id": "noise_context", "severity": "blocking"},
                ],
            },
        )

        matching = [issue for issue in issues if issue.field_id == "noise_context"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].severity, "blocking")

    def test_criteria_rules_keep_distinct_attachment_subjects(self) -> None:
        issues = svc.deterministic_issues(
            "building_residential",
            {"noise_context": ""},
            [],
            criteria={
                "criteria_set_id": "attachment-test",
                "sources": ["Attachment test"],
                "rules": [
                    {"id": "noise_plan", "field_id": "noise_context", "attachment_type": "noise_model", "severity": "minor"},
                    {"id": "traffic_plan", "field_id": "noise_context", "attachment_type": "traffic_plan", "severity": "blocking"},
                ],
            },
        )

        self.assertEqual(
            {issue.attachment_id for issue in issues if issue.attachment_id in {"noise_model", "traffic_plan"}},
            {"noise_model", "traffic_plan"},
        )

    def test_combined_criteria_rule_does_not_duplicate_baseline_subjects(self) -> None:
        issues = svc.deterministic_issues(
            "environmental_industrial",
            {},
            [],
            criteria={
                "criteria_set_id": "combined-test",
                "sources": ["Combined test"],
                "rules": [{"id": "bat", "field_id": "bat_assessment", "attachment_type": "bat_assessment", "severity": "major"}],
            },
        )

        matching = [issue for issue in issues if issue.field_id == "bat_assessment" or issue.attachment_id == "bat_assessment"]
        self.assertEqual(len(matching), 2)
        self.assertEqual({issue.id for issue in matching}, {"missing_bat_assessment", "criteria_bat"})
        self.assertEqual(len([issue for issue in matching if issue.field_id == "bat_assessment"]), 1)

    def test_package_analysis_uses_edited_municipality_criteria(self) -> None:
        kerava_case = db.create_case("Kerava package criteria", language="fi")
        svc.ensure_seed_state(db, kerava_case)
        svc.update_application_data(db, kerava_case, "building_residential", {"municipality": "Kerava"})
        criteria = svc.effective_criteria(db, kerava_case, "building_residential", "Kerava")
        criteria["rules"] = [{
            "id": "package_noise_context",
            "field_id": "noise_context",
            "severity": "blocking",
            "instruction": "Pakettianalyysin kuntavaatimus.",
        }]
        svc.save_criteria_version(db, kerava_case, "building_residential", criteria, municipality="Kerava")

        result = svc.package_analysis_from_current_state(db, kerava_case, "building_residential")

        self.assertIn("criteria_package_noise_context", {finding["id"] for finding in result["findings"]})

    def test_package_analysis_attachment_only_skips_wizard_field_validation(self) -> None:
        package_case = db.create_case("Attachment-only package analysis", language="fi")
        svc.ensure_seed_state(db, package_case)
        svc.update_application_data(db, package_case, "environmental_industrial", {"municipality": "Vantaa"})

        result = svc.package_analysis_from_current_state(
            db,
            package_case,
            "environmental_industrial",
            attachment_only=True,
        )

        self.assertFalse(any(finding.get("field_id") == "business_id" for finding in result["findings"]))
        self.assertTrue(all(finding.get("attachment_id") for finding in result["findings"]))

    def test_unknown_municipality_fallback_does_not_block_city_creation(self) -> None:
        city_case = db.create_case("Unknown municipality fallback", language="fi")
        svc.ensure_seed_state(db, city_case)

        fallback = svc.effective_criteria(db, city_case, "building_residential", "Espoo")
        created = svc.create_city_criteria(db, city_case, "Espoo")
        explicit = svc.effective_criteria(db, city_case, "building_residential", "Espoo")

        self.assertEqual(fallback["municipality"], "Vantaa")
        self.assertEqual(created["municipality"], "Espoo")
        self.assertEqual(explicit["municipality"], "Espoo")
        self.assertEqual(explicit["criteria_set_id"], "espoo_building_residential_custom")

    def test_city_scoped_artifact_with_wrong_identity_is_not_active(self) -> None:
        identity_case = db.create_case("Wrong municipality identity", language="fi")
        svc.ensure_seed_state(db, identity_case)
        svc.save_artifact(
            db,
            identity_case,
            "criteria_set",
            "building_residential:espoo",
            {"municipality": "Vantaa", "criteria_set_id": "vantaa_building_residential_demo_v1", "version": "same"},
        )

        active = svc.effective_criteria(db, identity_case, "building_residential", "Espoo")

        self.assertEqual(active["municipality"], "Vantaa")
        self.assertNotEqual(active.get("version"), "same")

    def test_known_city_creation_uses_city_specific_seed(self) -> None:
        city_case = db.create_case("Known municipality seed", language="fi")
        svc.ensure_seed_state(db, city_case)

        created = svc.create_city_criteria(db, city_case, "Kerava")
        explicit = svc.effective_criteria(db, city_case, "building_residential", "Kerava")

        self.assertEqual(created["municipality"], "Kerava")
        self.assertEqual(explicit["criteria_set_id"], "kerava_building_residential_demo_v1")
        self.assertIn("required_energy_report", {rule["id"] for rule in explicit["rules"]})

    def test_criteria_rule_edit_can_clear_field_and_attachment_targets(self) -> None:
        rule_case = db.create_case("Criteria rule clearing", language="fi")
        svc.ensure_seed_state(db, rule_case)
        criteria = svc.effective_criteria(db, rule_case, "building_residential", "Kerava")
        criteria["rules"] = [{"id": "clearable", "field_id": "noise_context", "attachment_type": "noise_model", "severity": "major"}]
        svc.save_artifact(db, rule_case, "criteria_set", "building_residential:kerava", criteria)

        updated = svc.upsert_criteria_rule(
            db,
            rule_case,
            "building_residential",
            {"id": "clearable", "field_id": "", "attachment_type": "", "severity": "minor", "instruction": "Yleinen ohje."},
            municipality="Kerava",
        )
        rule = next(item for item in updated["rules"] if item["id"] == "clearable")

        self.assertNotIn("field_id", rule)
        self.assertNotIn("attachment_type", rule)

    def test_criteria_test_results_are_scoped_to_municipality(self) -> None:
        result_case = db.create_case("Criteria test result scope", language="fi")
        svc.ensure_seed_state(db, result_case)

        svc.run_criteria_test(db, _flow_settings, result_case, "building_residential", municipality="Vantaa")
        svc.run_criteria_test(db, _flow_settings, result_case, "building_residential", municipality="Kerava")

        vantaa = svc.artifact_json(db, result_case, "criteria_test_result", "building_residential:vantaa", {})
        kerava = svc.artifact_json(db, result_case, "criteria_test_result", "building_residential:kerava", {})
        self.assertEqual(vantaa["municipality"], "Vantaa")
        self.assertEqual(kerava["municipality"], "Kerava")

    def test_package_analysis_records_criteria_provenance_and_staleness(self) -> None:
        package_case = db.create_case("Package criteria provenance", language="fi")
        svc.ensure_seed_state(db, package_case)
        svc.update_application_data(db, package_case, "building_residential", {"municipality": "Kerava"})

        result = svc.package_analysis_from_current_state(db, package_case, "building_residential")
        stored = svc.artifact_json(db, package_case, "package_analysis_result", "building_residential", {})
        self.assertEqual(stored["criteria_municipality"], "Kerava")
        self.assertEqual(stored["criteria_version"], "2026-08-demo-1")

        criteria = svc.effective_criteria(db, package_case, "building_residential", "Kerava")
        criteria["rules"] = []
        svc.save_criteria_version(db, package_case, "building_residential", criteria, municipality="Kerava")
        updated = svc.effective_criteria(db, package_case, "building_residential", "Kerava")

        self.assertNotEqual(updated["version"], result["criteria_version"])

    def test_package_analysis_fallback_criteria_is_not_falsely_stale(self) -> None:
        fallback_case = db.create_case("Fallback package freshness", language="fi")
        svc.ensure_seed_state(db, fallback_case)
        svc.update_application_data(db, fallback_case, "building_residential", {"municipality": "Espoo"})

        svc.package_analysis_from_current_state(db, fallback_case, "building_residential")
        context = svc.artifact_json(db, fallback_case, "package_analysis_result", "building_residential", {})
        active = svc.effective_criteria(db, fallback_case, "building_residential", "Espoo")

        self.assertEqual(context["criteria_municipality"], active["municipality"])
        self.assertEqual(context["criteria_version"], active["version"])

    def test_national_package_analysis_is_not_stale_for_request_municipality(self) -> None:
        fallback_case = db.create_case("National criteria package freshness", language="fi")
        svc.ensure_seed_state(db, fallback_case)
        svc.update_application_data(db, fallback_case, "environmental_industrial", {"municipality": "Vantaa"})

        result = svc.package_analysis_from_current_state(db, fallback_case, "environmental_industrial")
        active = svc.effective_criteria(db, fallback_case, "environmental_industrial", "Vantaa")

        self.assertEqual(result["criteria_set_id"], active["criteria_set_id"])
        self.assertEqual(result["criteria_version"], active["version"])
        self.assertFalse(svc.is_package_analysis_stale(result, active))

    def test_package_analysis_stale_when_criteria_set_identity_changes(self) -> None:
        identity_case = db.create_case("Criteria identity freshness", language="fi")
        svc.ensure_seed_state(db, identity_case)
        svc.update_application_data(db, identity_case, "building_residential", {"municipality": "Kerava"})

        result = svc.package_analysis_from_current_state(db, identity_case, "building_residential")
        original = svc.artifact_json(db, identity_case, "package_analysis_result", "building_residential", {})
        active = svc.effective_criteria(db, identity_case, "building_residential", "Kerava")
        active["criteria_set_id"] = "replacement-criteria-set"
        active["version"] = result["criteria_version"]
        svc.save_artifact(db, identity_case, "criteria_set", "building_residential:kerava", active)
        current = svc.effective_criteria(db, identity_case, "building_residential", "Kerava")

        self.assertEqual(original["criteria_version"], current["version"])
        self.assertNotEqual(original["criteria_set_id"], current["criteria_set_id"])

    def test_precheck_gate_stale_when_criteria_set_identity_changes(self) -> None:
        gate_case = db.create_case("Precheck criteria identity", language="fi")
        svc.ensure_seed_state(db, gate_case)
        svc.update_application_data(db, gate_case, "building_residential", {"municipality": "Kerava"})
        svc.run_application_check(db, gate_case, "building_residential", settings=_flow_settings)

        active = svc.effective_criteria(db, gate_case, "building_residential", "Kerava")
        active["criteria_set_id"] = "replacement-precheck-set"
        svc.save_artifact(db, gate_case, "criteria_set", "building_residential:kerava", active)
        gate = svc.precheck_gate_status(db, gate_case, "building_residential")

        self.assertFalse(gate["is_fresh"])
        self.assertIn("criteria_changed", gate["stale_reasons"])

    def test_internal_field_suggestion_uses_edited_municipality_criteria(self) -> None:
        suggestion_case = db.create_case("Field suggestion criteria provenance", language="fi")
        svc.ensure_seed_state(db, suggestion_case)
        svc.update_application_data(db, suggestion_case, "building_residential", {"municipality": "Kerava"})
        active = svc.effective_criteria(db, suggestion_case, "building_residential", "Kerava")
        active["version"] = "kerava-edited-suggestion"
        svc.save_artifact(db, suggestion_case, "criteria_set", "building_residential:kerava", active)

        suggestion = svc.create_field_suggestion(db, suggestion_case, "building_residential", "project_description")

        self.assertEqual(suggestion.criteria_version, "kerava-edited-suggestion")
        self.assertEqual(suggestion.evidence_refs[0].source_id, "kerava_building_residential_demo_v1")

    def test_municipality_fallback_suggestion_text_uses_target_city(self) -> None:
        suggestion = svc.deterministic_suggestion(
            "building_residential",
            "project_description",
            {},
            municipality="Kerava",
        )

        self.assertIn("Keravalle", suggestion.suggested_value)
        self.assertNotIn("Vantaalle", suggestion.suggested_value)

    def test_contextual_chat_suggestion_uses_edited_municipality_criteria(self) -> None:
        chat_case = db.create_case("Chat criteria provenance", language="fi")
        svc.ensure_seed_state(db, chat_case)
        svc.update_application_data(db, chat_case, "building_residential", {"municipality": "Kerava"})
        active = svc.effective_criteria(db, chat_case, "building_residential", "Kerava")
        active["version"] = "kerava-edited-chat"
        svc.save_artifact(db, chat_case, "criteria_set", "building_residential:kerava", active)

        svc.contextual_chat_answer(db, chat_case, "building_residential", "Ehdota project_description")
        pending = svc.artifact_json(db, chat_case, "ai_suggestions", "building_residential", {"suggestions": []})

        self.assertEqual(pending["suggestions"][-1]["criteria_version"], "kerava-edited-chat")

    def test_contextual_chat_context_exposes_active_criteria_and_stale_package_state(self) -> None:
        chat_case = db.create_case("Chat active criteria context", language="fi")
        svc.ensure_seed_state(db, chat_case)
        svc.update_application_data(db, chat_case, "building_residential", {"municipality": "Kerava"})
        svc.package_analysis_from_current_state(db, chat_case, "building_residential")
        active = svc.effective_criteria(db, chat_case, "building_residential", "Kerava")
        active["criteria_set_id"] = "chat-replaced-set"
        svc.save_artifact(db, chat_case, "criteria_set", "building_residential:kerava", active)

        turn = svc.contextual_chat_answer(db, chat_case, "building_residential", "Mitä seuraavaksi?")
        stored = svc.artifact_json(db, chat_case, "contextual_chat", "building_residential", {"messages": []})

        self.assertTrue(turn["answer"])
        self.assertEqual(stored["messages"][-1]["id"], turn["id"])

    def test_fresh_package_analysis_remains_in_contextual_chat_context(self) -> None:
        fresh_case = db.create_case("Fresh chat package context", language="fi")
        svc.ensure_seed_state(db, fresh_case)
        svc.update_application_data(db, fresh_case, "building_residential", {"municipality": "Kerava"})
        svc.package_analysis_from_current_state(db, fresh_case, "building_residential")

        context = svc.contextual_chat_context(db, fresh_case, "building_residential")
        active = svc.effective_criteria(db, fresh_case, "building_residential", "Kerava")

        self.assertEqual(context["package_analysis"]["criteria_set_id"], active["criteria_set_id"])
        self.assertEqual(context["package_analysis"]["criteria_version"], active["version"])

    def test_two_entry_paths_are_distinct(self) -> None:
        """Path A (Kevytasiointi) and Path B (wizard) must stay separate.

        Guard against mix-match: the two entry routes render different templates
        and must not be conflated. See AGENTS.md -> 'Two entry paths'.
        """
        login = client.post("/login", data={"customer_id": "p2x"})
        self.assertEqual(login.status_code, 303)

        # Path A: Kevytasiointi entry (materials + shared basics in LV -> Kevytasiointipalvelu)
        workbench_entry = client.get("/applicant/packages/new")
        self.assertEqual(workbench_entry.status_code, 200)
        self.assertIn("Kevyt integraatio", workbench_entry.text)
        self.assertIn("Hankkeen tausta-aineisto", workbench_entry.text)

        # Path B: form-first wizard entry
        wizard_entry = client.get("/projects/new")
        self.assertEqual(wizard_entry.status_code, 200)
        self.assertIn("Hankkeen lähtötiedot", wizard_entry.text)
        self.assertNotIn("Siirry Kevyt integraatioon", wizard_entry.text)

    def test_step1_saves_common_data_and_redirects(self) -> None:
        response = client.post(
            f"{base}/save",
            data={
                "redirect_to": f"{base}/wizard/2",
                "activity_name": "Biokaasulaitos",
                "activity_description": "Biokaasun tuotanto",
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"{base}/wizard/2")

        app_data = svc.artifact_json(db, case, "permit_application", PERMIT, {})
        self.assertEqual(app_data["form_data"].get("activity_name"), "Biokaasulaitos")

    def test_step2_upload_attachment_and_scope(self) -> None:
        upload = client.post(
            f"{base}/attachments/upload",
            files={"file": ("hakemus.txt", io.BytesIO(SAMPLE.encode()), "text/plain")},
            data={"delivery_scope": "official_submission"},
        )
        self.assertEqual(upload.status_code, 303)

        attachments = svc.list_attachments(db, case, PERMIT)
        self.assertEqual(len(attachments), 1)
        attachment_id = attachments[0]["id"]

        # Re-scope to AI-context-only and back
        rescope = client.post(
            f"{base}/attachments/{attachment_id}/scope",
            data={"delivery_scope": "ai_context_only"},
        )
        self.assertEqual(rescope.status_code, 303)
        self.assertEqual(svc.list_attachments(db, case, PERMIT)[0]["delivery_scope"], "ai_context_only")

    def test_step4_check_runs_and_writes_report(self) -> None:
        response = client.post(f"{base}/ai/check", data={})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"{base}/wizard/4#check")

        report = svc.artifact_json(db, case, "check_report", PERMIT, {})
        self.assertIsInstance(report.get("issues"), list)

    def test_submit_is_blocked_by_blocking_issues(self) -> None:
        """An incomplete application must be blocked at the submit gate."""
        response = client.post(f"{base}/submit", data={})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"{base}/wizard/4#check-blocking")

    def test_wizard_steps_all_render(self) -> None:
        for step in range(1, 7):
            with self.subTest(step=step):
                response = client.get(f"{base}/wizard/{step}")
                self.assertEqual(response.status_code, 200)
                self.assertIn(f"Vaihe {step} / 6", response.text)

        step_one = client.get(f"{base}/wizard/1")
        self.assertIn("← Hanke", step_one.text)
        self.assertNotIn("Nämä tiedot muodostavat hakemuksen lähtökohdan", step_one.text)
        self.assertNotIn('class="step-guidance"', step_one.text)
        self.assertNotIn("Tarkista Yhteisistä tiedoista tuodut tiedot", step_one.text)
        self.assertNotIn("Muokkaa yhteisiä tietoja LV:ssä", step_one.text)

    def test_send_step_shows_application_values_before_confirmation(self) -> None:
        response = client.get(f"{base}/wizard/5")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Hakemuksen tiedot", response.text)
        self.assertIn("Hakija", response.text)
        self.assertIn("Y-tunnus", response.text)
        self.assertIn("Lähetys", response.text)
        self.assertNotIn("Paketointi", response.text)

    def test_send_step_refreshes_stale_package_after_new_attachment(self) -> None:
        fresh_case = db.create_case("Refresh stale submission package", language="fi")
        svc.ensure_seed_state(db, fresh_case)
        svc.compose_submission_package(db, fresh_case, "building_residential")
        attachment = svc.add_attachment(
            db,
            fresh_case,
            "building_residential",
            "new-plan.pdf",
            4,
            delivery_scope="official_submission",
            text_excerpt="plan",
            settings=_flow_settings,
        )

        response = client.get(f"/cases/{fresh_case}/lupademo/application/building_residential/wizard/5")

        self.assertEqual(response.status_code, 200)
        self.assertIn(attachment["filename"], response.text)

    def test_applicant_precheck_copy_uses_canonical_terms(self) -> None:
        response = client.get(f"{base}/wizard/4")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Estävät puutteet", response.text)
        self.assertNotIn("blocking-puutteet", response.text)


class AuthorityJourney(unittest.TestCase):
    """Caseworker path: queue -> detail -> observations -> status update."""

    def _seed_queue_item(self) -> None:
        """Create a submitted application so the authority queue has an item."""
        queue = svc.artifact_json(db, case, "authority_queue", "main", {"items": []})
        queue.setdefault("items", []).append(
            {
                "application_id": PERMIT,
                "application_title": "Ympäristölupa / teollinen uusiutuvan energian toiminta",
                "receipt_id": "LV-TEST-000001",
                "applicant_name": "Testi Hakija",
                "municipality": "Vantaa",
                "status": "käsittelyssä",
                "submitted_at": "2026-08-03 10:00:00",
                "quality_score": 75,
                "open_issue_count": 0,
                "info_request_risk": "matala",
                "permit_type": PERMIT,
                "processing_stage": "vastaanotettu",
                "sla_due_at": "2026-09-02",
                "urgency": "normaali",
            }
        )
        svc.save_artifact(db, case, "authority_queue", "main", queue)

    def test_queue_renders_and_contains_case(self) -> None:
        self._seed_queue_item()
        response = client.get("/authority/queue")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Työjono", response.text)
        self.assertIn("Testi Hakija", response.text)

    def test_detail_renders_authority_panels(self) -> None:
        response = client.get(f"/cases/{case}/lupademo/authority/applications/{PERMIT}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("AI-yhteenveto", response.text)
        self.assertIn("Tarkista seuraavaksi", response.text)

    def test_authority_copy_translates_azure_generated_terms(self) -> None:
        svc.save_artifact(
            db,
            case,
            "authority_summary",
            PERMIT,
            {
                "summary_md": "Yhteenveto",
                "key_facts": [],
                "open_questions": ["Puuttuuko Site map -liite tai Zoning extract -liite?"],
                "evidence_refs": [],
            },
        )
        svc.save_artifact(
            db,
            case,
            "additional_info_draft",
            PERMIT,
            {"body_md": "Toimita Site map -liite ja Zoning extract -liite."},
        )

        response = client.get(f"/cases/{case}/lupademo/authority/applications/{PERMIT}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("asemapiirros / sijaintikartta", response.text)
        self.assertIn("kaavaote-liite", response.text)
        self.assertNotIn("Site map", response.text)
        self.assertNotIn("Zoning", response.text)
        visible_text = re.sub(r"<[^>]+>", " ", response.text)
        self.assertNotIn("extract", visible_text)

    def test_stale_precheck_is_visible_to_authority(self) -> None:
        stale_case = db.create_case("Stale authority detail", language="fi")
        svc.ensure_seed_state(db, stale_case)
        svc.update_application_data(db, stale_case, PERMIT, {"municipality": "Vantaa"})
        svc.run_application_check(db, stale_case, PERMIT, settings=None)
        svc.update_application_data(db, stale_case, PERMIT, {"activity_name": "Muokattu tarkistuksen jälkeen"})

        response = client.get(f"/cases/{stale_case}/lupademo/authority/applications/{PERMIT}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Tuore tarkistus puuttuu", response.text)
        self.assertIn("Hakemusta on muokattu tarkistuksen jälkeen.", response.text)
        self.assertNotIn("form_changed_after_check", response.text)

    def test_generate_authority_summary(self) -> None:
        response = client.post(
            f"/cases/{case}/lupademo/authority/applications/{PERMIT}/ai/summary",
            data={},
        )
        self.assertEqual(response.status_code, 303)
        summary = svc.artifact_json(db, case, "authority_summary", PERMIT, {})
        self.assertTrue(summary.get("summary_md"))

    def test_update_authority_status(self) -> None:
        self._seed_queue_item()
        response = client.post(
            f"/cases/{case}/lupademo/authority/applications/{PERMIT}/status",
            data={"status": "in_review"},
        )
        self.assertEqual(response.status_code, 303)

        queue = svc.artifact_json(db, case, "authority_queue", "main", {"items": []})
        item = next(i for i in queue["items"] if i.get("application_id") == PERMIT)
        self.assertEqual(item["status"], "in_review")

    def test_observation_status_update(self) -> None:
        panel = svc.caseworker_observation_panel(db, case, PERMIT)
        observations = panel.get("top_ai_observations") or []
        if not observations:
            self.skipTest("No observations available to update")
        observation_id = observations[0]["id"]

        response = client.post(
            f"/cases/{case}/lupademo/authority/applications/{PERMIT}/observations/{observation_id}/status",
            data={"status": "accepted", "note": "Käsitelty"},
        )
        self.assertEqual(response.status_code, 303)

        decisions = svc.artifact_json(db, case, "caseworker_observation_decisions", PERMIT, {"items": {}})
        self.assertEqual(decisions["items"][observation_id]["status"], "accepted")

        reloaded = client.get(f"/cases/{case}/lupademo/authority/applications/{PERMIT}")
        self.assertEqual(reloaded.status_code, 200)
        self.assertIn("Merkitty käsitellyksi", reloaded.text)
        events = svc.artifact_json(db, case, "lupademo_events", "main", {"events": []})
        self.assertTrue(any(item.get("action") == "authority.observation.reviewed" for item in events["events"]))


if __name__ == "__main__":
    unittest.main()


class AuthorityPhantomApplication(unittest.TestCase):
    """F13: GET on an unknown application id must 404 without creating artifacts."""

    def test_unknown_application_returns_404_and_creates_nothing(self) -> None:
        before = {row["id"] for row in db.list_artifacts(case)}
        response = client.get(f"/cases/{case}/lupademo/authority/applications/does_not_exist")
        self.assertEqual(response.status_code, 404)
        after = {row["id"] for row in db.list_artifacts(case)}
        self.assertEqual(before, after)
