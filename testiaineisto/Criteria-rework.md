# Kriteeristön uudistus — 4 vaihetta (2026-08-29)

Kohdejoukko: `national_environmental_industrial_demo_v1.json` (ympäristölupa, teollisuus, 22 sääntöä).
Aineisto: `testiaineisto/esimerkkihakemukset/` — P2X.zip (44 tiedostoa), Ren-Gas.zip (19), Sharpcell.zip (47).

## Vaihe 1 — Korpus ja totuusperusta

- Kaikki 3 zip-pakettia purettu: `esimerkkihakemukset/{P2X,Ren-Gas,Sharpcell}_extracted/`.
- Huomio: zippet on luotu macOS:llä → tiedostonimet **NFD-normalisoituja** (yhdistelmämerkit).
  `classify_attachment` ja vaatimusten kattavuustarkistus normalisoivat nimet NFC:ksi ennen vertailua.
- Jokaisen paketin tiedostoluettelo + moottorin tuottama `primary_type` dokumentoitu:
  `P2X.md`, `Ren-Gas.md`, `Sharpcell.md` (pohjana aiempi P2X.md-malli).

## Vaihe 2 — Luokittelusäännöt (`classify_attachment`, intake.py)

Sääntötaulukko korjattu ja järjestetty (ensimmäinen osuma voittaa):
- YVA-kokonaisuus: `perusteltu päätelmä` (myös genetiivi `perustellun päätelmä`) → `yva_statement`;
  `ajantasaisu`-vartalo / `uudely` → `judgement_freshness_assessment` (ennen YVA-sääntöjä).
- Kaavat: `asemakaava`, `yleiskaava`, `kaavaote` → `zoning_extract`.
- `dnsh` → `dnsh_statement` (aiemmin virheellinen `dns-h`-neula ei osunut `DNSH-selvitys`-nimeen).
- Kirjoitusvirhe `uuvely` → `uudely` korjattu myös `ATTACHMENT_REQUIREMENT_TERMS`-taulukosta.
- `chemical_inventory_or_sds` lisätty omaan aliasjoukkoonsa (SDS-liitteet täyttävät kemikaalisäännön).
- `air_monitoring_plan` -vaatimuksen alias: `monitoring_plan`-liite täyttää ilman tarkkailun.
- Pysyvä suoja: `tests/test_corpus_classification.py` (39 tarkistusta, mukana NFD-muodot).

## Vaihe 3 — Kriteeristö (HEAD-koommion `9ce1929` uudistus, tarkennettu tässä)

Säännöt sidottu oikeaan aineistoon (commit `9ce1929`), tässä vaiheessa tarkennettu: uudet liitetyypit (`chemical_inventory_or_sds`,
`judgement_freshness_assessment`, `dnsh_statement`, `tukes_statement`), ehdolliset
kemikaalisäännöt (`condition` `raw_materials_chemicals`-kentälle) ja pakettikohtaiset
perustelut instruktioissa. Vaiheen 2 korjaukset varmistivat, että liitekattavuus
vastaa todellisuutta (alla vaiheen 4 matriisi).

## Vaihe 4 — Validointi

- `attachment_requirement_covered`-matriisi kaikille attachment_type-säännöille × 3 pakettia:
  ainoat puutteet ovat aidot aineistopuutteet (esim. Sharpcellilla ei ole YVA-/BAT-/DNSH-/perustilaliitteitä,
  Ren-Gasilla ei kartta-/tukes-/DNSH-liitteitä, P2X:llä ei melumallinnus- eikä perinteistä kemikaaliluetteloliitettä).
  Ei spuriaisia MISS-tuloksia.
- Testit: `PYTHONWARNINGS=ignore KEHA_LV_FORCE_MOCK_LLM=true .venv/bin/python -m unittest discover tests`
  → 177 testiä OK (ml. uusi korpusluokittelutesti).
- Koodin tila: pi-lens diagnostics 0 blocking errors.

### Sääntökattavuusmatriisi (attachment_type-säännöt, viranomaisliitteinä)

| Sääntö | P2X | Ren-Gas | Sharpcell |
|---|---|---|---|
| yva_statement_required | OK | OK | puuttuu |
| yva_judgement_freshness | OK | OK | puuttuu |
| bat_assessment_required | OK | OK | puuttuu |
| chemical_inventory_via_sds | OK | OK | OK |
| chemical_inventory_required | puuttuu (SDS-käytäntö) | OK | OK |
| monitoring_plan_air | OK | puuttuu | OK |
| risk_preparedness_required | OK | OK | OK |
| risk_preparedness_tukes | OK | puuttuu | puuttuu |
| dns_compliance | OK | puuttuu | puuttuu |
| stormwater_management | OK | OK | puuttuu |
| flue_gas_modeling | OK | OK | puuttuu |
| noise_modeling | puuttuu | OK | OK |
| public_summary_recommended | OK | puuttuu | puuttuu |
| site_map_and_zoning_required | OK | puuttuu | OK |
| baseline_report | OK | OK | puuttuu |

Kenttäsäännöt (permit_basis, competent_authority, waste, sensitive_receptors, activity_description,
water_and_wastewater) evaluoituvat hakemuslomakkeen tietojen perusteella — katso pakettikohtaiset
odotetut tulokset kunkin paketin md-dokumentin Kriteeritarkistus-osiossa.
