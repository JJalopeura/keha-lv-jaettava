# KEHA LV AI-demo

Tämä on KEHA LV -lupahakemusdemon erillinen jaettava paketti. Se käynnistyy
oletuksena paikallisesti mock-LLM:llä, joten se ei tarvitse Azure- tai muita
ulkoisia yhteyksiä.

## Käynnistys

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./run.sh
```

Avaa selaimella http://127.0.0.1:8001 (portti on muutettavissa `APP_PORT`-ympäristömuuttujalla).

## Testit

```bash
.venv/bin/python -m unittest discover tests
```

Testit ajetaan mock-LLM:llä ja väliaikaiseen tietokantaan, joten ne eivät
koske `data/`-hakemistoon eivätkä tarvitse Azure-yhteyttä.

## Aineisto ja data

- `testiaineisto/esimerkkihakemukset/` sisältää erilliset testipaketit.
- `docs/archive/v5-specs/` sisältää määrittelydokumentit, joita sovelluskoodi ei lataa.
- `data/` syntyy ensimmäisellä käynnistyksellä ja on jätetty versionhallinnan ulkopuolelle.
- `.env.example` näyttää vain paikkamerkit mahdolliselle Azure OpenAI -asetukselle.

Älä jaa omaa `.env`-tiedostoa, `data/`-hakemistoa tai siitä syntyviä
lähetettyjä liitteitä ilman erillistä tietosuojatarkistusta.

## Demo-API

`app/domains/lupademo/tenants.json` sisältää vain näkyviä demoavaimia
(`demo-…-key`). Ne ovat käyttöliittymän harjoitusavaimia, eivät tunnuksia
eivätkä sovellu tuotantoturvaksi.
