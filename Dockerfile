# Not needed for Render (it builds the Python app natively); here so the service
# can be run anywhere that takes a container, and so `docker run` reproduces CI.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# Fonts for the fallback card renderer; without them Pillow falls back to a
# bitmap default and the card looks broken.
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY forge ./forge
ENV OUT_DIR=/data/out DB_PATH=/data/forge.db
RUN mkdir -p /data/out

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "forge.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
