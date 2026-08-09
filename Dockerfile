FROM python:3.11-slim

WORKDIR /app

# Build deps for cryptography / spaCy wheels where prebuilt wheels aren't available.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (better layer caching), including the Presidio substrate and its
# spaCy model — Presidio is the intended detection engine in the reproducible image.
COPY requirements.txt ./
RUN pip install --no-cache-dir --retries 5 --timeout 60 -r requirements.txt \
        pytest pytest-asyncio hypothesis
# spaCy model for the Presidio detector. Non-fatal: if the download flakes on the network, the
# build still succeeds and the service falls back to the native-regex detector at runtime
# (build_detector) instead of failing the whole deploy. Retried a couple of times first.
RUN python -m spacy download en_core_web_sm \
    || python -m spacy download en_core_web_sm \
    || echo "WARNING: spaCy model download failed; native-regex detector will be used"

COPY . .
RUN pip install --no-cache-dir -e .

# No secrets baked in: an ephemeral master key is generated at runtime if SCP_MASTER_KEY is
# unset (config.Settings.from_env). Provide ANTHROPIC_API_KEY to use a real LLM; the default
# is the keyless MockProvider.
#
# Default command serves the HTTP API on $PORT (Railway/most PaaS inject PORT; fall back to 8000
# for local `docker run`). `docker-compose up` overrides this to run the tests + demo instead.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn secure_context_pipeline.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
