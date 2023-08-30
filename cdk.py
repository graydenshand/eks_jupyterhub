from typing import Self

import aws_cdk as cdk
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_ecr_assets as ecr_assets
import aws_cdk.aws_eks as eks
import aws_cdk.aws_iam as iam
import yaml
from aws_cdk.lambda_layer_kubectl import KubectlLayer
from constructs import Construct

# These constants are set for a transient "dev" deployment
REMOVAL_POLICY = cdk.RemovalPolicy.DESTROY
DELETION_PROTECTION = False


class JupyterhubStack(cdk.Stack):
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
            "K8sCluster",
            version=eks.KubernetesVersion.V1_27,
            kubectl_layer=KubectlLayer(self, "kubectl-layer"),
            masters_role=masters_role,
            output_masters_role_arn=True,
            output_cluster_name=True,
            output_config_command=True,
            default_capacity=1,
            default_capacity_instance=ec2.InstanceType("m5.large"),
            cluster_logging=[
                eks.ClusterLoggingTypes.SCHEDULER,
                eks.ClusterLoggingTypes.API,
                eks.ClusterLoggingTypes.AUDIT,
                eks.ClusterLoggingTypes.AUTHENTICATOR,
                eks.ClusterLoggingTypes.CONTROLLER_MANAGER,
            ],
        )

        # Grant masters role necessary permissions
        masters_role.add_to_policy(
            iam.PolicyStatement(
                actions=["eks:AccessKubernetesApi", "eks:Describe*", "eks:List*"],
                resources=[cluster.cluster_arn],
            )
        )

        # Add EBS CSI driver addon
        oid_connect_issuer_id = cluster.open_id_connect_provider.open_id_connect_provider_issuer.replace("https://", "")
        ebs_csi_addon_role_policy_condition = cdk.CfnJson(
            self,
            "K8sEbsAddonPolicyCondition",
            value={
                f"{oid_connect_issuer_id}:aud": "sts.amazonaws.com",
                f"{oid_connect_issuer_id}:sub": "system:serviceaccount:kube-system:ebs-csi-controller-sa",
            },
        )
        ebs_csi_addon_role = iam.Role(
            self,
            "K8sEbsAddonRole",
            assumed_by=iam.FederatedPrincipal(
                federated=cluster.open_id_connect_provider.open_id_connect_provider_arn,
                conditions={"StringEquals": ebs_csi_addon_role_policy_condition},
                assume_role_action="sts:AssumeRoleWithWebIdentity",
            ),
        )
        ebs_csi_addon_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonEBSCSIDriverPolicy")
        )
        ebs_csi_addon = eks.CfnAddon(
            self,
            "K8sEbsCsiAddon",
            addon_name="aws-ebs-csi-driver",
            cluster_name=cluster.cluster_name,
            service_account_role_arn=ebs_csi_addon_role.role_arn,
        )
        ebs_csi_addon.apply_removal_policy(REMOVAL_POLICY)

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

        # Parse config for Jupyterhub helm chart
        with open("config.yaml", "r") as f:
            config = yaml.load(f, Loader=yaml.Loader)

        # Add our custom image to the helm chart config
        config["singleuser"]["image"] = {
            "name": image.repository.repository_uri,
            "tag": image.image_tag,
        }

        # Create a K8s secret to grant permissions to pull from private ECR registry
        config["imagePullSecret"] = {
            "create": True,
            "registry": image.repository.repository_uri,
            "username": "aws",
            "email": "__token__",
            "password": "aws ecr get-login-password --region us-east-1 | cut -d' ' -f6",
        }

        # Deploy Jupyterhub helm chart
        eks.HelmChart(
            self,
            "JupyterHubHelmChart",
            cluster=cluster,
            chart="jupyterhub",
            repository="https://jupyterhub.github.io/helm-chart/",
            namespace="jupyterhub",
            create_namespace=True,
            release="jupyterhub",
            version="3.0.2",
            wait=True,
            values=config,
        )

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

    JupyterhubStack(
        app,
        "EksJupyterhub",
        termination_protection=DELETION_PROTECTION,
        tags={"App": "EksJupyterhub"},
    )

    app.synth()
