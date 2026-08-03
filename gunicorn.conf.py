"""Gunicorn configuration for the KEHA LV demo on Azure Web App (Linux).

Used by the Azure startup command:
    python3 -m gunicorn app.main:app -c gunicorn.conf.py

Binds to port 8000, which is what Azure Web App expects (WEBSITES_PORT=8000).
"""

import multiprocessing

max_requests = 1000
max_requests_jitter = 50
log_file = "-"
bind = "0.0.0.0:8000"
timeout = 230
num_cpus = multiprocessing.cpu_count()
workers = (num_cpus * 2) + 1
worker_class = "uvicorn.workers.UvicornWorker"
