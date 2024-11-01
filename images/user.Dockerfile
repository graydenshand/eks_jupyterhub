# Dockerfile for custom jupyterlab user environment
FROM jupyter/datascience-notebook:6aded4bc1d84

USER root

# Install jupyter extensions and python packages
RUN pip install -U jupyterlab-git jupyter-scheduler ipython-sql voila

USER 1000
