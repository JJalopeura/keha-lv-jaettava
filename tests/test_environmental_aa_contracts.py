"""Contracts for the 2026-12 AA rework of the Ympäristölupa lane.

The environmental form was rebuilt around the Asiointipalvelu (AA) sections —
Tiivistelmä, Taustatiedot, Toiminta, Riskit, Ympäristö/päästöt/vaikutukset — and the
criteria set now holds exactly one rule per section, with the AA guidance text as the
rule instruction. These tests pin the two things that silently break such a rework:

1. a rule that points at a form field or attachment type nothing produces (the check
   then reports a gap that can never be filled), and
2. evidence retrieval that only reads the beginning of the 65-page application, so the
   sections living in its later chapters read as "not in the material".

Stdlib unittest on purpose: no pytest dependency.
    .venv/bin/python -m unittest tests.test_environmental_aa_contracts
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("KEHA_LV_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="keha-aa-"), "test.db"))
os.environ.setdefault("KEHA_LV_UPLOADS_DIR", tempfile.mkdtemp(prefix="keha-aa-uploads-"))

from app.domains.lupademo.services.application import _select_evidence  # noqa: E402
from app.domains.lupademo.services.criteria import load_criteria  # noqa: E402
from app.domains.lupademo.services.domain import load_form_schema  # noqa: E402
from app.domains.lupademo.services.intake import (  # noqa: E402
    ATTACHMENT_REQUIREMENT_ALIASES,
    _sampled_excerpt,
    attachment_requirement_covered,
    classify_attachment,
    evidence_chunks,
)

PERMIT = "environmental_industrial"
AA_TABS = {"overview", "location", "activity", "risks", "environment"}


class EnvironmentalSchemaContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = load_form_schema(PERMIT)
        self.criteria = load_criteria(PERMIT)
        self.field_ids = {str(field["id"]) for field in self.schema["fields"]}

    def test_form_keeps_the_five_aa_sections(self) -> None:
        tab_ids = {str(tab["id"]) for tab in self.schema["tabs"]}
        self.assertTrue(AA_TABS <= tab_ids, f"missing AA tabs: {AA_TABS - tab_ids}")

    def test_every_rule_targets_a_form_field_that_exists(self) -> None:
        for rule in self.criteria["rules"]:
            field_id = str(rule.get("field_id") or "")
            if field_id:
                self.assertIn(field_id, self.field_ids, f"rule {rule['id']} points at unknown field {field_id}")

    def test_one_rule_per_aa_section_with_guidance_as_instruction(self) -> None:
        rules = self.criteria["rules"]
        self.assertEqual(len(rules), 10, "the criteria set must hold exactly the ten AA sections")
        for rule in rules:
            self.assertTrue(str(rule.get("title") or "").strip(), f"rule {rule['id']} lacks a title")
            self.assertGreater(len(str(rule.get("instruction") or "")), 40, f"rule {rule['id']} instruction is a stub")
            self.assertTrue(str(rule.get("basis") or "").strip(), f"rule {rule['id']} lacks a basis")
            self.assertIn(str(rule.get("severity")), {"minor", "recommendation"}, f"rule {rule['id']} is not advisory")

    def test_form_fields_carry_the_aa_help_text(self) -> None:
        for field in self.schema["fields"]:
            if field["id"] in {"applicant_name", "business_id", "activity_name"}:
                continue
            self.assertTrue(str(field.get("help") or "").strip(), f"field {field['id']} has no guidance text")
            self.assertEqual(field.get("type"), "textarea", f"field {field['id']} must be prose")

    def test_byproducts_is_conditional_not_required(self) -> None:
        field = next(item for item in self.schema["fields"] if item["id"] == "byproducts")
        self.assertFalse(field.get("required"))
        rule = next(item for item in self.criteria["rules"] if item["id"] == "byproducts")
        self.assertTrue(rule.get("condition"), "byproducts rule must wait for the jätelaki 5 § trigger")


class AttachmentContractTest(unittest.TestCase):
    def test_criteria_attachment_types_are_produced_by_the_classifier(self) -> None:
        criteria = load_criteria(PERMIT)
        examples = {
            "general_map": "Liite 5 a. Lähestymiskartta 1-200000.pdf",
            "zoning_extract": "Liite 6 Asemakaavaote.pdf",
            "risk_preparedness_plan": "Liite 12. Ennaltavarautumissuunnitelma.pdf",
        }
        used: set[str] = set()
        for rule in criteria["rules"]:
            for part in str(rule.get("attachment_type") or "").split(","):
                if part.strip():
                    used.add(part.strip())
        self.assertTrue(used <= set(examples), f"untested attachment types: {used - set(examples)}")
        for attachment_type in used:
            with self.subTest(attachment_type=attachment_type):
                self.assertEqual(classify_attachment(examples[attachment_type]), attachment_type)
                self.assertIn(attachment_type, ATTACHMENT_REQUIREMENT_ALIASES)
                attachment = {
                    "delivery_scope": "official_submission",
                    "primary_type": attachment_type,
                    "type_tags": [],
                }
                self.assertTrue(attachment_requirement_covered(attachment_type, [attachment]))
                attachment["delivery_scope"] = "ai_context_only"
                self.assertFalse(attachment_requirement_covered(attachment_type, [attachment]))

    def test_kaavaselostus_is_not_a_kaavaote(self) -> None:
        self.assertEqual(classify_attachment("Kaavaselostus luonnos.pdf"), "zoning_report")
        self.assertEqual(classify_attachment("Asemakaavaote 2024.pdf"), "zoning_extract")

    def test_prosessikaavio_classifies_as_process_diagram(self) -> None:
        self.assertEqual(classify_attachment("Liite 3 Prosessikaavio.pdf"), "process_diagram")
        self.assertEqual(classify_attachment("Virtauskaavio laitoksesta.pdf"), "process_diagram")


class EvidenceSelectionContractTest(unittest.TestCase):
    """A 65-page application must contribute more than its cover page."""

    def _chunks(self) -> list[dict]:
        application = (
            "YMPÄRISTÖLUPAHAKEMUS\nSisältö\n1 Toiminta, jolle lupaa haetaan ....... 7\n"
            + ("Muu teksti. " * 200)
            + "\n5 RISKIT, HÄIRIÖTILANTEET JA NIIDEN VARAUTUMINEN\n"
            + "Riskien tunnistaminen, ennaltavarautumissuunnitelma, hälytys- ja sammutusjärjestelmät, huolto- ja varajärjestelmät."
            + (" Muu teksti. " * 200)
        )
        attachments = [
            {
                "id": "app",
                "filename": "Ympäristölupahakemus.pdf",
                "primary_type": "environmental_application",
                "type_tags": [],
                "delivery_scope": "official_submission",
                "text_excerpt": application,
            }
        ]
        return evidence_chunks(attachments, chunk_chars=900)

    def test_selection_reaches_a_later_chapter(self) -> None:
        chunks = self._chunks()
        self.assertGreater(len(chunks), 1)
        rules = [{
            "id": "risk_preparedness",
            "title": "Riskit – yleiskuvaus",
            "instruction": "Toimintaan liittyvät ympäristöriskit, onnettomuuksien estämiseksi suunnitellut "
                           "toimenpiteet ja toiminta häiriötilanteissa: tarkkailu-, hälytys- ja "
                           "sammutusjärjestelmät, huolto- ja varajärjestelmät, häiriötilanteiden kirjanpito. "
                           "Liitteeksi ennaltavarautumissuunnitelma.",
        }]
        selected = _select_evidence(chunks, rules, budget_chars=1200)
        texts = " ".join(item["text"] for item in selected["excerpts"])
        self.assertIn("ennaltavarautumissuunnitelma", texts.lower())
        self.assertNotIn("Sisältö", texts, "the cover page must not win over the risk chapter")

    def test_selection_is_deterministic(self) -> None:
        chunks = self._chunks()
        rules = [{"id": "activity_description", "title": "Toiminnan yleiskuvaus", "instruction": "Mitä tuotetaan ja millä menetelmillä."}]
        first = _select_evidence(chunks, rules, budget_chars=1000)
        second = _select_evidence(chunks, rules, budget_chars=1000)
        self.assertEqual(first, second)

    def test_sampled_excerpt_keeps_the_tail_of_a_long_document(self) -> None:
        text = "ALKU " + ("täyte " * 2000) + "LOPPU"
        sample = _sampled_excerpt(text, 900, windows=3)
        self.assertIn("ALKU", sample)
        self.assertIn("LOPPU", sample)


if __name__ == "__main__":
    unittest.main()
