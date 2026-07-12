"""Risk classification for tool actions (stub, extend as tools are added)."""
from typing import Literal

HIGH_RISK_KEYWORDS = ["delete", "rm -rf", "drop table", "format disk", "shutdown"]


def classify_risk(action_description: str) -> Literal["low", "medium", "high"]:
    lowered = action_description.lower()
    if any(word in lowered for word in HIGH_RISK_KEYWORDS):
        return "high"
    if "write" in lowered or "modify" in lowered or "install" in lowered:
        return "medium"
    return "low"
