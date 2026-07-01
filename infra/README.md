# infra/ — AWS CDK (Python)

Infrastructure for the serverless AWS deployment. See [ADR-0022](../docs/adr/0022-aws-serverless-deployment.md) for the full design and rationale.

## What's here

- **`network_stack.py`** — VPC (public / private-with-egress / isolated subnets), the fck-nat NAT instance (doubles as the SSM bastion for the RDS data load), and the private RDS PostgreSQL 16 instance.
- **`compute_stack.py`** — the four Lambdas (API + Function URL, jobs, llm, migrate) from one shared container image (`../Dockerfile.lambda`), the SQS queue + DLQ feeding the llm function at reserved concurrency, EventBridge scheduled rules mirroring `celery_beat.py`, and CloudWatch alarms → SNS email.
- **`cicd_stack.py`** — GitHub OIDC provider + `camara-github-deploy` role assumed by the deploy workflow (no static AWS keys). Bootstrap-once, deployed locally.
- **`app.py`** — CDK app entry; wires `NetworkStack` → `ComputeStack` (+ `CicdStack`).

Handlers live in `../app/lambdas/` (`api`, `jobs`, `llm`, `migrate`). The pipeline is `../.github/workflows/deploy.yml`.

## Prerequisites

- AWS CLI configured, and Session Manager plugin installed (for `just rds-tunnel`).
- Node + AWS CDK CLI: `npm i -g aws-cdk`.

## Usage

```bash
cd infra
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cdk bootstrap          # once per account/region
cdk synth
cdk deploy CamaraNetwork
```

## CI/CD bootstrap (one-time)

The deploy workflow authenticates via OIDC to a role that must exist first, so
the CI stack is created locally with admin creds before the pipeline can run:

```bash
cd infra
cdk deploy CamaraCicd -c github_owner=<owner> -c github_repo=camara-abierta-backend
```

Then add the **`AWS_ACCOUNT_ID`** repo secret. After that, pushes to `main` run
`.github/workflows/deploy.yml`: OIDC → `cdk deploy CamaraNetwork CamaraCompute`
(builds + pushes the image asset to ECR) → invoke `camara-migrate`
(`alembic upgrade head`). `CamaraCicd` is deliberately **not** deployed by the
pipeline, so the deploy role never edits its own trust.

## One-time data bootstrap (see ADR-0022)

The 1990→present backfill runs **locally**, then loads into the private RDS
data-only (Alembic owns the schema). From the repo root:

```bash
just coldstart                              # populate local postgres:16
# ... cdk deploy + invoke the migrate Lambda so RDS has the empty schema ...
just coldstart-dump                         # -> coldstart.dump
just rds-tunnel <rds-endpoint>              # resolves the fck-nat instance by tag; leave running
just restore-rds <rds-master-url@localhost:55432>   # data-only restore + verify sync sequence
```

## Notes

- **Region:** us-east-1 (ADR-0022).
- **PG version:** pin `VER_16_4` to whatever 16.x us-east-1 currently offers — it must match the local `postgres:16` engine for dump/restore.
- **DB secret:** the one deliberate Secrets Manager use (auto-generated master secret + rotation + `grant_read`); all other secrets are SSM Parameter Store `SecureString`.
