"""Gunicorn configuration for production deployment."""

bind = "0.0.0.0:8000"
workers = 4
worker_class = "uvicorn_worker.UvicornWorker"
accesslog = "-"
errorlog = "-"
loglevel = "info"
