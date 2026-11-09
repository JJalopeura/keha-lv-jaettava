"""Regression tests for jobs.py state-machine holes (deep-audit F25a-d).

Stdlib unittest, own temp DB — no shared app state.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root


from app.infra.db import DB  # noqa: E402

import app.domains.lupademo.services as svc  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_llm import install_fake_llm  # noqa: E402

install_fake_llm()


# Own temp DB instance — never via KEHA_LV_DB_PATH, or app.main (imported later
# by test_smoke) would pin this DB and cross-pollute the suite.
_JOBS_DB_PATH = os.path.join(tempfile.mkdtemp(prefix="keha-jobs-"), "test.db")

PERMIT = "environmental_industrial"


def _events(db: DB, case: str) -> list[dict]:
    return [e for e in svc.artifact_json(db, case, "lupademo_events", "main", {}).get("events", [])]


class JobsLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self.db = DB(_JOBS_DB_PATH)
        self.case = svc.ensure_demo_case(self.db)
        # Keep simulated progress deterministic and slow for the retry test.
        os.environ["LUPADEMO_AI_JOB_TOTAL_SECONDS"] = "60"

    def tearDown(self) -> None:
        os.environ.pop("LUPADEMO_AI_JOB_TOTAL_SECONDS", None)

    def _sim_job(self) -> dict:
        return svc.start_ai_job(
            self.db, self.case, PERMIT, "intake_analysis", "Testi-ajo",
            ["gap_analyzer"], eta_seconds=60,
        )

    def _job(self, job_id: str) -> dict:
        return svc.find_ai_job(self.db, job_id)["job"]

    def test_retry_resets_created_at_no_insta_complete(self) -> None:
        job = self._sim_job()
        job["created_at"] = time.time() - 120  # simulate old job past its window
        svc.retry_ai_job(self.db, job["id"])
        refreshed = self._job(job["id"])
        self.assertEqual(refreshed["status"], "running")
        self.assertLess(refreshed["progress_pct"], 100)

    def test_double_complete_does_not_overwrite_or_dup_event(self) -> None:
        job = self._sim_job()
        svc.complete_ai_job(self.db, self.case, PERMIT, job["id"], summary="first")
        first = self._job(job["id"])
        second = svc.complete_ai_job(self.db, self.case, PERMIT, job["id"], summary="second overwrite")
        after = self._job(job["id"])
        self.assertEqual(after["summary"], "first")
        self.assertEqual(after["finished_at"], first["finished_at"])
        events_now = [e for e in _events(self.db, self.case) if e.get("action") == "ai.job.completed"]
        self.assertEqual(len(events_now), 1)

    def test_mark_agent_complete_ignores_cancelled_job(self) -> None:
        job = self._sim_job()
        svc.cancel_ai_job(self.db, job["id"])
        svc.mark_agent_complete(self.db, self.case, PERMIT, job["id"], "gap_analyzer", "late finisher")
        refreshed = self._job(job["id"])
        agent = next(a for a in refreshed["agents"] if a["name"] == "gap_analyzer")
        self.assertNotEqual(agent["status"], "completed")

    def test_complete_on_cancelled_job_emits_no_completed_event(self) -> None:
        job = self._sim_job()
        svc.cancel_ai_job(self.db, job["id"])
        before = len(_events(self.db, self.case))
        svc.complete_ai_job(self.db, self.case, PERMIT, job["id"], summary="should not stick")
        after = self._job(job["id"])
        self.assertEqual(after["status"], "cancelled")
        new_events = _events(self.db, self.case)[before:]
        self.assertFalse(any(e.get("action") == "ai.job.completed" for e in new_events))


if __name__ == "__main__":
    unittest.main()
