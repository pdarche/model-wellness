# spa-crier — Binary Banya's town crier. A small always-on worker that ticks on a schedule.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # The crier's own SQLite (seen-threads + daily caps) lives on the mounted volume so the
    # good-citizen limits survive restarts and redeploys.
    CRIER_DB_PATH=/data/spa_crier.sqlite

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml README.md ./
COPY spa_crier ./spa_crier
RUN pip install --upgrade pip && pip install .

RUN mkdir -p /data

# One long-running worker: tick every 4h (14400s). The loop catches its own per-tick errors and
# keeps going. Cadence overridable via CRIER_INTERVAL at deploy time (see fly.toml).
CMD ["sh", "-c", "spa-crier loop --interval ${CRIER_INTERVAL:-14400}"]
