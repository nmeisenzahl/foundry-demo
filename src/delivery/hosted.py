"""Hosted-agent SDK operations and readiness polling."""

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from azure.ai.projects.models import (
    AgentEndpointProtocol,
    ContainerConfiguration,
    HostedAgentDefinition,
    ProtocolVersionRecord,
)

from foundry_demo.agents import HostedAgentSpec, RegisteredAgentSpec
from foundry_demo.agents.common.base import AgentSpecError
from foundry_demo.delivery.config import DeploymentConfig
from foundry_demo.delivery.contracts import Candidate, enforce_metadata_limit
from foundry_demo.delivery.prompt import CandidateVersionError
from foundry_demo.delivery.toolbox import create_toolbox_candidate


class HostedReadinessError(RuntimeError):
    pass


class HostedReadinessTimeout(HostedReadinessError):
    pass


def build_definition(
    spec: HostedAgentSpec,
    *,
    config: DeploymentConfig,
    immutable_image: str,
    toolbox_endpoint: str | None = None,
) -> HostedAgentDefinition:
    model = (spec.model_deployment_name or config.model_deployment_name).strip()
    if not model:
        raise AgentSpecError("Model deployment name is required and cannot be blank.")
    env_vars = {
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": model,
        "AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING": "true",
    }
    if toolbox_endpoint:
        env_vars["TOOLBOX_ENDPOINT"] = toolbox_endpoint
    return HostedAgentDefinition(
        protocol_versions=[
            ProtocolVersionRecord(
                protocol=AgentEndpointProtocol.RESPONSES, version=spec.protocol_version
            )
        ],
        cpu=spec.cpu,
        memory=spec.memory,
        container_configuration=ContainerConfiguration(image=immutable_image),
        environment_variables=env_vars,
    )


class HostedAgentOperations:
    def __init__(
        self,
        *,
        timeout_seconds: float = 600,
        poll_seconds: float = 5,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self.monotonic = monotonic
        self.sleep = sleep

    def create_candidate(
        self,
        project: Any,
        spec: RegisteredAgentSpec,
        config: DeploymentConfig,
        metadata: dict[str, str],
    ) -> Candidate:
        if not isinstance(spec, HostedAgentSpec):
            raise TypeError("HostedAgentOperations requires HostedAgentSpec")
        toolbox_cand = None
        if spec.toolbox is not None:
            toolbox_cand = create_toolbox_candidate(
                project, spec.toolbox, config.project_endpoint, metadata
            )

        image = config.get_hosted_image(spec)
        image_repository = image.reference.rsplit(":", 1)[0]
        immutable_image = f"{image_repository}@{image.digest}"
        definition = build_definition(
            spec,
            config=config,
            immutable_image=immutable_image,
            toolbox_endpoint=toolbox_cand.toolbox_endpoint if toolbox_cand else None,
        )
        serialized = definition.as_dict()
        definition_digest = hashlib.sha256(
            json.dumps(serialized, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        # Foundry caps version metadata at FOUNDRY_METADATA_MAX_ENTRIES entries, so
        # dependency names and versions travel as single `name@version` values. The
        # deployment record keeps the fully expanded provenance.
        version_metadata = {
            **metadata,
            "definition_sha256": definition_digest,
            "image_digest": image.digest,
            "image_reference": immutable_image,
        }
        if toolbox_cand is not None:
            version_metadata.update(
                {
                    "skill": f"{toolbox_cand.skill_name}@{toolbox_cand.skill_version}",
                    "skill_sha256": toolbox_cand.skill_sha256,
                    "toolbox": f"{toolbox_cand.toolbox_name}@{toolbox_cand.toolbox_version}",
                    "toolbox_sha256": toolbox_cand.toolbox_sha256,
                    "toolbox_endpoint": toolbox_cand.toolbox_endpoint,
                }
            )
        created = project.agents.create_version(
            agent_name=spec.name,
            definition=definition,
            metadata=enforce_metadata_limit(version_metadata, subject=spec.name),
            description=spec.description,
        )
        raw = getattr(created, "version", None) or getattr(created, "id", None)
        if raw is None or not str(raw).strip():
            raise CandidateVersionError(
                f"Foundry did not return a version identifier for agent {spec.name!r}."
            )
        return Candidate(
            str(raw).strip(),
            "hosted_definition",
            spec.name,
            definition_digest,
            getattr(created, "status", None),
            immutable_image,
            image.digest,
            skill_name=toolbox_cand.skill_name if toolbox_cand else None,
            skill_version=toolbox_cand.skill_version if toolbox_cand else None,
            skill_sha256=toolbox_cand.skill_sha256 if toolbox_cand else None,
            toolbox_name=toolbox_cand.toolbox_name if toolbox_cand else None,
            toolbox_version=toolbox_cand.toolbox_version if toolbox_cand else None,
            toolbox_endpoint=toolbox_cand.toolbox_endpoint if toolbox_cand else None,
            toolbox_sha256=toolbox_cand.toolbox_sha256 if toolbox_cand else None,
        )

    def wait_ready(
        self, project: Any, spec: RegisteredAgentSpec, candidate: Candidate
    ) -> Candidate:
        deadline = self.monotonic() + self.timeout_seconds
        while True:
            info = project.agents.get_version(agent_name=spec.name, agent_version=candidate.version)
            raw_status = getattr(info, "status", "") or ""
            status = str(getattr(raw_status, "value", raw_status)).lower()
            if status == "active":
                return replace(candidate, status=status)
            if status == "failed":
                error = getattr(info, "error", None)
                code = getattr(error, "code", None)
                message = getattr(error, "message", None)
                detail = message or "no details"
                raise HostedReadinessError(
                    f"Hosted agent provisioning failed ({code or 'unknown'}): {detail}"
                )
            if self.monotonic() >= deadline:
                target = f"{spec.name}:{candidate.version}"
                raise HostedReadinessTimeout(
                    f"Hosted agent {target} did not become active within "
                    f"{self.timeout_seconds:g} seconds."
                )
            self.sleep(self.poll_seconds)
