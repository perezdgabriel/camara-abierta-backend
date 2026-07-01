"""AWS Lambda entrypoints (see ADR-0022 / infra/compute_stack.py).

One container image, four handlers selected by CMD:
  - api.handler     — FastAPI via Mangum (Function URL)
  - jobs.handler    — EventBridge-scheduled ingestion dispatch
  - llm.handler     — SQS-driven bill summaries (concurrency-capped)
  - migrate.handler — `alembic upgrade head`
"""
