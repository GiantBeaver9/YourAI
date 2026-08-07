FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY secure_context_pipeline ./secure_context_pipeline
COPY tests ./tests
COPY fixtures ./fixtures
COPY demo.py ./

RUN pip install --no-cache-dir -e ".[dev]"

# Presidio is the intended detection substrate but a heavy install (spaCy model). The pipeline
# runs on the native rule-engine fallback without it. To enable the full Presidio NER substrate,
# uncomment:
# RUN pip install --no-cache-dir ".[presidio,docs]" && python -m spacy download en_core_web_lg

# Default: serve the API (Railway sets $PORT). Run the demo with:
#   docker-compose run --rm scp python demo.py
CMD ["sh", "-c", "uvicorn secure_context_pipeline.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
