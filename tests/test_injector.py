"""Injector: verify-before-send gate and token-safe chunking."""

from __future__ import annotations

import pytest

from secure_context_pipeline.grammar import TOKEN_REGEX, make_token
from secure_context_pipeline.llm.injector import (
    ContextInjector,
    VerifyBeforeSendError,
    complete_with_chunking,
    split_no_split_tokens,
)
from secure_context_pipeline.llm.provider import MockProvider


def test_verify_before_send_blocks_known_original():
    inj = ContextInjector()
    with pytest.raises(VerifyBeforeSendError):
        inj.build_request("Patient 123-45-6789 seen.", "Summarize.", {"123-45-6789"})


def test_verify_before_send_passes_when_clean():
    inj = ContextInjector()
    req = inj.build_request("Patient [SSN_0123456789ab] seen.", "Summarize.", {"123-45-6789"})
    assert "[SSN_0123456789ab]" in req.context


def test_split_keeps_tokens_intact():
    tok = make_token("NAME", "abcdefabcdef")
    text = ("word " * 50 + tok + " word" * 50)
    chunks = split_no_split_tokens(text, max_chars=80)
    assert len(chunks) > 1
    assert "".join(chunks) == text
    # the token appears whole in exactly one chunk, never split across two
    assert sum(c.count(tok) for c in chunks) == 1
    for c in chunks:
        # no chunk ends or starts mid-token
        assert not c.endswith(tok[:5]) or c.count(tok) >= 1


def test_split_single_chunk_when_it_fits():
    assert split_no_split_tokens("short text", 1000) == ["short text"]


async def test_chunked_completion_preserves_all_tokens():
    inj = ContextInjector()
    tokens = [make_token("NAME", f"{i:012x}") for i in range(6)]
    text = "\n".join(f"Line {i}: {t} present." for i, t in enumerate(tokens))
    out = await complete_with_chunking(inj, MockProvider(), text, "Summarize.", set(),
                                       max_chars=60, max_in_flight=3)
    for t in tokens:
        assert t in out  # every token survived the ordered fan-out/reassembly
