"""Golden-case gate for the diamond check pipeline (tickets 03/04/06/08).

Locks the acceptance checklist of the composed verdict in MOCK mode so the demo
showcases the same diamonds Azure produces instead of placeholders. Fails if the
pipeline regresses to empty issues, mock_criteria placeholders, silent fallback,
self-contradicting criteria/issue verdicts, or an invented (non-derived) score.

Stdlib unittest on purpose: no pytest dependency.
    .venv/bin/python -m unittest tests.test_diamond_check_golden
"""

import os
import tempfile
import unittest

os.environ["KEHA_LV_FORCE_MOCK_LLM"] = "true"
os.environ["KEHA_LV_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="keha-golden-"), "test.db")

from fastapi.testclient import TestClient  # noqa: E402

import app.domains.lupademo.services as svc  # noqa: E402
from app.domains.lupademo.scenarios import create_case_from_scenario  # noqa: E402
from app.main import app, db as app_db  # noqa: E402

PERMIT = "environmental_industrial"
GOLDEN_SCENARIO = "environmental_industrial_generic"


class DiamondCheckGoldenTest(unittest.TestCase):
    """08: the golden seeded case — environmental_industrial_generic, mock mode,
    known verdict shape. The checklist below is the definition of done."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.case_id = create_case_from_scenario(app_db, GOLDEN_SCENARIO)["case_id"]
        client = TestClient(app, follow_redirects=False)
        client.post(
            f"/cases/{cls.case_id}/lupademo/application/{PERMIT}/ai/check",
            data={},
        )
        cls.report = svc.artifact_json(app_db, cls.case_id, "check_report", PERMIT, {})
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
                self.assertTrue(
                    any(i.get("severity") in {"blocking", "major"} for i in linked),
                    f"yellow criterion {cid} must have an unresolved blocking/major issue",
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
        issue_score = max(0.0, 1 - 0.25 * counts.get("blocking", 0) - 0.10 * counts.get("major", 0) - 0.04 * counts.get("minor", 0))
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


if __name__ == "__main__":
    unittest.main()
