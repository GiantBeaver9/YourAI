"""String-level coreference clustering — runs BEFORE tokenization (review finding R1).

We tokenize the *cluster canonical*, not the raw surface string, so "John Smith" / "John" /
"Mr. Smith" collapse to one token. Names cluster by token-subset (one mention's words are a
subset of another's); other entities cluster by exact normalized value. NOT pronominal — that
is named as unsolvable-by-replacement, not faked.
"""

from __future__ import annotations

from ..entities import DetectedEntity, EntityType
from ..normalize import normalize_value


def assign_clusters(entities: list[DetectedEntity]) -> None:
    """Mutates each entity's ``cluster_id`` to the canonical value it should tokenize under."""
    names = [e for e in entities if e.entity_type is EntityType.NAME]
    others = [e for e in entities if e.entity_type is not EntityType.NAME]

    # names: union-find by token-subset; canonical = the longest mention in the group
    groups: list[dict] = []  # each: {"tokens": set, "canonical": str}
    for e in names:
        norm = normalize_value(e.text)
        toks = set(norm.split())
        placed = False
        for g in groups:
            if toks <= g["tokens"] or g["tokens"] <= toks:
                g["tokens"] |= toks
                if len(norm) > len(g["canonical"]):
                    g["canonical"] = norm
                e.cluster_id = g["canonical"]
                placed = True
                break
        if not placed:
            groups.append({"tokens": toks, "canonical": norm})
            e.cluster_id = norm
    # second pass: canonical may have grown after an entity was placed -> re-point to the group's canonical
    for e in names:
        norm = normalize_value(e.text)
        toks = set(norm.split())
        for g in groups:
            if toks <= g["tokens"]:
                e.cluster_id = g["canonical"]
                break

    # everything else: cluster by exact normalized value
    for e in others:
        e.cluster_id = normalize_value(e.text)
