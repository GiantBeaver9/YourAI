"""HTTP API over the Secure Context Pipeline — the deployable service entrypoint.

A thin, stateless FastAPI wrapper: each request runs its own session (fresh random ``K_s``)
and crypto-shreds it on the way out, so nothing PHI-bearing outlives the request. The obfuscate
→ LLM → restore flow is unchanged; this just exposes it over HTTP for Railway (or any host).

Run locally:   uvicorn secure_context_pipeline.api:app --reload
Run on Railway: uvicorn secure_context_pipeline.api:app --host 0.0.0.0 --port $PORT

Env:
  GEMINI_API_KEY     optional — real LLM leg (Gemini). Also ANTHROPIC_API_KEY for the Anthropic
                     leg. Provider auto-selects by whichever key is set (Gemini preferred), or
                     force it with SCP_LLM_PROVIDER=gemini|anthropic|mock. Unset -> MockProvider.
  SCP_MASTER_KEY     optional — 64 hex chars; unset -> ephemeral key generated at boot
  SCP_API_KEY        optional — if set, requests to /process and /obfuscate must send it as
                     `X-API-Key: <key>` (or `Authorization: Bearer <key>`). If UNSET the write
                     endpoints are OPEN — set it before exposing PHI traffic publicly.
"""

from __future__ import annotations

import logging
import os

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import __version__
from .pipeline import SecureContextPipeline

log = logging.getLogger("scp.api")

app = FastAPI(
    title="Secure Context Pipeline",
    version=__version__,
    description="PII/PHI obfuscation between a document store and external LLM providers.",
)

# One pipeline for the process; sessions are per-request and ephemeral.
_pipeline = SecureContextPipeline()


@app.on_event("startup")
async def _warn_if_open() -> None:
    if not os.environ.get("SCP_API_KEY"):
        log.warning(
            "SCP_API_KEY is not set — /process and /obfuscate are OPEN. Set SCP_API_KEY before "
            "exposing this service to real PHI traffic."
        )


def _require_api_key(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    """Optional shared-secret gate. No-op when SCP_API_KEY is unset (dev/demo)."""
    expected = os.environ.get("SCP_API_KEY")
    if not expected:
        return
    supplied = x_api_key
    if supplied is None and authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:]
    if supplied != expected:
        raise HTTPException(status_code=401, detail="missing or invalid API key")


# -- request/response models ------------------------------------------------------------------
class ProcessRequest(BaseModel):
    text: str = Field(..., description="The raw document text to protect.")
    task: str = Field("Summarize this document.", description="Instruction for the LLM.")
    doc_id: str | None = Field(None, description="Optional stable document id (per-doc tokens).")


class ProcessResponse(BaseModel):
    restored_text: str | None
    routed_to_human: bool
    meta: dict


class ObfuscateResponse(BaseModel):
    obfuscated_text: str
    entity_types: dict
    known_original_count: int
    detector: str


# -- endpoints --------------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict:
    """Liveness probe (used by Railway's healthcheck). No auth, no PHI."""
    return {"status": "ok", "version": __version__}


@app.get("/")
async def root() -> dict:
    return {
        "service": "secure-context-pipeline",
        "version": __version__,
        "detector": _pipeline.detector.name,
        "provider": _pipeline.provider.name,
        "endpoints": ["/health", "/process (POST)", "/obfuscate (POST)", "/docs"],
    }


@app.post("/process", response_model=ProcessResponse, dependencies=[Depends(_require_api_key)])
async def process(req: ProcessRequest) -> ProcessResponse:
    """Full round-trip: detect → obfuscate → LLM → restore. Returns the restored answer."""
    doc_id = req.doc_id or ("req_" + os.urandom(6).hex())
    session = _pipeline.sessions.create_session("api-user")
    try:
        result = await _pipeline.process(session, doc_id, req.task, text=req.text)
    finally:
        await _pipeline.sessions.destroy(session.session_id)  # crypto-shred per request

    obf = result.obfuscation
    return ProcessResponse(
        restored_text=result.restored_text,
        routed_to_human=result.routed_to_human,
        meta={
            "detector": obf.detector_name,
            "provider": _pipeline.provider.name,
            "entities_detected": len(obf.entities),
            "tokens_restored": result.deobfuscation.tokens_restored if result.deobfuscation else 0,
            "deobfuscation_clean": result.deobfuscation.clean if result.deobfuscation else None,
        },
    )


@app.post("/obfuscate", response_model=ObfuscateResponse, dependencies=[Depends(_require_api_key)])
async def obfuscate(req: ProcessRequest) -> ObfuscateResponse:
    """Inspect the outbound payload only — what WOULD be sent to the LLM (contains no PII)."""
    doc_id = req.doc_id or ("req_" + os.urandom(6).hex())
    session = _pipeline.sessions.create_session("api-user")
    try:
        obf = await _pipeline._engine.obfuscate(req.text, session, doc_id)  # noqa: SLF001
        counts: dict[str, int] = {}
        for e in obf.entities:
            counts[e.entity_type.value] = counts.get(e.entity_type.value, 0) + 1
        return ObfuscateResponse(
            obfuscated_text=obf.obfuscated_text,
            entity_types=counts,
            known_original_count=len(obf.known_originals),
            detector=obf.detector_name,
        )
    finally:
        await _pipeline.sessions.destroy(session.session_id)
