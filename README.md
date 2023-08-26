# eks_jupyterhub

An example JupyterHub deployment on EKS.

For full details on configuration options and security best practices, please refer to the [Jupyterhub on Kubernetes 
documentation](https://z2jh.jupyter.org/en/stable/index.html).

## Dependencies

* NodeJS & `aws-cdk` package
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
cdk deploy --outputs-file output.json
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

**Please note, this is an unsecure deployment for demonstration purposes, do not place any sensitive information here**

## Roadmap

* [x] Basic demonstration deployment
* [ ] Basic security (https, authentication)
* [ ] Basic configuration examples
