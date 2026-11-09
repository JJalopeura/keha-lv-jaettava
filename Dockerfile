FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so the layer cache survives code edits.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Runtime data is ephemeral in Container Apps; keep it on a writable path and
# outside the image layer.
ENV KEHA_LV_DATA_DIR=/tmp/keha-data
RUN mkdir -p /tmp/keha-data/uploads

EXPOSE 8000

# No --reload, no gunicorn multi-worker: the app keeps AI jobs in-process and
# SQLite is a single-writer store.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
