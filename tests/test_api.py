"""API surface: health, full round-trip, obfuscate-only. Uses FastAPI's TestClient (sync)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from secure_context_pipeline.api import app

client = TestClient(app)

_DOC = (
    "Patient Name: John Smith\n"
    "SSN: 123-45-6789    MRN: 00934812\n"
    "John is a 62-year-old Han Chinese male on warfarin."
)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_process_removes_pii_and_restores():
    r = client.post("/process", json={"document": _DOC, "task": "Summarize."})
    assert r.status_code == 200
    body = r.json()
    assert "123-45-6789" not in body["obfuscated"]
    assert "John Smith" not in body["obfuscated"]
    assert "Han Chinese" in body["obfuscated"]      # clinical signal transits
    assert "John Smith" in body["restored"]          # restored for the user
    assert body["leftover_tokens"] == []


def test_obfuscate_only_is_pii_free():
    r = client.post("/obfuscate", json={"document": _DOC, "task": "x"})
    assert r.status_code == 200
    assert "123-45-6789" not in r.json()["obfuscated"]
    assert r.json()["tokens"] > 0
