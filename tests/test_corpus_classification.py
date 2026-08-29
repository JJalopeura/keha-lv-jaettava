"""Corpus classification ground truth (P2X/Ren-Gas/Sharpcell esimerkkihakemukset).

Pinpoints the filenames that criteria rules depend on; guards the
classify_attachment rule table against regressions.
"""
import unittest

from app.domains.lupademo.services.intake import classify_attachment

EXPECTED = {
    # P2X (Joensuu, ISAVI-12315-2024)
    "P2X_Solutions_Oy_Joensuu_Ympäristölupahakemus_www.pdf": "environmental_application",
    "Liite 14a. Ympäristövaikutusten arviointiselostus_www.pdf": "yva_statement",
    "Liite 14b. Perusteltu päätelmä.pdf": "yva_statement",
    "Liite 14c. Lausunto perustellun päätelmän ajantasaisuudesta P2X Solutions.pdf": "judgement_freshness_assessment",
    "Liite 1. DNSH-selvitys.pdf": "dnsh_statement",
    "Liite 16. BAT-arviointi.pdf": "bat_assessment",
    "Liite 5. Lausunto Tukes.pdf": "tukes_statement",
    "Liite 10. Methyl Formate safety data sheet.pdf": "chemical_inventory_or_sds",
    "Liite 13. Ilmanlaatumallinnus.pdf": "air_quality_model",
    "Liite 12. Hulevesijärjestelmä.pdf": "stormwater_plan",
    "Liite 17. Tarkkailusuunnitelma.pdf": "monitoring_plan",
    "Liite 15. Ympäristöriskeihin varautumissuunnitelma.pdf": "risk_preparedness_plan",
    "Liite 7. Perustilaselvitys_www.pdf": "baseline_report",
    "Liite 3. Sijaintikartta.pdf": "site_map",
    "Liite 6. Asemakaavaote.pdf": "zoning_extract",
    # Ren-Gas (Kerava, ESAVI-14858-2025)
    "Ren-Gas Kerava - Ympäristölupahakemus 2025_0415.pdf": "environmental_application",
    "Liite 4 a. YVA-selostus ja perusteltu päätelmä_Ren-Gas Kerava.pdf": "yva_statement",
    "Liite 4 b. Perustellun päätelmän yhteenveto_Ren-Gas Kerava.pdf": "yva_statement",
    "Liite 4 c. UUDELY_lausunto Ren-Gas_YVA_ajantasaisuus_24032025.pdf": "judgement_freshness_assessment",
    "Liite 13 BAT-vertailu_Ren-Gas Kerava.pdf": "bat_assessment",
    "Liite 7. Kemikaaliluettelo_Ren-Gas Kerava.pdf": "chemical_inventory",
    "Liite 8. Savukaasumallinnus_Ren-Gas Kerava.pdf": "air_quality_model",
    "Ren-Gas Kerava - Melumallinnus päivitetty 2025_03.pdf": "noise_model",
    "Liite 10. Arvio hulevesien vaikutuksista Keravanjoen pohjaeliöstöön.pdf": "stormwater_plan",
    "Liite 12. Ennaltavarautumissuunnitelma Ren-Gas Kerava.pdf": "risk_preparedness_plan",
    "Ren-Gas Kerava - Perustilaselvitys päivitetty 2025_0417.pdf": "baseline_report",
    "Liite 14 a. Etelä-Suomen aluehallintoviraston päätös Nro 65-2019.pdf": "other",
    # Sharpcell (Valkeakoski, LSSAVI-8122-2025)
    "Ympäristölupahakemus_ SharpCell Oy 3_2025 Projekti Aurora_www.pdf": "environmental_application",
    "Lupahakemus-3688813-2025-04-16-10.15_www.pdf": "environmental_application",
    "Yleiskaava_Tervasaari_www.pdf": "zoning_extract",
    "Asemakaava_Tervasaari.pdf": "zoning_extract",
    "Yleiskaavamääräykset_Tervasaari_www.pdf": "zoning_extract",
    "Kemikaaliluettelo_www.xlsx": "chemical_inventory",
    "Sharpcell Oy Tervasaaren tehtaan tarkkailusuunnitelma 4_2025_www.pdf": "monitoring_plan",
    "SharpCell Oy VLK Ennaltavarautumissuunnitelma ympäristöriskit_www.pdf": "risk_preparedness_plan",
    "SharpCell Valkeakoski, kuivapaperikoneen meluselvitys_03092025_www.pdf": "noise_model",
    "Karttakuva_yl_836_toiminnansijainti-2.pdf": "site_map",
    "Täydennyspyyntö jätevesiin liittyen asiassa LSSAVI_8122_2025.pdf": "other",
    # NFD (macOS zip) unicode forms must classify identically
    "Liite 14b. Perusteltu päa\u0308telma\u0308.pdf": "yva_statement",
    "Ren-Gas Kerava - Perustilaselvitys pa\u0308ivitetty 2025_0417.pdf": "baseline_report",
}


class CorpusClassification(unittest.TestCase):
    def test_corpus_files_classify_correctly(self):
        mismatches = {name: classify_attachment(name) for name, want in EXPECTED.items() if classify_attachment(name) != want}
        self.assertEqual(mismatches, {}, "misclassified corpus files")


if __name__ == "__main__":
    unittest.main()
