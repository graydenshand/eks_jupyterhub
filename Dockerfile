# Dockerfile for custom jupyterlab user environment
FROM jupyter/datascience-notebook:6aded4bc1d84

# Install jupyter extensions
RUN pip install -U jupyterlab-git jupyter-scheduler ipython-sql voila
