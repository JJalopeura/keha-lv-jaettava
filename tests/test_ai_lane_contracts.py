"""Contracts for the AI check lane that used to fail silently.

Three behaviours are pinned here because each one produced a *plausible* wrong answer
rather than an error:

1. A truncated provider answer (``status: incomplete``) must not be accepted just because
   the surviving fragment happens to parse. It used to be stored as "no criteria at all",
   which then scored the criteria half as perfect.
2. An assessment of a criterion that does not exist in the active set must not become a
   finding — the model occasionally cites a plausible but unauthored criterion id.
3. A criterion verdict and the finding list must not contradict each other on the page: a
   green criterion plus an unresolved "this is missing" finding about that same criterion
   is exactly that contradiction.

Stdlib unittest on purpose: no pytest dependency.
    .venv/bin/python -m unittest tests.test_ai_lane_contracts
"""

import os
import tempfile
import unittest

os.environ.setdefault("KEHA_LV_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="keha-ai-lane-"), "test.db"))

from app.domains.lupademo.services import Issue  # noqa: E402
from app.domains.lupademo.services.application import (  # noqa: E402
    _derive_check_verdict,
    _sanitize_criteria_links,
    reconcile_verdict,
)
from app.infra.llm_utils import call_llm_json  # noqa: E402


class _TruncatingClient:
    """First answer is cut off at max_output_tokens, second one is complete."""

    model = "stub-model"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def responses(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output_text": '{"criteria_results": [{"criteria_id": "permit_basis", "status": "green"}',
            }
        return {
            "status": "completed",
            "output_text": '{"criteria_results": [{"criteria_id": "permit_basis", "status": "green"}]}',
        }

    @staticmethod
    def extract_output_text(resp):
        return resp.get("output_text")


class TruncatedOutputTest(unittest.TestCase):
    def test_truncated_answer_is_retried_not_accepted(self) -> None:
        client = _TruncatingClient()
        result = call_llm_json(client, "Palauta JSON.", max_output_tokens=6000)

        self.assertEqual(len(client.calls), 2, "a truncated answer must be retried once")
        self.assertGreater(
            client.calls[1]["max_output_tokens"], client.calls[0]["max_output_tokens"],
            "the retry must ask for a bigger output budget",
        )
        self.assertEqual(result["criteria_results"][0]["criteria_id"], "permit_basis")


class CriteriaResultHygieneTest(unittest.TestCase):
    def test_unknown_criterion_ids_are_not_synthesized_into_findings(self) -> None:
        criteria = {"rules": [{"id": "permit_basis", "severity": "minor"}]}
        issues, notes = _sanitize_criteria_links(
            [Issue(id="completeness-1", type="missing", severity="minor", title="X", detail="Y", confidence=0.8, criteria_id="kemikaaliturvallisuus")],
            criteria,
        )
        self.assertIsNone(issues[0].criteria_id)
        self.assertTrue(any("kemikaaliturvallisuus" in note for note in notes))

    def test_known_criterion_ids_survive(self) -> None:
        criteria = {"rules": [{"id": "permit_basis", "severity": "minor"}]}
        issues, notes = _sanitize_criteria_links(
            [Issue(id="completeness-1", type="missing", severity="minor", title="X", detail="Y", confidence=0.8, criteria_id="permit_basis")],
            criteria,
        )
        self.assertEqual(issues[0].criteria_id, "permit_basis")
        self.assertEqual(notes, [])


class GreenCriterionContradictionTest(unittest.TestCase):
    def test_green_criterion_resolves_missing_issue_for_same_criterion(self) -> None:
        issues = [Issue(id="criteria_stormwater_management", type="missing", severity="minor", title="Hulevedet puuttuvat", detail="Y", confidence=0.8, criteria_id="stormwater_management")]
        resolved, notes = reconcile_verdict(
            issues,
            [{"criteria_id": "stormwater_management", "status": "green", "rationale": "Liite 10 kattaa hulevedet."}],
        )
        self.assertTrue(resolved[0].resolved)
        self.assertTrue(any("P4" in note for note in notes))

    def test_green_criterion_keeps_advisory_quality_notes(self) -> None:
        issues = [Issue(id="completeness-1", type="incomplete", severity="minor", title="Kuvaus on ylimalkainen", detail="Y", confidence=0.8, criteria_id="stormwater_management")]
        resolved, _ = reconcile_verdict(
            issues,
            [{"criteria_id": "stormwater_management", "status": "green", "rationale": "Liite 10 kattaa hulevedet."}],
        )
        self.assertFalse(resolved[0].resolved, "quality notes are not contradictions and must stay")


class UnassessedCriteriaScoreTest(unittest.TestCase):
    def test_missing_criteria_results_score_neutral_not_perfect(self) -> None:
        _, score_no_results, _ = _derive_check_verdict([], [], criteria_expected=19)
        _, score_no_criteria_set, _ = _derive_check_verdict([], [], criteria_expected=0)
        self.assertLess(score_no_results, score_no_criteria_set)
        self.assertEqual(score_no_results, 75)  # 50 * 0.5 + 50 * 1.0

    def test_measured_criteria_still_dominate(self) -> None:
        results = [{"criteria_id": f"c{i}", "status": "green"} for i in range(4)]
        _, score, _ = _derive_check_verdict([], results, criteria_expected=4)
        self.assertEqual(score, 100)


if __name__ == "__main__":
    unittest.main()
