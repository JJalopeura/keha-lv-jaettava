"""No-LLM regression checks for the criteria-golden contracts."""

import os
import tempfile
import time
import unittest

os.environ.setdefault("KEHA_LV_LLM_PROVIDER", "azure")
os.environ.pop("KEHA_LV_FORCE_MOCK_LLM", None)
os.environ["KEHA_LV_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="criteria-golden-"), "test.db")
os.environ["KEHA_LV_UPLOADS_DIR"] = tempfile.mkdtemp(prefix="criteria-golden-uploads-")

from app.domains.lupademo.services import (  # noqa: E402
    attachment_requirement_covered,
    artifact_json,
    deterministic_issues,
    effective_criteria,
    ensure_application,
    ensure_seed_state,
    precheck_gate_status,
    run_criteria_test,
    save_artifact,
)
from app.domains.lupademo.services.application import (  # noqa: E402
    _clean_review_title,
    _derive_check_verdict,
    _normalize_criteria_results,
    _filter_covered_attachment_issues,
        _build_applicant_review,
)
from app.infra.config import load_settings  # noqa: E402
from app.infra.db import DB  # noqa: E402
from app.domains.lupademo.services.domain import Issue  # noqa: E402


class CriteriaGoldenContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db = DB(load_settings().db_path)
        cls.case_id = cls.db.create_case("criteria golden contract")
        ensure_seed_state(cls.db, cls.case_id)

    def test_official_role_tags_cover_requirement_but_ai_context_does_not(self) -> None:
        official = {
            "id": "official-summary",
            "delivery_scope": "official_submission",
            "primary_type": "other",
            "type_tags": ["public_summary"],
        }
        context = {**official, "id": "context-summary", "delivery_scope": "ai_context_only"}
        self.assertTrue(attachment_requirement_covered("public_summary", [official]))
        self.assertFalse(attachment_requirement_covered("public_summary", [context]))
        issues = deterministic_issues(
            "environmental_industrial",
            {},
            [official],
            criteria={"criteria_set_id": "golden", "sources": ["golden"], "rules": [{"id": "public_summary", "attachment_type": "public_summary", "severity": "recommendation"}]},
        )
        self.assertNotIn("public_summary", {issue.attachment_id for issue in issues})

    def test_gate_ignores_advisory_score_but_not_red_or_major(self) -> None:
        counts, score, readiness = _derive_check_verdict([], [{"status": "yellow"}])
        self.assertEqual(readiness, "ready")
        self.assertLess(score, 80)
        self.assertEqual(counts["yellow"], 1)
        self.assertEqual(_derive_check_verdict([Issue(id="major", type="missing", severity="major", title="x", detail="x", confidence=.5)], [{"status": "green"}])[2], "almost_ready")
        self.assertEqual(_derive_check_verdict([], [{"status": "red"}])[2], "almost_ready")

    def test_advisory_red_is_yellow_and_covered_attachment_is_green(self) -> None:
        criteria = {
            "rules": [
                {"id": "public_summary", "attachment_type": "public_summary", "severity": "recommendation"},
                {"id": "tukes_review", "attachment_type": "tukes_statement", "severity": "recommendation"},
            ]
        }
        results, notes = _normalize_criteria_results(
            [{"criteria_id": "public_summary", "status": "red"}, {"criteria_id": "tukes_review", "status": "red"}],
            criteria,
            {},
            [{"id": "a", "delivery_scope": "official_submission", "primary_type": "other", "type_tags": ["public_summary"]}],
        )
        self.assertEqual([item["status"] for item in results], ["green", "yellow"])
        self.assertEqual(len(notes), 2)

    def test_review_title_translates_rule_target_variants(self) -> None:
        criteria = {
            "permit_type": "environmental_industrial",
            "rules": [{
                "id": "risk_preparedness",
                "field_id": "risk_preparedness",
                "attachment_type": "risk_preparedness_plan",
                "severity": "minor",
            }],
        }

        exact = _clean_review_title("Kunnan kriteeri: risk_preparedness_plan puuttuu", criteria)
        model_variant = _clean_review_title(
            "Kunnan kriteeri: Riskit, onnettomuudet ja häiriötilanteet_plan puuttuu",
            criteria,
        )

        self.assertEqual(exact, "Ennaltavarautumissuunnitelma puuttuu")
        self.assertEqual(model_variant, "Ennaltavarautumissuunnitelma puuttuu")

    def test_combined_rule_names_missing_attachment_when_field_is_present(self) -> None:
        criteria = {
            "criteria_set_id": "golden",
            "permit_type": "environmental_industrial",
            "rules": [{
                "id": "monitoring_plan",
                "field_id": "water_and_wastewater",
                "attachment_type": "air_monitoring_plan",
                "severity": "recommendation",
            }],
        }

        issues = deterministic_issues(
            "environmental_industrial",
            {"water_and_wastewater": "Jätevesien käsittely kuvattu."},
            [],
            criteria=criteria,
        )

        issue = next(item for item in issues if item.criteria_id == "monitoring_plan")
        self.assertEqual(issue.title, "Aktiivinen kriteeri: Ilmapäästöjen tarkkailuohjelma puuttuu")

        review_title = _clean_review_title(
            "Kunnan kriteeri: Veden käyttö ja jätevedet puuttuu",
            criteria,
        )
        self.assertEqual(review_title, "Ilmapäästöjen tarkkailuohjelma puuttuu")

    def test_gate_artifact_requires_fresh_report_without_major_or_red(self) -> None:
        permit_type = "environmental_industrial"
        app = ensure_application(self.db, self.case_id, permit_type)
        municipality = app["form_data"].get("municipality") or artifact_json(self.db, self.case_id, "lv_common_data", "main", {"data": {}}).get("data", {}).get("municipality", "")
        criteria = effective_criteria(self.db, self.case_id, permit_type, municipality)
        now = time.time()
        save_artifact(
            self.db,
            self.case_id,
            "check_report",
            permit_type,
            {
                "checked_at": now,
                "form_updated_at": app["updated_at"],
                "attachments_updated_at": 0,
                "criteria_set_id": criteria["criteria_set_id"],
                "criteria_municipality": str(criteria.get("municipality") or municipality),
                "criteria_version": criteria["version"],
                "issues": [],
                "criteria_results": [{"criteria_id": "permit_basis", "status": "yellow"}],
            },
        )
        gate = precheck_gate_status(self.db, self.case_id, permit_type)
        self.assertTrue(gate["can_submit"])
        self.assertEqual(gate["red_count"], 0)
        self.assertEqual(gate["major_count"], 0)

    def test_workbench_test_runs_active_environmental_rules(self) -> None:
        result = run_criteria_test(self.db, load_settings(), self.case_id, "environmental_industrial")
        self.assertTrue(result["criteria_results"])
        self.assertIn(result["readiness"], {"ready", "almost_ready", "not_ready"})
        self.assertEqual(result["suggestions"], [])

    def test_only_false_missing_role_issue_is_filtered(self) -> None:
        issue = Issue(id="missing", type="missing", severity="minor", title="x", detail="x", attachment_id="public_summary", confidence=.5)
        incomplete = Issue(id="thin", type="incomplete", severity="minor", title="x", detail="x", attachment_id="public_summary", confidence=.5)
        attachments = [{"delivery_scope": "official_submission", "primary_type": "other", "type_tags": ["public_summary"]}]
        filtered = _filter_covered_attachment_issues([issue, incomplete], attachments)
        self.assertEqual([item.id for item in filtered], ["thin"])

        def test_combined_criterion_review_names_missing_attachment(self) -> None:
            criteria = {
                "permit_type": "environmental_industrial",
                "criteria_set_id": "golden",
                "rules": [{"id": "monitoring_plan", "field_id": "water_and_wastewater", "attachment_type": "air_monitoring_plan", "severity": "minor"}],
            }
            issue = Issue(
                id="criteria_monitoring_plan",
                type="missing",
                severity="minor",
                title="Aktiivinen kriteeri: Ilmapäästöjen tarkkailuohjelma puuttuu",
                detail="Tarkkailu jää vajaaksi.",
                confidence=0.9,
                criteria_id="monitoring_plan",
                field_id="water_and_wastewater",
                attachment_id="air_monitoring_plan",
            )
            review = _build_applicant_review(
                {"improvements": [{"title": "Veden käyttö ja jätevedet puuttuu", "detail": "Tarkkailu jää vajaaksi.", "evidence_refs": [{"source_id": "monitoring_plan"}]}]},
                "Hakemus on valmis lähetettäväksi.",
                [issue],
                [{"criteria_id": "monitoring_plan", "status": "yellow", "rationale": "Tarkkailu", "evidence_refs": [{"source_id": "monitoring_plan"}]}],
                criteria,
                [{"id": "application", "filename": "hakemus.pdf", "primary_type": "environmental_application", "delivery_scope": "official_submission"}],
                {"water_and_wastewater": "Vesipuolen kuvaus on toimitettu."},
            )
            self.assertEqual(review["improvements"][0]["title"], "Ilmapäästöjen tarkkailuohjelma puuttuu")

if __name__ == "__main__":
    unittest.main()
