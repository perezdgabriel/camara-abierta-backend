"""Network + data foundation for the AWS deployment (see ADR-0022).

Defines the VPC, the fck-nat NAT instance (which doubles as the SSM bastion for
the one-time RDS data load — see `just rds-tunnel`), and the private RDS
PostgreSQL 16 instance. Compute (API / job / LLM Lambdas, SQS, EventBridge,
CloudWatch alarms) lives in a separate stack that consumes `vpc`, `db`,
`db_sg`, and `nat` exported here.

Layout:
  - public              : fck-nat lives here
  - PRIVATE_WITH_EGRESS : API + job + LLM + migrate Lambdas (egress to Congress
                          APIs / Anthropic / Secrets Manager / SSM via fck-nat)
  - PRIVATE_ISOLATED    : RDS only (no internet route at all)
"""

from aws_cdk import Stack, Tags
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from cdk_fck_nat import FckNatInstanceProvider
from constructs import Construct


class NetworkStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # --- NAT: t3.micro via fck-nat (ASG min=max=1, self-healing) ---
        # t3.micro (x86) is free-tier eligible (750h/mo/12mo); the ARM t4g.nano
        # the ADR originally chose is not, and new-account Free Plans hard-fail
        # its launch. fck-nat auto-selects the matching x86 AMI. enable_ssm
        # defaults True, so this box is a Session Manager target out of the box —
        # what lets it double as the bastion for `just rds-tunnel`.
        self.nat = FckNatInstanceProvider(
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3, ec2.InstanceSize.MICRO
            ),
            # fck-nat defaults to its arm64 AMI and does NOT auto-switch when the
            # instance type is x86 -> launch-template arch mismatch. Pin the amd64
            # fck-nat AMI (owner 568608671756) to match t3.micro.
            machine_image=ec2.LookupMachineImage(
                name="fck-nat-al2023-*-x86_64-ebs",
                owners=["568608671756"],
            ),
        )

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

        # Deterministic tag so `just rds-tunnel` can resolve the live NAT box by
        # `role=nat-bastion`. The NAT runs under an ASG (only populated once the
        # VPC above has configured it), so tag the ASG — CDK propagates ASG tags
        # to instances at launch. Tagging the provider directly fails (it isn't a
        # construct in the tree until configured).
        for asg in self.nat.auto_scaling_groups:
            Tags.of(asg).add("role", "nat-bastion")

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
            # db.t3.micro (x86) is RDS-free-tier eligible; matches the Free Plan.
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3, ec2.InstanceSize.MICRO
            ),
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            security_groups=[self.db_sg],
            multi_az=False,
            # Explicit DB name so the generated secret carries `dbname=camara`
            # deterministically (app builds DATABASE_URL from it; restore-rds
            # targets it). Without this the secret may omit dbname.
            database_name="camara",
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
            "SSM tunnel via NAT bastion to Postgres",
        )

        # Shared SG for all DB-talking Lambdas. Defined here (not in ComputeStack)
        # so the db_sg <- lambda_sg ingress rule lives entirely in this stack;
        # putting the rule across stacks makes NetworkStack depend on ComputeStack
        # while ComputeStack already depends on NetworkStack -> dependency cycle.
        self.lambda_sg = ec2.SecurityGroup(self, "LambdaSg", vpc=self.vpc)
        self.db_sg.add_ingress_rule(
            self.lambda_sg, ec2.Port.tcp(5432), "Lambda to Postgres"
        )
