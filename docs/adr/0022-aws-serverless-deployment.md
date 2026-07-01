# AWS serverless deployment architecture

The backend deploys to AWS as a **fully serverless stack**, not a lift-and-shift of the `docker-compose.yml` topology. The workload is bursty (beat fires ingestion a handful of times a day — see `celery_beat.py`) and the API is read-mostly, so paying for always-on Celery workers, a beat process, and Redis is wasted spend. We collapse the six always-on containers into on-demand functions and drop Redis entirely. Primary constraints: solo project, cost-sensitive (6-month free tier + $200 credit), while still demonstrating breadth of AWS knowledge.

## Shape

- **API** — FastAPI packaged as a **container-image Lambda** behind a **Function URL**, VPC-attached for RDS access only (no outbound internet, so no NAT dependency). Called server-to-server from the frontend with a shared-secret header.
- **Scheduled ingestion** — each `celery_beat.py` entry becomes an **EventBridge Scheduler** schedule invoking a **job Lambda** (same container image, private subnet).
- **LLM summarization** — replaces the `-c 1` Celery `llm` queue with **SQS → LLM Lambda** at low reserved concurrency, with a DLQ. Preserves the original intent: cap concurrent Anthropic calls so a backfill can't spike cost/rate limits.
- **Database** — managed **RDS PostgreSQL `db.t4g.micro`**, single-AZ, private subnet, always warm.
- **Egress** — job Lambdas reach the Congress APIs + Anthropic through a **NAT instance** (`t4g.nano`, fck-nat), not a managed NAT Gateway.
- **Frontend** — Next.js (RSC + ISR + Suspense) on **Vercel**, off AWS. ISR caching means most reads never touch the API, which neutralizes Lambda cold starts and keeps RDS load near zero.
- **IaC** — AWS CDK (Python). **Deploy** — GitHub Actions authenticating via OIDC (no static AWS keys) → ECR → `cdk deploy`. **Secrets** — SSM Parameter Store (`SecureString`), not Secrets Manager. **Migrations** — a migration Lambda runs `alembic upgrade head` post-deploy (see caveat in CLAUDE.md). **Observability** — CloudWatch alarms (Lambda errors, SQS DLQ depth, NAT status-check) → SNS → email.

Container/Compose mapping: `api` → API Lambda · `beat` → EventBridge Scheduler · `worker-default` → job Lambdas · `worker-llm` → SQS + LLM Lambda · `redis` → *deleted* · `postgres` → RDS.

## Deliberate deviations (things a reviewer might otherwise "fix")

- **No Celery, no Redis.** EventBridge Scheduler absorbs the cron role and SQS absorbs the queue role; a persistent broker is unjustified for a few-times-a-day workload. The Celery code paths are replaced, not ported.
- **NAT instance, not NAT Gateway.** A managed NAT Gateway (~$32/mo fixed) would by itself break the cost goal for job Lambdas that only run a few times a day. RDS stays fully private either way.
- **Scrapers descoped from the deployed image.** The `diario_oficial` / `cgr_reglamentos` scrapers (and their Playwright/Chromium deps) are dropped from the deployed image and their beat entries removed — their subdomains have no API routes and are unserved in v0.1. This is what makes the image lean enough for Lambda and removes the 15-min-ceiling concern.
- **Cold starts accepted, not engineered away.** Container-image Lambda + Function URL, with cold hits (~1–2 s) masked by Vercel ISR/Suspense. Provisioned Concurrency is left as a later toggle, not a day-one cost.

## Cost

Effectively **$0** during the 6-month free-tier/credit window; **~$16–20/mo** steady-state, dominated by RDS (~$13) and the NAT instance (~$3). Everything else (Lambda, SQS, EventBridge, Function URL, ECR, CloudWatch, Parameter Store) rounds to zero at portfolio traffic.

## Consequences

- Deployed schema is **Alembic-only**; `recreate_db.py` is local-dev-only (it drops all data). CLAUDE.md's "no Alembic in pre-release" rule is scoped to local/ephemeral DBs.
- Region is **us-east-1** (cheapest, best free-tier coverage); Chilean-audience latency shows only on Vercel cache-misses, which ISR makes rare.
- Lambda→RDS connections are managed with a small pool + modest reserved concurrency rather than RDS Proxy (a deliberate cost saving); revisit if connection exhaustion appears.
- **Initial data load is bootstrapped by dump/restore, not an in-cloud `coldstart`.** The 1990→present backfill is run *locally* (`just coldstart`), then loaded into RDS **data-only** through an SSM tunnel via the NAT instance (`just coldstart-dump` / `rds-tunnel` / `restore-rds`) — Alembic still owns the schema. This avoids the 15-min Lambda ceiling and re-hammering the Congress APIs. The NAT instance therefore doubles as an SSM bastion (needs `AmazonSSMManagedInstanceCore`), and RDS must be PostgreSQL 16 to match the local engine. Watch `global_sync_version_seq` — a data-only dump carries its `setval`, but verify `last_value` post-load or delta sync breaks.
