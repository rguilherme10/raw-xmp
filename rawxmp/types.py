"""Shared value types."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Estimate:
    """Every decision module returns value + confidence + human-readable reason."""
    value: Any
    confidence: float = 1.0
    reason: str = ""

    def to_dict(self) -> dict:
        v = self.value
        if hasattr(v, "item"):
            v = v.item()
        return {"value": v, "confidence": round(float(self.confidence), 3), "reason": self.reason}


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def clip01(x: float) -> float:
    return clamp(x, 0.0, 1.0)
