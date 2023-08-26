import aws_cdk as cdk
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_eks as eks
import aws_cdk.aws_iam as iam
import yaml
from constructs import Construct
from typing import Self
from aws_cdk.lambda_layer_kubectl import KubectlLayer

REMOVAL_POLICY = cdk.RemovalPolicy.DESTROY
DELETION_PROTECTION = False


class JupyterHubStack(cdk.Stack):
    def __init__(
        self, scope: Construct, id: str, vpc_id: str | None = None, masters_role_arn: str | None = None, **kwargs
    ) -> Self:
        super().__init__(scope, id, **kwargs)

        if masters_role_arn is None:
            masters_role = iam.Role(
                self,
                "MastersRole",
                assumed_by=iam.AccountPrincipal(self.account),
            )
        else:
            masters_role = iam.Role.from_role_arn(self, "MastersRole", masters_role_arn)

        if vpc_id is None:
            vpc = ec2.Vpc(self, "Vpc")
        else:
            vpc = ec2.Vpc.from_lookup(vpc_id=vpc_id)

        # provisioning a cluster
        cluster = eks.Cluster(
            self,
            "K8sCluster",
            version=eks.KubernetesVersion.V1_27,
            kubectl_layer=KubectlLayer(self, "kubectl-layer"),
            vpc=vpc,
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

        # Grant masters role necessary permissions
        masters_role.add_to_policy(
            iam.PolicyStatement(
                actions=["eks:AccessKubernetesApi", "eks:Describe*", "eks:List*"],
                resources=[cluster.cluster_arn],
            )
        )

        # Deploy Jupyterhub helm chart
        with open("config.yaml", "r") as f:
            eks.HelmChart(
                self,
                "JupyterHubHelmChart",
                cluster=cluster,
                chart="jupyterhub",
                repository="https://jupyterhub.github.io/helm-chart/",
                namespace="jupyterhub",
                create_namespace=True,
                release="jupyterhub",
                version="3.0.1",
                wait=True,
                values=yaml.load(f, Loader=yaml.Loader),
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

    JupyterHubStack(
        app,
        "EksJupyterhub",
        termination_protection=DELETION_PROTECTION,
        tags={"App": "EksJupyterhub"},
    )

    app.synth()
