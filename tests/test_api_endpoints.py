"""API surface: /rules listing and /process-pdf (extract -> normal pipeline; quarantine)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import secure_context_pipeline.api as api


@pytest.fixture
def client():
    return TestClient(api.app)


def test_list_rules(client):
    r = client.get("/rules")
    assert r.status_code == 200
    body = r.json()
    assert body["standard_rules_count"] > 0
    assert any(rule["id"] == "ssn" for rule in body["standard_rules"])
    assert "custom_rules" in body


def _text_pdf(lines: list[str]) -> bytes:
    fpdf = pytest.importorskip("fpdf")
    pdf = fpdf.FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for line in lines:
        pdf.cell(0, 8, line, ln=1)
    return bytes(pdf.output())


def test_process_pdf_extracts_then_runs_pipeline(client):
    data = _text_pdf(["Patient: Jonathan Reyes", "SSN: 482-19-7734", "MRN: 4457812"])
    r = client.post("/process-pdf", files={"file": ("note.pdf", data, "application/pdf")},
                    data={"task": "Summarize."})
    assert r.status_code == 200
    body = r.json()
    assert body["routed_to_human"] is False
    assert body["meta"]["pages"] == 1
    assert body["meta"]["entities_detected"] >= 3
    assert body["meta"]["deobfuscation_clean"] is True


def test_process_pdf_quarantines_image_only(client):
    fpdf = pytest.importorskip("fpdf")
    pdf = fpdf.FPDF()
    pdf.add_page()  # blank page, no extractable text -> scanned/image proxy
    data = bytes(pdf.output())
    r = client.post("/process-pdf", files={"file": ("scan.pdf", data, "application/pdf")})
    assert r.status_code == 200
    body = r.json()
    assert body["routed_to_human"] is True
    assert body["restored_text"] is None
    assert "OCR" in body["meta"]["reason"] or "extractable" in body["meta"]["reason"]


def test_process_pdf_rejects_non_pdf(client):
    r = client.post("/process-pdf", files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 400
