"""Unit tests for the serverless deployment seam (ADR-0022).

Covers the dispatch router, the API shared-secret middleware + docs gating, the
jobs handler dispatch map + revalidation, the LLM SQS handler, the revalidate
helper, and the cold-start secret resolver. All run under the default SQLite
suite (no AWS, no PostgreSQL).
"""

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import boto3
import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import dispatch as dispatch_mod
from app.core import secrets as secrets_mod
from app.core.config import settings
from app.main import create_app
from app.services.revalidate import revalidate


class FakeTask:
    def __init__(self, name: str) -> None:
        self.name = name
        self.delay = MagicMock()
        self.apply = MagicMock()


# --------------------------------------------------------------------------- #
# Workstream 4 — dispatch routing
# --------------------------------------------------------------------------- #
def test_dispatch_celery_backend_uses_delay(monkeypatch):
    monkeypatch.setattr(settings, "dispatch_backend", "celery")
    task = FakeTask("app.tasks.anything")

    dispatch_mod.dispatch(task, 1, 2, key="v")

    task.delay.assert_called_once_with(1, 2, key="v")
    task.apply.assert_not_called()


def test_dispatch_serverless_non_llm_runs_inline(monkeypatch):
    monkeypatch.setattr(settings, "dispatch_backend", "serverless")
    task = FakeTask("app.tasks.voting.sync_voting_session")

    result = dispatch_mod.dispatch(task, 7)

    task.apply.assert_called_once_with(args=[7], kwargs={})
    task.delay.assert_not_called()
    assert result == task.apply.return_value.get.return_value


def test_dispatch_serverless_llm_task_goes_to_sqs(monkeypatch):
    monkeypatch.setattr(settings, "dispatch_backend", "serverless")
    monkeypatch.setattr(settings, "llm_queue_url", "https://sqs.local/queue")
    sqs = MagicMock()
    monkeypatch.setattr(boto3, "client", lambda service: sqs)
    task = FakeTask("app.tasks.bills.generate_bill_summary_layer")

    dispatch_mod.dispatch(task, 42, "proposal")

    task.apply.assert_not_called()
    task.delay.assert_not_called()
    sqs.send_message.assert_called_once()
    body = json.loads(sqs.send_message.call_args.kwargs["MessageBody"])
    assert body == {
        "task": "app.tasks.bills.generate_bill_summary_layer",
        "args": [42, "proposal"],
        "kwargs": {},
    }


def test_dispatch_serverless_llm_without_queue_url_raises(monkeypatch):
    monkeypatch.setattr(settings, "dispatch_backend", "serverless")
    monkeypatch.setattr(settings, "llm_queue_url", None)
    task = FakeTask("app.tasks.bills.generate_bill_summary_layer")

    with pytest.raises(RuntimeError):
        dispatch_mod.dispatch(task, 1, "proposal")


# --------------------------------------------------------------------------- #
# Workstream 5 — API middleware + docs gating
# --------------------------------------------------------------------------- #
def test_middleware_open_when_secret_unset(monkeypatch):
    monkeypatch.setattr(settings, "api_shared_secret", "")
    client = TestClient(create_app(include_admin=False))

    assert client.get("/health").status_code == 200
    # A non-exempt unknown path passes the middleware (404, not 401).
    assert client.get("/api/v1/does-not-exist").status_code == 404


def test_middleware_blocks_without_header_when_secret_set(monkeypatch):
    monkeypatch.setattr(settings, "api_shared_secret", "s3cret")
    client = TestClient(create_app(include_admin=False))

    assert client.get("/api/v1/does-not-exist").status_code == 401
    # exempt paths stay open even with the secret set
    assert client.get("/health").status_code == 200
    assert client.get("/admin/anything").status_code != 401


def test_middleware_allows_correct_header(monkeypatch):
    monkeypatch.setattr(settings, "api_shared_secret", "s3cret")
    client = TestClient(create_app(include_admin=False))

    resp = client.get("/api/v1/does-not-exist", headers={"X-Camara-Api-Key": "s3cret"})
    assert resp.status_code == 404  # passed middleware, route just doesn't exist


def test_docs_disabled_returns_404(monkeypatch):
    monkeypatch.setattr(settings, "docs_enabled", False)
    monkeypatch.setattr(settings, "api_shared_secret", "")
    client = TestClient(create_app(include_admin=False))

    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


# --------------------------------------------------------------------------- #
# Workstream 5 — jobs handler
# --------------------------------------------------------------------------- #
def test_jobs_handler_run_func_and_revalidates(monkeypatch):
    import app.lambdas.jobs as jobs

    monkeypatch.setattr(jobs, "_resolve", lambda m, a: lambda: {"ran": a})
    reval = MagicMock()
    monkeypatch.setattr(jobs, "revalidate", reval)

    out = jobs.handler({"task": "ingest_bills"}, None)

    assert out == {"task": "ingest_bills", "result": {"ran": "run_ingest_bills"}}
    reval.assert_called_once_with(["bills", "dashboard"])


def test_jobs_handler_celery_task_runs_eagerly(monkeypatch):
    import app.lambdas.jobs as jobs

    task = MagicMock()
    task.apply.return_value.get.return_value = {"done": True}
    monkeypatch.setattr(jobs, "_resolve", lambda m, a: task)
    monkeypatch.setattr(jobs, "revalidate", MagicMock())

    out = jobs.handler({"task": "alert_orphan_votes"}, None)

    task.apply.assert_called_once_with()
    assert out["result"] == {"done": True}


def test_jobs_handler_unknown_task_raises():
    import app.lambdas.jobs as jobs

    with pytest.raises(ValueError):
        jobs.handler({"task": "nope"}, None)


def test_jobs_dispatch_targets_all_resolve():
    import app.lambdas.jobs as jobs

    for module_name, attr in {**jobs._RUN_FUNCS, **jobs._CELERY_TASKS}.values():
        assert jobs._resolve(module_name, attr) is not None


# --------------------------------------------------------------------------- #
# Workstream 5 — LLM handler
# --------------------------------------------------------------------------- #
def test_llm_handler_runs_named_task(monkeypatch):
    import app.lambdas.llm as llm

    task = MagicMock()
    monkeypatch.setitem(llm.celery_app.tasks, "test.task", task)
    event = {
        "Records": [
            {"body": json.dumps({"task": "test.task", "args": [1, "p"], "kwargs": {}})}
        ]
    }

    llm.handler(event, None)

    task.apply.assert_called_once_with(args=[1, "p"], kwargs={})
    task.apply.return_value.get.assert_called_once()


def test_llm_handler_propagates_failure(monkeypatch):
    import app.lambdas.llm as llm

    task = MagicMock()
    task.apply.return_value.get.side_effect = RuntimeError("boom")
    monkeypatch.setitem(llm.celery_app.tasks, "test.task", task)
    event = {"Records": [{"body": json.dumps({"task": "test.task"})}]}

    with pytest.raises(RuntimeError):
        llm.handler(event, None)


# --------------------------------------------------------------------------- #
# Workstream 5 — revalidate helper
# --------------------------------------------------------------------------- #
def test_revalidate_noop_without_config(monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", None)
    monkeypatch.setattr(settings, "frontend_revalidate_token", None)
    post = MagicMock()
    monkeypatch.setattr(httpx, "post", post)

    revalidate(["bills"])

    post.assert_not_called()


def test_revalidate_noop_on_empty_tags(monkeypatch):
    post = MagicMock()
    monkeypatch.setattr(httpx, "post", post)

    revalidate([])

    post.assert_not_called()


def test_revalidate_posts_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", "https://front.example/")
    monkeypatch.setattr(settings, "frontend_revalidate_token", "tok")
    post = MagicMock(return_value=SimpleNamespace(status_code=200, text="ok"))
    monkeypatch.setattr(httpx, "post", post)

    revalidate(["bills", "dashboard"])

    post.assert_called_once()
    assert post.call_args.args[0] == "https://front.example/api/revalidate"
    assert post.call_args.kwargs["json"] == {"tags": ["bills", "dashboard"]}
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


def test_revalidate_swallows_transport_errors(monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", "https://front.example")
    monkeypatch.setattr(settings, "frontend_revalidate_token", "tok")
    monkeypatch.setattr(
        httpx, "post", MagicMock(side_effect=httpx.ConnectError("down"))
    )

    revalidate(["bills"])  # must not raise


# --------------------------------------------------------------------------- #
# Workstream 2 — secret resolver
# --------------------------------------------------------------------------- #
def test_hydrate_secrets_noop_when_unset(monkeypatch):
    monkeypatch.delenv("DB_SECRET_ARN", raising=False)
    for param in secrets_mod._PARAM_ENV_MAP:
        monkeypatch.delenv(param, raising=False)
    # Should not touch boto3 at all.
    monkeypatch.setattr(boto3, "client", MagicMock(side_effect=AssertionError))

    secrets_mod.hydrate_secrets_into_env()


def test_hydrate_builds_database_url_from_secret(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")  # falsy -> resolver proceeds
    monkeypatch.setenv("DB_SECRET_ARN", "arn:aws:secret")
    for param in secrets_mod._PARAM_ENV_MAP:
        monkeypatch.delenv(param, raising=False)
    sm = MagicMock()
    sm.get_secret_value.return_value = {
        "SecretString": json.dumps(
            {"username": "u", "password": "p", "host": "h", "port": 5432, "dbname": "d"}
        )
    }
    monkeypatch.setattr(boto3, "client", lambda service: sm)

    secrets_mod.hydrate_secrets_into_env()

    assert os.environ["DATABASE_URL"] == "postgresql://u:p@h:5432/d"


def test_hydrate_resolves_ssm_param(monkeypatch):
    monkeypatch.delenv("DB_SECRET_ARN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY_PARAM", "/camara/anthropic-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ssm = MagicMock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": "sk-secret"}}
    monkeypatch.setattr(boto3, "client", lambda service: ssm)

    secrets_mod.hydrate_secrets_into_env()

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-secret"
    ssm.get_parameter.assert_called_once_with(
        Name="/camara/anthropic-key", WithDecryption=True
    )
