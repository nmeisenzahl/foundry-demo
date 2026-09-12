from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from azure.ai.projects.models import AgentVersionStatus

from foundry_demo.agents.architecture_advisor import ARCHITECTURE_ADVISOR_SPEC
from foundry_demo.agents.incident_triage import INCIDENT_TRIAGE_SPEC
from foundry_demo.delivery.config import DeploymentConfig
from foundry_demo.delivery.contracts import (
    FOUNDRY_METADATA_MAX_ENTRIES,
    Candidate,
    MetadataLimitError,
    enforce_metadata_limit,
)
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
    assert metadata["image_reference"] == candidate.image_reference
    assert metadata["image_digest"] == candidate.image_digest
    assert metadata["skill"] == "architecture-decision-brief@1"
    assert metadata["toolbox"] == "architecture-advisor-toolbox@2"
    assert definition.protocol_versions[0].version == "2.0.0"
    assert definition.environment_variables["AZURE_AI_MODEL_DEPLOYMENT_NAME"] == "model"
    assert definition.environment_variables["AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING"] == "true"
    assert definition.environment_variables["TOOLBOX_ENDPOINT"] == candidate.toolbox_endpoint


def toolbox_free_config():
    return DeploymentConfig(
        project_endpoint="https://example.services.ai.azure.com/api/projects/dev",
        model_deployment_name="model",
        temperature=None,
        environ={
            "FOUNDRY_INCIDENT_TRIAGE_IMAGE": "example.azurecr.io/incident-triage:v1",
            "FOUNDRY_INCIDENT_TRIAGE_IMAGE_DIGEST": "sha256:" + "b" * 64,
        },
    )


def test_create_hosted_candidate_without_a_toolbox():
    project = MagicMock()
    project.agents.create_version.return_value = SimpleNamespace(version="3", status="creating")

    candidate = HostedAgentOperations().create_candidate(
        project, INCIDENT_TRIAGE_SPEC, toolbox_free_config(), {"deployment_id": "d"}
    )

    definition = project.agents.create_version.call_args.kwargs["definition"]
    metadata = project.agents.create_version.call_args.kwargs["metadata"]

    # No skill or toolbox version is created, and nothing toolbox-shaped leaks
    # into the definition or the release metadata.
    project.beta.skills.create_from_files.assert_not_called()
    project.toolboxes.create_version.assert_not_called()
    assert "TOOLBOX_ENDPOINT" not in definition.environment_variables
    assert candidate.skill_name is None
    assert candidate.skill_version is None
    assert candidate.toolbox_name is None
    assert candidate.toolbox_endpoint is None
    assert not {"skill", "toolbox", "toolbox_endpoint"} & set(metadata)

    assert candidate.version == "3"
    assert candidate.artifact_reference == "incident-triage"
    assert definition.container_configuration.image == (
        "example.azurecr.io/incident-triage@sha256:" + "b" * 64
    )
    assert (definition.cpu, definition.memory) == ("1", "2Gi")
    assert definition.environment_variables["AZURE_AI_MODEL_DEPLOYMENT_NAME"] == "model"


def test_hosted_version_metadata_fits_foundry_limit():
    project = MagicMock()
    project.agents.create_version.return_value = SimpleNamespace(version="7", status="creating")
    project.beta.skills.create_from_files.return_value = SimpleNamespace(version="1")
    project.toolboxes.create_version.return_value = SimpleNamespace(version="2")
    full_audit_metadata = {
        "deployment_id": "d",
        "git_sha": "abc123",
        "repository": "org/repo",
        "run_id": "42",
        "run_attempt": "3",
        "workflow_url": "https://github.com/org/repo/actions/runs/42",
        "git_dirty": "false",
    }

    HostedAgentOperations().create_candidate(
        project, ARCHITECTURE_ADVISOR_SPEC, config(), full_audit_metadata
    )

    metadata = project.agents.create_version.call_args.kwargs["metadata"]
    toolbox_metadata = project.toolboxes.create_version.call_args.kwargs["metadata"]
    assert len(metadata) <= FOUNDRY_METADATA_MAX_ENTRIES
    assert len(toolbox_metadata) <= FOUNDRY_METADATA_MAX_ENTRIES


def test_enforce_metadata_limit_reports_offending_keys():
    oversized = {f"key_{index}": "value" for index in range(FOUNDRY_METADATA_MAX_ENTRIES + 1)}

    with pytest.raises(MetadataLimitError) as excinfo:
        enforce_metadata_limit(oversized, subject="architecture-advisor")

    assert "architecture-advisor" in str(excinfo.value)
    assert "key_0" in str(excinfo.value)


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
