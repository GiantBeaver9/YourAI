"""HTTP API for the Secure Context Pipeline (FastAPI) — the deployable surface.

Per-request session model: each call mints a session, runs the round-trip, and
**crypto-shreds the vault when the request finishes** — the session-scoped-destroy design
mapped onto HTTP, so no PHI-bearing reversal map outlives a request.

Run: ``uvicorn secure_context_pipeline.api:app --host 0.0.0.0 --port $PORT``
"""

from __future__ import annotations

import dataclasses
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import ObfuscationPolicy
from .detection import get_detector
from .llm import AnthropicProvider, MockProvider
from .obfuscation.engine import RouteToHumanError
from .pipeline import SecureContextPipeline
from .vault.session import SessionManager

app = FastAPI(
    title="Secure Context Pipeline",
    version="0.1.0",
    description="PII/PHI obfuscation between a document store and external LLM providers.",
)

_manager = SessionManager()
_detector = get_detector()  # built once — Presidio load (when present) is expensive


def _provider():
    return AnthropicProvider() if os.environ.get("ANTHROPIC_API_KEY") else MockProvider()


class ProcessRequest(BaseModel):
    document: str = Field(..., description="Raw document text (may contain PII/PHI).")
    task: str = Field("Summarize this document.", description="Instruction for the LLM.")
    user_id: str = "api_user"
    doc_id: str = "doc"


class ProcessResponse(BaseModel):
    obfuscated: str
    llm_reply: str
    restored: str
    entities_detected: int
    tokens: int
    leftover_tokens: list[str]


class ObfuscateResponse(BaseModel):
    obfuscated: str
    entities_detected: int
    tokens: int


@app.get("/health")
async def health():
    return {"status": "ok", "detector": type(_detector).__name__, "provider": type(_provider()).__name__}


@app.post("/process", response_model=ProcessResponse)
async def process(req: ProcessRequest):
    """Full round-trip: detect -> obfuscate -> LLM -> restore. Vault shredded on completion."""
    session = _manager.create_session(req.user_id)
    try:
        pipe = SecureContextPipeline(detector=_detector, provider=_provider())
        result = await pipe.process(session, req.document, req.task, req.doc_id)
        return ProcessResponse(**dataclasses.asdict(result))
    except RouteToHumanError as exc:
        raise HTTPException(status_code=422, detail=f"routed to human review: {exc}")
    finally:
        await _manager.destroy(session.session_id)


@app.post("/obfuscate", response_model=ObfuscateResponse)
async def obfuscate(req: ProcessRequest):
    """Obfuscate only — returns the exact payload that would go to the LLM (zero PII). No call
    is made; the session is shredded immediately (nothing to restore)."""
    session = _manager.create_session(req.user_id)
    try:
        pipe = SecureContextPipeline(detector=_detector)
        _, obf = await pipe.build_payload(session, req.document, req.task, req.doc_id)
        return ObfuscateResponse(
            obfuscated=obf.obfuscated_text,
            entities_detected=len(obf.entities),
            tokens=obf.token_count,
        )
    except RouteToHumanError as exc:
        raise HTTPException(status_code=422, detail=f"routed to human review: {exc}")
    finally:
        await _manager.destroy(session.session_id)
