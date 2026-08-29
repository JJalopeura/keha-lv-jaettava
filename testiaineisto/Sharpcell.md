# SharpCell Oy — Esimerkkihakemus (Jätevesien käsittely Tervasaari)

**Lupatyyppi:** Ympäristölupa (teollisuus — UPM Tervasaari)
**Sijainti:** Valkeakoski, LSSAVI-8122-2025
**Paketti:** `testiaineisto/esimerkkihakemukset/Sharpcell.zip`
**Koko:** ~130MB (45 tiedostoa, sisältää .xlsx-tiedostoja)

## Yhteenveto

SharpCell-hakemus koskee paperitehtaan jätevesien käsittelyä UPM Tervasaaren
tehtaalla Valkeakoskella. Hakemus on käsitelty Länsi- ja Sisä-Suomen
aluehallintoviraston (LSSAVI) alaisuudessa.

## Liitteet — nimi → primary_type -kartoitus

| Nimi | Suositeltu `primary_type` | Perustelu |
|------|--------------------------|-----------|
| SharpCell Oy Tervasaari jätevesien kuormitusenneuste 28.5.2025_www.xlsx | `other` (kenttä: jätevesikuormitus) | Excel, ei liitetyyppiä — luo kenttäpoiminta jätevesiin |
| Liite 7.6 - Jätejakeet 2024_www.xlsx | `other` (kenttä: jätejakeet) | Excel, ei liitetyyppiä — luo kenttäpoiminta jätteisiin |
| Ympäristöriskit 2025 SharpCell Oy Tervasaari_www.xlsx | `other` (kenttä: ympäristöriskit) | Excel — luo kenttäpoiminta riskeihin |
| Ympäristölupahakemus SharpCell Oy 3_2025 Projekti Aurora_www.pdf | `environmental_application` | "lupahakemu" → ympäristölupahakemus |
| SharpCell Oy Aurora projekti etusijamenettely rev 1_www.pdf | `other` (kenttä: vihreän siirtymä) | Etusijamenettely → luo kenttäpoiminta vihreään siirtymään |
| UPM Tervasaari yleistiedot 21.5.2025_www.pdf | `other` (kenttä: toiminnan kuvaus) | Yleistiedot — luo kenttäpoiminta toiminnasta |
| P2X Joensuu ISAVI-12315-2024 Täydennykset_www.pdf | `other` (kenttä: täydennys) | Täydennykset — luo kenttäpoiminta lisätietoihin |
| P2X Joensuu ISAVI-12315-2024 Täydennykset_www.pdf | `other` (kenttä: täydennys) | Täydennykset — luo kenttäpoiminta lisätietoihin |
| Päätös (3).pdf / Päätöskuulutus_LSSAVI-8122-2025_*.pdf | `other` (kenttä: päätös) | Päätökset ja kuulutukset — luo kenttäpoiminta prosessista |
| Vireilletulotiedot (3).pdf | `other` (kenttä: vireilletulo) | Vireilletulotiedot — luo kenttäpoiminta prosessista |
| Kuulutus_LSSAVI-8122-2025_*.pdf | `other` (kenttä: kuulutus) | Kuulutustiedot — luo kenttäpoiminta prosessista |
| Kirjeenvaihto.pdf / Sähköpostisaate (1-3).pdf | `other` (kenttä: kirjeenvaihto) | Kirjeenvaihto — luo kenttäpoiminta prosessista |
| Täydennys (1-3).pdf / täydennykset sähköpostitse_www.pdf | `other` (kenttä: täydennys) | Täydennykset — luo kenttäpoiminta lisätietoihin |
| Täydennyspyyntö jätevesiin liittyen asiassa LSSAVI_8122_2025.pdf | `other` (kenttä: täydennys) | Täydennyspyyntö jätevesiin — luo kenttäpoiminta lisätietoihin |
| Täydennyspyyntö-20250506-145323.pdf | `other` (kenttä: täydennys) | Täydennyspyyntö — luo kenttäpoiminta lisätietoihin |
| Täydennyspyyntö_2_LSSAVI_8122_2025.pdf | `other` (kenttä: täydennys) | Täydennyspyyntö — luo kenttäpoiminta lisätietoihin |
| Täydennys, kohta 11_www.pdf | `other` (kenttä: täydennys) | Täydennykset — luo kenttäpoiminta lisätietoihin |
| Täydennys, kohta 7_www.pdf | `other` (kenttä: täydennys) | Täydennykset — luo kenttäpoiminta lisätietoihin |
| Täydennys.pdf / täydennys sähköpostitse_www (1).pdf | `other` (kenttä: täydennys) | Täydennykset — luo kenttäpoiminta lisätietoihin |
| Sharpcell Valkeakoski.pdf / kuivapaperikoneen meluselvitys_03092025.pdf | `noise_model` tai `other` (kenttä: melu) | Meluselvitys → "melu" → `noise_model` (jos tunnetaan), muuten kenttä |
| Sharpcell Oy Tervasaari tarkkailusuunnitelma 4_2025.pdf | `monitoring_plan` tai `other` (kenttä: tarkkailu) | Tarkkailusuunnitelma → "tarkkailu" → `monitoring_plan` (jos tunnetaan), muuten kenttä |
| SharpCell Oy VLK Ennaltavarautumissuunnitelma ympäristöriskit_www.pdf | `risk_preparedness_plan` tai `other` (kenttä: varautuminen) | Ennaltavarautumissuunnitelma → "varautum" → `risk_preparedness_plan` (jos tunnetaan), muuten kenttä |
| Sharpcell Valkeakoski, kuivapaperikoneen meluselvitys_03092025.pdf | `noise_model` tai `other` (kenttä: melu) | Meluselvitys → "melu" → `noise_model` (jos tunnetaan), muuten kenttä |
| Sharpcell Raportti_Sharpcell_2025_pölymallinnus.pdf | `other` (kenttä: pölypäästöt) | Pölymallinnus — luo kenttäpoiminta päästöistä |
| Jätevesien tarkkailupiste ja liittymispiste UPM Tervasaaren jätevesilinjaan_www.pdf | `other` (kenttä: jätevedet) | Jätevesien tarkkailu → luo kenttäpoiminta jätevesiin |
| Teollisuusjätevesisopimus 2.10.2025_NEW_www.pdf | `other` (kenttä: jätevesisopimus) | Jätevesisopimus — luo kenttäpoiminta jätevesiin |
| Asemakaava_Tervasaari.pdf / Yleiskaava_Tervasaari_www.pdf | `zoning_extract` tai `site_map` | Kaavat → "kaava" → `zoning_extract` (jos tunnetaan), muuten kenttä |
| Asemakaavamääräykset_Tervasaari_www.pdf / Yleiskaavamääräykset_Tervasaari_www.pdf | `zoning_extract` tai `other` (kenttä: kaavamääräykset) | Kaavamääräykset → "kaava" → `zoning_extract` (jos tunnetaan), muuten kenttä |
| Karttakuva_yl_* 4 tiedostoa (luonto, vesistö, maa, toiminta) | `site_map` tai `other` (kenttä: kartat) | Karttakuva → "kartta" tai "yl_" → `site_map` (jos tunnetaan), muuten kenttä |
| Liite 10. Arvio hulevesien vaikutuksista Keravanjoen pohjaeliöstöön.pdf | `stormwater_plan` tai `other` (kenttä: hulevedet) | Hulevesi → "huleves" → `stormwater_plan` (jos tunnetaan), muuten kenttä |
| Liite 12. Ennaltavarautumissuunnitelma Ren-Gas Kerava.pdf | `risk_preparedness_plan` tai `other` (kenttä: varautuminen) | Ennaltavarautumissuunnitelma → "varautum" → `risk_preparedness_plan` (jos tunnetaan), muuten kenttä |
| Liite 13 BAT-vertailu_Ren-Gas Kerava.pdf | `bat_assessment` tai `other` (kenttä: BAT) | BAT-vertailu → "BAT" → `bat_assessment` (jos tunnetaan), muuten kenttä |
| Liite 14 a/b/c: YVA-selostus, perusteltu päätelmä, UUDELY-lausunto | `yva_statement` / `judgement_freshness_assessment` tai `other` (kenttä: YVA) | YVA → "yva" / perustellun päätelmän ajantasaisuus → `judgement_freshness_assessment` (jos tunnetaan), muuten kenttä |
| Liite 4 a/b/c: YVA-selostus, perusteltu päätelmä yhteenveto, UUDELY_lausunto | `yva_statement` / `judgement_freshness_assessment` tai `other` (kenttä: YVA) | Perustellun päätelmän yhteenveto + UUDELY → `judgement_freshness_assessment` (jos tunnetaan), muuten kenttä |
| Liite 7. Kemikaaliluettelo_Ren-Gas Kerava.pdf | `chemical_inventory` tai `other` (kenttä: kemikaalit) | Kemikaaliluettelo → "kemikaali" → `chemical_inventory` (jos tunnetaan), muuten kenttä |
| Liite 8. Savukaasumallinnus_Ren-Gas Kerava.pdf | `air_quality_model` tai `other` (kenttä: päästöt) | Savukaasumallinnus → "savukaasu" → `air_quality_model` (jos tunnetaan), muuten kenttä |
| Ren-Gas Kerava - Melumallinnus päivitetty 2025_03.pdf | `noise_model` tai `other` (kenttä: melu) | Melumallinnus → "melu" → `noise_model` (jos tunnetaan), muuten kenttä |
| Ren-Gas Kerava - Perustilaselvitys päivitetty 2025_0417.pdf | `baseline_report` tai `other` (kenttä: perustila) | Perustilaselvitys → "perustila" → `baseline_report` (jos tunnetaan), muuten kenttä |
| Ren-Gas Kerava - Täydennys ilmapäästöihin_2025_0527.pdf | `other` (kenttä: ilmapäästöt) | Täydennys ilmapäästöihin — luo kenttäpoiminta päästöistä |
| Ren-Gas Kerava - Ympäristölupahakemus 2025_0415.pdf | `environmental_application` tai `other` (kenttä: lupahakemus) | Lupahakemu → "lupahaku" tai "ympäristölupa" → `environmental_application` (jos tunnetaan), muuten kenttä |
| Päätös 396_2025_www.pdf / Päätöskuulutus_ESAVI-14858-2025_*.pdf | `other` (kenttä: päätös) | Päätökset ja kuulutukset — luo kenttäpoiminta prosessista |
| EI_julkaistavat_asiakirjat (3).pdf / Kuulutus*.pdf | `other` (kenttä: ei-julkiset) | Ei julkaistavat asiakirjat — luo kenttäpoiminta tietosuojaan |
| Vireilletulotiedot (3).pdf | `other` (kenttä: vireilletulo) | Vireilletulotiedot — luo kenttäpoiminta prosessista |

## Huomioita Sharpcellista

- **Ei safety data sheets** — SharpCell käyttää kemikaaliluetteloita Excel-muodossa (Liite 7.6)
- **Eri liitenumerojärjestelmä** — ei P2X/Ren-Gas -tyyppistä "Liite N." järjestelmää
- **Täydennykset** — runsaasti täydennyspyyntöjä ja vastauksia (10+ tiedostoa)
- **Päätös on jo olemassa** — "päätöskuulutus" viittaa siihen, että lupaprosessi on jo käyty
- **Sharpcell ei ole vihreän siirtymän etusija** — P2X/Ren-Gas tapaan, mutta Sharpcellissa ei mainita etusijamenettelyä
