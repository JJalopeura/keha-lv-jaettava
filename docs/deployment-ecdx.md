# Deploying KEHA LV to the ECDX environment

Live since 2026-11-09, on **Azure App Service (Linux, code deploy)**:

**https://keha-lv-web.azurewebsites.net**

Public HTTPS, no authentication in front of the app. The free tier has no IP
restrictions to configure; the site answers on `*.azurewebsites.net` only, because
custom domains are not part of F1.

## Where it runs

| | |
|---|---|
| Subscription | `tieto-ecdx-proto-001` (`f65d2df4-565a-4689-8453-2461622ddcf0`) |
| Resource group | `rg-hackathon-ecdx-proto-sdc-001` |
| Plan | `keha-lv-plan` — Linux, **F1 (free)**, 1 instance |
| Web app | `keha-lv-web`, `PYTHON\|3.12`, startup `gunicorn --config gunicorn.conf.py app.main:app` |
| Persistent state | `/home/data` (`KEHA_LV_DATA_DIR`), survives restarts and deploys |
| AI | `foundry-ecdx-cs-proto` — core `DeepSeek-V4-Flash`, chat `gpt-6-luna` |

There is no container, no registry and no Log Analytics workspace: the app is
deployed as code and Oryx installs `requirements.txt` on the worker.

## State is durable now (this is the point of the move)

`KEHA_LV_DATA_DIR=/home/data` is the App Service home directory, backed by the
site's persistent filesystem. SQLite (`/home/data/keha_lv.db`) and uploaded files
(`/home/data/uploads`) survive:

- an idle unload (F1 has no *Always On*, so the site unloads after ~20 min idle),
- a restart, a redeploy, or a plan change,
- the machine recycling the worker.

The previous Container Apps deployment kept state in `/tmp`, which is why every
deploy — and later scale-to-zero — wiped the demo. That deployment was deleted on
2026-11-09 for exactly that reason.

## Free-tier limits to know

- **60 CPU-minutes per day** across the app. Text extraction from a large PDF
  package is the expensive part; a normal Ren-Gas run is a few minutes of it.
  When the quota is exhausted the site returns 503 until the daily reset.
- **1 GB disk** shared with the deployment. A single demo package is ~100 MB, so
  one or two packages fit comfortably; prune `/home/data/uploads` if it fills.
- **No Always On** — first request after ~20 minutes idle pays a cold start
  (worker boot + gunicorn). Data is unaffected.
- **1 instance** — SQLite is single-writer and AI jobs run in-process threads, so
  this is not a limitation in practice; scale out only after moving off SQLite.

If the quota becomes a nuisance before teardown, `az appservice plan update -g
rg-hackathon-ecdx-proto-sdc-001 -n keha-lv-plan --sku B1` (≈ €12/month) removes the
daily CPU cap without changing anything else.

## Configuration

App settings (values are on the site, never in the repo):

```
AZURE_OPENAI_HOST_CORE=https://foundry-ecdx-cs-proto.services.ai.azure.com
AZURE_OPENAI_HOST_CHAT=https://foundry-ecdx-cs-proto.services.ai.azure.com
AZURE_OPENAI_MODEL_CORE=DeepSeek-V4-Flash
AZURE_OPENAI_MODEL_CHAT=gpt-6-luna
AZURE_OPENAI_TIMEOUT_S_CORE=120
AZURE_OPENAI_TIMEOUT_S_CHAT=120
AZURE_OPENAI_API_KEY_CORE=<set>
AZURE_OPENAI_API_KEY_CHAT=<set>
KEHA_LV_SESSION_SECRET=<set>
KEHA_LV_DATA_DIR=/home/data
SCM_DO_BUILD_DURING_DEPLOYMENT=true
WEBSITES_PORT=8000
PYTHONUNBUFFERED=1
```

Change one value:

```bash
az webapp config appsettings set -g rg-hackathon-ecdx-proto-sdc-001 -n keha-lv-web \
  --settings AZURE_OPENAI_TIMEOUT_S_CORE=180 -o none
```

## Deploying a new version

Release = a committed artifact, so the pipeline and a human produce the same bytes.

```bash
./scripts/build_deploy_zip.sh      # git archive HEAD -> deploy/artifacts/keha-lv-<sha>.zip
./scripts/rollback.sh <sha>        # deploy that artifact, then poll /healthz
git add deploy/artifacts && git commit -m "Release <sha>"
```

`build_deploy_zip.sh` uses `git archive`, so the zip contains exactly the tracked
`app/`, `requirements.txt` and `gunicorn.conf.py` of that commit — no `__pycache__`,
no untracked files, and identical bytes on a rebuild. Oryx installs the
requirements on the worker (`SCM_DO_BUILD_DURING_DEPLOYMENT=true`).

`/home/data` is untouched by the deploy, so an in-progress demo keeps its hankkeet.

## Backup and rollback

**The released zip in `deploy/artifacts/` is the backup.** It is committed with the
release, so a rollback needs no rebuild and no branch state — it is a deploy of a
file that already exists in the repo:

```bash
./scripts/rollback.sh                 # list artifacts
./scripts/rollback.sh 75000d5a9d02    # roll back to that release
```

Rollback swaps code only. Demo state lives in `/home/data`, which survives the
deploy, so rolling back mid-demo does not cost the hankkeet you already built.

The manifest — which artifact belongs to which commit and when it was deployed — is
[`deploy/artifacts/RELEASES.md`](../deploy/artifacts/RELEASES.md); update it with
every release. Each artifact is ~470 KB.

Anything not committed as an artifact is not a backup: a git tag plus a rebuild is
only equivalent if the build is reproducible, which is exactly what the script
guarantees and a hand-rolled `zip -r` does not.

Publishing from the repo works again: `.github/workflows/deploy-ecdx.yml` builds
the artifact, deploys it with `az webapp deploy`, waits for `/healthz`, and commits
the artifact with `[skip ci]` so the release point lands in the repo. It runs on
pushes to `main` and `polishing`, and on manual dispatch — with one caveat that
applies to `workflow_dispatch`: GitHub resolves a workflow by name from the
**default branch**, and `main` is still an old tree that does not contain this
file, so manual dispatch returns HTTP 404 until `main` carries it. Until then, a
push to `polishing` (or the three commands above) is the way in.

Auth is OIDC to the Entra app `keha-lv-deploy` (federated credentials exist for
both `main` and `polishing`), which holds **Website Contributor on this web app
only** — no subscription-wide rights, no stored secret.

## Inspecting and operating

```bash
az webapp log tail -g rg-hackathon-ecdx-proto-sdc-001 -n keha-lv-web
az webapp show -g rg-hackathon-ecdx-proto-sdc-001 -n keha-lv-web \
  --query "{state:state,host:defaultHostName}" -o json
curl -s https://keha-lv-web.azurewebsites.net/healthz
```

The log stream is the only log sink — with Log Analytics gone there is no
workspace to query, which is the trade that removed the Container Apps
environment.

## Removing it

```bash
az webapp delete -g rg-hackathon-ecdx-proto-sdc-001 -n keha-lv-web
az appservice plan delete -g rg-hackathon-ecdx-proto-sdc-001 -n keha-lv-plan --yes
```

Deleting the web app discards `/home/data`, i.e. the demo database and uploads.

## History

The first deployment (2026-11-09) ran as Container App `keha-lv` in environment
`luvn-finbert-svc-env`, image `caed29c7d480acr.azurecr.io/keha-lv`. Container Apps
requires a Log Analytics workspace and a registry, and its replicas keep state only
in `/tmp`; setting `minReplicas: 0` would have erased the demo on every idle
window, and keeping `minReplicas: 1` meant paying for a replica around the clock
for a two-week demo. App Service F1 answers both: code deploy, no registry, no
workspace, and a durable `/home`.
