"""Detection package — sensitive data scanning for SandboxShift."""

from .sensitivity import (
    DetectionLayer,
    Finding,
    Recommendation,
    SensitivityResult,
    SensitivityScanner,
)

__all__ = [
    "DetectionLayer",
    "Finding",
    "Recommendation",
    "SensitivityResult",
    "SensitivityScanner",
]
