"""Cold-start secret resolution for the deployed (Lambda) path.

The app reads all config through ``Settings`` env aliases (``DATABASE_URL``,
``ANTHROPIC_API_KEY``, ...). In AWS we don't want secret *values* in the CDK
template, so CDK passes **references** instead:

- ``DB_SECRET_ARN`` — the RDS-generated credentials secret (Secrets Manager).
- ``*_PARAM`` env vars — SSM SecureString parameter *names* (e.g.
  ``ANTHROPIC_API_KEY_PARAM=/camara/anthropic-key``).

``hydrate_secrets_into_env()`` resolves those references and writes the plain
env vars **before** ``Settings()`` is constructed (see ``get_settings()``), so
the existing aliases and the module-level engine just work. It is a complete
no-op when none of the references are set — local dev is untouched.
"""

from __future__ import annotations

import json
import os

# SSM ``*_PARAM`` env var -> the plain env var the corresponding Setting reads.
_PARAM_ENV_MAP: dict[str, str] = {
    "ANTHROPIC_API_KEY_PARAM": "ANTHROPIC_API_KEY",
    "INGESTOR_RESTSIL_API_KEY_PARAM": "INGESTOR_RESTSIL_API_KEY",
    "API_SHARED_SECRET_PARAM": "API_SHARED_SECRET",
    "FRONTEND_REVALIDATE_TOKEN_PARAM": "FRONTEND_REVALIDATE_TOKEN",
}


def _needs_hydration() -> bool:
    return bool(os.environ.get("DB_SECRET_ARN")) or any(
        os.environ.get(param) for param in _PARAM_ENV_MAP
    )


def hydrate_secrets_into_env() -> None:
    """Resolve AWS secret references into plain env vars, if any are set.

    Safe to call unconditionally: does nothing (and imports no AWS SDK) when
    running locally without ``DB_SECRET_ARN`` / ``*_PARAM``.
    """
    if not _needs_hydration():
        return

    import boto3  # lazy: only imported in the deployed path

    _hydrate_database_url(boto3)
    _hydrate_ssm_params(boto3)


def _hydrate_database_url(boto3) -> None:
    secret_arn = os.environ.get("DB_SECRET_ARN")
    if not secret_arn or os.environ.get("DATABASE_URL"):
        # An explicit DATABASE_URL always wins (lets us override in a pinch).
        return
    client = boto3.client("secretsmanager")
    payload = json.loads(client.get_secret_value(SecretId=secret_arn)["SecretString"])
    user = payload["username"]
    password = payload["password"]
    host = payload["host"]
    port = payload.get("port", 5432)
    # RDS sets `database_name="camara"` (network_stack.py), so the attached
    # secret carries dbname; fall back to the always-present "postgres" db if not.
    dbname = payload.get("dbname") or "postgres"
    os.environ["DATABASE_URL"] = (
        f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
    )


def _hydrate_ssm_params(boto3) -> None:
    pending = {
        param: target
        for param, target in _PARAM_ENV_MAP.items()
        if os.environ.get(param) and not os.environ.get(target)
    }
    if not pending:
        return
    client = boto3.client("ssm")
    for param_env, target_env in pending.items():
        name = os.environ[param_env]
        value = client.get_parameter(Name=name, WithDecryption=True)
        os.environ[target_env] = value["Parameter"]["Value"]
