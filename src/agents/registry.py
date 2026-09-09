"""Convention-based discovery for agent specifications."""

from importlib import import_module
from pathlib import Path
from pkgutil import iter_modules

from foundry_demo.agents.common.base import (
    AgentSpecError,
    HostedAgentSpec,
    PromptAgentSpec,
    RegisteredAgentSpec,
)


def _discover_agents() -> dict[str, RegisteredAgentSpec]:
    agents: dict[str, RegisteredAgentSpec] = {}
    package_dir = Path(__file__).parent

    for module in iter_modules([str(package_dir)]):
        if not module.ispkg or module.name == "common":
            continue
        package = import_module(f"{__package__}.{module.name}")
        spec = getattr(package, "AGENT_SPEC", None)
        if spec is None:
            raise AgentSpecError(
                f"Agent package {module.name!r} must export an AGENT_SPEC instance."
            )
        if not isinstance(spec, PromptAgentSpec | HostedAgentSpec):
            raise AgentSpecError(
                f"Agent package {module.name!r} exports an invalid AGENT_SPEC."
            )
        if spec.name in agents:
            raise AgentSpecError(f"Duplicate registered agent name: {spec.name!r}.")
        agents[spec.name] = spec

    return agents


AGENTS = _discover_agents()


def list_agents() -> list[str]:
    return sorted(AGENTS)


def get_agent(name: str) -> RegisteredAgentSpec:
    cleaned = name.strip()
    if cleaned not in AGENTS:
        available = ", ".join(repr(key) for key in list_agents())
        raise AgentSpecError(f"Agent {name!r} not found. Available agents: {available}")
    return AGENTS[cleaned]
