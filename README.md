# KEHA LV — lupahakemuspalvelu

Tekoälyavusteinen lupahakemuspalvelu (LV) kuntien ja aluehallinnon käyttöön.
Sovellus tekee kaikki AI-kutsut **Azure AI Foundryn** kautta; mukana ei ole
mock-LLM:ää, offline-tilaa eikä demoaineiston esitäyttöä.

## Käynnistys

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # täytä Foundry-endpoint, deployment-nimet ja avain
./run.sh
```

Avaa selain: http://127.0.0.1:8003 (portti vaihdettavissa `APP_PORT`-muuttujalla).

Julkinen ympäristö: **https://keha-lv.kindplant-ccd82e27.swedencentral.azurecontainerapps.io**
— Container App `keha-lv` ECDX-ympäristössä `luvn-finbert-svc-env`.
Ks. [docs/deployment-ecdx.md](docs/deployment-ecdx.md) (julkaisu, asetukset, rajoitteet).

Palvelin ei käynnisty väärin asetuksin: jos endpoint, deployment-nimi tai avain
puuttuu, AI-kutsu epäonnistuu näkyvästi sen sijaan että palvelu vaikenisi
varavastaukseen.

## Azure AI Foundry -asetukset

`.env` (ei koskaan versionhallintaan):

| Muuttuja | Merkitys |
|---|---|
| `AZURE_OPENAI_HOST_CORE` / `_CHAT` | Foundry-resurssin endpoint, esim. `https://<resurssi>.services.ai.azure.com` |
| `AZURE_OPENAI_MODEL_CORE` | Deployment, joka tekee raskaan JSON-työn (kenttäehdotukset, aineistoanalyysi, tarkistukset) |
| `AZURE_OPENAI_MODEL_CHAT` | Deployment, joka tekee keskustelevan proosan (chat, yhteenvedot, muistiot) |
| `AZURE_OPENAI_API_KEY_CORE` / `_CHAT` | Foundry-resurssin avain |
| `KEHA_LV_SESSION_SECRET` | Istuntocookien allekirjoitusavain (pitkä satunnainen arvo) |

Oletusarvo tässä paketissa on asiakaspalvelun Foundry-projekti
(`foundry-ecdx-cs-proto-project`): core = `DeepSeek-V4-Flash`,
chat = `gpt-6-luna`.

## Testit

```bash
PYTHONWARNINGS=ignore .venv/bin/python -m unittest discover tests
```

Testit eivät kutsu Azurea. `tests/fake_llm.py` on deterministinen testitupla,
joka asennetaan monkeypatchaamalla `create_llm_client` sovelluksen kutsupisteissä
— tuotantokoodissa ei ole mock-provideria. Testit käyttävät väliaikaista
tietokantaa eivätkä tarvitse `data/`-hakemistoa tai tunnuksia.

## Data

- `data/` syntyy ensimmäisellä käynnistyksellä (SQLite + `uploads/`), gitignoressa.
- `testiaineisto/esimerkkihakemukset/` sisältää esimerkkiaineistoa.
- `docs/archive/v5-specs/` on historiallista suunnitteluaineistoa, ei ajonaikaista.

Älä jaa omaa `.env`-tiedostoa, `data/`-hakemistoa tai lähetettyjä liitteitä ilman
tietosuojatarkistusta.

## Suggestion service -API

`app/domains/lupademo/tenants.json` sisältää näkyvät demoavaimet
(`demo-…-key`). Ne ovat harjoitusavaimia, eivät tuotantotunnuksia.
