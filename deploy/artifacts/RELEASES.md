# Released artifacts

Each file is the exact zip that was deployed to App Service, built by
`./scripts/build_deploy_zip.sh` from the commit in its name (`git archive`, so the
bytes are reproducible from that commit). **The artifact is the backup**: rolling
back is deploying a file that is already in the repo, not rebuilding a branch that
may have moved.

| Artifact | Commit | Deployed | Note |
|---|---|---|---|
| `keha-lv-75000d5a9d02.zip` | `75000d5` | 2026-11-09 | App Service migration; durable `/home/data`; Ympäristölupa criteria `2026-11-demo-3`; summary copy fix |

## Rolling back a release

```bash
./scripts/rollback.sh 75000d5a9d02     # or any artifact name without the prefix
./scripts/rollback.sh                  # list what is available
```

The script deploys the zip, then polls `/healthz` until the site answers. State in
`/home/data` is untouched by a deploy, so a rollback does not cost the current
demo data — only the code version changes.

## Adding a release

```bash
./scripts/build_deploy_zip.sh          # writes deploy/artifacts/keha-lv-<sha>.zip
./scripts/rollback.sh <sha>            # deploy + verify what you just built
git add deploy/artifacts && git commit -m "Release <sha>"
```

The artifact for commit `X` is therefore committed in the release commit that
follows `X`; the table above is the record of which file belongs to which commit.

## Retention

Keep every artifact while the demo is live — each is ~470 KB, and the repo already
carries the 400 MB test corpus. If the archive ever becomes noise, delete the oldest
entries here and in `deploy/artifacts/`; the current release and the last release
before it are the two that matter.
