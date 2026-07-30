from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Optional
import uuid



class MockLLMClient:
    model = "mock"

    def create_vector_store(self, name: str) -> str:
        return f"vs_mock_{uuid.uuid4().hex}"

    def upload_file_from_path(self, path: str) -> tuple[str, str]:
        return (f"upload_mock_{uuid.uuid4().hex}", f"file_mock_{uuid.uuid4().hex}")

    def attach_file_to_vector_store(self, vector_store_id: str, file_id: str) -> str:
        return f"vs_file_mock_{uuid.uuid4().hex}"

    def wait_vector_store_file_completed(self, vector_store_id: str, vs_file_id: str) -> None:
        pass

    def _seed(self, text: str) -> int:
        return int(hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:8], 16)
    def _response_id(self, input_text: str) -> str:
        return f"mock-{self._seed(input_text):08x}"

    def _mock_json(self, input_text: str) -> Dict[str, Any]:
        low = (input_text or "").lower()
        seed = self._seed(input_text)

        if "lupahakemuksen kenttäkohtainen ai-avustaja" in low:
            field_id = self._extract_first(r'"field_id"\s*:\s*"([^"]+)"', input_text) or self._extract_first(r'"id"\s*:\s*"([^"]+)"', input_text) or "project_description"
            return {
                "request_id": f"mock-{seed:08x}",
                "criteria_version": "mock-criteria-v1",
                "suggestions": [self._lupademo_suggestion(field_id, seed)],
                "warnings": [],
            }

        if "lupahakemuksen lomakekenttien esitäyttöön erikoistunut ai-avustaja" in low:
            field_ids = self._extract_all(r'"id"\s*:\s*"([^"]+)"', input_text)[:5] or ["project_description"]
            return {
                "request_id": f"mock-{seed:08x}",
                "criteria_version": "mock-criteria-v1",
                "suggestions": [self._lupademo_suggestion(field_id, seed + i) for i, field_id in enumerate(dict.fromkeys(field_ids))],
                "warnings": [],
            }

        if "lupahakemusten laadunvarmistaja" in low or "lupahakemusten ristiriitojen tunnistaja" in low:
            return {"issues": []}

        if "lupahakemuksen kriteerivertailija" in low:
            return {
                "criteria_results": [
                    {
                        "criteria_id": "mock_criteria",
                        "status": "yellow",
                        "rationale": "Mock-arvio: tieto on osittain riittävä ja käsittelijän tarkistettava.",
                        "related_fields": [],
                        "issues": [],
                        "evidence_refs": [self._lupademo_evidence("mock_criteria")],
                        "confidence": 0.62,
                    }
                ]
            }

        if "hakijaa auttava lupahakemuksen esitarkistusavustaja" in low:
            return {
                "summary": "Hakemus on esitarkistettu mock-agentilla. Käsittelijän päätöstä ei tehdä.",
                "readiness": "almost_ready",
                "score": 76,
                "sections": [
                    {"title": "Tarkistettavat kohdat", "items": ["Täsmennä liitteiden ajantasaisuus ennen lähetystä."]},
                    {"title": "AI-huomio", "items": ["Kaikki havainnot ovat ehdotuksia."]},
                ],
                "blocking_issue_count": 0,
                "major_issue_count": 1,
            }

        if "viranomaiskäsittelijän valmisteluavustaja" in low:
            return {
                "summary_md": "## Yhteenveto\n\nHakemus on koottu käsittelijän arvioitavaksi. AI ei tee päätöstä.",
                "key_facts": ["Hakemus sisältää lomaketiedot ja liiteyhteenvedot.", "Mahdolliset avoimet kohdat tulee tarkistaa."],
                "open_questions": ["Ovatko kaikki viranomaiselle toimitettavat liitteet mukana?"],
                "evidence_refs": [self._lupademo_evidence("check_report")],
                "confidence": 0.7,
            }

        if "täydennyspyyntöluonnos" in low:
            return {
                "subject": "Täydennyspyyntöluonnos lupahakemukseen",
                "body_md": "Hyvä hakija,\n\npyydämme täydentämään alla kuvatut kohdat. Tämä on käsittelijän muokattava luonnos.",
                "requested_items": [
                    {"title": "Tarkennettava tieto", "reason": "AI havaitsi mahdollisen puutteen.", "related_issue_id": "mock_issue", "evidence_refs": [self._lupademo_evidence("check_report")]}
                ],
                "review_notes_for_caseworker": ["Tarkista määräpäivä ja säädösviittaukset ennen lähettämistä."],
            }

        if "viranomaisen valmistelumuistion luonnostelija" in low:
            return {
                "memo_md": "## Valmistelumuistio\n\nTämä on käsittelijälle tarkoitettu luonnos, ei päätös.",
                "facts": ["Lupatyyppi ja hakemuksen tila on tarkistettava järjestelmästä."],
                "ai_observations": ["Liitteet ja lomaketiedot vaikuttavat pääosin jäsentyneiltä."],
                "caseworker_checks": ["Tarkista toimivalta, liitteiden riittävyys ja mahdolliset täydennykset."],
                "evidence_refs": [self._lupademo_evidence("submission_package")],
            }

        if "lupahakemusten liitteiden luokitteluun erikoistunut ai-avustaja" in low:
            file_id = self._extract_first(r'"file_id"\s*:\s*"([^"]+)"', input_text) or "mock_file"
            filename = self._extract_first(r"filename[:\s]+([^\n]+)", input_text) or "liite.pdf"
            return {
                "file_id": file_id,
                "primary_type": "site_plan" if "asemapiir" in low else "general_attachment",
                "type_tags": ["mock_classified"],
                "summary": f"Mock-luokittelu tiedostolle {filename.strip()[:80]}.",
                "suggested_delivery_scope": "official_submission",
                "evidence_refs": [self._lupademo_evidence(file_id)],
                "confidence": 0.72,
            }

        if "epärakenteisen lupahakemustiedon rakenteistaja" in low:
            return {
                "request_id": f"mock-{seed:08x}",
                "extracted_fields": [
                    {
                        "field_id": "project_description" if "project_description" in low else "activity_description",
                        "value": "Mock-rakenteistus lähdetekstin perusteella.",
                        "confidence": 0.58,
                        "evidence_refs": [self._lupademo_evidence("source_text")],
                    }
                ]
            }

        if '"criteria"' in low or "kriteerien arviointi" in low or "7 §" in low:
            return {"criteria": self._criteria_template(seed)}

        if '"actions"' in low or "toimenpidelista" in low:
            return {
                "actions": [
                    {
                        "type": "request_to_applicant",
                        "text": "Täsmennä kassavirtaennusteen oletukset 12 kk aikajänteellä.",
                        "evidence_refs": ["Liite_4_Tulos_ja_kassavirtaennuste_2026-2028.docx"],
                    },
                    {
                        "type": "request_to_applicant",
                        "text": "Toimita omarahoitusosuuden lähde ja vakuusjärjestelyt eriteltynä.",
                        "evidence_refs": ["Liite_5_Omarahoitus_ja_vakuudet.docx"],
                    },
                    {
                        "type": "internal_check",
                        "text": "Varmista toimialan rajausehdot ja suomalaisen intressin täyttyminen.",
                        "evidence_refs": ["Liite_2_Liiketoimintasuunnitelma.docx", "Yrityksen perustiedot"],
                    },
                ]
            }

        if '"language"' in low and '"deliverables"' in low and '"executive_summary_md"' in low:
            return {
                "language": "fi",
                "title_suggestion": "Asiakkaan tarjousvastauksen runko",
                "executive_summary_md": "## Yhteenveto\nMock-intake analyysi tunnisti keskeiset vaatimukset ja riskit.",
                "rfp_summary_short": "Tarjouspyyntö painottaa toimituskykyä, laatua ja referenssejä.",
                "evaluation_breakdown": {"price_weight": 40, "quality_weight": 60, "price_components": [], "quality_components": []},
                "deliverables": [
                    {
                        "id": "solution_overview",
                        "title": "Ratkaisukuvaus",
                        "type": "document",
                        "purpose": "Kuvaa ehdotettu ratkaisu",
                        "format": "docx",
                        "max_pages": "10",
                        "constraints": ["Selkeys", "Perusteltavuus"],
                        "enabled": True,
                        "suggested_sections": [{"title": "Nykytila", "level": 1, "notes": ""}],
                    }
                ],
                "key_dates": [],
                "evaluation_criteria": ["Laatu", "Hinta", "Toimituskyky"],
                "expert_requirements": [],
                "risks": ["Aikatauluriski"],
                "open_questions_for_customer": ["Tarvitaanko vaiheistus?"],
                "assumptions": ["Asiakas toimittaa lähtöaineiston ajoissa."],
            }

        return {
            "mock": True,
            "note": "Demo mode response",
            "summary": "Rakenteinen mock-vastaus",
            "input_preview": (input_text or "")[:220],
        }

    def _extract_first(self, pattern: str, text: str) -> Optional[str]:
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        return match.group(1) if match else None

    def _extract_all(self, pattern: str, text: str) -> list[str]:
        return re.findall(pattern, text or "", flags=re.IGNORECASE)

    def _lupademo_evidence(self, source_id: str) -> dict[str, Any]:
        return {
            "source_id": source_id,
            "title": "Mock-lähdeaineisto",
            "locator": "mock",
            "quote": "Mock-vastaus perustuu annettuun promptikontekstiin.",
            "confidence": 0.7,
        }

    def _lupademo_suggestion(self, field_id: str, seed: int) -> dict[str, Any]:
        values = {
            "stormwater_management": "Hulevedet hallitaan ensisijaisesti tontilla viivyttämällä ja imeyttämällä. Ylivirtaama johdetaan hallitusti kunnan hulevesiverkostoon suunnitelmien mukaisesti.",
            "project_description": "Hankkeessa toteutetaan lupahakemuksessa kuvattu rakennus- tai toimintakokonaisuus. Kuvaus on tarkistettava suunnitelmista ennen hyväksyntää.",
            "activity_description": "Toiminta koostuu hakemuksessa kuvatusta tuotannosta, varastoinnista ja vaikutusten hallinnasta.",
            "event_description": "Tapahtuma on yleisölle avoin kaupunkitapahtuma, jonka ohjelma, aikataulu ja vastuuhenkilöt täsmennetään hakemuksessa.",
        }
        return {
            "field_id": field_id,
            "suggested_value": values.get(field_id, f"Täydennä kenttä {field_id} hakemuksen ja lähdeaineiston perusteella."),
            "rationale": "Mock-agentti muodosti ehdotuksen annetusta lomake-, kriteeri- ja lähdekontekstista.",
            "confidence": 0.65 + ((seed % 12) / 100),
            "evidence_refs": [self._lupademo_evidence(field_id)],
            "warnings": ["Tarkista ehdotus ennen hyväksyntää."],
        }

    def _criteria_template(self, seed: int) -> list[dict[str, Any]]:
        verdicts = ["green", "yellow", "red"]
        criteria = [
            "Maksukyky",
            "Taloudelliset edellytykset",
            "Kehitysmahdollisuudet",
            "Suomalainen intressi",
            "Ympäristö- ja vaikutusnäkökohdat",
            "Elinkeinopoliittiset näkökohdat",
        ]
        out = []
        for i, c in enumerate(criteria):
            # Deterministinen vaihtelu; mukana myös punaisia demoja varten.
            verdict = verdicts[(seed + i) % 3]
            out.append(
                {
                    "criteria": c,
                    "verdict": verdict,
                    "rationale": f"{c}: arvio perustuu hakemuksen tietoihin ja liitteiden uskottavuuteen.",
                    "evidence_refs": ["Liiketoimintasuunnitelma", "Tulos- ja kassavirtaennuste"],
                }
            )
        return out

    def _mock_text(self, input_text: str) -> str:
        low = (input_text or "").lower()

        if "esitarkistukseen erikoistunut" in low or "esiarvio" in low:
            return (
                "## 1) Yleistilanne\n"
                "Hakemus on kokonaisuutena hyvin jäsennelty, mutta muutama kohta kannattaa täsmentää ennen lähettämistä.\n\n"
                "## 2) Havaitut vahvuudet\n"
                "- Liiketoiminnan kuvaus on selkeä ja kohdemarkkina rajattu.\n"
                "- Rahoituksen käyttötarkoitus on looginen kasvutavoitteisiin nähden.\n\n"
                "## 3) Kehityskohdat\n"
                "- Talous: avaa kassavirtaennusteen keskeiset oletukset.\n"
                "- Rahoitus: täsmennä omarahoitusosuuden lähde ja vakuusmalli.\n"
                "- Liitteet: varmista, että kaikki vaaditut liitteet ovat mukana.\n\n"
                "## 4) Riskihavainnot\n"
                "- Arvioinnin kannalta huomioitavaa on ennusteiden herkkyys kysyntämuutoksille.\n\n"
                "## 5) Suositeltavat seuraavat askeleet\n"
                "1. Täydennä kassavirtaoletukset.\n"
                "2. Lisää omarahoitus- ja vakuusliite.\n"
                "3. Tarkista liitteiden yhdenmukaisuus."
            )

        if "käsittelijälle nopeasti silmäiltävä" in low or "caseworker summary" in low:
            return (
                "## 1) Yhteenveto\nHakemus on käsiteltävissä, mutta vaatii kaksi lisäselvitystä ennen lopullista ratkaisua.\n\n"
                "## 2) Vahvuudet\n- Selkeä liiketoimintamalli\n- Uskottava kasvusuunta\n- Liitepaketti on laaja\n\n"
                "## 3) Huomiot\n- Kassavirtaennusteessa epävarmuuksia\n- Omarahoitusosuuden dokumentointi osin kesken\n- Vakuustiedot vaativat tarkennusta\n\n"
                "## 4) Avoimet kysymykset\n- Miten sesonkivaihtelu vaikuttaa velanhoitoon?\n- Mitkä vakuudet ovat heti käytettävissä?\n- Onko tilauskanta vahvistettu?"
            )

        if "portfolio analyst" in low or "osastopäällikön analyytikko" in low:
            return (
                "## 1) Yleistilanne\nPortfolio on vakaa, mutta käsittelyaika pitenee hakemuksissa joissa liitepaketti on puutteellinen.\n\n"
                "## 2) Keskeiset ajurit\n- Investointihakemusten kasvu\n- Käyttöpääomarahoituksen tarve\n- Toimialakohtainen kausivaihtelu\n\n"
                "## 3) Pullonkaulat\n- Puuttuvat talousliitteet\n- Hidas lisäselvityskierros\n- Vakuustietojen epäyhtenäisyys\n\n"
                "## 4) Riski- ja mahdollisuushavainnot\n- Riski: läpimenoajan kasvu\n- Mahdollisuus: priorisointi kriteeriväreillä\n\n"
                "## 5) Suositellut toimet\n- Käynnistä puutteellisten hakemusten fast-track lisäselvityspyyntö.\n- Seuraa SLA-riskihakemuksia päivittäin."
            )

        if "riskihavainnot" in low:
            return "## Riskihavainnot\n- Kassavirran vaihtelu nostaa lyhyen aikavälin epävarmuutta.\n- Vakuuksien kattavuus tulee varmistaa."

        if "tuotesoveltuvuus" in low:
            return "## Tuotesoveltuvuus\nHakemus soveltuu alustavasti haettuun rahoitustuotteeseen, kun omarahoitus ja vakuudet täsmennetään."

        if "suositus" in low:
            return "## Suositus\nEtene ehdolliseen hyväksyntään. Edellytä lisäselvitykset ennen lopullista päätöstä."

        return "Hei! Olen AI-apuri ja autan sinua hakemuksen kanssa. Kysy rohkeasti hakemuksen tiedoista, liitteistä tai tarkistuksista — vastaan suomeksi käyttäen hakemuksesi tietoja."

    def responses(
        self,
        *,
        input_text: str,
        vector_store_ids: list[str] | None,
        response_format: str | None,
        max_output_tokens: int | None,
        previous_response_id: str | None = None,
        reasoning_effort: str | None,
        text_verbosity: str | None,
    ) -> dict[str, Any]:
        if response_format == "json_object":
            payload = self._mock_json(input_text or "")
            output_text = json.dumps(payload, ensure_ascii=False)
        else:
            output_text = self._mock_text(input_text or "")
        return {
            "id": self._response_id(input_text or ""),
            "output_text": output_text,
        }

    def extract_output_text(self, resp: dict[str, Any]) -> str | None:
        return resp.get("output_text")

    def responses_stream(self, *, input_text: str, **kwargs: Any):
        """Simulate streaming by yielding mock text in chunks."""
        text = self._mock_text(input_text or "")
        for i in range(0, len(text), 3):
            yield text[i:i+3]
