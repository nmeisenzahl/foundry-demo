"""Shared contracts and base specifications for Foundry agents."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from foundry_demo.delivery.smoke import SmokeEvidence


class AgentSpecError(ValueError):
    """Raised when agent specification inputs or definitions are invalid."""


class AgentKind(StrEnum):
    PROMPT = "prompt"
    HOSTED = "hosted"


@dataclass(frozen=True)
class BaseAgentSpec:
    """Base specification shared across all Foundry agent kinds."""

    name: str
    description: str
    smoke_prompt: str
    validate_smoke: Callable[["SmokeEvidence"], None] | None = None
    model_deployment_name: str | None = None
    smoke_tool_choice: str | None = None


@dataclass(frozen=True)
class PromptAgentSpec(BaseAgentSpec):
    """Specification for a Foundry prompt agent."""

    instructions: str = ""
    tools_factory: Callable[[], list[Any]] = list
    kind: AgentKind = AgentKind.PROMPT


@dataclass(frozen=True)
class SkillSpec:
    name: str
    package: str
    resource_path: str


@dataclass(frozen=True)
class MCPToolSpec:
    server_label: str
    server_url: str
    allowed_tools: tuple[str, ...]
    require_approval: str = "always"


@dataclass(frozen=True)
class ToolboxSpec:
    name: str
    skill: SkillSpec
    mcp_tools: tuple[MCPToolSpec, ...]


@dataclass(frozen=True)
class HostedAgentSpec(BaseAgentSpec):
    """Specification for a Foundry hosted agent."""

    image_env_var: str = ""
    image_digest_env_var: str = ""
    cpu: str = "1"
    memory: str = "2Gi"
    protocol_version: str = "2.0.0"
    toolbox: ToolboxSpec | None = None
    smoke_setup_prompt: str | None = None
    smoke_setup_tool_name: str | None = None
    smoke_max_attempts: int = 1
    smoke_retry_seconds: float = 0
    kind: AgentKind = AgentKind.HOSTED


AgentSpec = PromptAgentSpec | HostedAgentSpec
RegisteredAgentSpec = AgentSpec
