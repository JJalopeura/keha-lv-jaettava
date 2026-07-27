# Määrittelydokumentit

Tämän hakemiston tiedostoja ei ladata sovelluskoodissa. Ne ovat suunnittelu- ja
määrittelydokumentteja, jotka siirrettiin pois `app/`-paketista, jotta
ajettavan sovelluksen ja dokumentaation ero on selvä.

- `v5/` — vaiheen 5 määrittelyt (näkymät, API-sopimukset, telemetria,
  hyväksymistestit, käyttäjäpolut, visuaaliset tokenit). Tiedostot ovat YAML-
  muodossa eikä projektissa ole YAML-kirjastoa, joten koodi ei voi lukea niitä.
- `*.json` — varhaisia domain-, rooli-, käyttöliittymä- ja työnkulkuluonnoksia.

Sovelluksen oikeasti käyttämät määrittelyt ovat `app/domains/lupademo/specs/`
-hakemistossa (`form_schemas/`, `criteria_sets/`, `prompts/`,
`case_inventory.json`, `scenarios/`) sekä `app/domains/lupademo/tenants.json`.
