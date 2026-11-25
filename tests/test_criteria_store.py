"""Criteria are one national definition per permit type, not per-hanke state.

Field report that produced these tests: criteria edited in the admin view showed the
edit, then a new test hanke (created right after) ran its check against the old
bundled JSON — because the criteria artifact was keyed by case id and every new hanke
was seeded from the file. The store fixed that; the case artifact is only a mirror.

Stdlib unittest on purpose: no pytest dependency.
"""

import os
import tempfile
import unittest
from unittest import mock

import app.domains.lupademo.services as svc
from app.domains.lupademo.services import criteria as criteria_svc
from app.infra.db import DB

PERMIT = "environmental_industrial"


class CriteriaStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = DB(os.path.join(tempfile.mkdtemp(prefix="keha-criteria-"), "test.db"))
        self.case_a = self._new_case("Hanke A")

    def _new_case(self, title: str) -> str:
        case_id = self.db.create_case(title, language="fi")
        svc.ensure_seed_state(self.db, case_id)
        return case_id

    def _rule(self, case_id: str, rule_id: str) -> dict:
        criteria = svc.effective_criteria(self.db, case_id, PERMIT)
        return next(rule for rule in criteria["rules"] if rule["id"] == rule_id)

    def test_edit_in_one_hanke_is_seen_by_a_later_hanke(self) -> None:
        """The reported bug: the edit existed only in the hanke where it was made."""
        criteria = svc.effective_criteria(self.db, self.case_a, PERMIT)
        next(rule for rule in criteria["rules"] if rule["id"] == "site_location")["instruction"] = "Uusi ohje: omistajat ja haltijat."
        svc.save_criteria_version(self.db, self.case_a, PERMIT, criteria)

        case_b = self._new_case("Hanke B")

        self.assertEqual(self._rule(case_b, "site_location")["instruction"], "Uusi ohje: omistajat ja haltijat.")

    def test_rule_edit_in_one_hanke_is_seen_by_a_later_hanke(self) -> None:
        svc.upsert_criteria_rule(
            self.db,
            self.case_a,
            PERMIT,
            {"id": "zoning_context", "severity": "major", "instruction": "Kaavaote ja kaavanmukaisuus.", "field_id": "zoning_context"},
        )

        case_b = self._new_case("Hanke C")

        rule = self._rule(case_b, "zoning_context")
        self.assertEqual(rule["instruction"], "Kaavaote ja kaavanmukaisuus.")
        self.assertEqual(rule["severity"], "major")

    def test_history_is_shared_by_every_hanke(self) -> None:
        svc.upsert_criteria_rule(
            self.db,
            self.case_a,
            PERMIT,
            {"id": "byproducts", "severity": "major", "instruction": "Sivutuoteluokittelu.", "field_id": "byproducts"},
        )

        case_b = self._new_case("Hanke D")
        rows = svc.criteria_history_rows(svc.criteria_history(self.db, case_b, PERMIT))

        self.assertTrue(rows, "a hanke created after the edit must see the edit history")

    def test_unedited_store_follows_a_new_shipped_default(self) -> None:
        first = svc.effective_criteria(self.db, self.case_a, PERMIT)
        bumped = dict(first, version="2027-01-demo-1")

        with mock.patch.object(criteria_svc, "load_criteria", return_value=bumped):
            seen = svc.effective_criteria(self.db, self.case_a, PERMIT)

        self.assertEqual(seen["version"], "2027-01-demo-1")

    def test_edited_store_is_not_replaced_by_a_new_shipped_default(self) -> None:
        svc.upsert_criteria_rule(
            self.db,
            self.case_a,
            PERMIT,
            {"id": "risk_preparedness", "severity": "major", "instruction": "Riskit ja varautuminen.", "field_id": "risk_preparedness"},
        )
        edited = svc.effective_criteria(self.db, self.case_a, PERMIT)
        bumped = dict(edited, version="2027-01-demo-1")

        with mock.patch.object(criteria_svc, "load_criteria", return_value=bumped):
            seen = svc.effective_criteria(self.db, self.case_a, PERMIT)

        self.assertEqual(seen["version"], edited["version"])

    def test_case_artifact_stays_a_mirror_of_the_store(self) -> None:
        case_b = self._new_case("Hanke E")
        svc.effective_criteria(self.db, case_b, PERMIT)
        mirrored = svc.artifact_json(self.db, case_b, "criteria_set", PERMIT, {})

        self.assertEqual(mirrored["version"], svc.effective_criteria(self.db, case_b, PERMIT)["version"])
