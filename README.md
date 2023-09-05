# eks_jupyterhub

An example Jupyterhub deployment on EKS.

For full details on configuration options and security best practices, please refer to the [Jupyterhub on Kubernetes 
documentation](https://z2jh.jupyter.org/en/stable/index.html).

`cdk.py` includes a [Cloud Development Kit (CDK)](https://docs.aws.amazon.com/cdk/v2/guide/home.html) app that will 
create the following resources in your AWS account:
* A VPC
* A Docker image in ECR
* An EFS file system
* An IAM role
* A single node EKS cluster running Jupyterhub

`config.yaml` contains [Jupyterhub helm chart configurations](https://z2jh.jupyter.org/en/latest/resources/reference.html).
In addition to setting up the EFS storage integration, it defines two profiles with different cpu and memory settings.

`Dockerfile` defines the custom jupyterlab image used in the deployment. Extending Jupyter's 
[datascience](https://hub.docker.com/r/jupyter/datascience-notebook) base image, it installs four common extensions: 
[`jupyterlab-git`](https://pypi.org/project/jupyterlab-git/), [`ipython-sql`](https://pypi.org/project/ipython-sql/), 
[`voila`](https://pypi.org/project/voila/), [`jupyter-scheduler`](https://pypi.org/project/jupyter-scheduler/).

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
cdk deploy -O output.json
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
