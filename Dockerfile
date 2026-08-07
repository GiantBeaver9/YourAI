FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY secure_context_pipeline ./secure_context_pipeline
COPY tests ./tests
COPY fixtures ./fixtures
COPY demo.py ./

# Install with the Presidio detection substrate (NER for free-text names) + doc extractors + tests.
RUN pip install --no-cache-dir -e ".[dev,presidio,docs]"

# Presidio's spaCy NER model. lg = best accuracy (~560MB, default); pass
# --build-arg SPACY_MODEL=en_core_web_sm for a much smaller/faster image with lower recall.
ARG SPACY_MODEL=en_core_web_lg
RUN python -m spacy download ${SPACY_MODEL}
ENV SCP_SPACY_MODEL=${SPACY_MODEL}

# Default: serve the API (Railway sets $PORT). Run the demo with:
#   docker-compose run --rm scp python demo.py
CMD ["sh", "-c", "uvicorn secure_context_pipeline.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
