# Production environment runbook

How to stand up a second, production deployment of this backend from `main`,
next to the existing one that `dev` deploys today. The visual companion to this
file is `docs/system-guide/index.html` (open it in a browser).

Everything here was read out of this repository and its GitHub Actions history.
Anything the repo cannot tell us (the HTTPS front door, where Neo4j is hosted,
the exact values of the `dev` environment variables) is marked **LOOK UP** —
find it in the AWS console or in GitHub → Settings → Environments → `dev`.

---

## 0. Facts that shape the plan

| Fact | Where it comes from | Why it matters |
|---|---|---|
| `deploy.yml` runs on every push to **`dev` and `main`** | `.github/workflows/deploy.yml:3-7` | Pushing anything to `main` starts a prod deploy. Pushing this docs branch does not. |
| `main` uses GitHub environment **`prod`**, everything else uses **`dev`** | `deploy.yml:35` | All prod settings live in a GitHub environment named exactly `prod`. |
| All 84 EC2 deploys so far came from `dev`; `main` has never run this workflow | GitHub Actions history | The `prod` environment has probably never been configured. |
| `main` and `dev` share **no git history** | `git merge-base origin/main origin/dev` returns nothing | `main` is the old Firebase backend (1,101 commits, last one 2026-03-30). A normal merge of `dev` into `main` is refused. |
| `ENVIRONMENT=dev` **turns off API-key checks** | `dependencies.py:51` | If prod runs with `ENVIRONMENT=prod`, every write call needs an `X-API-Key` header. |
| `OPENSEARCH_AUTH_MODE` is never set by the workflow, so it is `none` | `config.py:25`, `deploy.yml:58-71` | The OpenSearch domain must accept unsigned requests from inside the VPC, or you add `aws` auth. |
| No AWS keys are written to `.env` | `deploy.yml:58-71` | S3 access comes from the **EC2 instance's IAM role**. |
| S3 rollback reads object versions | `storage/s3.py` `rollback_base_text` | Turn on **versioning** for the prod bucket. |
| Neo4j image in tests is `neo4j:2026.07.1` | `tests/conftest.py:155` | Run prod Neo4j on the same major version. |
| APOC triggers enforce data rules | `database/neo4j_triggers.py`, `scripts/install_neo4j_triggers.py` | Prod Neo4j needs APOC with triggers enabled. |
| OpenSearch needs the `analysis-tibetan` and `analysis-bdrc` plugins, engine `OpenSearch_3.5` | `scripts/deploy_opensearch_plugins.sh` | The script already has a `prod` mode: domain `os-prod`, bucket `openpecha-prod`. |

---

## 1. Decide two things first

### 1a. How `main` becomes "the same as `dev`"

`main` cannot be merged with `dev` normally. Recommended:

```bash
# keep the old Firebase code reachable
git fetch origin main dev
git push origin origin/main:refs/heads/legacy-firebase-main

# point main at exactly what dev runs today
# (temporarily allow force-push on main in Settings → Branches if it is protected)
git push --force-with-lease=main:ecdf248942e98180cb1cbe9b219d4f3f8b5a7f30 origin origin/dev:refs/heads/main
```

**Do not do this until steps 2–6 are finished.** This push starts the first prod
deploy.

The alternative, `git merge --allow-unrelated-histories`, keeps the Firebase files
(`functions/`, `firebase.json`, the old `main.yml` workflow) mixed into the tree. Avoid it.

> The old `.github/workflows/main.yml` (Deploy to Firebase) lives only on the old
> `main`. After `main` points at `dev`, it is gone from `main`, so Firebase deploys stop.
> If anything still uses the Firebase backend, check that first.

### 1b. Will prod require API keys?

- Check the `ENVIRONMENT` variable in GitHub → Settings → Environments → `dev`.
  If it is `dev`, today's apps send no API key.
- Option A, matching today's behaviour: set `ENVIRONMENT=dev` in `prod` too. This is simple but leaves the API open.
- Option B, recommended for real users: set `ENVIRONMENT=prod`, create keys (step 5.4),
  and ship an app build that sends `X-API-Key` (and `X-Application` for app-bound keys)
  before you switch the URL.

---

## 2. AWS resources for prod

Region: **LOOK UP** the `AWS_REGION` var in `dev`. The scripts default to `ap-southeast-1`.

| # | Resource | Dev counterpart | Prod settings |
|---|---|---|---|
| 2.1 | EC2 instance (Ubuntu) | `vars.EC2_INSTANCE_ID` in `dev` | Same size as dev, in the same VPC as the prod OpenSearch domain |
| 2.2 | Instance IAM role | **LOOK UP** on the dev instance | `AmazonSSMManagedInstanceCore` plus S3 read/write on the prod bucket |
| 2.3 | S3 bucket | `vars.AWS_S3_BUCKET` (script default `openpecha-dev`) | `openpecha-prod`, **versioning on**, block public access |
| 2.4 | OpenSearch domain | `os-dev` | `os-prod`, engine **OpenSearch_3.5**, VPC access, TLS policy `Policy-Min-TLS-1-2-PFS-2023-10`. Content search also uses `icu_tokenizer` (the `analysis-icu` plugin, which Amazon OpenSearch Service ships as a built-in option) |
| 2.5 | Neo4j | `vars.NEO4J_URI` (**LOOK UP** whether it is Aura or self-hosted) | Separate database, version 2026.x, APOC with triggers enabled |
| 2.6 | HTTPS front door | **LOOK UP**: gunicorn listens on plain `:8000`, so something in front terminates TLS | Recommended: ALB with an ACM certificate → target group on port 8000, health check `GET /__/health` |
| 2.7 | GitHub OIDC deploy role | `vars.AWS_ROLE` in `dev` | New role that trusts only the `prod` environment (below) |
| 2.8 | Grafana Cloud (optional) | `OTEL_*` vars | Same stack, `OTEL_SERVICE_NAME=openpecha-api`, `ENVIRONMENT` tags the traces |

### 2.7 Deploy role trust policy

The account must already have the GitHub OIDC provider (dev uses it). Trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": "repo:Webuddhist-tech/Webuddhist-library:environment:prod"
      }
    }
  }]
}
```

Permissions policy. These are the only AWS calls `deploy.yml` makes:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": "ssm:SendCommand",
      "Resource": [
        "arn:aws:ec2:<REGION>:<ACCOUNT_ID>:instance/<PROD_INSTANCE_ID>",
        "arn:aws:ssm:<REGION>::document/AWS-RunShellScript"
      ] },
    { "Effect": "Allow", "Action": ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"], "Resource": "*" }
  ]
}
```

Compare this with the dev role (`vars.AWS_ROLE`) before you create it, and copy anything extra it has.

---

## 3. Prepare the EC2 instance

Run this on the new instance (SSM Session Manager or SSH). It recreates what the
deploy script expects to find:

```bash
# Python 3.14: the deploy script exits if python3.14 is missing (deploy.yml:73-78)
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update
sudo apt-get install -y python3.14 python3.14-venv git

# SSM agent ships with Ubuntu AMIs; confirm it is running
sudo snap services amazon-ssm-agent

# The code lives in /opt/openpecha-backend, owned by ubuntu (the service user)
sudo git clone https://github.com/Webuddhist-tech/Webuddhist-library.git /opt/openpecha-backend
sudo chown -R ubuntu:ubuntu /opt/openpecha-backend
#   If the repo is private, root needs read access, because SSM runs the deploy as root with HOME=/root:
#   add a read-only deploy key in /root/.ssh and clone with the git@github.com: URL.

# systemd unit: the file is in the repo
sudo cp /opt/openpecha-backend/openpecha-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable openpecha-api
```

The service runs `gunicorn main:app -c gunicorn.conf.py`: 4 Uvicorn workers on
`0.0.0.0:8000`, with environment from `/opt/openpecha-backend/.env` (the deploy writes it).

---

## 4. GitHub environment `prod`

GitHub → Settings → Environments → **New environment** → `prod`.

- **Deployment branches:** only `main`.
- **Required reviewers:** add yourself. Each prod deploy then waits for a click,
  so a stray push to `main` cannot go live by accident.

Copy each item from `dev` and change the value where prod differs:

| Name | Type | Prod value |
|---|---|---|
| `AWS_ROLE` | var | ARN of the role from 2.7 |
| `AWS_REGION` | var | same as dev |
| `EC2_INSTANCE_ID` | var | the prod instance id `i-…` |
| `NEO4J_URI` | var | prod Neo4j, e.g. `neo4j+s://…` |
| `NEO4J_USERNAME` | var | prod user |
| `NEO4J_PASSWORD` | **secret** | prod password |
| `AWS_S3_BUCKET` | var | `openpecha-prod` |
| `OPENSEARCH_ENDPOINT` | var | `https://vpc-os-prod-…es.amazonaws.com` |
| `SEARCH_API_URL` | var | copy from dev (still in config; no code path reads it today) |
| `ENVIRONMENT` | var | see decision 1b: `prod` enforces API keys |
| `OTEL_ENABLED` | var | `true` or `false` |
| `OTEL_SERVICE_NAME` | var | `openpecha-api` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | var | copy from dev |
| `OTEL_EXPORTER_OTLP_HEADERS` | **secret** | copy from dev |

Not set by the workflow, so the defaults apply: `NEO4J_DATABASE=neo4j`,
`OPENSEARCH_INDEX=content-search`, `OPENSEARCH_CATALOG_INDEX=catalog-search`,
`OPENSEARCH_AUTH_MODE=none`.

---

## 5. Seed prod data

Run these from a machine that can reach prod Neo4j, S3 and OpenSearch (the prod
EC2 instance is easiest), with a `.env` pointing at **prod**.

```bash
cd /opt/openpecha-backend && source .venv/bin/activate   # after the first deploy creates .venv
```

### 5.1 Neo4j: copy the data

Prod Neo4j server settings first. These mirror `tests/conftest.py:154-170`, the
only place in the repo that spells them out:

```properties
# neo4j.conf, or NEO4J_* env vars in Docker; APOC plugin installed
apoc.trigger.enabled=true
apoc.trigger.refresh=1000
dbms.security.procedures.unrestricted=apoc.*
dbms.security.procedures.allowlist=apoc.*
```

```cypher
// run against the system database
ALTER DATABASE neo4j SET DEFAULT LANGUAGE CYPHER 25
```

APOC is required even without triggers: read queries call `apoc.map.fromPairs`
(e.g. `database/text_database.py:45`). Copy the same settings from the dev Neo4j
if you can see them.

Self-hosted (`neo4j-admin`, same major version on both sides):

```bash
# on dev Neo4j host
neo4j-admin database dump neo4j --to-path=/backups
# copy the .dump to prod host, then
neo4j-admin database load neo4j --from-path=/backups --overwrite-destination=true
```

On Aura, use the console's snapshot export and import instead.

Then make sure the constraints and triggers exist on prod:

```bash
# constraints / indexes (idempotent IF NOT EXISTS statements)
cypher-shell -a "$NEO4J_URI" -u "$NEO4J_USERNAME" -p "$NEO4J_PASSWORD" -f database/neo4j_constraints.cypher
# APOC triggers
python -m scripts.install_neo4j_triggers
# structural audit: should report no violations
python -m scripts.audit
```

### 5.2 S3: copy base texts and recordings

```bash
aws s3 sync s3://<DEV_BUCKET>/base_texts  s3://openpecha-prod/base_texts
aws s3 sync s3://<DEV_BUCKET>/recordings  s3://openpecha-prod/recordings
```

Neo4j stores only the S3 key layout (`base_texts/{text_id}/{edition_id}.txt`,
`recordings/{edition_id}/{recording_id}.{ext}`), so copied keys line up with copied nodes.

### 5.3 OpenSearch: plugins, indexes, reindex

```bash
# plugins: uploads ZIPs to s3://openpecha-prod/os_plugins/, associates them with os-prod
scripts/deploy_opensearch_plugins.sh prod

# indexes (built from Neo4j + S3, so run after 5.1 and 5.2)
python -m scripts.catalog_search recreate-index
python -m scripts.catalog_search reindex
python -m scripts.content_search recreate-index
python -m scripts.content_search reindex
```

### 5.4 API keys (only if `ENVIRONMENT=prod`)

```bash
python scripts/api_key.py create --name "Master Admin Key" --email "<you>"
python scripts/api_key.py create --name "WeBuddhist Key" --email "<you>" --application webuddhist
python scripts/api_key.py list
```

Keys are printed once. Store them in a password manager.

---

## 6. First prod deploy

1. Do step 1a: point `main` at `dev`. The push starts **Deploy to EC2** with environment `prod`.
2. Approve the run if you set required reviewers.
3. The run tests first (~4 min), then the deploy job runs over SSM (~20 s).
4. Check it:
   ```bash
   curl -s https://<prod-host>/__/health        # {"status":"healthy"}
   open https://<prod-host>/docs                # Swagger UI
   ```
   On the instance: `sudo systemctl status openpecha-api` and `journalctl -u openpecha-api -n 100`.
5. If the deploy job fails, its log prints the SSM output table (`deploy.yml:110-118`).

## 7. Switch the apps

1. Point a test build of the WeBuddhist app at the prod URL and click through reads, search, and a write.
2. Freeze writes on dev, or re-run 5.1–5.3 just before switching, so that prod has the latest content.
3. Ship the app config with the prod URL (plus the API key if you chose option B).
4. Keep dev running unchanged. To roll back, point the app at dev again.

## 8. Day-to-day after the split

```
feature branch → PR into dev  → auto-deploys to the dev EC2  → test with the app's dev build
dev            → PR into main → auto-deploys to the prod EC2 (after approval) → real users
```

Data no longer flows between environments after the switch. Dev keeps its own
data from then on.

## Known sharp edges

- **Overlapping deploys.** On 2026-09-28 a deploy that was still running finished after a rollback
  and redeployed the wrong commit (see the "Redeploy rollback" commit on `dev`).
  Adding `concurrency: { group: deploy-${{ github.ref_name }}, cancel-in-progress: false }`
  to `deploy.yml` would queue deploys instead.
- **The `.env` file is rewritten on every deploy.** Edit values in GitHub, not on the server.
- **`git checkout <branch>` runs on the server.** Local edits in `/opt/openpecha-backend` can block a deploy.
- **README says deploys happen on `main`.** That was true for Firebase. Today they happen on `dev`.
