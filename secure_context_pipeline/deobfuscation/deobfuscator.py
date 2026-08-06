"""De-obfuscation — restore tokens in the model's reply, then prove none survived.

This is not the mirror image of obfuscation: the reply is free-form output we don't control, so
reversal is strategy-dependent (de-obfuscation-deep-dive.md):

* **Tokenized values** reverse as a *parsing* problem — a strict grammar the model copies
  verbatim. Regex the grammar (single-sourced from :mod:`grammar`), tolerate affixes (brackets
  delimit, so ``[NAME_..]'s`` needs no special case), tolerate case-mangling conservatively,
  then vault-resolve. A miss **redacts** (never guesses) and is caught **loud** by the leftover
  guard.
* **Pseudonyms** reverse as an *entity-resolution* problem — a realistic name with no grammar
  to anchor, whose misses fail **silent**. So when (and only when) pseudonymization was used, a
  response-side net searches the reply for every known fake surface (and its parts). This is
  scoped to pseudonym runs, not paid on every response.

Final gate: :func:`grammar.has_token_residue` must be clean. Any residue is redacted and
flagged — we never ship a token to the user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..audit.audit import AuditLog
from ..grammar import TOKEN_REGEX, has_token_residue, make_token
from ..vault.session import Session

# Case-insensitive twin of the authoritative grammar (built FROM it, not hand-copied) so a
# model that lower-cases a token still gets restored; the match is normalized before lookup.
_TOKEN_CI = re.compile(TOKEN_REGEX.pattern, re.IGNORECASE)
_REDACTION = "[redacted]"


@dataclass
class DeobfuscationResult:
    restored_text: str
    tokens_restored: int = 0
    vault_misses: list[str] = field(default_factory=list)   # token type tags only, never PII
    pseudonyms_restored: int = 0
    leftover_guard_fired: bool = False

    @property
    def clean(self) -> bool:
        return not self.leftover_guard_fired and not self.vault_misses


class Deobfuscator:
    def __init__(self, audit: AuditLog | None = None) -> None:
        self._audit = audit

    async def restore(
        self,
        response_text: str,
        session: Session,
        *,
        used_pseudonymization: bool = False,
    ) -> DeobfuscationResult:
        result = DeobfuscationResult(restored_text=response_text)

        text = await self._restore_tokens(response_text, session, result)
        if used_pseudonymization:
            text = await self._restore_pseudonyms(text, session, result)

        # Leftover guard (always on): assert the authoritative grammar finds no residue.
        if has_token_residue(text):
            result.leftover_guard_fired = True
            text = TOKEN_REGEX.sub(_REDACTION, text)  # fail closed — redact any survivor

        result.restored_text = text
        return result

    async def _restore_tokens(
        self, text: str, session: Session, result: DeobfuscationResult
    ) -> str:
        # Collect matches, resolve, then splice right-to-left (offset-safe).
        matches = list(_TOKEN_CI.finditer(text))
        resolutions: list[tuple[int, int, str]] = []
        for m in matches:
            type_tag, digest = m.group(1).upper(), m.group(2).lower()
            canonical_token = make_token(type_tag, digest)  # normalize mangled case
            original = await session.vault.resolve(canonical_token)
            if original is not None:
                resolutions.append((m.start(), m.end(), original))
                result.tokens_restored += 1
            else:
                # Vault miss: unknown / hallucinated / cross-session token -> redact, never guess.
                resolutions.append((m.start(), m.end(), _REDACTION))
                result.vault_misses.append(type_tag)
                if self._audit is not None:
                    self._audit.emit(
                        session_id=session.session_id, entity_type=type_tag,
                        action="VAULT_MISS_REDACT", token=canonical_token,
                    )
        for start, end, repl in sorted(resolutions, key=lambda r: r[0], reverse=True):
            text = text[:start] + repl + text[end:]
        return text

    async def _restore_pseudonyms(
        self, text: str, session: Session, result: DeobfuscationResult
    ) -> str:
        """Search the reply for known fake surfaces and restore them. Fakes have no grammar, so
        this is best-effort entity resolution — documented as fail-silent, which is exactly why
        tokenization (not this) is the default for round-trip-critical values."""
        # Longest surfaces first so a full fake name is restored before its bare surname.
        fakes = [s for s in session.vault.surfaces() if not _TOKEN_CI.fullmatch(s)]
        for fake in sorted(fakes, key=len, reverse=True):
            original = await session.vault.resolve(fake)
            if original is None:
                continue
            for needle in self._pseudonym_needles(fake):
                pattern = re.compile(r"\b" + re.escape(needle) + r"\b")
                text, n = pattern.subn(original, text)
                result.pseudonyms_restored += n
        return text

    @staticmethod
    def _pseudonym_needles(fake: str) -> list[str]:
        needles = [fake]
        needles += [part for part in fake.split() if len(part) >= 4]
        # de-dup, preserve order, longest first
        seen: set[str] = set()
        ordered = []
        for n in sorted(needles, key=len, reverse=True):
            if n not in seen:
                seen.add(n)
                ordered.append(n)
        return ordered
