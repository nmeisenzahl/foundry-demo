"""Agent registry and specifications."""

from foundry_demo.agents.common.base import (
    AgentKind,
    AgentSpec,
    AgentSpecError,
    BaseAgentSpec,
    HostedAgentSpec,
    MCPToolSpec,
    PromptAgentSpec,
    RegisteredAgentSpec,
    SkillSpec,
    ToolboxSpec,
)
from foundry_demo.agents.registry import AGENTS, get_agent, list_agents

__all__ = [
    "AGENTS",
    "AgentKind",
    "AgentSpec",
    "AgentSpecError",
    "BaseAgentSpec",
    "HostedAgentSpec",
    "MCPToolSpec",
    "PromptAgentSpec",
    "RegisteredAgentSpec",
    "SkillSpec",
    "ToolboxSpec",
    "get_agent",
    "list_agents",
]
