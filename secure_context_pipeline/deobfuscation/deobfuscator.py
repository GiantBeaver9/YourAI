"""De-obfuscation subsystem — token restoration with fail-closed leftover guard.

CRITICAL (BUILD SPEC):
  - Single-source token grammar: imports TOKEN_REGEX and has_token_residue from grammar.py.
  - Leftover guard: raises RuntimeError if ANY token-grammar residue survives post-restore.
  - Vault-miss -> never guess, fail closed via leftover guard.
"""

from __future__ import annotations

from ..grammar import TOKEN_REGEX, has_token_residue
from ..vault.session import Session, SessionClosed


class LeftoverResidueError(RuntimeError):
    """Raised when an un-restored token survives de-obfuscation."""


class Deobfuscator:
    """Restores tokens back to original PII values using the session vault."""

    async def restore(self, text: str, session: Session) -> str:
        """Replace all tokens in text with original values from session.vault.

        Raises LeftoverResidueError if any token-grammar residue survives restoration.
        """
        if session.state.value != "ACTIVE" or session.is_expired():
            raise SessionClosed(f"Session {session.session_id} is no longer active")

        # Find all token matches
        matches = list(TOKEN_REGEX.finditer(text))
        if not matches:
            return text

        # Sort matches right-to-left for offset-safe replacement
        matches.sort(key=lambda m: m.start(), reverse=True)

        restored_text = text
        for match in matches:
            start, end = match.span()
            token = match.group(0)
            original = await session.vault.resolve(token)
            if original is not None:
                restored_text = restored_text[:start] + original + restored_text[end:]

        # Leftover guard check
        if has_token_residue(restored_text):
            raise LeftoverResidueError(
                "De-obfuscation leftover guard fired: un-restored token residue detected in response payload"
            )

        return restored_text
