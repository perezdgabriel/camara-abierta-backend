"""Migration handler: runs `alembic upgrade head` against RDS.

Invoked by CI after `cdk deploy` (and once before the initial data restore).
This is the deployed-environment schema path — `recreate_db.py` is local-only
(it drops all data). See ADR-0022 and the CLAUDE.md Alembic caveat.
"""

from typing import Any

from alembic import command
from alembic.config import Config


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    cfg = Config("alembic.ini")  # copied to LAMBDA_TASK_ROOT in Dockerfile.lambda
    command.upgrade(cfg, "head")
    return {"ok": True}
