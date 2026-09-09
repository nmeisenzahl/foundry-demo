"""Prompt-agent SDK operations."""

import hashlib
import json
from typing import Any

from azure.ai.projects.models import PromptAgentDefinition

from foundry_demo.agents import PromptAgentSpec, RegisteredAgentSpec
from foundry_demo.agents.common.base import AgentSpecError
from foundry_demo.delivery.config import DeploymentConfig
from foundry_demo.delivery.contracts import Candidate


class CandidateVersionError(RuntimeError):
    """Raised when Foundry does not return a usable candidate version."""


def build_definition(
    spec: PromptAgentSpec,
    *,
    default_model: str,
    temperature: float | None = None,
) -> PromptAgentDefinition:
    model = (spec.model_deployment_name or default_model).strip()
    if not model:
        raise AgentSpecError("Model deployment name is required and cannot be blank.")
    instructions = spec.instructions.strip()
    if not instructions:
        raise AgentSpecError(f"Agent {spec.name!r} instructions cannot be empty.")
    kwargs: dict[str, object] = {
        "model": model,
        "instructions": instructions,
        "tools": spec.tools_factory(),
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    return PromptAgentDefinition(**kwargs)


class PromptAgentOperations:
    def create_candidate(
        self,
        project: Any,
        spec: RegisteredAgentSpec,
        config: DeploymentConfig,
        metadata: dict[str, str],
    ) -> Candidate:
        if not isinstance(spec, PromptAgentSpec):
            raise TypeError("PromptAgentOperations requires PromptAgentSpec")
        definition = build_definition(
            spec, default_model=config.model_deployment_name, temperature=config.temperature
        )
        serialized = definition.as_dict()
        digest = hashlib.sha256(
            json.dumps(serialized, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        version = project.agents.create_version(
            agent_name=spec.name,
            definition=definition,
            metadata={**metadata, "artifact_sha256": digest, "definition_sha256": digest},
            description=spec.description,
        )
        raw = getattr(version, "version", None) or getattr(version, "id", None)
        if raw is None or not str(raw).strip():
            raise CandidateVersionError(
                f"Foundry did not return a version identifier for agent {spec.name!r}."
            )
        return Candidate(str(raw).strip(), "prompt_definition", spec.name, digest)

    def wait_ready(
        self, project: Any, spec: RegisteredAgentSpec, candidate: Candidate
    ) -> Candidate:
        return candidate
