"""Encrypted document store + text extraction."""

from .extractor import extract_text
from .store import EncryptedStore

__all__ = ["EncryptedStore", "extract_text"]
