"""Compute + orchestration for the AWS deployment (see ADR-0022).

One container image (Dockerfile.lambda), four functions selected by CMD:
  - API   (isolated subnets, Function URL, RDS-only)        cmd: app.lambdas.api.handler
  - jobs  (egress subnets, EventBridge-scheduled ingestion) cmd: app.lambdas.jobs.handler
  - llm   (egress subnets, SQS-driven summaries, capped)    cmd: app.lambdas.llm.handler
  - migrate (isolated, `alembic upgrade head`, CI-invoked)  cmd: app.lambdas.migrate.handler

Consumes NetworkStack for the VPC, RDS instance, and DB security group.
"""

import os

from aws_cdk import (
    Duration,
    Stack,
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
    aws_ec2 as ec2,
    aws_events as events,
    aws_events_targets as targets,
    aws_lambda as _lambda,
    aws_lambda_event_sources as sources,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
    aws_sqs as sqs,
)
from constructs import Construct

from network_stack import NetworkStack

_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")

# Mirrors app/core/celery_beat.py. Scraper entries are intentionally dropped
# (descoped from the deployed image — see ADR-0022). Cron is UTC.
_SCHEDULES: list[tuple[str, dict, str]] = [
    ("ingest-bills", {"minute": "0", "hour": "5,9,13,17,21"}, "ingest_bills"),
    ("ingest-senate-votes", {"minute": "15", "hour": "5,9,13,17,21"}, "ingest_senate_votes"),
    ("ingest-chamber-votes", {"minute": "30", "hour": "5,9,13,17,21"}, "ingest_chamber_votes"),
    ("ingest-legislators", {"minute": "0", "hour": "3"}, "ingest_legislators"),
    ("ingest-committees", {"minute": "0", "hour": "3"}, "ingest_committees"),
    ("ingest-legislature", {"minute": "0", "hour": "3"}, "ingest_legislature"),
    ("refresh-voting-window", {"minute": "0", "hour": "4"}, "refresh_voting_window_aggregate"),
    ("refresh-legislator-stats", {"minute": "20", "hour": "4"}, "refresh_legislator_voting_stats"),
    ("alert-orphan-votes", {"minute": "45", "hour": "5"}, "alert_orphan_votes"),
]


class ComputeStack(Stack):
    def __init__(
        self, scope: Construct, id: str, *, network: NetworkStack, **kwargs
    ) -> None:
        super().__init__(scope, id, **kwargs)

        # One SG for all DB-talking functions; allowed into Postgres once.
        lambda_sg = ec2.SecurityGroup(self, "LambdaSg", vpc=network.vpc)
        network.allow_lambda(lambda_sg)

        base_env = {"DB_SECRET_ARN": network.db.secret.secret_arn}

        def image(handler: str) -> _lambda.DockerImageCode:
            # Same build context/hash for every function -> image is built and
            # pushed to ECR once; only the CMD (imageConfig) differs per function.
            return _lambda.DockerImageCode.from_image_asset(
                _REPO_ROOT, file="Dockerfile.lambda", cmd=[handler]
            )

        common = dict(
            vpc=network.vpc,
            security_groups=[lambda_sg],
            environment=base_env,
        )

        # --- API: isolated subnets (RDS-only, no NAT), Function URL ---
        self.api_fn = _lambda.DockerImageFunction(
            self,
            "ApiFn",
            code=image("app.lambdas.api.handler"),
            memory_size=512,
            timeout=Duration.seconds(30),
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
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
            reserved_concurrent_executions=2,  # caps parallel Anthropic calls / spend
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            **common,
        )
        self.llm_fn.add_event_source(
            sources.SqsEventSource(self.llm_queue, batch_size=1)
        )

        # --- jobs: EventBridge-scheduled ingestion; enqueues summaries to SQS ---
        self.job_fn = _lambda.DockerImageFunction(
            self,
            "JobFn",
            code=image("app.lambdas.jobs.handler"),
            memory_size=1024,
            timeout=Duration.minutes(15),
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            environment={**base_env, "LLM_QUEUE_URL": self.llm_queue.queue_url},
        )
        self.llm_queue.grant_send_messages(self.job_fn)

        # Classic EventBridge scheduled rules (stable L2). If you want the newer
        # EventBridge Scheduler L2, add @aws-cdk/aws-scheduler-alpha.
        for rule_id, cron, task in _SCHEDULES:
            events.Rule(
                self,
                f"Sched-{rule_id}",
                schedule=events.Schedule.cron(**cron),
                targets=[
                    targets.LambdaFunction(
                        self.job_fn, event=events.RuleTargetInput.from_object({"task": task})
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
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            **common,
        )

        # --- secrets: DB master (Secrets Manager) to every DB-talking function ---
        for fn in (self.api_fn, self.job_fn, self.llm_fn, self.migrate_fn):
            network.db.secret.grant_read(fn)
        # App secrets live in SSM Parameter Store; grant per-function, e.g.:
        #   anthropic = ssm.StringParameter.from_secure_string_parameter_attributes(...)
        #   anthropic.grant_read(self.llm_fn)   # + jobs if it enqueues

        # --- observability: CloudWatch alarms -> SNS -> email ---
        topic = sns.Topic(self, "AlarmTopic")
        topic.add_subscription(subs.EmailSubscription("you@example.com"))  # TODO: real inbox
        action = cw_actions.SnsAction(topic)

        for name, fn in (("Api", self.api_fn), ("Job", self.job_fn), ("Llm", self.llm_fn)):
            fn.metric_errors(period=Duration.minutes(5)).create_alarm(
                self, f"{name}Errors", threshold=1, evaluation_periods=1
            ).add_alarm_action(action)

        # DLQ depth > 0 => summaries are failing silently. This is the one that
        # catches the "stale data, no user-facing symptom" failure mode.
        llm_dlq.metric_approximate_number_of_messages_visible().create_alarm(
            self, "LlmDlqDepth", threshold=0, evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
        ).add_alarm_action(action)
        # TODO: NAT instance status-check alarm (per-ASG-instance metric).
