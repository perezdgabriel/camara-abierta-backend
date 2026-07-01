#!/usr/bin/env python3
"""CDK app entry point (see ADR-0022).

Currently deploys the network + data foundation. The compute stack (API / job /
LLM Lambdas from the shared container image, SQS + DLQ, EventBridge Scheduler,
migration Lambda, CloudWatch alarms -> SNS) is the next piece to add and will
consume NetworkStack's `vpc` / `db` / `db_sg` / `nat`.
"""

import aws_cdk as cdk

from cicd_stack import CicdStack
from compute_stack import ComputeStack
from network_stack import NetworkStack

app = cdk.App()

# us-east-1 per ADR-0022 (cheapest / best free-tier coverage).
env = cdk.Environment(region="us-east-1")

network = NetworkStack(app, "CamaraNetwork", env=env)
ComputeStack(app, "CamaraCompute", network=network, env=env)

# Bootstrap-once (deploy locally). owner/repo via -c or defaults below.
CicdStack(
    app,
    "CamaraCicd",
    github_owner=app.node.try_get_context("github_owner") or "YOUR_GH_OWNER",
    github_repo=app.node.try_get_context("github_repo") or "camara-abierta-backend",
    env=env,
)

app.synth()
