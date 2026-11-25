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
            # 2026-12-demo-3: the domain reviewer raised every AA section to major, so a
            # finding reads "Merkittävä". Major findings are advisory for the submit gate
            # (only a blocking finding or a red criterion can close it).
            self.assertEqual(str(rule.get("severity")), "major", f"rule {rule['id']} lost its reviewed severity")

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


class EvidenceHygieneContractTest(unittest.TestCase):
    """Trust boundaries the deployed check must keep (from the 2026-11-15 review)."""

    def _attachments(self) -> list[dict]:
        body = "Kaavoitus: asemakaava osoittaa alueen teollisuuskäyttöön. " * 40
        return [
            {
                "id": "official",
                "filename": "Kaavoitus.pdf",
                "primary_type": "zoning_extract",
                "type_tags": [],
                "delivery_scope": "official_submission",
                "text_excerpt": body,
            },
            {
                "id": "internal",
                "filename": "Sisäinen muistio.pdf",
                "primary_type": "other",
                "type_tags": [],
                "delivery_scope": "internal_authority_only",
                "text_excerpt": "Viranomaisen sisäinen arvio, ei hakijan aineistoa. " * 40,
            },
            {
                "id": "tiny",
                "filename": "Lyhyt muistio.txt",
                "primary_type": "other",
                "type_tags": [],
                "delivery_scope": "official_submission",
                "text_excerpt": "Lyhyt mutta olennainen havainto kaavoituksesta.",
            },
        ]

    def test_authority_only_material_never_becomes_evidence(self) -> None:
        chunks = evidence_chunks(self._attachments())
        sources = {chunk["source_id"] for chunk in chunks}
        self.assertEqual(sources, {"official", "tiny"})
        self.assertNotIn("Viranomaisen sisäinen", " ".join(chunk["text"] for chunk in chunks))

    def test_short_documents_are_kept(self) -> None:
        chunks = evidence_chunks(self._attachments())
        self.assertIn("tiny", {chunk["source_id"] for chunk in chunks})

    def test_each_rule_gets_its_own_passage(self) -> None:
        # Two rules with disjoint vocabulary; the package has much more text about the
        # first topic. Both rules must still get a passage inside the budget.
        chunks = evidence_chunks(
            [
                {
                    "id": "doc",
                    "filename": "Aineisto.pdf",
                    "primary_type": "environmental_application",
                    "type_tags": [],
                    "delivery_scope": "official_submission",
                    "text_excerpt": ("Sivutuotteet ja jätelaki 5 pykälä. " * 120)
                    + ("Melumallinnus ja meluntorjunta. " * 6),
                }
            ],
            chunk_chars=400,
        )
        rules = [
            {"id": "byproducts", "title": "Sivutuotteet", "instruction": "sivutuotteet jätelaki luokittelu"},
            {"id": "noise", "title": "Melu", "instruction": "melumallinnus meluntorjunta"},
        ]
        selected = _select_evidence(chunks, rules, budget_chars=1400)
        text = " ".join(item["text"] for item in selected["excerpts"]).lower()
        self.assertIn("sivutuotteet", text)
        self.assertIn("melumallinnus", text)


class ValuePresenceContractTest(unittest.TestCase):
    """Presence, applicability and evidence edge cases the reviewer found."""

    def test_numeric_zero_is_a_value_not_a_missing_field(self) -> None:
        from app.domains.lupademo.services.application import deterministic_issues

        issues = deterministic_issues(
            "building_residential",
            {"floor_area_m2": 0, "building_height_m": "8.5", "distance_to_boundary_m": "4"},
            [],
        )
        self.assertNotIn("missing_floor_area_m2", {issue.id for issue in issues})

    def test_filling_the_rule_field_makes_a_conditional_rule_apply(self) -> None:
        from app.domains.lupademo.services.application import _normalize_criteria_results

        criteria = {
            "permit_type": "environmental_industrial",
            "rules": [
                {
                    "id": "byproducts",
                    "field_id": "byproducts",
                    "title": "Sivutuotteet",
                    "condition": {"field_id": "process_description", "contains_any": ["sivutuote"]},
                    "severity": "recommendation",
                }
            ],
        }
        results, _notes = _normalize_criteria_results(
            [{"criteria_id": "byproducts", "status": "yellow", "rationale": "x"}],
            criteria,
            {"byproducts": "Kyllä, haetaan luokittelua.", "process_description": "Prosessit kuvattu."},
            [],
        )
        self.assertEqual(results[0]["status"], "yellow")

    def test_a_too_large_chunk_does_not_end_the_rule_search(self) -> None:
        chunks = [
            {"source_id": "a", "title": "a.pdf", "primary_type": "other", "type_tags": [], "delivery_scope": "official_submission", "text": "sivutuote " + "x" * 2000},
            {"source_id": "b", "title": "b.pdf", "primary_type": "other", "type_tags": [], "delivery_scope": "official_submission", "text": "sivutuote lyhyt maininta."},
        ]
        selected = _select_evidence(
            chunks,
            [{"id": "byproducts", "title": "Sivutuotteet", "instruction": "sivutuotteet jätelaki"}],
            budget_chars=600,
        )
        self.assertEqual([item["source_id"] for item in selected["excerpts"]], ["b"])


class PolicyConflictFilterTest(unittest.TestCase):
    """Agent findings that contradict the app's own stated policy are dropped."""

    def _issue(self, **kwargs):
        from app.domains.lupademo.services.domain import Issue

        base = dict(id="x", type="inconsistent", severity="major", title="", detail="", confidence=0.8)
        base.update(kwargs)
        return Issue(**base)

    def test_consultant_business_id_is_not_the_applicants_contradiction(self) -> None:
        from app.domains.lupademo.services.application import _demote_policy_conflicts

        issues = _demote_policy_conflicts([
            self._issue(
                id="consistency-2",
                title="Hakijan Y-tunnuksen ristiriita lomakedatan ja liitteen välillä",
                detail="Liitteen on laatinut AFRY Finland Oy ja sen Y-tunnus 0625905-6 poikkeaa hakijan Y-tunnuksesta.",
            ),
        ])
        ids = {issue.id for issue in issues}
        self.assertNotIn("consistency-2", ids)
        self.assertIn("policy_filter_note", ids)

    def test_unsynced_common_data_is_not_a_contradiction(self) -> None:
        from app.domains.lupademo.services.application import _demote_policy_conflicts

        issues = _demote_policy_conflicts([
            self._issue(
                id="consistency-3",
                title="Yhteisissä tiedoissa Hankkeen yleiskuvaus on tyhjä, lomakedatassa on kuvaus",
                detail="Yhteisten tietojen permit_basis on tyhjä.",
            ),
        ])
        self.assertNotIn("consistency-3", {issue.id for issue in issues})

    def test_a_real_capacity_contradiction_survives(self) -> None:
        from app.domains.lupademo.services.application import _demote_policy_conflicts

        issues = _demote_policy_conflicts([
            self._issue(
                id="consistency-4",
                title="Metaanin tuotantokapasiteetti eri lähteissä ristiriidassa",
                detail="Hakemuksessa 15 000 t/a, liitteessä 12 000 t/a ilman selitystä.",
            ),
        ])
        self.assertEqual([issue.id for issue in issues], ["consistency-4"])

    def test_summary_vs_document_detail_is_a_completeness_note(self) -> None:
        from app.domains.lupademo.services.application import _demote_policy_conflicts

        issues = _demote_policy_conflicts([
            self._issue(
                id="consistency-5",
                title="Kaavoituksen ristiriita lomakkeen ja liitteen välillä",
                detail="Lomakkeella kaavatilanne on kuvattu suppeammin kuin liitteessä, jossa asia kerrotaan tarkemmin.",
            ),
        ])
        demoted = next(issue for issue in issues if issue.id == "consistency-5")
        self.assertEqual(demoted.type, "incomplete")
        self.assertEqual(demoted.severity, "minor")
        self.assertTrue(any(issue.id == "policy_reclassify_note" for issue in issues))

    def test_annual_production_vs_storage_is_not_a_contradiction(self) -> None:
        from app.domains.lupademo.services.application import _demote_policy_conflicts

        issues = _demote_policy_conflicts([
            self._issue(
                id="consistency-6",
                title="Vetykapasiteetti ristiriidassa lomakedatan ja kemikaaliluettelon välillä",
                detail="Lomakkeella vety 7 500 t/v, kemikaaliluettelossa vetyä varastoidaan 10 t säiliöissä.",
            ),
        ])
        demoted = next(issue for issue in issues if issue.id == "consistency-6")
        self.assertEqual(demoted.type, "incomplete")
        self.assertEqual(demoted.severity, "minor")
        self.assertTrue(demoted.title.startswith("Täsmennettävä:"))


class NoMandatoryDocumentContractTest(unittest.TestCase):
    """No document is hard-required in the demo lane: whatever was uploaded is the package."""

    def _issue(self, **kwargs):
        from app.domains.lupademo.services.domain import Issue

        base = dict(id="x", type="inconsistent", severity="major", title="", detail="", confidence=0.8)
        base.update(kwargs)
        return Issue(**base)

    def test_schema_requires_no_documents(self) -> None:
        schema = load_form_schema(PERMIT)
        self.assertEqual(schema.get("required_attachments"), [])

    def test_missing_documents_are_advisory_never_blocking(self) -> None:
        """No document is mandatory: a missing attachment may be major, never blocking."""
        from app.domains.lupademo.services.application import deterministic_issues

        issues = deterministic_issues(PERMIT, {}, [])
        attachment_issues = [issue for issue in issues if issue.attachment_id]
        for issue in attachment_issues:
            self.assertIn(
                issue.severity,
                {"minor", "major", "recommendation"},
                f"missing attachment {issue.attachment_id} must stay advisory, got {issue.severity}",
            )

    def test_invented_field_ids_do_not_claim_form_fields_are_missing(self) -> None:
        from app.domains.lupademo.services.application import _demote_policy_conflicts

        issues = _demote_policy_conflicts(
            [self._issue(id="completeness-7", type="missing", severity="major", title="Tiivistelmästä puuttuu hankkeen aikataulu", field_id="Tiivistelmän aikataulu")],
            {"permit_basis", "site_location"},
        )
        self.assertIsNone(issues[0].field_id)
        self.assertEqual(issues[0].severity, "major")

    def test_documented_capacity_change_is_a_completeness_note(self) -> None:
        from app.domains.lupademo.services.application import _demote_policy_conflicts

        issues = _demote_policy_conflicts([
            self._issue(
                id="consistency-8",
                title="Metaanin tuotantokapasiteetin arvo ristiriidassa YVA-ajantasaisuuslausunnon kanssa",
                detail="Liitteessä 4 c todetaan, että kapasiteetti on kasvanut 20 MW:sta 25 MW:iin; muutos on dokumentoitu lausunnossa.",
            ),
        ])
        demoted = next(issue for issue in issues if issue.id == "consistency-8")
        self.assertEqual(demoted.type, "incomplete")
        self.assertEqual(demoted.severity, "minor")

    def test_outdated_modelling_stays_a_real_finding(self) -> None:
        from app.domains.lupademo.services.application import _demote_policy_conflicts

        issues = _demote_policy_conflicts([
            self._issue(
                id="consistency-9",
                title="Ilmapäästöjen leviämismallinnus perustuu vanhaan kapasiteettiin",
                detail="Mallinnus on tehty 20 MW:lle, hakemus on 25 MW; vaikutusarviota ei ole päivitetty.",
            ),
        ])
        kept = next(issue for issue in issues if issue.id == "consistency-9")
        self.assertEqual(kept.severity, "major")


class FilledFieldFindingTest(unittest.TestCase):
    """A finding about a filled field is thin content, not a missing required field."""

    def _issue(self, **kwargs):
        from app.domains.lupademo.services.domain import Issue

        base = dict(id="completeness-9", type="missing", severity="minor", title="", detail="", confidence=0.8)
        base.update(kwargs)
        return Issue(**base)

    def test_missing_finding_on_a_filled_field_becomes_incomplete(self) -> None:
        from app.domains.lupademo.services.application import _reclassify_filled_field_findings

        issues = _reclassify_filled_field_findings(
            [self._issue(field_id="site_location", title="Asianosaisliite puuttuu (kiinteistön omistajat/haltijat)")],
            {"site_location": "Hankealue sijaitsee Keravalla, Myllykorventie 20."},
        )

        self.assertEqual(issues[0].type, "incomplete")
        self.assertEqual(issues[0].severity, "minor")

    def test_missing_finding_on_an_empty_field_stays_missing(self) -> None:
        from app.domains.lupademo.services.application import _reclassify_filled_field_findings

        issues = _reclassify_filled_field_findings(
            [self._issue(field_id="site_location", severity="blocking", title="Sijainti on täyttämättä lomakkeella")],
            {"site_location": "   "},
        )

        self.assertEqual(issues[0].type, "missing")
        self.assertEqual(issues[0].severity, "blocking")


class FinnishCheckTextTest(unittest.TestCase):
    """Applicant-facing check text is Finnish, never internal English identifiers.

    The check screen — "Muut parannusehdotukset" in particular — is read by the
    applicant. Field ids (`production_capacity`), attachment types (`byproducts`) and
    finding ids (`consistency-1`) are internal names and must not surface there.
    """

    def _issue(self, **kwargs):
        from app.domains.lupademo.services.domain import Issue

        base = dict(id="x", type="incomplete", severity="minor", title="", detail="", confidence=0.8)
        base.update(kwargs)
        return Issue(**base)

    def test_issue_text_replaces_internal_names_with_finnish_labels(self) -> None:
        from app.domains.lupademo.services.application import _finnish_issue_text

        issues = _finnish_issue_text(
            [
                self._issue(
                    id="consistency-1",
                    title="Täsmennettävä: production_capacity",
                    detail="Lomakkeen byproducts-kohdassa viitataan liitteeseen bat_assessment.",
                    suggested_fix="Täydennä byproducts ja tarkista zoning_context.",
                ),
            ],
            load_criteria(PERMIT),
        )
        text = " ".join([issues[0].title, issues[0].detail, issues[0].suggested_fix])
        self.assertNotRegex(text, r"[a-z]+_[a-z]+")
        self.assertIn("Tuotanto ja tuotteet", text)
        self.assertIn("Sivutuotteet", text)
        self.assertIn("BAT-arviointi", text)

    def test_evidence_source_labels_are_finnish_too(self) -> None:
        from app.domains.lupademo.services.application import _finnish_issue_text

        issues = _finnish_issue_text(
            [
                self._issue(
                    title="Prosessikuvaus on ylimalkainen",
                    detail="Kuvaus jää ylätasolle.",
                    evidence_refs=[
                        {"source_id": "form_data", "title": "Lomakedata: production_capacity", "locator": "production_capacity", "quote": "Metaanin tuotanto 210 GWh/vuosi"},
                    ],
                ),
            ],
            load_criteria(PERMIT),
        )
        ref = issues[0].evidence_refs[0]
        self.assertEqual(ref["title"], "Lomakedata: Tuotanto ja tuotteet")
        self.assertEqual(ref["locator"], "Tuotanto ja tuotteet")
        self.assertEqual(ref["quote"], "Metaanin tuotanto 210 GWh/vuosi")

    def test_dataclass_evidence_refs_are_sanitized(self) -> None:
        """Reconcile verdicts build EvidenceRef objects; those leaked 'criteria' labels."""
        from app.domains.lupademo.services.application import _finnish_issue_text
        from app.domains.lupademo.services.domain import EvidenceRef

        issues = _finnish_issue_text(
            [
                self._issue(
                    title="Kaavoitus on osittain täytetty",
                    detail="Kaavaote puuttuu.",
                    evidence_refs=[
                        EvidenceRef(source_id="zoning_context", title="Näyttö kentästä zoning_context", locator="criteria"),
                    ],
                ),
            ],
            load_criteria(PERMIT),
        )
        ref = issues[0].evidence_refs[0]
        self.assertEqual(ref["locator"], "kriteeristö")
        self.assertEqual(ref["title"], "Näyttö kentästä Kaavoitus")

    def test_english_source_labels_become_finnish(self) -> None:
        from app.domains.lupademo.services.application import _finnish_public_text

        self.assertEqual(_finnish_public_text("help", {}), "ohje")
        self.assertEqual(_finnish_public_text("criteria", {}), "kriteeristö")
        self.assertEqual(_finnish_public_text("Form_data", {}), "lomakedata")

    def test_unknown_identifiers_are_cut_rather_than_shown(self) -> None:
        from app.domains.lupademo.services.application import _finnish_public_text

        text = _finnish_public_text(
            "Havainto koskee kenttää unknown_metric ja havaintoa consistency-7.",
            {},
        )
        self.assertNotIn("unknown_metric", text)
        self.assertNotIn("consistency-7", text)
        self.assertTrue(text.startswith("Havainto koskee kenttää"))

    def test_policy_note_counts_findings_instead_of_naming_them(self) -> None:
        from app.domains.lupademo.services.application import _demote_policy_conflicts

        issues = _demote_policy_conflicts(
            [
                self._issue(
                    id="consistency-2",
                    type="inconsistent",
                    title="Hakijan Y-tunnuksen ristiriita lomakedatan ja liitteen välillä",
                    detail="Liitteen on laatinut AFRY Finland Oy ja sen Y-tunnus 0625905-6 poikkeaa hakijan Y-tunnuksesta.",
                ),
            ]
        )
        note = next(issue for issue in issues if issue.id == "policy_filter_note")
        visible = " ".join([note.title, note.detail, note.suggested_fix or ""])
        self.assertNotIn("consistency", visible)
        self.assertNotIn("policy", visible)
        self.assertIn("1", visible)
