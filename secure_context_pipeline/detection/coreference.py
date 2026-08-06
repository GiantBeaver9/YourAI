"""Overlap resolution, known-name propagation, and ambiguity-aware name coreference.

All pure functions over detected spans, run before obfuscation:

* :func:`resolve_overlaps` — keep a **disjoint** set (most-confident, then longest, greedy), so
  a confident specific rule wins over the low-confidence catch-all, and a full-name span wins
  over its own sub-parts.
* :func:`propagate_names` — once a full name is known in a document, **every** occurrence of it
  or its parts is that name. NER misses a bare surname mid-sentence ("Reyes tolerates…") and in
  signature blocks; we re-scan for the known name surfaces and parts to catch those, so recall
  doesn't depend on the model tagging every mention. This is what closes the surname-in-prose
  leak.
* :func:`cluster_names` — collapse coreferent mentions to **one token**, but *safely*. A mention
  that is an unambiguous subset of exactly one full name joins that name's cluster ("John",
  "Mr. Smith" → "John Smith"). A surname shared by **two** people ("Reyes" when both Jonathan
  and Maria appear) is **ambiguous** — it gets its own per-surface cluster rather than bridging
  two distinct people into one token (which would restore one of them to the wrong name).
  String-level only; pronominal coreference ("the patient") is out of scope and named as such.
"""

from __future__ import annotations

import re

from ..entities import DetectedEntity, EntityType
from .rules import HONORIFICS

_HONORIFIC_SET = {h.lower() for h in HONORIFICS}
_WORD = re.compile(r"[A-Za-z][A-Za-z'’.-]*")


def resolve_overlaps(entities: list[DetectedEntity]) -> list[DetectedEntity]:
    """Return a disjoint subset: greedy by confidence, then span length."""
    ordered = sorted(entities, key=lambda e: (-e.confidence, -(e.end - e.start), e.start))
    kept: list[DetectedEntity] = []
    for cand in ordered:
        if not any(cand.overlaps(k) for k in kept):
            kept.append(cand)
    kept.sort(key=lambda e: e.start)
    return kept


def _name_tokens(surface: str) -> list[str]:
    """Lowercased name tokens with honorifics dropped (for matching/subset tests)."""
    toks = [t.strip(".,'’").lower() for t in surface.split()]
    return [t for t in toks if t and t not in _HONORIFIC_SET]


def _clean_surface(surface: str) -> str:
    """Original-case surface with leading honorific tokens removed ("Mr. Reyes" -> "Reyes")."""
    parts = [p for p in surface.split() if p.strip(".,'’").lower() not in _HONORIFIC_SET]
    return " ".join(parts).strip()


def propagate_names(text: str, entities: list[DetectedEntity]) -> list[DetectedEntity]:
    """Add NAME spans for every occurrence of a known name surface or part.

    Returns ``entities`` plus propagated candidates; the caller re-runs :func:`resolve_overlaps`
    so full-name spans absorb overlapping single parts and only genuinely-new occurrences (a
    bare surname NER missed) survive."""
    name_ents = [e for e in entities if e.entity_type is EntityType.NAME]
    if not name_ents:
        return entities

    # Full surfaces (higher confidence, preferred on overlap) and individual parts.
    full_needles: set[str] = set()
    part_needles: set[str] = set()
    for e in name_ents:
        cleaned = _clean_surface(e.text)
        toks = cleaned.split()
        if len(toks) >= 2:
            full_needles.add(cleaned)
        for tok in toks:
            bare = tok.strip(".,'’")
            if len(bare) >= 2 and bare[0].isupper():
                part_needles.add(bare)

    extra: list[DetectedEntity] = []
    for needle, conf in [(n, 0.9) for n in full_needles] + [(n, 0.85) for n in part_needles]:
        for m in re.finditer(r"\b" + re.escape(needle) + r"\b", text):
            extra.append(
                DetectedEntity(
                    start=m.start(), end=m.end(), entity_type=EntityType.NAME,
                    text=needle, confidence=conf, source="propagated",
                )
            )
    return entities + extra


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)


def cluster_names(entities: list[DetectedEntity]) -> dict[str, str]:
    """Cluster NAME entities in place (sets ``cluster_id``); return ``cluster_id -> canonical``.

    Safe collapse: a single-token mention joins a full name only if that full name is the unique
    one containing it; an ambiguous part (shared surname) forms its own per-surface cluster and
    never bridges two people."""
    names = [e for e in entities if e.entity_type is EntityType.NAME]
    if not names:
        return {}

    toks = [_name_tokens(e.text) for e in names]
    full_sets = [frozenset(toks[i]) for i in range(len(names)) if len(toks[i]) >= 2]

    def key_for(i: int) -> tuple:
        t = toks[i]
        if len(t) >= 2:
            return ("full", tuple(sorted(set(t))))
        if len(t) == 1:
            containing = {fs for fs in full_sets if t[0] in fs}
            if len(containing) == 1:
                return ("full", tuple(sorted(next(iter(containing)))))
            return ("part", t[0])  # ambiguous or orphan surname -> own cluster
        return ("part", "")

    clusters: dict[tuple, list[int]] = {}
    for i in range(len(names)):
        clusters.setdefault(key_for(i), []).append(i)

    canonicals: dict[str, str] = {}
    for idx, (_key, members) in enumerate(clusters.items()):
        cluster_id = f"name_{idx}"
        canonical = max(
            (_clean_surface(names[i].text) for i in members),
            key=lambda s: (len(s.split()), len(s)),
        )
        canonicals[cluster_id] = canonical
        for i in members:
            names[i].cluster_id = cluster_id
    return canonicals
