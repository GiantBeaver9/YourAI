"""``LLMProvider`` — the outbound provider seam (one of the four real interfaces).

The leg is **LLM-agnostic**: any class implementing ``LLMProvider`` drops in, chosen by config.
``MockProvider`` is the keyless default: deterministic, and it **echoes the tokens back
verbatim** in a plausible reply, so tests and the demo exercise the full de-obfuscation path
without a network call or an API key. ``GeminiProvider`` and ``AnthropicProvider`` are the real
legs (behind ``GEMINI_API_KEY`` / ``ANTHROPIC_API_KEY``); adding another provider is one class.

Security note (llm-injector.md §2): the provider only ever receives obfuscated text. Even a
successful prompt injection in the document cannot exfiltrate PHI — there is nothing real in the
payload to steal. The provider is outside the trust boundary by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..grammar import TOKEN_REGEX


@dataclass(frozen=True)
class LLMRequest:
    system: str
    context: str   # the fenced, obfuscated document
    task: str

    @property
    def payload(self) -> str:
        """The full assembled text that would leave our infrastructure — what verify scans."""
        return f"{self.system}\n{self.context}\n{self.task}"


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    async def complete(self, request: LLMRequest) -> str:
        ...


class MockProvider:
    """Deterministic, keyless. Produces a plausible reply that preserves every placeholder
    token verbatim (models copy opaque strings faithfully — we model that best case), so the
    de-obfuscation and round-trip paths are fully exercised offline."""

    name = "mock"

    async def complete(self, request: LLMRequest) -> str:
        tokens: list[str] = []
        seen: set[str] = set()
        for m in TOKEN_REGEX.finditer(request.context):
            tok = m.group(0)
            if tok not in seen:
                seen.add(tok)
                tokens.append(tok)

        lead = tokens[0] if tokens else "the patient"
        preserved = " ".join(tokens)
        return (
            f"[mock summary] Reviewed the de-identified record for {lead}. "
            f"The task was: {request.task.strip()[:120]} "
            f"All placeholder tokens are preserved verbatim for restoration: {preserved}."
        )


class AnthropicProvider:
    """Real provider, behind ``ANTHROPIC_API_KEY``. Imported lazily so the SDK is optional."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5") -> None:
        from anthropic import AsyncAnthropic  # lazy: optional dependency

        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def complete(self, request: LLMRequest) -> str:
        msg = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=request.system,
            messages=[{"role": "user", "content": f"{request.context}\n\n{request.task}"}],
        )
        return "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")


class GeminiProvider:
    """Real provider backed by Google Gemini, behind ``GEMINI_API_KEY`` (SDK: ``google-genai``).

    Imported lazily so the SDK stays optional — the MockProvider default needs neither this nor
    a key. Like every provider it only ever receives obfuscated text, so it sits outside the
    trust boundary exactly as AnthropicProvider does; swapping providers changes nothing about
    the security guarantees (the obfuscation ran before the payload ever reached here)."""

    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash") -> None:
        from google import genai  # lazy: optional dependency

        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def complete(self, request: LLMRequest) -> str:
        from google.genai import types

        resp = await self._client.aio.models.generate_content(
            model=self._model,
            contents=f"{request.context}\n\n{request.task}",
            config=types.GenerateContentConfig(system_instruction=request.system),
        )
        return resp.text or ""
