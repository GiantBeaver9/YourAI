"""Single source of truth for the token grammar.

CRITICAL (review finding R2/R4): the tokenizer AND the de-obfuscation leftover-guard both
derive from THIS module — never from a hand-copied literal. A grammar change here changes
both in lockstep, so the guard can never drift out of sync with the emitted token format.

Grammar: ``[TYPE_hexdigest]`` — bracketed and uppercase-typed.
  * Brackets make it unambiguous in prose (nothing a model writes looks like ``[SSN_ab12...]``),
    which is why de-obfuscation can detect leftover tokens with zero false positives.
  * Brackets also delimit the token, so grammatical affixes the model adds (``[NAME_..]'s``,
    ``([NAME_..])``, ``[NAME_..].``) sit *outside* the brackets and need no special casing.
  * Fixed-width digest (48 bits) makes collisions negligible at any realistic document scale,
    so there is NO variable-length "extend on collision" path (review finding R10) — token
    assignment stays deterministic and order-independent under async.
"""

from __future__ import annotations

import re

# 48 bits. P(collision) among N entities ~ N^2 / 2^49; at N=10_000 that is ~1e-7. No extend path.
DIGEST_HEX_LEN = 12

# One regex, used for BOTH restoration (find tokens to replace) and the leftover guard
# (assert none survived). Group 1 = type tag, group 2 = digest.
TOKEN_REGEX = re.compile(r"\[([A-Z][A-Z0-9]*)_([0-9a-f]{%d})\]" % DIGEST_HEX_LEN)


def make_token(type_tag: str, digest_hex: str) -> str:
    """Build a token from a type tag and a hex digest. The ONE place tokens are formatted."""
    return f"[{type_tag}_{digest_hex[:DIGEST_HEX_LEN]}]"


def find_tokens(text: str) -> list[re.Match]:
    """All token occurrences, left to right."""
    return list(TOKEN_REGEX.finditer(text))


def has_token_residue(text: str) -> bool:
    """Leftover-token guard: True if ANY token-grammar residue survives.

    A True here after restoration is a hard failure — we never ship a token to the user.
    """
    return TOKEN_REGEX.search(text) is not None
