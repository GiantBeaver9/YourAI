"""De-obfuscation — restore tokens in the LLM response, fail closed on anything unresolved.

The token grammar is bracketed, so grammatical affixes the model adds (``[NAME_..]'s``,
``([NAME_..])``, ``[NAME_..].``) sit outside the brackets and need no special handling. Uses
the SAME `grammar.TOKEN_REGEX` as the emitter (single source of truth). A vault miss is never
guessed — the leftover guard replaces any unresolved token with a marker so no token ships to
the user, and reports it so restoration failure is visible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..grammar import TOKEN_REGEX, has_token_residue
from ..obfuscation.engine import apply_replacements

_UNRESTORED = "[UNRESTORED]"


@dataclass
class RestoreResult:
    text: str
    restored: int = 0
    leftover_tokens: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.leftover_tokens


class Deobfuscator:
    def __init__(self, vault, known_pseudonyms: dict[str, str] | None = None) -> None:
        self.vault = vault
        # response-side net for pseudonyms (fail-silent otherwise): fake -> original
        self._pseudonyms = known_pseudonyms or {}

    async def restore(self, text: str) -> RestoreResult:
        repls: list[tuple[int, int, str]] = []
        restored = 0
        leftover: list[str] = []

        for m in TOKEN_REGEX.finditer(text):
            token = m.group(0)
            original = await self.vault.resolve(token)
            if original is not None:
                repls.append((m.start(), m.end(), original))
                restored += 1
            else:
                # vault miss / hallucinated token -> never guess; mark and report (fail closed)
                repls.append((m.start(), m.end(), _UNRESTORED))
                leftover.append(token)

        text = apply_replacements(text, repls)

        # response-side net: restore any known pseudonym the model echoed back
        for fake, original in self._pseudonyms.items():
            if fake and fake in text:
                text = text.replace(fake, original)

        # post-condition: no token-grammar residue may remain
        assert not has_token_residue(text), "leftover-guard: token residue survived restoration"
        return RestoreResult(text=text, restored=restored, leftover_tokens=leftover)
