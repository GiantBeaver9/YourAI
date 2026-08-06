"""Secure Context Pipeline — PII/PHI obfuscation for external LLM providers.

detect -> obfuscate -> call LLM -> restore, so raw PII/PHI never leaves in readable form.
"""

__version__ = "0.1.0"
