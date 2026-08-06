FROM python:3.11-slim

WORKDIR /app

# Build deps for cryptography / spaCy wheels where prebuilt wheels aren't available.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (better layer caching), including the Presidio substrate and its
# spaCy model — Presidio is the intended detection engine in the reproducible image.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt pytest pytest-asyncio hypothesis \
    && python -m spacy download en_core_web_sm

COPY . .
RUN pip install --no-cache-dir -e .

# No secrets baked in: an ephemeral master key is generated at runtime if SCP_MASTER_KEY is
# unset (config.Settings.from_env). Provide ANTHROPIC_API_KEY to use a real LLM; the default
# is the keyless MockProvider.
CMD ["sh", "-c", "pytest -q && python demo.py"]
