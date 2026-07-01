"""Network + data foundation for the AWS deployment (see ADR-0022).

Defines the VPC, the fck-nat NAT instance (which doubles as the SSM bastion for
the one-time RDS data load — see `just rds-tunnel`), and the private RDS
PostgreSQL 16 instance. Compute (API / job / LLM Lambdas, SQS, EventBridge,
CloudWatch alarms) lives in a separate stack that consumes `vpc`, `db`,
`db_sg`, and `nat` exported here.

Layout:
  - public              : fck-nat lives here
  - PRIVATE_WITH_EGRESS : job + LLM Lambdas (egress to Congress APIs / Anthropic via fck-nat)
  - PRIVATE_ISOLATED    : RDS + the API Lambda (no internet route at all)
"""

from aws_cdk import Stack, Tags, aws_ec2 as ec2, aws_rds as rds
from cdk_fck_nat import FckNatInstanceProvider
from constructs import Construct


class NetworkStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # --- NAT: t4g.nano via fck-nat (ASG min=max=1, self-healing) ---
        # enable_ssm defaults True, so the SSM policy is attached automatically
        # and this box is a Session Manager target out of the box. That's what
        # lets it double as the bastion for `just rds-tunnel`.
        self.nat = FckNatInstanceProvider(
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T4G, ec2.InstanceSize.NANO
            ),
        )
        # Deterministic tag so `rds-tunnel` can resolve the live ASG instance.
        Tags.of(self.nat).add("role", "nat-bastion")

        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=1,  # one NAT instance total (cost)
            nat_gateway_provider=self.nat,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="app",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="data",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )

        # fck-nat gotcha: its SG must permit the private subnets to route out.
        self.nat.security_group.add_ingress_rule(
            ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            ec2.Port.all_traffic(),
            "Allow VPC private subnets to egress via fck-nat",
        )

        # --- RDS: private, single-AZ, PG16 to match the local `postgres:16` engine ---
        self.db_sg = ec2.SecurityGroup(
            self, "DbSg", vpc=self.vpc, allow_all_outbound=False
        )

        self.db = rds.DatabaseInstance(
            self,
            "Db",
            engine=rds.DatabaseInstanceEngine.postgres(
                # Pin to a 16.x currently offered in the target region; must match local.
                version=rds.PostgresEngineVersion.VER_16_4,
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO
            ),
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            security_groups=[self.db_sg],
            multi_az=False,
            allocated_storage=20,
            storage_type=rds.StorageType.GP3,
            # The one justified Secrets Manager use (~$0.40/mo): generated master
            # secret + `db.secret.grant_read(fn)` per Lambda. App secrets (Anthropic
            # key, Vercel shared secret, ...) stay in SSM Parameter Store per ADR-0022.
            credentials=rds.Credentials.from_generated_secret("camara"),
            # Portfolio project: keep it easy to tear down. Flip both for anything real.
            deletion_protection=False,
        )

        # This is what makes `just rds-tunnel` actually reach RDS: the SSM
        # port-forward lands on the NAT box, which must be allowed to hop to Postgres.
        self.db_sg.add_ingress_rule(
            self.nat.security_group,
            ec2.Port.tcp(5432),
            "SSM tunnel via NAT bastion -> Postgres",
        )

    def allow_lambda(self, lambda_sg: ec2.ISecurityGroup) -> None:
        """Let a compute-stack Lambda's SG reach Postgres. Call from ComputeStack."""
        self.db_sg.add_ingress_rule(
            lambda_sg, ec2.Port.tcp(5432), "Lambda -> Postgres"
        )
