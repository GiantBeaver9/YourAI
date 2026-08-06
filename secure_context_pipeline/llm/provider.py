"""LLM providers behind one protocol. Mock is the keyless default; Anthropic is real."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from ..grammar import TOKEN_REGEX


@dataclass
class LLMRequest:
    system: str
    context: str
    task: str

    def user_prompt(self) -> str:
        return f"{self.task}\n\n<document>\n{self.context}\n</document>"


class LLMProvider(Protocol):
    async def complete(self, request: LLMRequest) -> str:
        ...


class MockProvider:
    """Deterministic, keyless. Echoes the context's tokens back verbatim (with one possessive
    affix, to exercise de-obf affix tolerance) so the round-trip has something to restore."""

    async def complete(self, request: LLMRequest) -> str:
        tokens: list[str] = []
        for m in TOKEN_REGEX.finditer(request.context):
            if m.group(0) not in tokens:
                tokens.append(m.group(0))
        if not tokens:
            return "No identifiable entities were present in the document."
        parts = [f"Summary: {tokens[0]}'s record was reviewed."]
        if len(tokens) > 1:
            parts.append("Referenced entities: " + ", ".join(tokens[1:]) + ".")
        return " ".join(parts)


class AnthropicProvider:
    """Real provider — used only when a key is present; never in the default test path."""

    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-4-5") -> None:
        self._key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._model = model

    async def complete(self, request: LLMRequest) -> str:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=self._key)
        msg = await client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=request.system,
            messages=[{"role": "user", "content": request.user_prompt()}],
        )
        return "".join(b.text for b in msg.content if b.type == "text")
