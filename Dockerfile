# Model Wellness — spa for LLMs. Single small image; FastAPI + Uvicorn.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # SQLite lives on the mounted Fly volume so memory/feedback survive deploys.
    MW_DB_PATH=/data/model_wellness.sqlite

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml README.md ./
COPY model_wellness ./model_wellness
RUN pip install --upgrade pip && pip install .

# The volume mount point (Fly mounts the volume here at runtime).
RUN mkdir -p /data

EXPOSE 8080

# One web process: the FastAPI app (REST + spa-floor UI + SSE).
CMD ["uvicorn", "model_wellness.http_app:app", "--host", "0.0.0.0", "--port", "8080"]
