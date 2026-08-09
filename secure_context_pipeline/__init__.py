"""Secure Context Pipeline — PII/PHI obfuscation for external LLM providers.

detect -> obfuscate -> call LLM -> restore, so raw PII/PHI never leaves in readable form.
"""

from .config import ObfuscationPolicy, Settings
from .entities import Action, DetectedEntity, EntityType
from .pipeline import ProcessResult, SecureContextPipeline

__version__ = "0.1.0"

__all__ = [
    "SecureContextPipeline",
    "ProcessResult",
    "ObfuscationPolicy",
    "Settings",
    "EntityType",
    "Action",
    "DetectedEntity",
    "__version__",
]
