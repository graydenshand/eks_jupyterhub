from typing import Self

import aws_cdk as cdk
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_ecr_assets as ecr_assets
import aws_cdk.aws_efs as efs
import aws_cdk.aws_eks as eks
import aws_cdk.aws_iam as iam
import yaml
from aws_cdk.lambda_layer_kubectl import KubectlLayer
from constructs import Construct

# These constants are set for a transient "dev" deployment
REMOVAL_POLICY = cdk.RemovalPolicy.DESTROY
DELETION_PROTECTION = False
AUTOMATIC_BACKUPS = False


class JupyterhubStack(cdk.Stack):
    cluster_service_ipv4_cidr = "172.20.0.0/16"

    def __init__(
        self, scope: Construct, id: str, vpc_id: str | None = None, masters_role_arn: str | None = None, **kwargs
    ) -> Self:
        super().__init__(scope, id, **kwargs)

        masters_role = iam.Role(
            self,
            "MastersRole",
            assumed_by=iam.AccountPrincipal(self.account),
        )

        # Provision a Kubernetes cluster
        cluster = eks.Cluster(
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
            service_ipv4_cidr=self.cluster_service_ipv4_cidr,
        )

        # Grant masters role necessary permissions
        masters_role.add_to_policy(
            iam.PolicyStatement(
                actions=["eks:AccessKubernetesApi", "eks:Describe*", "eks:List*"],
                resources=[cluster.cluster_arn],
            )
        )

        # Add autoscaling node group
        cluster.add_nodegroup_capacity(
            "ClusterNodeGroup",
            min_size=1,
            max_size=10,
            instance_types=[ec2.InstanceType("m5.large")],
        )

        # Build and deploy custom docker image
        image = ecr_assets.DockerImageAsset(
            self,
            "UserServerBaseImage",
            directory=".",
            platform=ecr_assets.Platform.LINUX_AMD64,
        )
        cdk.CfnOutput(
            self,
            "ImageUri",
            value=image.image_uri,
            description="URI of image deployed to ECR repository.",
        )

        # Set up EFS file system and csi addon for notebook storage
        efs_security_group = ec2.SecurityGroup(
            self,
            "EfsSecurityGroup",
            vpc=cluster.vpc,
            allow_all_outbound=True,
        )
        efs_security_group.add_ingress_rule(
            ec2.Peer.ipv4(cluster.vpc.vpc_cidr_block),
            ec2.Port.tcp(2049),
            description="Allow all inbound NFS traffic from VPC.",
        )
        efs_security_group.add_ingress_rule(
            ec2.Peer.ipv4(self.cluster_service_ipv4_cidr),
            ec2.Port.tcp(2049),
            description="Allow all inbound NFS traffic from EKS cluster.",
        )

        file_system = efs.FileSystem(
            self,
            "FileSystem",
            vpc=cluster.vpc,
            lifecycle_policy=efs.LifecyclePolicy.AFTER_14_DAYS,
            out_of_infrequent_access_policy=efs.OutOfInfrequentAccessPolicy.AFTER_1_ACCESS,
            removal_policy=REMOVAL_POLICY,
            security_group=efs_security_group,
            enable_automatic_backups=AUTOMATIC_BACKUPS,
        )

        oid_connect_issuer_id = cluster.open_id_connect_provider.open_id_connect_provider_issuer.replace("https://", "")
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
                federated=cluster.open_id_connect_provider.open_id_connect_provider_arn,
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
            cluster_name=cluster.cluster_name,
            service_account_role_arn=efs_csi_addon_role.role_arn,
        )
        efs_csi_addon.apply_removal_policy(REMOVAL_POLICY)

        eks_namespace = cluster.add_manifest(
            "EksNamespace",
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": "jupyterhub"},
            },
        )

        efs_storage_class = cluster.add_manifest(
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

        efs_shared_volume = cluster.add_manifest(
            "EfsSharedVolume",
            {
                "apiVersion": "v1",
                "kind": "PersistentVolume",
                "metadata": {"name": "jupyterhub-shared", "namespace": "jupyterhub"},
                "spec": {
                    "capacity": {"storage": "100Gi"},
                    "volumeMode": "Filesystem",
                    "accessModes": ["ReadWriteMany"],
                    "storageClassName": "efs",
                    "persistentVolumeReclaimPolicy": "Retain",
                    "csi": {"driver": "efs.csi.aws.com", "volumeHandle": file_system.file_system_id},
                },
            },
        )
        efs_shared_volume.node.add_dependency(efs_storage_class)

        efs_shared_volume_claim = cluster.add_manifest(
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

        # Parse config for Jupyterhub helm chart
        with open("config.yaml", "r") as f:
            config = yaml.load(f, Loader=yaml.Loader)

        # Add our custom image to the helm chart config
        config["singleuser"]["image"] = {
            "name": image.repository.repository_uri,
            "tag": image.image_tag,
        }

        # Create a  secret to grant permissions to pull from private ECR registry
        config["imagePullSecret"] = {
            "create": True,
            "registry": image.repository.repository_uri,
            "username": "aws",
            "email": "__token__",
            "password": "aws ecr get-login-password --region us-east-1 | cut -d' ' -f6",
        }

        # Deploy Jupyterhub helm chart
        jupyterhub = eks.HelmChart(
            self,
            "JupyterhubHelmChart",
            cluster=cluster,
            chart="jupyterhub",
            repository="https://jupyterhub.github.io/helm-chart/",
            namespace="jupyterhub",
            create_namespace=True,
            release="jupyterhub",
            version="3.3.7",
            wait=True,
            values=config,
        )
        jupyterhub.node.add_dependency(efs_storage_class)

        # Expose service endpoint as stack output
        jupyterhub_endpoint = cluster.get_service_load_balancer_address("proxy-public", namespace="jupyterhub")
        cdk.CfnOutput(
            self,
            "JupyterhubEndpoint",
            value=jupyterhub_endpoint,
            description="The web address of the Jupyterhub load balancer.",
        )


if __name__ == "__main__":
    app = cdk.App()
    app_name = "Jupyterhub"
    JupyterhubStack(
        app,
        app_name,
        termination_protection=DELETION_PROTECTION,
        tags={"App": app_name},
    )

    app.synth()
