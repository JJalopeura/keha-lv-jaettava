"""Golden-case gate for the diamond check pipeline (tickets 03/04/06/08).

Locks the acceptance checklist of the composed verdict in MOCK mode so the demo
showcases the same diamonds Azure produces instead of placeholders. Fails if the
pipeline regresses to empty issues, mock_criteria placeholders, silent fallback,
self-contradicting criteria/issue verdicts, or an invented (non-derived) score.

Stdlib unittest on purpose: no pytest dependency.
    .venv/bin/python -m unittest tests.test_diamond_check_golden
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["KEHA_LV_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="keha-golden-"), "test.db")
os.environ["KEHA_LV_UPLOADS_DIR"] = tempfile.mkdtemp(prefix="keha-golden-uploads-")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.domains.lupademo.services as svc  # noqa: E402
from app.domains.lupademo.routes import create_lupademo_router  # noqa: E402
from app.domains.lupademo.scenarios import create_case_from_scenario  # noqa: E402
from app.infra.config import load_settings  # noqa: E402
from app.infra.db import DB  # noqa: E402
from app.infra.session import DemoSessionMiddleware  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_llm import install_fake_llm  # noqa: E402

install_fake_llm()


# Fully isolated app + DB so the golden gate never touches the shared `app.main`
# state that test_smoke.py relies on (app.main pins its DB at import time —
# whichever module imports it first wins; golden must not be that module).
_settings = load_settings()
_db = DB(_settings.db_path)
app = FastAPI(title="KEHA LV AI-demo (golden gate)")
app.add_middleware(
    DemoSessionMiddleware,
    secret_key=_settings.session_secret,
    session_cookie="lv_session",
    same_site="lax",
)
app.include_router(create_lupademo_router(lambda: _db, lambda: _settings))

PERMIT = "environmental_industrial"
GOLDEN_SCENARIO = "environmental_industrial_generic"


class DiamondCheckGoldenTest(unittest.TestCase):
    """08: the golden seeded case — environmental_industrial_generic, mock mode,
    known verdict shape. The checklist below is the definition of done."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.case_id = create_case_from_scenario(_db, GOLDEN_SCENARIO)["case_id"]
        client = TestClient(app, follow_redirects=False)
        client.post(
            f"/cases/{cls.case_id}/lupademo/application/{PERMIT}/ai/check",
            data={},
        )
        cls.report = svc.artifact_json(_db, cls.case_id, "check_report", PERMIT, {})
        assert cls.report, "check_report artifact missing — the check did not run"

    # -- 06: mock mirrors reality, never placeholders -------------------------

    def test_not_placeholders(self) -> None:
        self.assertTrue(self.report.get("issues"), "mock must produce issues, not []")
        criteria = self.report.get("criteria_results", [])
        self.assertTrue(criteria, "criteria_results must be populated")
        self.assertNotIn(
            "mock_criteria",
            [c.get("criteria_id") for c in criteria],
            "criteria ids must be real, not the mock_criteria placeholder",
        )
        self.assertIn("permit_basis", [c.get("criteria_id") for c in criteria])
        self.assertEqual(self.report.get("degraded", {}).get("fallback_used_agents", []), [])

    # -- 02: the Issue contract --------------------------------------------------

    def test_issue_contract(self) -> None:
        valid_severities = {"blocking", "major", "minor"}
        valid_types = {"missing", "incomplete", "inconsistent", "risk"}
        for issue in self.report.get("issues", []):
            self.assertNotIn(issue.get("severity"), {"recommendation", None},
                             f"issue {issue.get('id')} violates the severity contract")
            self.assertIn(issue.get("severity"), valid_severities)
            self.assertIn(issue.get("type"), valid_types)
            self.assertTrue(issue.get("evidence_refs"), f"issue {issue.get('id')} lacks evidence_refs")
            iid = issue.get("id", "")
            self.assertTrue(iid, "issue id must not be empty")
            if not iid.startswith(("missing_", "invalid_", "criteria_")):
                self.assertRegex(iid, r"^(completeness|consistency)-\d+$",
                                 f"agent issue id {iid} must be runtime-namespaced")

    # -- 03 D3: no self-contradiction (reconciliation invariant) -----------------

    def test_no_self_contradiction(self) -> None:
        issues = self.report.get("issues", [])
        for c in self.report.get("criteria_results", []):
            cid = c.get("criteria_id")
            linked = [i for i in issues if i.get("criteria_id") == cid and not i.get("resolved")]
            if c.get("status") == "red":
                self.assertTrue(
                    any(i.get("severity") == "blocking" for i in linked),
                    f"red criterion {cid} must have an unresolved blocking issue with its criteria_id",
                )
            elif c.get("status") == "yellow":
                # Minor-only policy: yellow criterion = osittain täytetty → vähäinen (minor)
                # review point, never blocking/major (reconcile_verdict C1/P2 target).
                self.assertTrue(
                    any(i.get("severity") == "minor" for i in linked),
                    f"yellow criterion {cid} must have an unresolved minor issue",
                )
            else:  # green
                for i in linked:
                    self.assertEqual(
                        i.get("severity"), "minor",
                        f"green criterion {cid} must not have unresolved blocking/major issues",
                    )

    # -- 03/04: score is derived from the verdict, never invented ------------------

    def test_score_derived(self) -> None:
        counts = self.report.get("counts", {})
        criteria = self.report.get("criteria_results", [])
        crit_score = 1.0 if not criteria else (counts.get("green", 0) + 0.5 * counts.get("yellow", 0)) / len(criteria)
        # capped minor penalty — mirror of _derive_check_verdict (minor-only policy)
        issue_score = max(0.0, 1 - 0.25 * counts.get("blocking", 0) - 0.10 * counts.get("major", 0) - min(0.04 * counts.get("minor", 0), 0.20))
        expected = round(50 * crit_score + 50 * issue_score)
        self.assertEqual(self.report.get("score"), expected,
                         "score must equal the code-owned 50/50 derivation from counts")

    def test_counts_match_issues(self) -> None:
        counts = self.report.get("counts", {})
        unresolved = [i for i in self.report.get("issues", []) if not i.get("resolved")]
        for sev in ("blocking", "major", "minor"):
            self.assertEqual(counts.get(sev), sum(1 for i in unresolved if i.get("severity") == sev))
        criteria = self.report.get("criteria_results", [])
        for status in ("red", "yellow", "green"):
            self.assertEqual(counts.get(status), sum(1 for c in criteria if c.get("status") == status))

    def test_readiness_derived(self) -> None:
        score = self.report.get("score", 0)
        blockers = self.report.get("counts", {}).get("blocking", 0)
        expected = "ready" if (score >= 80 and blockers == 0) else ("almost_ready" if score >= 40 else "not_ready")
        self.assertEqual(self.report.get("readiness"), expected)

    # -- 05/07: verdict shape surfaces (D5) ----------------------------------------

    def test_verdict_shape(self) -> None:
        for key in ("score", "readiness", "summary", "issues", "criteria_results", "counts", "degraded", "criteria_version"):
            self.assertIn(key, self.report, f"check_report must carry {key}")
        self.assertIsInstance(self.report.get("degraded", {}).get("reconciliation_notes"), list)

    # -- 07: the /api/v1 precheck surface renders the SAME diamond verdict ---------

    def test_api_precheck_surface_matches_wizard_derivation(self) -> None:
        """The external precheck must compose the verdict via the same code-owned
        derivation as the wizard/authority check — not the legacy score formula.
        (Regression gate for the suggestion_service_check divergence found in the
        verification-audit run; broken by the precheck skipping reconcile/derive.)"""
        from app.domains.lupademo import security  # noqa: PLC0415

        tenant = next((t for t in security._load_tenants() if "environmental_industrial" in t.get("permit_types", [])), None)
        self.assertIsNotNone(tenant, "tenants.json must define an environmental_industrial tenant")
        client = TestClient(app, follow_redirects=False)
        response = client.post(
            "/api/v1/applications:precheck",
            json={"permit_type": PERMIT},
            headers={"X-Lupademo-Api-Key": tenant["api_key"]},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        result = body.get("result", {})
        counts = result.get("counts", {})
        criteria = result.get("criteria_results", [])
        self.assertIn("blocking", counts, "precheck must carry derived counts")
        self.assertIsInstance(result.get("readiness"), str)
        self.assertIn(result.get("readiness"), {"ready", "almost_ready", "not_ready"})
        crit_score = (counts.get("green", 0) + 0.5 * counts.get("yellow", 0)) / len(criteria) if criteria else 1.0
        # capped minor penalty — mirror of _derive_check_verdict (minor-only policy)
        issue_score = max(0.0, 1 - 0.25 * counts.get("blocking", 0) - 0.10 * counts.get("major", 0) - min(0.04 * counts.get("minor", 0), 0.20))
        expected = round(50 * crit_score + 50 * issue_score)
        self.assertEqual(result.get("score"), expected, "precheck score must be code-derived")
        unresolved = [i for i in result.get("issues", []) if not i.get("resolved")]
        self.assertEqual(counts.get("blocking"), sum(1 for i in unresolved if i.get("severity") == "blocking"))
        self.assertIsInstance(result.get("degraded", {}).get("reconciliation_notes"), list)
        self.assertNotEqual(result.get("summary"), "API-tarkistus valmis.")


if __name__ == "__main__":
    unittest.main()
