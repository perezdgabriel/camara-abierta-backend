"""GitHub Actions OIDC deploy role (see ADR-0022).

Bootstrap-once stack: creates the GitHub OIDC provider and a role that the
`deploy` workflow assumes via short-lived tokens — no static AWS keys anywhere.
Deploy this locally with admin creds *before* the pipeline can run:

    cdk bootstrap
    cdk deploy CamaraCicd -c github_owner=<owner> -c github_repo=<repo>

Then set the `AWS_ACCOUNT_ID` GitHub secret. The role only needs to assume the
CDK bootstrap roles (which do the actual deploy) + invoke the migrate function.
"""

from aws_cdk import CfnOutput, Duration, Stack, aws_iam as iam
from constructs import Construct


class CicdStack(Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        github_owner: str,
        github_repo: str,
        branch: str = "main",
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        # One OIDC provider per account for GitHub. If it already exists, import it
        # with OpenIdConnectProvider.from_open_id_connect_provider_arn(...) instead.
        provider = iam.OpenIdConnectProvider(
            self,
            "GithubOidc",
            url="https://token.actions.githubusercontent.com",
            client_ids=["sts.amazonaws.com"],
        )

        deploy_role = iam.Role(
            self,
            "GithubDeployRole",
            role_name="camara-github-deploy",
            max_session_duration=Duration.hours(1),
            assumed_by=iam.WebIdentityPrincipal(
                provider.open_id_connect_provider_arn,
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
                    },
                    # Deploys only from the given repo's main branch.
                    "StringLike": {
                        "token.actions.githubusercontent.com:sub": f"repo:{github_owner}/{github_repo}:ref:refs/heads/{branch}"
                    },
                },
            ),
        )

        # CDK deploys by assuming the bootstrap roles; the GH role just needs to
        # assume those, then invoke the migrate function post-deploy.
        deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole"],
                resources=[f"arn:aws:iam::{self.account}:role/cdk-*"],
            )
        )
        deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[
                    f"arn:aws:lambda:{self.region}:{self.account}:function:camara-migrate"
                ],
            )
        )

        CfnOutput(self, "DeployRoleArn", value=deploy_role.role_arn)
