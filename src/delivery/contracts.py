"""Contracts shared by agent-kind-specific delivery adapters."""

from dataclasses import dataclass
from typing import Any, Protocol

from foundry_demo.agents import RegisteredAgentSpec
from foundry_demo.delivery.config import DeploymentConfig

# Foundry rejects a version whose metadata carries more entries than this, so every
# adapter keeps its payload compact and asserts the budget before calling the SDK.
FOUNDRY_METADATA_MAX_ENTRIES = 16


class MetadataLimitError(ValueError):
    """Raised when version metadata would exceed the Foundry entry cap."""


def enforce_metadata_limit(metadata: dict[str, str], *, subject: str) -> dict[str, str]:
    """Fail with the offending keys instead of an opaque Foundry `invalid_payload`."""
    if len(metadata) > FOUNDRY_METADATA_MAX_ENTRIES:
        keys = ", ".join(sorted(metadata))
        raise MetadataLimitError(
            f"Version metadata for {subject!r} has {len(metadata)} entries but Foundry "
            f"accepts at most {FOUNDRY_METADATA_MAX_ENTRIES}: {keys}"
        )
    return metadata


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
