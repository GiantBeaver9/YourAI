"""LLM provider integration with verify-before-send gate and bounded concurrency chunking.

VERIFY-BEFORE-SEND GATE (BUILD SPEC):
  Required security control: scans outbound payload for any known original PII string
  before it hits the LLM network boundary. Raises SecurityLeakError if a leak is detected.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol


class SecurityLeakError(RuntimeError):
    """Raised when the verify-before-send gate detects raw PII in outbound LLM payload."""


class LLMProvider(Protocol):
    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        ...


class MockProvider:
    """Deterministic mock LLM for testing and offline execution."""

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        # Echoes back the prompt content while maintaining tokens
        return (
            "Clinical Assessment Summary:\n"
            f"Evaluated input:\n{prompt}\n\n"
            "Plan & Guidance:\nContinue monitoring patient status under active clinical protocol."
        )


class AnthropicProvider:
    """Real Anthropic LLM provider."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5") -> None:
        import anthropic

        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text


SYSTEM_PROMPT = (
    "You are a secure clinical assistant processing obfuscated medical records.\n"
    "CRITICAL MANDATE:\n"
    "1. Preserve ALL tokens formatted as [TAG_hex] (e.g., [NAME_ab1234567890], [DATE_1234567890ab]) EXACTLY as they appear.\n"
    "2. NEVER alter, remove, guess, or invent replacement values for tokens.\n"
    "3. Perform the requested analysis while maintaining full token integrity."
)


@dataclass
class LLMRequest:
    prompt: str
    system_prompt: str


class ContextInjector:
    """Builds LLM requests with token preservation prompt and verify-before-send gate."""

    def __init__(self, max_concurrency: int = 4) -> None:
        self.semaphore = asyncio.Semaphore(max_concurrency)

    def verify_before_send(self, payload: str, known_originals: list[str]) -> None:
        """Scan payload for direct raw PII strings. Fail closed if any survive."""
        for raw in known_originals:
            if not raw or len(raw.strip()) < 2:
                continue
            if raw.strip() in payload:
                raise SecurityLeakError(
                    f"VERIFY-BEFORE-SEND GATE FIRED: Direct PII string '{raw}' detected in outbound payload!"
                )

    def build_request(
        self,
        obfuscated_text: str,
        task: str,
        known_originals: list[str],
    ) -> LLMRequest:
        prompt = f"Task: {task}\n\nDocument Content:\n{obfuscated_text}"

        # Verify before send gate
        self.verify_before_send(prompt, known_originals)

        return LLMRequest(prompt=prompt, system_prompt=SYSTEM_PROMPT)

    async def execute_bounded(
        self,
        provider: LLMProvider,
        request: LLMRequest,
    ) -> str:
        async with self.semaphore:
            return await provider.generate(request.prompt, request.system_prompt)
