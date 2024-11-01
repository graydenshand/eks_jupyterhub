# This is the image used by the jupyterhub Hub pod. The default juptyerhub 
# base image is extended to install boto3 as a python dependency. This allows
# looking up secretsmanager secrets dynamically from secretsmanager on 
# pod startup.

# From default jupyterhub hub image
FROM jupyterhub/k8s-hub:3.0.3

RUN pip install boto3