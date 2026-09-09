"""Contracts shared by agent-kind-specific delivery adapters."""

from dataclasses import dataclass
from typing import Any, Protocol

from foundry_demo.agents import RegisteredAgentSpec
from foundry_demo.delivery.config import DeploymentConfig


@dataclass(frozen=True)
class Candidate:
    version: str
    artifact_type: str
    artifact_reference: str
    artifact_sha256: str
    status: str | None = None
    image_reference: str | None = None
    image_digest: str | None = None
    skill_name: str | None = None
    skill_version: str | None = None
    skill_sha256: str | None = None
    toolbox_name: str | None = None
    toolbox_version: str | None = None
    toolbox_endpoint: str | None = None
    toolbox_sha256: str | None = None


class AgentOperations(Protocol):
    def create_candidate(
        self,
        project: Any,
        spec: RegisteredAgentSpec,
        config: DeploymentConfig,
        metadata: dict[str, str],
    ) -> Candidate: ...

    def wait_ready(
        self, project: Any, spec: RegisteredAgentSpec, candidate: Candidate
    ) -> Candidate: ...
