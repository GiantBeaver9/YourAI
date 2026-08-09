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
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import __version__
from .pipeline import SecureContextPipeline

log = logging.getLogger("scp.api")

app = FastAPI(
    title="Secure Context Pipeline",
    version=__version__,
    description="PII/PHI obfuscation between a document store and external LLM providers.",
)

# Lazy singleton. Building the pipeline loads the Presidio/spaCy model, which is slow and
# memory-heavy — doing it at import would delay the first /health and can blow Railway's
# healthcheck window. Instead the container starts instantly, /health answers immediately, and
# the model loads on the first real request.
_pipeline: SecureContextPipeline | None = None


def _get_pipeline() -> SecureContextPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = SecureContextPipeline()
    return _pipeline


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
@app.get("/demo", response_class=HTMLResponse)
async def demo_console() -> str:
    """A self-contained test console, served same-origin so browser fetch needs no CORS."""
    return _DEMO_HTML


@app.get("/health")
async def health() -> dict:
    """Liveness probe (used by Railway's healthcheck). No auth, no PHI."""
    return {"status": "ok", "version": __version__}


@app.get("/")
async def root() -> dict:
    pipe = _get_pipeline()
    return {
        "service": "secure-context-pipeline",
        "version": __version__,
        "detector": pipe.detector.name,
        "provider": pipe.provider.name,
        "custom_rules_loaded": len(pipe.custom_rules),
        "endpoints": ["/health", "/demo", "/process (POST)", "/obfuscate (POST)", "/docs"],
    }


@app.post("/process", response_model=ProcessResponse, dependencies=[Depends(_require_api_key)])
async def process(req: ProcessRequest) -> ProcessResponse:
    """Full round-trip: detect → obfuscate → LLM → restore. Returns the restored answer."""
    pipe = _get_pipeline()
    doc_id = req.doc_id or ("req_" + os.urandom(6).hex())
    session = pipe.sessions.create_session("api-user")
    try:
        result = await pipe.process(session, doc_id, req.task, text=req.text)
    finally:
        await pipe.sessions.destroy(session.session_id)  # crypto-shred per request

    obf = result.obfuscation
    return ProcessResponse(
        restored_text=result.restored_text,
        routed_to_human=result.routed_to_human,
        meta={
            "detector": obf.detector_name,
            "provider": pipe.provider.name,
            "entities_detected": len(obf.entities),
            "tokens_restored": result.deobfuscation.tokens_restored if result.deobfuscation else 0,
            "deobfuscation_clean": result.deobfuscation.clean if result.deobfuscation else None,
        },
    )


@app.post("/obfuscate", response_model=ObfuscateResponse, dependencies=[Depends(_require_api_key)])
async def obfuscate(req: ProcessRequest) -> ObfuscateResponse:
    """Inspect the outbound payload only — what WOULD be sent to the LLM (contains no PII)."""
    pipe = _get_pipeline()
    doc_id = req.doc_id or ("req_" + os.urandom(6).hex())
    session = pipe.sessions.create_session("api-user")
    try:
        obf = await pipe._engine.obfuscate(req.text, session, doc_id)  # noqa: SLF001
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
        await pipe.sessions.destroy(session.session_id)


# --------------------------------------------------------------------------------------------
# Test console (served at /demo). Same-origin fetch -> no CORS. Self-contained; theme-aware.
# --------------------------------------------------------------------------------------------
_DEMO_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Secure Context Pipeline — Test Console</title>
<style>
  :root{--bg:#f6f7f9;--card:#fff;--fg:#1a1d21;--mut:#5b6572;--line:#e3e6ea;--accent:#2f6f4f;
        --tok:#0b6b52;--tokbg:#d9f2e8;--bad:#b42318;--badbg:#fde7e5;--ok:#0b6b52;--code:#f0f2f4;}
  @media (prefers-color-scheme:dark){:root{--bg:#0f1215;--card:#171b20;--fg:#e7eaee;--mut:#9aa4b0;
        --line:#2a3038;--accent:#5cc79a;--tok:#7ee6bf;--tokbg:#123328;--bad:#ff6b5e;--badbg:#3a1714;
        --ok:#7ee6bf;--code:#0c0f12;}}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
  .wrap{max-width:1080px;margin:0 auto;padding:24px}
  h1{font-size:22px;margin:0 0 2px} .sub{color:var(--mut);margin:0 0 20px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:16px}
  label{display:block;font-weight:600;font-size:13px;margin:0 0 6px}
  input,textarea{width:100%;background:var(--bg);color:var(--fg);border:1px solid var(--line);
        border-radius:8px;padding:10px;font:inherit}
  textarea{min-height:150px;font-family:ui-monospace,Consolas,monospace;font-size:13px;white-space:pre}
  .row{display:flex;gap:12px;flex-wrap:wrap} .row>div{flex:1;min-width:220px}
  .btns{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
  button{cursor:pointer;border:0;border-radius:8px;padding:11px 16px;font:inherit;font-weight:600}
  .primary{background:var(--accent);color:#fff} .ghost{background:var(--code);color:var(--fg);border:1px solid var(--line)}
  button:disabled{opacity:.55;cursor:progress}
  pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:12px;overflow:auto;
        white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,Consolas,monospace;font-size:13px}
  .tok{background:var(--tokbg);color:var(--tok);border-radius:4px;padding:0 3px;font-weight:600}
  .pills{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}
  .pill{background:var(--code);border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:12px}
  .status{font-size:13px;margin-top:8px}
  .ok{color:var(--ok);font-weight:600} .bad{color:var(--bad);font-weight:600}
  .banner{background:var(--badbg);color:var(--bad);border:1px solid var(--bad);border-radius:8px;
        padding:8px 12px;margin-top:10px;font-size:13px;display:none}
  h2{font-size:14px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);margin:0 0 8px}
  .hint{color:var(--mut);font-size:12px;margin-top:4px}
</style>
</head>
<body>
<div class="wrap">
  <h1>Secure Context Pipeline — Test Console</h1>
  <p class="sub">detect → obfuscate → (LLM) → restore. This page talks to <code id="origin"></code>.</p>

  <div class="card">
    <div class="row">
      <div>
        <label for="key">API key (X-API-Key)</label>
        <input id="key" type="password" placeholder="scp_… (leave blank if SCP_API_KEY is unset)"/>
        <div class="hint">Stored only in your browser (localStorage), never sent anywhere but this API.</div>
      </div>
      <div>
        <label for="task">Task (for full round-trip)</label>
        <input id="task" value="Summarize this patient in two sentences."/>
      </div>
    </div>
    <div style="margin-top:12px">
      <label for="text">Document text (synthetic — no real PII)</label>
      <textarea id="text">Patient: Jonathan Reyes
DOB: 03/15/1985
SSN: 482-19-7734
MRN: 4457812
Phone: (415) 555-0132   Email: jreyes@example.com
Address: 1420 Alderwood Street, San Jose, CA 95112

He is a 62-year-old Han Chinese male. Viral load 100000 copies/mL, dose 500 mg.
Attending Physician: Dr. Priya Nair</textarea>
    </div>
    <div class="btns">
      <button class="primary" id="btnObf">Obfuscate — show outbound payload</button>
      <button class="ghost" id="btnProc">Full round-trip (process)</button>
    </div>
    <div class="banner" id="banner"></div>
    <div class="status" id="status"></div>
  </div>

  <div class="card" id="outCard" style="display:none">
    <div id="leak"></div>
    <div class="pills" id="pills"></div>
    <h2 id="outTitle">Outbound payload</h2>
    <pre id="out"></pre>
    <div id="restoredBlock" style="display:none">
      <h2>Restored (user-facing)</h2>
      <pre id="restored"></pre>
      <div class="pills" id="meta"></div>
    </div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
$("origin").textContent = location.origin;
$("key").value = localStorage.getItem("scp_key") || "";
$("key").addEventListener("change", () => localStorage.setItem("scp_key", $("key").value));

const esc = s => s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
const hiTokens = s => esc(s).replace(/\[[A-Z][A-Z0-9]*_[0-9a-f]{12}\]/g, m => '<span class="tok">'+m+'</span>');

function headers(){ const h={"Content-Type":"application/json"}; const k=$("key").value.trim();
  if(k) h["X-API-Key"]=k; return h; }

async function call(path, body){
  $("banner").style.display="none"; $("status").textContent="Running…";
  document.querySelectorAll("button").forEach(b=>b.disabled=true);
  try{
    const r = await fetch(location.origin+path,{method:"POST",headers:headers(),body:JSON.stringify(body)});
    const txt = await r.text();
    let data; try{ data=JSON.parse(txt); }catch(e){ data={raw:txt}; }
    if(!r.ok){ throw new Error((data.detail||txt||r.statusText)+" (HTTP "+r.status+")"); }
    return data;
  } finally { document.querySelectorAll("button").forEach(b=>b.disabled=false); $("status").textContent=""; }
}

function showBanner(msg){ const b=$("banner"); b.textContent=msg; b.style.display="block"; }

// crude client-side leak check: does the outbound payload still contain obvious PII from input?
function leakScan(payload){
  const src = $("text").value;
  const candidates = [];
  (src.match(/\b\d{3}-\d{2}-\d{4}\b/g)||[]).forEach(x=>candidates.push(x));      // SSNs
  (src.match(/\b[\w.+-]+@[\w.-]+\.\w{2,}\b/g)||[]).forEach(x=>candidates.push(x)); // emails
  const leaked = [...new Set(candidates)].filter(c => payload.includes(c));
  return leaked;
}

$("btnObf").onclick = async () => {
  try{
    const d = await call("/obfuscate", {text:$("text").value});
    $("outCard").style.display="block"; $("restoredBlock").style.display="none";
    $("outTitle").textContent="Outbound payload (what would be sent to the LLM)";
    $("out").innerHTML = hiTokens(d.obfuscated_text||"");
    $("pills").innerHTML = Object.entries(d.entity_types||{})
      .map(([k,v])=>'<span class="pill">'+k+' × '+v+'</span>').join("")
      + '<span class="pill">detector: '+d.detector+'</span>';
    const leaked = leakScan(d.obfuscated_text||"");
    $("leak").innerHTML = leaked.length
      ? '<span class="bad">⚠ LEAK: '+leaked.map(esc).join(", ")+' still in payload</span>'
      : '<span class="ok">✓ No SSN/email from the input survived in the outbound payload</span>';
  }catch(e){ showBanner(e.message); }
};

$("btnProc").onclick = async () => {
  try{
    const d = await call("/process", {text:$("text").value, task:$("task").value});
    $("outCard").style.display="block";
    $("outTitle").textContent="LLM interaction ran; tokens restored below";
    $("out").textContent = "(the outbound payload is shown by the Obfuscate button)";
    $("pills").innerHTML="";
    $("leak").innerHTML="";
    $("restoredBlock").style.display="block";
    $("restored").textContent = d.restored_text ?? "(routed to human review)";
    const m=d.meta||{};
    $("meta").innerHTML = Object.entries(m).map(([k,v])=>'<span class="pill">'+k+': '+v+'</span>').join("");
  }catch(e){ showBanner(e.message); }
};
</script>
</body>
</html>
"""
