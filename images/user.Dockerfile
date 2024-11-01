# Dockerfile for custom jupyterlab user environment
FROM jupyter/datascience-notebook:6aded4bc1d84

ENV AWS_DEFAULT_REGION=us-east-1

USER root

RUN apt update -y && apt install -y libpq-dev build-essential python3-dev pkg-config

# Install jupyter extensions and python packages
RUN pip install -U jupyterlab-git jupyter-scheduler ipython-sql voila

USER 1000
