from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AccessProfile:
    name: str
    tools: list[str]
    write: bool
    network: bool
    secrets: str
    notes: str = ""


@dataclass(slots=True)
class AgentDefinition:
    id: str
    role: str
    description: str
    tags: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    preferred_profile: str = "balanced"
    risk_level: str = "medium"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AgentDefinition":
        return cls(
            id=value["id"],
            role=value["role"],
            description=value.get("description", ""),
            tags=value.get("tags", []),
            capabilities=value.get("capabilities", []),
            required_tools=value.get("required_tools", []),
            preferred_profile=value.get("preferred_profile", "balanced"),
            risk_level=value.get("risk_level", "medium"),
        )
