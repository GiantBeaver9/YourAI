"""Context injector — the outbound seam.

Assembles the obfuscated text into a request, primes the model to preserve tokens, and runs
the REQUIRED verify-before-send gate: no known original value may appear in the payload, or we
fail closed and refuse to send. (The security itself is the stripping — nothing real is in the
payload — but this asserts that at the wire; you don't leave your keys in the FedEx package.)
"""

from __future__ import annotations

from dataclasses import dataclass

from .provider import LLMRequest

_SYSTEM_PROMPT = """You are analyzing a document in which personal identifiers have been \
replaced by opaque tokens of the form [TYPE_hexid] (for example [NAME_a1b2c3d4e5f6]).

Rules:
- Treat every token as an opaque placeholder for a real value you cannot see.
- Preserve tokens VERBATIM in your response. Never expand, translate, reformat, or invent them.
- Refer to entities by their token. The TYPE tag (NAME, SSN, MRN, DIAGNOSIS, ...) is the role.
- Do not comment on the redaction or attempt to guess the underlying values.
Reason normally about the content; the tokens stand in for real people and identifiers."""


class VerifyBeforeSendError(RuntimeError):
    """Fail closed: a known original value is present in the payload — do not transmit."""


@dataclass
class ContextInjector:
    system: str = _SYSTEM_PROMPT

    def build_request(
        self, obfuscated_text: str, task: str, known_originals: set[str]
    ) -> LLMRequest:
        leaked = sorted(o for o in known_originals if o and o in obfuscated_text)
        if leaked:
            raise VerifyBeforeSendError(
                f"refusing to send: {len(leaked)} original value(s) present in payload"
            )
        return LLMRequest(system=self.system, context=obfuscated_text, task=task)
