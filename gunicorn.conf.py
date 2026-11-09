"""Gunicorn configuration for the KEHA LV app on Azure Web App (Linux).

Used by the Azure startup command:
    python3 -m gunicorn app.main:app -c gunicorn.conf.py

Binds to port 8000, which is what Azure Web App expects (WEBSITES_PORT=8000).
"""

max_requests = 1000
max_requests_jitter = 50
log_file = "-"
bind = "0.0.0.0:8000"
# AI checks can take 30-60 s; the HTTP response returns immediately (background
# job), but keep headroom for slow synchronous endpoints such as package analysis.
timeout = 600
# SQLite is a single-writer store and AI jobs run in-process threads, so one
# worker keeps job ownership and writes consistent. Scale out only after moving
# state to a shared database and a real job queue.
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"
