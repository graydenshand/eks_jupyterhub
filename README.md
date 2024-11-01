# eks_jupyterhub

An example Jupyterhub deployment on EKS.

For full details on configuration options and security best practices, please refer to the [Jupyterhub on Kubernetes 
documentation](https://z2jh.jupyter.org/en/stable/index.html).

`cdk.py` includes a [Cloud Development Kit (CDK)](https://docs.aws.amazon.com/cdk/v2/guide/home.html) app that will 
create the following resources in your AWS account:
* A VPC
* A few Docker images in ECR
* An EFS file system
* An RDS Database
* A few IAM roles
* An autoscaling EKS cluster running Jupyterhub on between 1-15 nodes.

The `templates` directory contains jinja2 templated files for this deployment:

- `config.yaml.j2` contains [Jupyterhub helm chart configurations](https://z2jh.jupyter.org/en/latest/resources/reference.html).
In addition to setting up the EFS file system and RDS database integration, it defines two profiles with different cpu and memory settings
and creates a "shared" directory which all users can access.
- `config_secrets.py.j2` is a python script that is executed while the hub container is starting. This script allows us
to securely pass secret values to the jupyterhub config that otherwise would be exposed in the cloudformation template.
Specifically, it is used here to build the postgres connection string from the credentials in secretsmanager.

The `images` directory defines Dockerfiles used in the deployment.

- Extending Jupyter's [datascience](https://hub.docker.com/r/jupyter/datascience-notebook) base image, `user.Dockerfile`
installs four common extensions: [`jupyterlab-git`](https://pypi.org/project/jupyterlab-git/),
[`ipython-sql`](https://pypi.org/project/ipython-sql/), [`voila`](https://pypi.org/project/voila/),
[`jupyter-scheduler`](https://pypi.org/project/jupyter-scheduler/).
- `hub.Dockerfile` extends jupyterhubs "Hub" base image, adding boto3 to the python environment to enable looking up
secret from AWS secretsmanager while starting.
- `traefik.Dockerfile`, while developing this, I was getting some rate limit errors from Dockerhub. We publish
this image to AWS ECR to avoid those.

**Autoscaling**

This application uses two autoscaling node groups. One for system containers (e.g. the Hub service, idle-culler, etc) and
one for user containers (actual end user jupyter sessions).

This is implemented using kubernetes cluster-autoscaler, with taints to associate specific containers and node groups.
When a  user signs on and starts a session, if there is not sufficient capacity on the cluster for the requested
resources, a new node is started. This can take a few minutes, which can be a bit painful for end users. There is a way
to pre-emptively autoscale before resources are actually needed (called 'user placeholders'). This feature is spelled out
in the z2jh docs (linked below), however I wasn't able to get it working. If I can get it working I will update this
implementation.

To learn more about this, please refer to the [z2jh docs on autoscaling](https://z2jh.jupyter.org/en/stable/administrator/optimization.html),
and the [kubernetes autoscaler](https://github.com/kubernetes/autoscaler/blob/master/cluster-autoscaler/cloudprovider/aws/README.md) docs.

## Dependencies

* NodeJS & [`aws-cdk`](https://www.npmjs.com/package/aws-cdk)
* Python>=3.11
* [PDM](https://pypi.org/project/pdm/)
* [kubectl](https://kubernetes.io/docs/tasks/tools/) (optional, but recommended)

## Installation

```
pdm sync -G dev
```

## Deploy

To deploy the stack to the account your currently authenticated against, run:

```
cdk deploy 'Jupyterhub/*' -O output.json
```

View `output.json` to find the command to add the deployed cluster to your kubeconfig. You're looking for the 
output named something like `K8sClusterConfigCommand7A4D6E5F`. Its value is the command you need to run, something like:

```
aws eks update-kubeconfig --name K8sClusterA30C4AE4-abcefabcdef1234567812345678 --region us-east-1 --role-arn arn:aws:iam::XXXXXXXXX:role/EksJupyterhub-MastersRole0257C11B-8P2R1A12Q234Q1
```

Once you've added the cluster to your kubeconfig, you can view your pods.
```
$ kubectl -n jupyterhub get pods
NAME                              READY   STATUS    RESTARTS   AGE
continuous-image-puller-xjgw7     1/1     Running             0          8s
hub-7c4ddbf7b4-dwvwf              0/1     ContainerCreating   0          8s
proxy-77dfcb58df-qcst2            1/1     Running             0          8s
user-scheduler-7574d67cdb-f89m2   1/1     Running             0          8s
user-scheduler-7574d67cdb-fgv9n   1/1     Running             0          8s
```

Once all pods are running, you can view your jupyterhub deployment by going to the address found in your output.json.
Specifically you're looking for the output named `JupyterhubEndpoint`, it will look something like
`abcd12345678fedcba87654321-1044896179.us-east-1.elb.amazonaws.com`.

Go to that address in your web browser.
E.g. http://abcd12345678fedcba87654321-1044896179.us-east-1.elb.amazonaws.com.

Once there, type in any username and password and you can lauch a user server and start creating notebooks.

## Next Steps

1. [Setting up Authentication](https://jupyterhub.readthedocs.io/en/stable/reference/authenticators.html)
2. [Enabling HTTPS](https://z2jh.jupyter.org/en/latest/administrator/security.html#https)
