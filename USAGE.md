# Using the Secure Context Pipeline API

A short, practical guide for using the deployed service. For the design/security rationale see
[`README.md`](README.md) and [`docs/`](docs/).

**What it does:** you send a document + a task; it detects PII/PHI, replaces it with opaque
tokens, sends only the obfuscated text to an LLM, then restores the real values in the reply
before you see it. Raw PII never leaves in readable form.

- **Live demo:** `https://merry-playfulness-production-d238.up.railway.app`
- **Test console (open in a browser):** [`/demo`](https://merry-playfulness-production-d238.up.railway.app/demo)
- **Interactive API docs:** [`/docs`](https://merry-playfulness-production-d238.up.railway.app/docs)

> Replace the URL above with your own Railway domain if you redeploy.

---

## 1. The easiest way — the test console

Open **`/demo`** in a browser. Paste your API key (top field, stored only in your browser),
edit the sample document, and click:

- **Obfuscate — show outbound payload** → see exactly what would be sent to the LLM. PII is
  replaced with `[NAME_…]` / `[SSN_…]` tokens; a green line confirms no SSN/email survived.
- **Full round-trip (process)** → runs the whole flow and shows the restored, user-facing text.

No install, no shell quoting. Best for a live walkthrough.

---

## 2. The endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | none | Liveness — `{"status":"ok"}` |
| `GET` | `/` | none | Service info (active detector + provider + custom-rule count) |
| `GET` | `/demo` | none | Browser test console |
| `GET` | `/docs` | none | OpenAPI / Swagger UI |
| `GET` | `/rules` | API key | List all rules (custom rows in full + built-in Safe Harbor) |
| `POST` | `/obfuscate` | API key | Returns only the **outbound payload** (no PII) |
| `POST` | `/process` | API key | Full **detect → obfuscate → LLM → restore** round-trip |
| `POST` | `/process-pdf` | API key | **PDF upload → extract text → same pipeline** (image PDFs quarantined) |

`POST` (JSON) bodies are `{"text": "...", "task": "...", "doc_id": "optional"}`; `/process-pdf`
is a `multipart/form-data` upload (`file=@doc.pdf`, optional `task` field). The API key (if
configured) goes in the **`X-API-Key`** header (or `Authorization: Bearer <key>`).

**List the active rules / send a PDF (CMD):**
```cmd
curl -H "X-API-Key: YOUR_KEY" https://merry-playfulness-production-d238.up.railway.app/rules
curl -X POST https://merry-playfulness-production-d238.up.railway.app/process-pdf -H "X-API-Key: YOUR_KEY" -F "file=@note.pdf" -F "task=Summarize."
```

---

## 3. Command-line examples

### Windows (CMD)
```cmd
curl https://merry-playfulness-production-d238.up.railway.app/health

curl -s -X POST https://merry-playfulness-production-d238.up.railway.app/process -H "Content-Type: application/json" -H "X-API-Key: YOUR_KEY" -d "{\"text\":\"Patient: Jonathan Reyes\nSSN: 482-19-7734\",\"task\":\"Summarize this patient.\"}"
```

### macOS / Linux
```bash
curl -s -X POST https://merry-playfulness-production-d238.up.railway.app/process \
  -H "Content-Type: application/json" -H "X-API-Key: YOUR_KEY" \
  -d '{"text":"Patient: Jonathan Reyes\nSSN: 482-19-7734","task":"Summarize this patient."}'
```

---

## 4. Run the whole test battery (scripts in this repo)

The [`scripts/`](scripts/) folder has ready-to-run suites that check liveness, the auth gate,
PII-leakage on `/obfuscate`, clinical-value preservation, and a full round-trip.

**Windows (CMD):**
```cmd
scripts\test_api.bat
scripts\test_api.bat https://your-app.up.railway.app scp_yourkey
```

**macOS / Linux / Git-Bash:**
```bash
bash scripts/test_api.sh
bash scripts/test_api.sh https://your-app.up.railway.app scp_yourkey
```

With no arguments they target the demo deployment with the demo key. Output is `[PASS]` /
`[FAIL]` per check with a summary; exit code is non-zero if anything fails (handy for CI).

---

## 5. Configuration (env vars)

Set these in **Railway → your service → Variables**:

| Variable | Required? | What it does |
|---|---|---|
| `SCP_API_KEY` | Recommended | Shared secret for `/process` + `/obfuscate`. **If unset, those endpoints are open.** |
| `SCP_MASTER_KEY` | Optional | 64-hex at-rest encryption key. Unset → ephemeral per boot. |
| `GEMINI_API_KEY` | Optional | Enables the real **Gemini** model. Unset → deterministic mock reply. |
| `SCP_GEMINI_MODEL` | Optional | Gemini model id (default `gemini-2.0-flash`). |
| `ANTHROPIC_API_KEY` | Optional | Alternative real provider. |
| `SCP_LLM_PROVIDER` | Optional | Force a provider: `gemini` \| `anthropic` \| `mock` (default auto-selects, Gemini first). |
| `PORT` | **Don't set** | Injected by Railway automatically. |

**Demo mode:** leave all LLM keys blank — detection/obfuscation/restore run for real via
Presidio, and the LLM reply is a deterministic stub. **Real mode:** add `GEMINI_API_KEY` and
redeploy; it switches to Gemini automatically, no code change.

### Updating detection rules (no code change)

Detection is rules-as-data, so you can add site-specific identifiers without editing code:

1. Copy [`custom_rules.example.json`](custom_rules.example.json) to `custom_rules.json` and edit it.
2. Set `SCP_CUSTOM_RULES_PATH=custom_rules.json` in Railway → Variables (commit the file to the repo).
3. Redeploy. `GET /` will report `custom_rules_loaded: N`.

Each rule has an optional `preceding` anchor, a value `regex`, an optional `succeeding` boundary,
an `entity_type`, and an `action`. Rules are **additive** (catch more, never suppress a built-in
rule); a malformed regex is skipped and logged, never crashes the service. Full runtime
self-service authoring (with RE2 validation + a live preview) is the documented next step.

---

## 6. Run it locally (optional)

```bash
pip install -e ".[api]"
python -m spacy download en_core_web_sm      # once, for the Presidio detector
uvicorn secure_context_pipeline.api:app --port 8000
# then open http://localhost:8000/demo
```
