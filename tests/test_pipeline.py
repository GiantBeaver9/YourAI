"""End-to-end pipeline across both detector substrates, including the encrypted-store path."""

from __future__ import annotations

from secure_context_pipeline import ObfuscationPolicy, SecureContextPipeline, Settings
from secure_context_pipeline.fixtures import FIXTURE_DOC_ID, FIXTURE_PII, FIXTURE_PRESERVED
from secure_context_pipeline.fixtures.clinical_note import FIXTURE_TEXT
from secure_context_pipeline.llm.provider import MockProvider


def _pipe(detector, tmp_path) -> SecureContextPipeline:
    settings = Settings(master_key=b"\x06" * 32, store_root=str(tmp_path))
    return SecureContextPipeline(
        settings=settings, policy=ObfuscationPolicy(), detector=detector, provider=MockProvider(),
    )


async def test_end_to_end_no_leak_and_preserved(detector, tmp_path):
    pipe = _pipe(detector, tmp_path)
    sess = pipe.sessions.create_session("u")
    res = await pipe.process(sess, FIXTURE_DOC_ID, "Summarize.", text=FIXTURE_TEXT)

    for value in FIXTURE_PII.values():
        assert value not in res.obfuscation.obfuscated_text
    for value in FIXTURE_PRESERVED.values():
        assert value in res.obfuscation.obfuscated_text
    assert res.deobfuscation.clean
    assert res.restored_text


async def test_store_backed_flow(detector, tmp_path):
    pipe = _pipe(detector, tmp_path)
    await pipe.ingest("user-a", FIXTURE_DOC_ID, FIXTURE_TEXT)
    sess = pipe.sessions.create_session("user-a")
    res = await pipe.process(sess, FIXTURE_DOC_ID, "Summarize.", user_id="user-a")
    assert res.restored_text
    for value in FIXTURE_PII.values():
        assert value not in res.obfuscation.obfuscated_text
