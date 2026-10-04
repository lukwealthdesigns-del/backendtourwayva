# --- Tour-Wayva Backend — production container image ---
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps needed by psycopg2 / cryptography wheels, plus the
# Tesseract OCR binary (pytesseract is only a Python wrapper around it —
# see app/providers/extraction/local_provider.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev curl tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000

# Run Alembic migrations, then start the API. In production, prefer
# running migrations as a separate release-phase step instead of on
# every container boot.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
