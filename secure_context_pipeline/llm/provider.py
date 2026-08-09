"""``LLMProvider`` — the outbound provider seam (one of the four real interfaces).

``MockProvider`` is the keyless default: deterministic, and it **echoes the tokens back
verbatim** in a plausible reply, so tests and the demo exercise the full de-obfuscation path
without a network call or an API key. ``AnthropicProvider`` is the real leg, behind
``ANTHROPIC_API_KEY``.

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
