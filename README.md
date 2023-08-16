# eks_jupyterhub

An example JupyterHub deployment on EKS.

## Dependencies

* NodeJS & `aws-cdk` package
* Python>=3.11
* [PDM](https://pypi.org/project/pdm/)

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

Once all pods are running, get your proxy endpoint.
```
$ kubectl --namespace=jupyterhub describe service proxy-public
Name:                     proxy-public
Namespace:                jupyterhub
Labels:                   app=jupyterhub
                          app.kubernetes.io/managed-by=Helm
                          chart=jupyterhub-3.0.1
                          component=proxy-public
                          heritage=Helm
                          release=jupyterhub
Annotations:              meta.helm.sh/release-name: jupyterhub
                          meta.helm.sh/release-namespace: jupyterhub
Selector:                 app=jupyterhub,component=proxy,release=jupyterhub
Type:                     LoadBalancer
IP Family Policy:         SingleStack
IP Families:              IPv4
IP:                       172.20.113.234
IPs:                      172.20.113.234
LoadBalancer Ingress:     abcd12345678fedcba87654321-1044896179.us-east-1.elb.amazonaws.com
Port:                     http  80/TCP
TargetPort:               http/TCP
NodePort:                 http  31530/TCP
Endpoints:                10.0.220.27:8000
Session Affinity:         None
External Traffic Policy:  Cluster
Events:
  Type    Reason                Age   From                Message
  ----    ------                ----  ----                -------
  Normal  EnsuringLoadBalancer  12m   service-controller  Ensuring load balancer
  Normal  EnsuredLoadBalancer   12m   service-controller  Ensured load balancer
```

Look for the value of **LoadBalancer Ingress**, now go to that address in your web browser.
E.g. http://abcd12345678fedcba87654321-1044896179.us-east-1.elb.amazonaws.com.

Once there, type in any username and password and you can lauch a user server and start creating notebooks.

**Please note, this is an unsecure deployment for demonstration purposes, do not place any sensitive information here**

## Roadmap

* [x] Basic demonstration deployment
* [ ] Basic security (https, authentication)
* [ ] Basic configuration examples
