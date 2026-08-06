"""Detection: Presidio substrate (when installed) + native rule-engine fallback."""

from .base import Detector, RuleSpec
from .clustering import assign_clusters
from .presidio_detector import PresidioDetector, get_detector
from .rule_engine import RuleEngineDetector

__all__ = [
    "Detector",
    "RuleSpec",
    "RuleEngineDetector",
    "PresidioDetector",
    "get_detector",
    "assign_clusters",
]
