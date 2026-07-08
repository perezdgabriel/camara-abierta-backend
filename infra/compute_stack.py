"""Compute + orchestration for the AWS deployment (see ADR-0022).

One container image (Dockerfile.lambda), four functions selected by CMD:
  - API   (isolated subnets, Function URL, RDS-only)        cmd: app.lambdas.api.handler
  - jobs  (egress subnets, EventBridge-scheduled + S3-triggered ingestion)
          cmd: app.lambdas.jobs.handler
  - llm   (egress subnets, SQS-driven summaries, capped)    cmd: app.lambdas.llm.handler
  - migrate (isolated, `alembic upgrade head`, CI-invoked)  cmd: app.lambdas.migrate.handler

Consumes NetworkStack for the VPC, RDS instance, and DB security group.
"""

import os

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_cloudwatch as cw,
)
from aws_cdk import (
    aws_cloudwatch_actions as cw_actions,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_ecr_assets as ecr_assets,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_events_targets as targets,
)
from aws_cdk import (
    aws_lambda as _lambda,
)
from aws_cdk import (
    aws_lambda_event_sources as sources,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_s3_notifications as s3_notifications,
)
from aws_cdk import (
    aws_sns as sns,
)
from aws_cdk import (
    aws_sns_subscriptions as subs,
)
from aws_cdk import (
    aws_sqs as sqs,
)
from aws_cdk import (
    aws_ssm as ssm,
)
from constructs import Construct
from network_stack import NetworkStack

_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")

# SSM SecureString parameter *names* holding the app secrets. Created out-of-band
# once (see infra/README.md); the app resolves them at cold start via
# app/core/secrets.py (the function only ever receives the *name* + GetParameter
# permission, never the value — keeps secrets out of the CFN template).
_ANTHROPIC_KEY_PARAM = "/camara/anthropic-key"
_RESTSIL_KEY_PARAM = "/camara/restsil-key"
_API_SHARED_SECRET_PARAM = "/camara/api-shared-secret"
_FRONTEND_REVALIDATE_TOKEN_PARAM = "/camara/frontend-revalidate-token"
_ADMIN_USERNAME_PARAM = "/camara/admin-username"
_ADMIN_PASSWORD_PARAM = "/camara/admin-password"
_ADMIN_SECRET_KEY_PARAM = "/camara/admin-secret-key"

# Mirrors app/core/celery_beat.py. Scraper entries are intentionally dropped
# (descoped from the deployed image — see ADR-0022). Cron is UTC.
_SCHEDULES: list[tuple[str, dict, str]] = [
    (
        "ingest-bills",
        {"minute": "0", "hour": "10,13,17,21,23", "week_day": "MON-FRI"},
        "ingest_bills",
    ),
    (
        "ingest-senate-votes",
        {"minute": "15", "hour": "10,13,17,21,23", "week_day": "MON-FRI"},
        "ingest_senate_votes",
    ),
    (
        "ingest-chamber-votes",
        {"minute": "30", "hour": "10,13,17,21,23", "week_day": "MON-FRI"},
        "ingest_chamber_votes",
    ),
    (
        "ingest-legislators",
        {"minute": "0", "hour": "3", "week_day": "MON-FRI"},
        "ingest_legislators",
    ),
    (
        "ingest-legislature",
        {"minute": "0", "hour": "3", "week_day": "MON-FRI"},
        "ingest_legislature",
    ),
    (
        "refresh-voting-window",
        {"minute": "0", "hour": "4", "week_day": "MON-FRI"},
        "refresh_voting_window_aggregate",
    ),
    (
        "refresh-legislator-stats",
        {"minute": "20", "hour": "4", "week_day": "MON-FRI"},
        "refresh_legislator_voting_stats",
    ),
    (
        "alert-orphan-votes",
        {"minute": "45", "hour": "5", "week_day": "MON-FRI"},
        "alert_orphan_votes",
    ),
]


class ComputeStack(Stack):
    def __init__(
        self, scope: Construct, id: str, *, network: NetworkStack, **kwargs
    ) -> None:
        super().__init__(scope, id, **kwargs)

        # Shared SG for all DB-talking functions is defined in NetworkStack (so the
        # db_sg <- lambda_sg ingress rule stays in one stack and avoids a cycle).
        lambda_sg = network.lambda_sg

        base_env = {"DB_SECRET_ARN": network.db.secret.secret_arn}

        def image(handler: str) -> _lambda.DockerImageCode:
            # Same build context/hash for every function -> image is built and
            # pushed to ECR once; only the CMD (imageConfig) differs per function.
            # Pin the build platform to linux/amd64 so the image arch matches the
            # functions' default x86_64 architecture regardless of the builder's
            # host arch (Apple Silicon would otherwise produce arm64 -> the Lambda
            # runtime can't spawn the entrypoint: Runtime.InvalidEntrypoint).
            return _lambda.DockerImageCode.from_image_asset(
                _REPO_ROOT,
                file="Dockerfile.lambda",
                cmd=[handler],
                platform=ecr_assets.Platform.LINUX_AMD64,
            )

        common = dict(
            vpc=network.vpc,
            security_groups=[lambda_sg],
        )

        # --- API: egress subnet + Function URL ---
        # In PRIVATE_WITH_EGRESS (not isolated) so cold-start secret resolution
        # can reach Secrets Manager + SSM via the fck-nat; RDS stays isolated.
        self.api_fn = _lambda.DockerImageFunction(
            self,
            "ApiFn",
            code=image("app.lambdas.api.handler"),
            memory_size=512,
            timeout=Duration.seconds(30),
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            environment={
                **base_env,
                # serverless so any accidental dispatch runs inline instead of
                # reaching a nonexistent Redis broker.
                "DISPATCH_BACKEND": "serverless",
                "DOCS_ENABLED": "false",
                "API_SHARED_SECRET_PARAM": _API_SHARED_SECRET_PARAM,
                "ADMIN_USERNAME_PARAM": _ADMIN_USERNAME_PARAM,
                "ADMIN_PASSWORD_PARAM": _ADMIN_PASSWORD_PARAM,
                "ADMIN_SECRET_KEY_PARAM": _ADMIN_SECRET_KEY_PARAM,
            },
            **common,
        )
        # AuthType NONE + in-app shared-secret header (validated against SSM). The
        # frontend (Vercel RSC) sends the secret server-to-server.
        self.api_url = self.api_fn.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.NONE
        )

        # --- LLM: SQS-driven, concurrency-capped (the `-c 1` worker analog) ---
        llm_dlq = sqs.Queue(self, "LlmDlq", retention_period=Duration.days(14))
        self.llm_queue = sqs.Queue(
            self,
            "LlmQueue",
            visibility_timeout=Duration.seconds(360),  # >= llm_fn timeout
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=llm_dlq),
        )
        self.llm_fn = _lambda.DockerImageFunction(
            self,
            "LlmFn",
            code=image("app.lambdas.llm.handler"),
            memory_size=1024,
            timeout=Duration.seconds(300),
            # NOTE: reserved concurrency (to cap parallel Anthropic calls) is
            # omitted because new accounts have a total Lambda concurrency quota
            # of 10, and reserving any amount would drop unreserved below the
            # required minimum of 10. The account-wide limit of 10 is the de-facto
            # cap for now; after a Service Quotas increase, restore the intended
            # cap by adding `reserved_concurrent_executions=2` here.
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            environment={
                **base_env,
                "DISPATCH_BACKEND": "serverless",
                "ANTHROPIC_API_KEY_PARAM": _ANTHROPIC_KEY_PARAM,
                "AI_SUMMARY_ENABLED": "true",
            },
            **common,
        )
        self.llm_fn.add_event_source(
            sources.SqsEventSource(self.llm_queue, batch_size=1)
        )

        # --- tabla-semanal ingest bucket: human uploads PDF, triggers job_fn via
        # S3 event. Portfolio project: DESTROY + auto_delete so `cdk destroy`
        # doesn't orphan a bucket (mirrors RDS's deletion_protection=False stance).
        self.tabla_semanal_bucket = s3.Bucket(
            self,
            "TablaSemanalBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True,
            enforce_ssl=True,
        )

        # --- jobs: EventBridge-scheduled ingestion; enqueues summaries to SQS ---
        self.job_fn = _lambda.DockerImageFunction(
            self,
            "JobFn",
            code=image("app.lambdas.jobs.handler"),
            memory_size=1024,
            timeout=Duration.minutes(15),
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            environment={
                **base_env,
                "DISPATCH_BACKEND": "serverless",
                "LLM_QUEUE_URL": self.llm_queue.queue_url,
                "INGESTOR_RESTSIL_API_KEY_PARAM": _RESTSIL_KEY_PARAM,
                "AI_SUMMARY_ENABLED": "true",
                # Post-ingest cache revalidation ping to the frontend. Not a
                "FRONTEND_URL": "https://camaraabierta.cl",
                "FRONTEND_REVALIDATE_TOKEN_PARAM": _FRONTEND_REVALIDATE_TOKEN_PARAM,
            },
            **common,
        )
        self.llm_queue.grant_send_messages(self.job_fn)

        # S3 upload -> job_fn (ADR-0017 amendment: delivery mechanism change only).
        self.tabla_semanal_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3_notifications.LambdaDestination(self.job_fn),
            s3.NotificationKeyFilter(prefix="tabla-semanal/", suffix=".pdf"),
        )
        self.tabla_semanal_bucket.grant_read(self.job_fn)

        # Classic EventBridge scheduled rules (stable L2). If you want the newer
        # EventBridge Scheduler L2, add @aws-cdk/aws-scheduler-alpha.
        for rule_id, cron, task in _SCHEDULES:
            events.Rule(
                self,
                f"Sched-{rule_id}",
                schedule=events.Schedule.cron(**cron),
                targets=[
                    targets.LambdaFunction(
                        self.job_fn,
                        event=events.RuleTargetInput.from_object({"task": task}),
                    )
                ],
            )

        # --- migration Lambda: `alembic upgrade head`, invoked by CI post-deploy ---
        self.migrate_fn = _lambda.DockerImageFunction(
            self,
            "MigrateFn",
            function_name="camara-migrate",  # stable name so CI can invoke it post-deploy
            code=image("app.lambdas.migrate.handler"),
            memory_size=512,
            timeout=Duration.seconds(300),
            # Egress subnet so it can read the DB secret from Secrets Manager.
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            environment=base_env,
            **common,
        )

        # --- secrets: DB master (Secrets Manager) to every DB-talking function ---
        for fn in (self.api_fn, self.job_fn, self.llm_fn, self.migrate_fn):
            network.db.secret.grant_read(fn)

        # App secrets live in SSM Parameter Store (SecureString). Grant GetParameter
        # per function that needs each one; the value is resolved at cold start.
        def secure_param(cid: str, name: str) -> ssm.IStringParameter:
            return ssm.StringParameter.from_secure_string_parameter_attributes(
                self, cid, parameter_name=name
            )

        secure_param("AnthropicKeyParam", _ANTHROPIC_KEY_PARAM).grant_read(self.llm_fn)
        secure_param("RestsilKeyParam", _RESTSIL_KEY_PARAM).grant_read(self.job_fn)
        secure_param("ApiSharedSecretParam", _API_SHARED_SECRET_PARAM).grant_read(
            self.api_fn
        )
        secure_param(
            "FrontendRevalidateTokenParam", _FRONTEND_REVALIDATE_TOKEN_PARAM
        ).grant_read(self.job_fn)
        secure_param("AdminUsernameParam", _ADMIN_USERNAME_PARAM).grant_read(
            self.api_fn
        )
        secure_param("AdminPasswordParam", _ADMIN_PASSWORD_PARAM).grant_read(
            self.api_fn
        )
        secure_param("AdminSecretKeyParam", _ADMIN_SECRET_KEY_PARAM).grant_read(
            self.api_fn
        )

        # --- observability: CloudWatch alarms -> SNS -> email ---
        # Set with `cdk deploy -c alarm_email=me@example.com`.
        alarm_email = self.node.try_get_context("alarm_email")
        if not alarm_email:
            raise ValueError(
                "alarm_email context is required "
                "(cdk deploy -c alarm_email=you@example.com)"
            )
        topic = sns.Topic(self, "AlarmTopic")
        topic.add_subscription(subs.EmailSubscription(alarm_email))
        action = cw_actions.SnsAction(topic)

        for name, fn in (
            ("Api", self.api_fn),
            ("Job", self.job_fn),
            ("Llm", self.llm_fn),
        ):
            fn.metric_errors(period=Duration.minutes(5)).create_alarm(
                self, f"{name}Errors", threshold=1, evaluation_periods=1
            ).add_alarm_action(action)

        # DLQ depth > 0 => summaries are failing silently. This is the one that
        # catches the "stale data, no user-facing symptom" failure mode.
        llm_dlq.metric_approximate_number_of_messages_visible().create_alarm(
            self,
            "LlmDlqDepth",
            threshold=0,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
        ).add_alarm_action(action)
        # TODO: NAT instance status-check alarm (per-ASG-instance metric).

        # --- outputs: consumed by the frontend agent / CI ---
        CfnOutput(self, "ApiFunctionUrl", value=self.api_url.url)
        CfnOutput(self, "LlmQueueUrl", value=self.llm_queue.queue_url)
        CfnOutput(
            self, "TablaSemanalBucketName", value=self.tabla_semanal_bucket.bucket_name
        )
