"""Encrypted-at-rest document store (envelope encryption)."""

from .store import EncryptedDocumentStore, StoreError

__all__ = ["EncryptedDocumentStore", "StoreError"]
