"""Secure Context Pipeline — PII/PHI obfuscation for external LLM providers.

detect -> obfuscate -> call LLM -> restore, so raw PII/PHI never leaves in readable form.
"""

from .config import ObfuscationPolicy, Settings
from .entities import Action, DataClass, DetectedEntity, EntityType
from .pipeline.pipeline import PipelineResult, SecureContextPipeline
from .vault.session import Session, SessionClosed, SessionManager

__version__ = "0.1.0"

__all__ = [
    "SecureContextPipeline",
    "PipelineResult",
    "ObfuscationPolicy",
    "Settings",
    "SessionManager",
    "SessionClosed",
    "Session",
    "EntityType",
    "DataClass",
    "Action",
    "DetectedEntity",
]
