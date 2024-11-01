import os
from typing import Self

import aws_cdk as cdk
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_ecr_assets as ecr_assets
import aws_cdk.aws_efs as efs
import aws_cdk.aws_eks as eks
import aws_cdk.aws_iam as iam
import aws_cdk.aws_rds as rds
import aws_cdk.aws_route53 as route53
import aws_cdk.aws_secretsmanager as secretsmanager
from aws_cdk import custom_resources as cr
import yaml
from aws_cdk.lambda_layer_kubectl import KubectlLayer
from constructs import Construct
from typing import Optional
import requests

from jinja2 import Environment
from jinja2.loaders import FileSystemLoader

jinja_env = Environment(loader=FileSystemLoader("templates"))

# CIDR block used for kubernetes cluster services
# CDK DOCS> The CIDR block to assign Kubernetes service IP addresses from. Default: -
# CDK DOCS> Kubernetes assigns addresses from either the 10.100.0.0/16 or 172.20.0.0/16 CIDR blocks
CLUSTER_SERVICE_IPV4_CIDR = "172.20.0.0/16"


SYSTEM_AUTOSCALING_GROUP_MIN_SIZE = 1
SYSTEM_AUTOSCALING_GROUP_MAX_SIZE = 5
USER_AUTOSCALING_GROUP_MIN_SIZE = 0
USER_AUTOSCALING_GROUP_MAX_SIZE = 10


class Vpc(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        vpc_id: Optional[str] = None,
        **kwargs,
    ) -> Self:
        """Initialize a Vpc stack."""
        super().__init__(scope, id, **kwargs)
        if vpc_id is not None:
            self.vpc = ec2.Vpc.from_lookup(self, "Vpc", vpc_id=vpc_id)
        else:
            self.vpc = ec2.Vpc(self, "Vpc")


class Database(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        vpc: ec2.Vpc,
        removal_policy: cdk.RemovalPolicy,
        instance_type: ec2.InstanceType,
        **kwargs,
    ) -> Self:
        """Initialize a Jupyterhub DB stack.

        Args:
            vpc_id: ID of the vpc in which to create this database
            removal_policy: cdk removal policy for resources in this stack
            instance_type: Ec2 instance type of this db instance
        """
        super().__init__(scope, id, **kwargs)
        self.security_group = ec2.SecurityGroup(
            self,
            "JupyterhubDbSecurityGroup",
            vpc=vpc,
            allow_all_outbound=True,
        )
        self.security_group.add_ingress_rule(
            ec2.Peer.ipv4(vpc.vpc_cidr_block),
            ec2.Port.tcp(5432),
            description="Allow all inbound postgres traffic from VPC.",
        )

        self.security_group.add_ingress_rule(
            ec2.Peer.ipv4("10.0.0.0/8"),
            ec2.Port.tcp(5432),
            description="Allow all inbound postgres traffic from VPN.",
        )

        self.security_group.add_ingress_rule(
            ec2.Peer.ipv4(CLUSTER_SERVICE_IPV4_CIDR),
            ec2.Port.tcp(5432),
            description="Allow all inbound postgres traffic from EKS cluster.",
        )

        self.db = rds.DatabaseInstance(
            self,
            "JupyterhubDb",
            engine=rds.DatabaseInstanceEngine.postgres(version=rds.PostgresEngineVersion.VER_16),
            storage_encrypted=True,
            instance_type=instance_type,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnets=vpc.select_subnets().subnets),
            enable_performance_insights=True,
            security_groups=[self.security_group],
            removal_policy=removal_policy,
        )


class FileSystem(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        vpc: ec2.Vpc,
        removal_policy: cdk.RemovalPolicy,
        automatic_backups: bool,
        **kwargs,
    ) -> Self:
        """Initialize a JupyterhubFileSystem stack.

        Args:
            vpc_id: ID of the vpc in which to create this application
            removal_policy: cdk removal policy for resources in this stack
            automatic_backups: Whether or not to enable automatic backups for the EFS file system
        """
        super().__init__(scope, id, **kwargs)

        # Set up EFS file system
        efs_security_group = ec2.SecurityGroup(
            self,
            "EfsSecurityGroup",
            vpc=vpc,
            allow_all_outbound=True,
        )
        efs_security_group.add_ingress_rule(
            ec2.Peer.ipv4(vpc.vpc_cidr_block),
            ec2.Port.tcp(2049),
            description="Allow all inbound NFS traffic from VPC.",
        )

        efs_security_group.add_ingress_rule(
            ec2.Peer.ipv4("10.0.0.0/8"),
            ec2.Port.tcp(2049),
            description="Allow all inbound NFS traffic from VPN.",
        )

        efs_security_group.add_ingress_rule(
            ec2.Peer.ipv4(CLUSTER_SERVICE_IPV4_CIDR),
            ec2.Port.tcp(2049),
            description="Allow all inbound NFS traffic from EKS cluster.",
        )

        self.file_system = efs.FileSystem(
            self,
            "FileSystem",
            vpc=vpc,
            lifecycle_policy=efs.LifecyclePolicy.AFTER_14_DAYS,
            out_of_infrequent_access_policy=efs.OutOfInfrequentAccessPolicy.AFTER_1_ACCESS,
            removal_policy=removal_policy,
            security_group=efs_security_group,
            enable_automatic_backups=automatic_backups,
        )


class Application(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        vpc: ec2.Vpc,
        file_system: efs.FileSystem,
        removal_policy: cdk.RemovalPolicy,
        hub_db_secret_arn: str,
        user_node_instance_type: ec2.InstanceType,
        system_node_instance_type: ec2.InstanceType,
        **kwargs,
    ) -> Self:
        """Initialize a jupyterhub environment running on an EKS cluster."""
        super().__init__(scope, id, **kwargs)
        # The masters role will be granted permission to view and modify cluster resources
        masters_role = iam.Role(
            self,
            "MastersRole",
            assumed_by=iam.AccountPrincipal(self.account),
        )

        # Provision a Kubernetes cluster
        self.cluster = eks.Cluster(
            self,
            "Cluster",
            version=eks.KubernetesVersion.V1_29,
            kubectl_layer=KubectlLayer(self, "kubectl-layer"),
            masters_role=masters_role,
            output_masters_role_arn=True,
            output_cluster_name=True,
            output_config_command=True,
            default_capacity=0,
            cluster_logging=[
                eks.ClusterLoggingTypes.SCHEDULER,
                eks.ClusterLoggingTypes.API,
                eks.ClusterLoggingTypes.AUDIT,
                eks.ClusterLoggingTypes.AUTHENTICATOR,
                eks.ClusterLoggingTypes.CONTROLLER_MANAGER,
            ],
            service_ipv4_cidr=CLUSTER_SERVICE_IPV4_CIDR,
            vpc=vpc,
            vpc_subnets=[ec2.SubnetSelection(subnets=vpc.select_subnets().subnets)],
            place_cluster_handler_in_vpc=True,
            endpoint_access=eks.EndpointAccess.PRIVATE,
            tags=kwargs.get("tags", []),
        )

        # Apply tags to VPC private subnets to enable EKS managed load balancers
        cr.AwsCustomResource(
            self,
            "SetSubnetTagElb",
            on_create=cr.AwsSdkCall(
                service="EC2",
                action="CreateTags",
                parameters={
                    "Resources": [s.subnet_id for s in vpc.private_subnets],
                    "Tags": [
                        {"Key": "kubernetes.io/role/internal-elb", "Value": "1"},
                        {"Key": f"kubernetes.io/cluster/{self.cluster.cluster_name}", "Value": "shared"},
                    ],
                },
                physical_resource_id=cr.PhysicalResourceId.of(f"{vpc.vpc_id}-{self.cluster.cluster_name}"),
            ),
            on_delete=cr.AwsSdkCall(
                service="EC2",
                action="DeleteTags",
                parameters={
                    "Resources": [s.subnet_id for s in vpc.private_subnets],
                    "Tags": [{"Key": f"kubernetes.io/cluster/{self.cluster.cluster_name}"}],
                },
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE),
        )

        # Grant masters role necessary permissions
        masters_role.add_to_policy(
            iam.PolicyStatement(
                actions=["eks:AccessKubernetesApi", "eks:Describe*", "eks:List*"],
                resources=[self.cluster.cluster_arn],
            )
        )

        system_node_group = self.cluster.add_nodegroup_capacity(
            "SystemNodeGroup",
            min_size=SYSTEM_AUTOSCALING_GROUP_MIN_SIZE,
            max_size=SYSTEM_AUTOSCALING_GROUP_MAX_SIZE,
            instance_types=[system_node_instance_type],
        )
        system_node_group.role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("SecretsManagerReadWrite")
        )
        system_node_group.role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=[
                    "autoscaling:DescribeAutoScalingGroups",
                    "autoscaling:DescribeAutoScalingInstances",
                    "autoscaling:DescribeLaunchConfigurations",
                    "autoscaling:DescribeScalingActivities",
                    "autoscaling:DescribeTags",
                    "ec2:DescribeInstanceTypes",
                    "ec2:DescribeLaunchTemplateVersions",
                    "autoscaling:SetDesiredCapacity",
                    "autoscaling:TerminateInstanceInAutoScalingGroup",
                    "ec2:DescribeImages",
                    "ec2:GetInstanceTypesFromInstanceRequirements",
                    "eks:DescribeNodegroup",
                ],
                resources=["*"],
            )
        )

        # Autoscaling is managed via an AWS managed node group and K8s ClusterAutoscaler.
        user_node_group = self.cluster.add_nodegroup_capacity(
            "UserNodeGroup",
            min_size=USER_AUTOSCALING_GROUP_MIN_SIZE,
            max_size=USER_AUTOSCALING_GROUP_MAX_SIZE,
            instance_types=[user_node_instance_type],
            labels={"hub.jupyter.org/node-purpose": "user"},
            taints=[eks.TaintSpec(effect=eks.TaintEffect.NO_SCHEDULE, key="hub.jupyter.org/dedicated", value="user")],
        )
        user_node_group.role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("SecretsManagerReadWrite")
        )
        user_node_group.role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=[
                    "autoscaling:DescribeAutoScalingGroups",
                    "autoscaling:DescribeAutoScalingInstances",
                    "autoscaling:DescribeLaunchConfigurations",
                    "autoscaling:DescribeScalingActivities",
                    "autoscaling:DescribeTags",
                    "ec2:DescribeInstanceTypes",
                    "ec2:DescribeLaunchTemplateVersions",
                    "autoscaling:SetDesiredCapacity",
                    "autoscaling:TerminateInstanceInAutoScalingGroup",
                    "ec2:DescribeImages",
                    "ec2:GetInstanceTypesFromInstanceRequirements",
                    "eks:DescribeNodegroup",
                ],
                resources=["*"],
            )
        )

        r = requests.get(
            "https://raw.githubusercontent.com/kubernetes/autoscaler/master/cluster-autoscaler/cloudprovider/aws/examples/cluster-autoscaler-autodiscover.yaml"
        )
        manifest_yaml = r.text
        manifest_yaml = manifest_yaml.replace("<YOUR CLUSTER NAME>", self.cluster.cluster_name)
        autoscaler_manifests = yaml.full_load_all(manifest_yaml)
        self.cluster.add_manifest("Autoscaler", *autoscaler_manifests)

        # Set up EFS driver
        oid_connect_issuer_id = self.cluster.open_id_connect_provider.open_id_connect_provider_issuer.replace(
            "https://", ""
        )
        efs_csi_addon_role_policy_condition = cdk.CfnJson(
            self,
            "EfsAddonPolicyCondition",
            value={
                f"{oid_connect_issuer_id}:aud": "sts.amazonaws.com",
                f"{oid_connect_issuer_id}:sub": "system:serviceaccount:kube-system:efs-csi-*",
            },
        )
        efs_csi_addon_role = iam.Role(
            self,
            "EfsAddonRole",
            assumed_by=iam.FederatedPrincipal(
                federated=self.cluster.open_id_connect_provider.open_id_connect_provider_arn,
                conditions={"StringLike": efs_csi_addon_role_policy_condition},
                assume_role_action="sts:AssumeRoleWithWebIdentity",
            ),
        )
        efs_csi_addon_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonEFSCSIDriverPolicy")
        )
        efs_csi_addon = eks.CfnAddon(
            self,
            "EfsCsiAddon",
            addon_name="aws-efs-csi-driver",
            cluster_name=self.cluster.cluster_name,
            service_account_role_arn=efs_csi_addon_role.role_arn,
        )
        efs_csi_addon.apply_removal_policy(removal_policy)

        # Cloudwatch Observability Addon
        cloudwatch_addon_role_policy_condition = cdk.CfnJson(
            self,
            "CloudwatchAddonPolicyCondition",
            value={
                f"{oid_connect_issuer_id}:aud": "sts.amazonaws.com",
                f"{oid_connect_issuer_id}:sub": "system:serviceaccount:kube-system:cloudwatch*",
            },
        )
        cloudwatch_addon_role = iam.Role(
            self,
            "CloudWatchObservabilityRole",
            assumed_by=iam.FederatedPrincipal(
                federated=self.cluster.open_id_connect_provider.open_id_connect_provider_arn,
                conditions={"StringLike": cloudwatch_addon_role_policy_condition},
                assume_role_action="sts:AssumeRoleWithWebIdentity",
            ),
        )
        cloudwatch_addon_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AWSXrayWriteOnlyAccess")
        )
        cloudwatch_addon_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchAgentServerPolicy")
        )

        cloudwatch_addon = eks.CfnAddon(
            self,
            "CloudWatchObservabilityAddon",
            addon_name="amazon-cloudwatch-observability",
            cluster_name=self.cluster.cluster_name,
            service_account_role_arn=cloudwatch_addon_role.role_arn,
        )
        cloudwatch_addon.apply_removal_policy(removal_policy)

        eks_namespace = self.cluster.add_manifest(
            "EksNamespace",
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": "jupyterhub"},
            },
        )

        efs_storage_class = self.cluster.add_manifest(
            "EfsStorageClass",
            {
                "apiVersion": "storage.k8s.io/v1",
                "kind": "StorageClass",
                "metadata": {"name": "efs", "namespace": "jupyterhub"},
                "provisioner": "efs.csi.aws.com",
                "parameters": {
                    "provisioningMode": "efs-ap",
                    "fileSystemId": file_system.file_system_id,
                    "directoryPerms": "700",
                },
            },
        )
        efs_storage_class.node.add_dependency(eks_namespace)

        efs_shared_volume_claim = self.cluster.add_manifest(
            "EfsSharedVolumeClaim",
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {"name": "jupyterhub-shared-claim", "namespace": "jupyterhub"},
                "spec": {
                    "storageClassName": "efs",
                    "accessModes": ["ReadWriteMany"],
                    "resources": {"requests": {"storage": "100Gi"}},
                },
            },
        )
        efs_shared_volume_claim.node.add_dependency(efs_storage_class)

        # User environment role
        user_service_account_name = "jupyterhub-user"
        user_eks_principal_condition = cdk.CfnJson(
            self,
            "SingleUserServiceAccountPolicyCondition",
            value={
                f"{oid_connect_issuer_id}:aud": "sts.amazonaws.com",
                f"{oid_connect_issuer_id}:sub": f"system:serviceaccount:jupyterhub:{user_service_account_name}",
            },
        )
        user_eks_principal = iam.FederatedPrincipal(
            federated=self.cluster.open_id_connect_provider.open_id_connect_provider_arn,
            conditions={"StringLike": user_eks_principal_condition},
            assume_role_action="sts:AssumeRoleWithWebIdentity",
        )
        user_role = iam.Role(self, "JupyterhubUserRole", assumed_by=user_eks_principal)
        user_service_account = self.cluster.add_manifest(
            "SingleUserServiceAccount",
            {
                "apiVersion": "v1",
                "kind": "ServiceAccount",
                "metadata": {
                    "name": user_service_account_name,
                    "namespace": "jupyterhub",
                    "annotations": {"eks.amazonaws.com/role-arn": user_role.role_arn},
                },
            },
        )
        user_service_account.node.add_dependency(eks_namespace)
        cdk.CfnOutput(
            self,
            "JupyterhubUserRoleArn",
            value=user_role.role_arn,
            description="Jupyterhub hub user execution role arn",
        )

        # Build and deploy custom docker images
        user_image = ecr_assets.DockerImageAsset(
            self,
            "UserServerBaseImage",
            directory="images",
            file="user.Dockerfile",
            platform=ecr_assets.Platform.LINUX_AMD64,
            build_secrets={"netrc": f"src={os.environ['HOME']}/.netrc"},
            build_ssh="default",
        )
        hub_image = ecr_assets.DockerImageAsset(
            self,
            "HubBaseImage",
            directory="images",
            file="hub.Dockerfile",
            platform=ecr_assets.Platform.LINUX_AMD64,
            build_secrets={"netrc": f"src={os.environ['HOME']}/.netrc"},
            build_ssh="default",
        )
        # Copy Traifik Image to ECR to avoid rate limit errors from Dockerhub
        traefik_image = ecr_assets.DockerImageAsset(
            self,
            "TraefikImage",
            directory="images",
            file="traefik.Dockerfile",
            platform=ecr_assets.Platform.LINUX_AMD64,
        )

        config_template = jinja_env.get_template("config.yaml.j2")
        config_secrets_template = jinja_env.get_template("config_secrets.py.j2")

        config_secrets_script = config_secrets_template.render(hub_db_secret_arn=hub_db_secret_arn)
        config = yaml.full_load(
            config_template.render(
                user_image_repository_uri=user_image.repository.repository_uri,
                user_image_tag=user_image.image_tag,
                hub_image_repository_uri=hub_image.repository.repository_uri,
                hub_image_tag=hub_image.image_tag,
                traefik_image_repository_uri=traefik_image.repository.repository_uri,
                traefik_image_tag=traefik_image.image_tag,
                user_service_account_name=user_service_account_name,
            )
        )
        config["hub"]["extraConfig"] = {"config_secrets.py": config_secrets_script}

        # Deploy Jupyterhub helm chart
        jupyterhub = eks.HelmChart(
            self,
            "JupyterhubHelmChart",
            cluster=self.cluster,
            chart="jupyterhub",
            repository="https://jupyterhub.github.io/helm-chart/",
            namespace="jupyterhub",
            create_namespace=True,
            release="jupyterhub",
            version="3.2.1",
            wait=True,
            values=config,
        )
        jupyterhub.node.add_dependency(efs_storage_class)

        # Expose service endpoint as stack output
        jupyterhub_endpoint = self.cluster.get_service_load_balancer_address("proxy-public", namespace="jupyterhub")
        cdk.CfnOutput(
            self,
            "JupyterhubEndpoint",
            value=jupyterhub_endpoint,
            description="The web address of the Jupyterhub load balancer.",
        )


class Jupyterhub(cdk.Stage):
    def __init__(
        self,
        scope: Construct,
        id: str,
        production: bool,
        vpc_id: str,
        automatic_backups: bool,
        db_instance_type: ec2.InstanceType,
        user_node_instance_type: ec2.InstanceType,
        system_node_instance_type: ec2.InstanceType,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> Self:
        """Initialize a Jupyterhub application.

        Args:
            vpc_id: ID of the vpc in which to create this application
            dns_zone_name: Name of the Route53 hosted zone in which to host this application
            removal_policy: cdk removal policy for resources in this stack
            automatic_backups: Whether or not to enable automatic backups for the EFS file system
            db_instance_type: ec2 instance type of the RDS postgres DB
            user_node_instance_type: ec2 instance type of nodes running user servers in k8s cluster
            system_node_instance_type: ec2 instance type of nodes running system pods in k8s cluster
            events_api_secret_arn: secretsmanager secret ARN of credentials for events api
        """
        super().__init__(scope, id, **kwargs)

        removal_policy = cdk.RemovalPolicy.RETAIN if production else cdk.RemovalPolicy.DESTROY
        termination_protection = True if production else False

        vpc = Vpc(self, "Vpc", vpc_id=vpc_id, tags=tags, termination_protection=termination_protection)

        database = Database(
            self,
            "Database",
            vpc=vpc.vpc,
            removal_policy=removal_policy,
            instance_type=db_instance_type,
            tags=tags,
            termination_protection=termination_protection,
        )

        file_system = FileSystem(
            self,
            "FileSystem",
            vpc=vpc.vpc,
            automatic_backups=automatic_backups,
            removal_policy=removal_policy,
            tags=tags,
            termination_protection=termination_protection,
        )

        Application(
            self,
            "Application",
            vpc=vpc.vpc,
            file_system=file_system.file_system,
            removal_policy=removal_policy,
            hub_db_secret_arn=database.db.secret.secret_arn,
            user_node_instance_type=user_node_instance_type,
            system_node_instance_type=system_node_instance_type,
            tags=tags,
            termination_protection=termination_protection,
        )


if __name__ == "__main__":
    app = cdk.App()

    # Dev
    Jupyterhub(
        app,
        "Jupyterhub",
        production=False,
        vpc_id="vpc-0e77574dac29b54b8",
        automatic_backups=False,
        db_instance_type=ec2.InstanceType.of(ec2.InstanceClass.BURSTABLE4_GRAVITON, ec2.InstanceSize.MICRO),
        user_node_instance_type=ec2.InstanceType.of(ec2.InstanceClass.M7I, ec2.InstanceSize.XLARGE),
        system_node_instance_type=ec2.InstanceType.of(ec2.InstanceClass.M7I, ec2.InstanceSize.LARGE),
        env=cdk.Environment(account=os.environ["CDK_DEFAULT_ACCOUNT"], region=os.environ["CDK_DEFAULT_REGION"]),
    )

    app.synth()
