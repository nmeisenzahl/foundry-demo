from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from azure.ai.projects.models import AgentVersionStatus

from foundry_demo.agents.architecture_advisor import ARCHITECTURE_ADVISOR_SPEC
from foundry_demo.delivery.config import DeploymentConfig
from foundry_demo.delivery.contracts import Candidate
from foundry_demo.delivery.hosted import (
    HostedAgentOperations,
    HostedReadinessError,
    HostedReadinessTimeout,
)


def config():
    return DeploymentConfig(
        project_endpoint="https://example.services.ai.azure.com/api/projects/dev",
        model_deployment_name="model",
        temperature=None,
        environ={
            "FOUNDRY_ARCHITECTURE_ADVISOR_IMAGE": "example.azurecr.io/architecture-advisor:v1",
            "FOUNDRY_ARCHITECTURE_ADVISOR_IMAGE_DIGEST": "sha256:" + "a" * 64,
        },
    )


def test_create_hosted_candidate():
    project = MagicMock()
    project.agents.create_version.return_value = SimpleNamespace(version="7", status="creating")
    project.beta.skills.create_from_files.return_value = SimpleNamespace(version="1")
    project.toolboxes.create_version.return_value = SimpleNamespace(version="2")
    project.send_request.return_value = SimpleNamespace(status_code=200)
    candidate = HostedAgentOperations().create_candidate(
        project, ARCHITECTURE_ADVISOR_SPEC, config(), {"deployment_id": "d"}
    )
    definition = project.agents.create_version.call_args.kwargs["definition"]
    metadata = project.agents.create_version.call_args.kwargs["metadata"]
    assert candidate.version == "7"
    assert len(candidate.artifact_sha256) == 64
    assert candidate.artifact_type == "hosted_definition"
    assert candidate.artifact_reference == "architecture-advisor"
    assert candidate.skill_name == "architecture-decision-brief"
    assert candidate.skill_version == "1"
    assert candidate.toolbox_name == "architecture-advisor-toolbox"
    assert candidate.toolbox_version == "2"
    assert (
        candidate.toolbox_endpoint
        == "https://example.services.ai.azure.com/api/projects/dev/toolboxes/architecture-advisor-toolbox/versions/2/mcp?api-version=v1"
    )
    assert definition.container_configuration.image == (
        "example.azurecr.io/architecture-advisor@sha256:" + "a" * 64
    )
    assert candidate.image_reference == definition.container_configuration.image
    assert candidate.image_digest == "sha256:" + "a" * 64
    assert metadata["definition_sha256"] == candidate.artifact_sha256
    assert metadata["artifact_sha256"] == candidate.artifact_sha256
    assert metadata["image_reference"] == candidate.image_reference
    assert metadata["image_digest"] == candidate.image_digest
    assert metadata["toolbox_version"] == "2"
    assert definition.protocol_versions[0].version == "2.0.0"
    assert definition.environment_variables["AZURE_AI_MODEL_DEPLOYMENT_NAME"] == "model"
    assert definition.environment_variables["AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING"] == "true"
    assert definition.environment_variables["TOOLBOX_ENDPOINT"] == candidate.toolbox_endpoint


def test_create_hosted_candidate_honors_model_override():
    project = MagicMock()
    project.agents.create_version.return_value = SimpleNamespace(version="7", status="creating")
    project.beta.skills.create_from_files.return_value = SimpleNamespace(version="1")
    project.toolboxes.create_version.return_value = SimpleNamespace(version="2")
    spec = replace(ARCHITECTURE_ADVISOR_SPEC, model_deployment_name="agent-model")

    HostedAgentOperations().create_candidate(project, spec, config(), {"deployment_id": "d"})

    definition = project.agents.create_version.call_args.kwargs["definition"]
    assert definition.environment_variables["AZURE_AI_MODEL_DEPLOYMENT_NAME"] == "agent-model"


def test_wait_ready_reaches_active():
    project = MagicMock()
    project.agents.get_version.side_effect = [
        SimpleNamespace(status=AgentVersionStatus.CREATING),
        SimpleNamespace(status=AgentVersionStatus.ACTIVE),
    ]
    clock = iter([0.0, 1.0])
    result = HostedAgentOperations(
        timeout_seconds=10,
        poll_seconds=0,
        monotonic=lambda: next(clock),
        sleep=lambda _: None,
    ).wait_ready(
        project,
        ARCHITECTURE_ADVISOR_SPEC,
        Candidate("1", "hosted_definition", "ref", "a" * 64),
    )
    assert result.status == "active"


def test_wait_ready_failed():
    project = MagicMock()
    project.agents.get_version.return_value = SimpleNamespace(
        status="failed", error=SimpleNamespace(code="image_pull_failed", message="denied")
    )
    with pytest.raises(HostedReadinessError, match="image_pull_failed"):
        HostedAgentOperations().wait_ready(
            project,
            ARCHITECTURE_ADVISOR_SPEC,
            Candidate("1", "hosted_definition", "ref", "a" * 64),
        )


def test_wait_ready_timeout():
    project = MagicMock()
    project.agents.get_version.return_value = SimpleNamespace(status="creating")
    clock = iter([0.0, 2.0])
    with pytest.raises(HostedReadinessTimeout):
        HostedAgentOperations(
            timeout_seconds=1,
            poll_seconds=0,
            monotonic=lambda: next(clock),
            sleep=lambda _: None,
        ).wait_ready(
            project,
            ARCHITECTURE_ADVISOR_SPEC,
            Candidate("1", "hosted_definition", "ref", "a" * 64),
        )
