FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    RTHEATFLOW_HOST=0.0.0.0

WORKDIR /app

# build-essential: occasionally needed by the pandapipes/pandapower
# scientific stack when a wheel is missing for the platform
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY pyproject.toml .
COPY src/ ./src/
COPY scripts/ ./scripts/

# The committed dataset (networks, profiles, scenarios) ships in the image —
# the container runs standalone; mount ./data to persist recordings/imports.
COPY data/ ./data/

EXPOSE 8000
CMD ["python", "-m", "rtheatflow.main"]
