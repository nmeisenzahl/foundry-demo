"""Deployment automation config loader and matrix renderer."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from foundry_demo.agents import HostedAgentSpec, get_agent, list_agents

SCHEMA_VERSION = "2"
TOP_LEVEL_KEYS = frozenset(
    {"schema_version", "shared_paths", "environments", "agents"}
)
ENVIRONMENT_KEYS = frozenset({"github_environment", "artifact_retention_days"})
AGENT_KEYS = frozenset(
    {
        "name",
        "kind",
        "image_context",
        "image_repository",
        "image_reference_env",
        "image_digest_env",
        "source_paths",
    }
)
AGENT_KINDS = frozenset({"prompt", "hosted"})


class AutomationConfigError(ValueError):
    """Raised when deployment automation configuration is invalid."""


@dataclass(frozen=True)
class AutomationEnvironment:
    github_environment: str
    artifact_retention_days: int


@dataclass(frozen=True)
class AgentAutomation:
    name: str
    kind: str
    image_context: str
    image_repository: str
    image_reference_env: str
    image_digest_env: str
    source_paths: tuple[str, ...]


@dataclass(frozen=True)
class AutomationConfig:
    schema_version: str
    shared_paths: tuple[str, ...]
    environments: Mapping[str, AutomationEnvironment]
    agents: tuple[AgentAutomation, ...]


def _assert_exact_keys(
    value: Mapping[str, Any], *, expected: frozenset[str], path: str
) -> None:
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if unknown:
        raise AutomationConfigError(f"{path} contains unknown keys: {unknown}")
    if missing:
        raise AutomationConfigError(f"{path} is missing required keys: {missing}")


def _as_mapping(value: Any, *, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AutomationConfigError(f"{path} must be a JSON object.")
    return value


def _as_string(value: Any, *, path: str, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise AutomationConfigError(f"{path} must be a string.")
    cleaned = value.strip()
    if not allow_empty and not cleaned:
        raise AutomationConfigError(f"{path} cannot be blank.")
    return cleaned


def _as_positive_int(value: Any, *, path: str) -> int:
    if type(value) is not int or value <= 0:
        raise AutomationConfigError(f"{path} must be a positive integer.")
    return value


def _normalize_path(value: str) -> str:
    cleaned = value.strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def _as_repository_path(value: Any, *, path: str) -> str:
    cleaned = _normalize_path(_as_string(value, path=path, allow_empty=False))
    segments = cleaned.split("/")
    if (
        cleaned.startswith("/")
        or "\\" in cleaned
        or any(segment in {"..", "."} for segment in segments)
    ):
        raise AutomationConfigError(
            f"{path} must be repository-relative without '..', '.', "
            f"backslashes, or a leading '/': {cleaned!r}"
        )
    return cleaned


def _as_path_tuple(value: Any, *, path: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise AutomationConfigError(f"{path} must be a JSON array of strings.")
    if not value:
        raise AutomationConfigError(f"{path} must contain at least one path.")
    paths = tuple(
        _as_repository_path(item, path=f"{path}[{index}]")
        for index, item in enumerate(value)
    )
    if len(set(paths)) != len(paths):
        raise AutomationConfigError(f"Duplicate paths are not allowed in {path}.")
    return paths


def _change_matches(changed_path: str, configured_path: str) -> bool:
    prefix = configured_path.rstrip("/")
    return changed_path == prefix or changed_path.startswith(f"{prefix}/")


def _matches_any(changed_path: str, configured_paths: Sequence[str]) -> bool:
    return any(_change_matches(changed_path, path) for path in configured_paths)


def _parse_environment(name: str, payload: Any) -> AutomationEnvironment:
    env_name = _as_string(name, path="environments.<key>", allow_empty=False)
    raw = _as_mapping(payload, path=f"environments.{env_name}")
    _assert_exact_keys(raw, expected=ENVIRONMENT_KEYS, path=f"environments.{env_name}")
    return AutomationEnvironment(
        github_environment=_as_string(
            raw["github_environment"],
            path=f"environments.{env_name}.github_environment",
            allow_empty=False,
        ),
        artifact_retention_days=_as_positive_int(
            raw["artifact_retention_days"],
            path=f"environments.{env_name}.artifact_retention_days",
        ),
    )


def _parse_agent(raw: Any, *, index: int) -> AgentAutomation:
    payload = _as_mapping(raw, path=f"agents[{index}]")
    _assert_exact_keys(payload, expected=AGENT_KEYS, path=f"agents[{index}]")
    agent = AgentAutomation(
        name=_as_string(payload["name"], path=f"agents[{index}].name", allow_empty=False),
        kind=_as_string(payload["kind"], path=f"agents[{index}].kind", allow_empty=False),
        image_context=_as_string(
            payload["image_context"], path=f"agents[{index}].image_context", allow_empty=True
        ),
        image_repository=_as_string(
            payload["image_repository"],
            path=f"agents[{index}].image_repository",
            allow_empty=True,
        ),
        image_reference_env=_as_string(
            payload["image_reference_env"],
            path=f"agents[{index}].image_reference_env",
            allow_empty=True,
        ),
        image_digest_env=_as_string(
            payload["image_digest_env"],
            path=f"agents[{index}].image_digest_env",
            allow_empty=True,
        ),
        source_paths=_as_path_tuple(
            payload["source_paths"], path=f"agents[{index}].source_paths"
        ),
    )
    if agent.kind not in AGENT_KINDS:
        raise AutomationConfigError(
            f"agents[{index}].kind must be one of {sorted(AGENT_KINDS)}: {agent.kind!r}"
        )

    image_fields = (
        agent.image_context,
        agent.image_repository,
        agent.image_reference_env,
        agent.image_digest_env,
    )
    if agent.kind == "hosted" and any(not field for field in image_fields):
        raise AutomationConfigError(
            f"agents[{index}] hosted agent metadata is incomplete; "
            "image_context, image_repository, image_reference_env, "
            "and image_digest_env are required."
        )
    if agent.kind == "prompt" and any(field for field in image_fields):
        raise AutomationConfigError(
            f"agents[{index}] prompt agent must not define image metadata."
        )
    return agent


def _validate_registry_alignment(config: AutomationConfig) -> None:
    configured_kinds = {agent.name: agent.kind for agent in config.agents}
    expected_kinds = {name: get_agent(name).kind.value for name in list_agents()}
    if configured_kinds != expected_kinds:
        raise AutomationConfigError(
            "Configured agents do not match runtime registry kinds. "
            f"configured={configured_kinds!r} expected={expected_kinds!r}"
        )

    for agent in config.agents:
        spec = get_agent(agent.name)
        if isinstance(spec, HostedAgentSpec):
            if agent.image_reference_env != spec.image_env_var:
                raise AutomationConfigError(
                    f"Hosted agent {agent.name!r} image_reference_env "
                    f"{agent.image_reference_env!r} does not match {spec.image_env_var!r}."
                )
            if agent.image_digest_env != spec.image_digest_env_var:
                raise AutomationConfigError(
                    f"Hosted agent {agent.name!r} image_digest_env "
                    f"{agent.image_digest_env!r} does not match {spec.image_digest_env_var!r}."
                )
        elif any(
            (
                agent.image_context,
                agent.image_repository,
                agent.image_reference_env,
                agent.image_digest_env,
            )
        ):
            raise AutomationConfigError(
                f"Prompt agent {agent.name!r} must not define image metadata."
            )


def _registry_sorted_agents(config: AutomationConfig) -> tuple[AgentAutomation, ...]:
    by_name = {agent.name: agent for agent in config.agents}
    registry_names = tuple(list_agents())
    missing = [name for name in registry_names if name not in by_name]
    extra = sorted(set(by_name) - set(registry_names))
    if missing or extra:
        raise AutomationConfigError(
            "Configured agents do not match runtime registry names. "
            f"missing={missing!r} unexpected={extra!r}"
        )
    return tuple(by_name[name] for name in registry_names)


def load_automation_config(path: Path) -> AutomationConfig:
    try:
        raw_payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AutomationConfigError(f"Config file does not exist: {path}") from exc
    except OSError as exc:
        raise AutomationConfigError(f"Failed to read config file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AutomationConfigError(f"Config file is not valid JSON: {path}") from exc

    payload = _as_mapping(raw_payload, path="root")
    _assert_exact_keys(payload, expected=TOP_LEVEL_KEYS, path="root")

    schema_version = _as_string(payload["schema_version"], path="schema_version", allow_empty=False)
    if schema_version != SCHEMA_VERSION:
        raise AutomationConfigError(
            f"Unsupported schema_version {schema_version!r}. Expected {SCHEMA_VERSION!r}."
        )

    shared_paths = _as_path_tuple(payload["shared_paths"], path="shared_paths")

    raw_environments = _as_mapping(payload["environments"], path="environments")
    if not raw_environments:
        raise AutomationConfigError("environments must define at least one environment.")
    environments = {
        _as_string(name, path="environments.<key>", allow_empty=False): _parse_environment(
            name, env_payload
        )
        for name, env_payload in raw_environments.items()
    }

    raw_agents = payload["agents"]
    if not isinstance(raw_agents, Sequence) or isinstance(raw_agents, str):
        raise AutomationConfigError("agents must be a JSON array.")
    if not raw_agents:
        raise AutomationConfigError("agents must contain at least one configured agent.")

    agents = tuple(_parse_agent(item, index=index) for index, item in enumerate(raw_agents))
    names = [agent.name for agent in agents]
    if len(set(names)) != len(names):
        raise AutomationConfigError("Duplicate agent names are not allowed in automation config.")

    return AutomationConfig(
        schema_version=schema_version,
        shared_paths=shared_paths,
        environments=MappingProxyType(environments),
        agents=agents,
    )


def select_agents(config: AutomationConfig, selector: str) -> tuple[AgentAutomation, ...]:
    cleaned_selector = selector.strip()
    if not cleaned_selector:
        raise AutomationConfigError("Agent selector cannot be empty.")

    sorted_agents = _registry_sorted_agents(config)
    by_name = {agent.name: agent for agent in sorted_agents}
    if cleaned_selector == "all":
        return sorted_agents

    requested = [part.strip() for part in cleaned_selector.split(",")]
    if any(not name for name in requested):
        raise AutomationConfigError("Agent selector contains a blank name.")
    if len(set(requested)) != len(requested):
        raise AutomationConfigError("Agent selector contains duplicate names.")

    unknown = [name for name in requested if name not in by_name]
    if unknown:
        raise AutomationConfigError(
            f"Unknown agent name(s) in selector: {', '.join(sorted(unknown))}."
        )

    requested_set = set(requested)
    return tuple(agent for agent in sorted_agents if agent.name in requested_set)


def select_changed_agents(
    config: AutomationConfig, changed_paths: Sequence[str]
) -> tuple[AgentAutomation, ...]:
    """Select agents affected by a set of repository-relative changed paths.

    Any change under a configured shared path selects every registered agent,
    because shared delivery code and packaging affect all of them.
    """
    normalized = tuple(
        normalized_path
        for normalized_path in (_normalize_path(path) for path in changed_paths)
        if normalized_path
    )
    agents = _registry_sorted_agents(config)
    if any(_matches_any(path, config.shared_paths) for path in normalized):
        return agents
    return tuple(
        agent
        for agent in agents
        if any(_matches_any(path, agent.source_paths) for path in normalized)
    )


def read_changed_paths(path: Path) -> tuple[str, ...]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AutomationConfigError(f"Failed to read changed paths file {path}: {exc}") from exc
    return tuple(line.strip() for line in content.splitlines() if line.strip())


def render_matrix(
    config: AutomationConfig,
    selector: str,
    environment: str,
    changed_paths: Sequence[str] | None = None,
) -> str:
    _validate_registry_alignment(config)

    environment_name = environment.strip()
    if not environment_name:
        raise AutomationConfigError("Environment cannot be empty.")
    if environment_name not in config.environments:
        available = ", ".join(sorted(config.environments))
        raise AutomationConfigError(
            f"Unknown environment {environment_name!r}. Available: {available}"
        )

    env = config.environments[environment_name]
    selected = select_agents(config, selector)
    if changed_paths is not None:
        affected = {agent.name for agent in select_changed_agents(config, changed_paths)}
        selected = tuple(agent for agent in selected if agent.name in affected)

    include = [
        {
            "agent_name": agent.name,
            "agent_kind": agent.kind,
            "environment": environment_name,
            "github_environment": env.github_environment,
            "artifact_retention_days": env.artifact_retention_days,
            "image_context": agent.image_context,
            "image_repository": agent.image_repository,
            "image_reference_env": agent.image_reference_env,
            "image_digest_env": agent.image_digest_env,
        }
        for agent in selected
    ]
    return json.dumps({"include": include}, separators=(",", ":"))


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="deployment-matrix",
        description="Render a GitHub Actions matrix for reviewed Foundry agent deployments.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Path to deployment automation config JSON.",
    )
    parser.add_argument(
        "--environment",
        required=True,
        help="Deployment environment key from config.json (for example: dev).",
    )
    parser.add_argument(
        "--agents",
        required=True,
        help='Agent selector: "all" or a comma-separated list of registered names.',
    )
    parser.add_argument(
        "--changed-paths-file",
        type=Path,
        help=(
            "Optional file with one repository-relative changed path per line. "
            "When given, the selection is narrowed to the agents those paths affect."
        ),
    )
    parsed = parser.parse_args(args)
    try:
        config = load_automation_config(parsed.config)
        changed_paths = (
            None
            if parsed.changed_paths_file is None
            else read_changed_paths(parsed.changed_paths_file)
        )
        matrix = render_matrix(
            config, parsed.agents, parsed.environment, changed_paths=changed_paths
        )
    except AutomationConfigError as exc:
        parser.exit(2, f"{parser.prog}: error: {exc}\n")
    print(matrix)


__all__ = [
    "AgentAutomation",
    "AutomationConfig",
    "AutomationConfigError",
    "AutomationEnvironment",
    "load_automation_config",
    "main",
    "read_changed_paths",
    "render_matrix",
    "select_agents",
    "select_changed_agents",
]
