# Deploying KEHA LV to the ECDX environment

Live since 2026-11-09:

**https://keha-lv.kindplant-ccd82e27.swedencentral.azurecontainerapps.io**

Public HTTPS, no authentication in front of the app, no IP restrictions
(`external: true`, `ipSecurityRestrictions: null`, `allowInsecure: false`).

## Where it runs

| | |
|---|---|
| Subscription | `tieto-ecdx-proto-001` (`f65d2df4-565a-4689-8453-2461622ddcf0`) |
| Resource group | `rg-hackathon-ecdx-proto-sdc-001` |
| Container Apps environment | `luvn-finbert-svc-env` (public, no VNet injection) |
| Container app | `keha-lv`, 1 replica, 1 vCPU / 2 GiB, target port 8000 |
| Image | `caed29c7d480acr.azurecr.io/keha-lv:<tag>` |
| Identity (image pull) | `keha-lv-identity`, `AcrPull` on `caed29c7d480acr` |
| Identity (CI deploy) | Entra app `keha-lv-deploy` (`4b105d80-3439-42fb-80e6-5241c0f56597`), OIDC federated to this GitHub repo |

The app's AI calls go to the customer-service Foundry project
`foundry-ecdx-cs-proto/foundry-ecdx-cs-proto-project` — core `DeepSeek-V4-Flash`,
chat `gpt-6-luna` — over the resource's own endpoint, not through this RG.

## Configuration on the container app

Environment variables (non-secret):

```
AZURE_OPENAI_HOST_CORE=https://foundry-ecdx-cs-proto.services.ai.azure.com
AZURE_OPENAI_HOST_CHAT=https://foundry-ecdx-cs-proto.services.ai.azure.com
AZURE_OPENAI_MODEL_CORE=DeepSeek-V4-Flash
AZURE_OPENAI_MODEL_CHAT=gpt-6-luna
AZURE_OPENAI_TIMEOUT_S_CORE=120
AZURE_OPENAI_TIMEOUT_S_CHAT=120
KEHA_LV_DATA_DIR=/tmp/keha-data
```

Container app secrets, referenced as `secretref:` from the variables above:

- `foundry-key` → `AZURE_OPENAI_API_KEY_CORE` and `AZURE_OPENAI_API_KEY_CHAT`
- `session-secret` → `KEHA_LV_SESSION_SECRET` (rotating it logs everyone out)

Change a value:

```bash
az containerapp secret set -n keha-lv -g rg-hackathon-ecdx-proto-sdc-001 \
  --secrets foundry-key='<key>'
```

## Deploying a new version

Push to `main`, or run the `Deploy KEHA LV to ECDX` workflow manually:

```bash
gh workflow run deploy-ecdx.yml --ref main
```

The workflow builds the image with `az acr build` (no Docker on the runner),
updates the container app's image, and waits for `/healthz` to return 200.
It never touches env vars or secrets.

Manual equivalent:

```bash
az acr build --registry caed29c7d480acr --image keha-lv:$(git rev-parse --short=12 HEAD) --file Dockerfile .
az containerapp update -n keha-lv -g rg-hackathon-ecdx-proto-sdc-001 \
  --image caed29c7d480acr.azurecr.io/keha-lv:$(git rev-parse --short=12 HEAD)
```

## Storage is ephemeral — read this before a customer sees it

`KEHA_LV_DATA_DIR=/tmp/keha-data` lives inside the container. Every new revision,
restart or scale event starts with an empty SQLite database and no uploads:
hankkeet, applications and AI results are lost.

For anything beyond a demo, mount an Azure Files share at `/tmp/keha-data`
(or move `app/infra/db.py` to Azure SQL/Postgres) and then decide whether
`max-replicas` may exceed 1. It must stay at 1 while state is SQLite plus
in-process job threads.

## Inspecting and operating

```bash
az containerapp logs show -n keha-lv -g rg-hackathon-ecdx-proto-sdc-001 --tail 100
az containerapp revision list -n keha-lv -g rg-hackathon-ecdx-proto-sdc-001 -o table
curl -s https://keha-lv.kindplant-ccd82e27.swedencentral.azurecontainerapps.io/healthz
```

## Removing it

```bash
az containerapp delete -n keha-lv -g rg-hackathon-ecdx-proto-sdc-001 --yes
az identity delete -n keha-lv-identity -g rg-hackathon-ecdx-proto-sdc-001
az ad app delete --id 4b105d80-3439-42fb-80e6-5241c0f56597
# images stay in caed29c7d480acr under the keha-lv repository
```

## Why not the old App Service

`Keha-LV-demo`, the target of the previous GitHub workflow, no longer exists in
the subscription — the workflow would fail at the deploy step. The only App
Service plan in `rg-customerservice-ecdx-proto-sdc-001` is `plan-ecdx-delivery`
(FC1 Flex Consumption), which can host Functions but not this FastAPI server.
The container app above is the replacement.
