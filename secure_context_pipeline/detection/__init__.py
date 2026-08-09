"""Detection: rules-as-data compiled onto Presidio (substrate) or native regex (fallback)."""

from .coreference import cluster_names, propagate_names, resolve_overlaps
from .detector import Detector
from .factory import build_detector
from .native import RuleEngineDetector
from .rules import STANDARD_RULES, Rule

__all__ = [
    "Detector",
    "build_detector",
    "RuleEngineDetector",
    "Rule",
    "STANDARD_RULES",
    "resolve_overlaps",
    "cluster_names",
    "propagate_names",
]
