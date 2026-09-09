from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock
from zipfile import ZipFile

import pytest
from azure.ai.projects._utils.utils import prepare_multipart_form_data
from azure.ai.projects.operations._operations import (
    build_beta_skills_create_from_files_request,
)

from foundry_demo.agents.architecture_advisor.spec import ARCHITECTURE_ADVISOR_SPEC
from foundry_demo.delivery.prompt import CandidateVersionError
from foundry_demo.delivery.toolbox import (
    ToolboxEndpointError,
    build_skill_upload_body,
    create_toolbox_candidate,
    validate_toolbox_endpoint,
)


def test_validate_toolbox_endpoint_success() -> None:
    endpoint = (
        "https://example.services.ai.azure.com/api/projects/dev/"
        "toolboxes/architecture-advisor-toolbox/versions/1/mcp"
    )
    validated = validate_toolbox_endpoint(
        endpoint,
        project_endpoint="https://example.services.ai.azure.com/api/projects/dev",
    )
    assert validated == endpoint


def test_validate_toolbox_endpoint_rejects_invalids() -> None:
    # Not https
    with pytest.raises(ValueError, match="https"):
        validate_toolbox_endpoint(
            "http://example.services.ai.azure.com/api/projects/dev/toolboxes/t/versions/1/mcp",
            project_endpoint="https://example.services.ai.azure.com/api/projects/dev",
        )
    # Different host
    with pytest.raises(ValueError, match="host"):
        validate_toolbox_endpoint(
            "https://other.services.ai.azure.com/api/projects/dev/toolboxes/t/versions/1/mcp",
            project_endpoint="https://example.services.ai.azure.com/api/projects/dev",
        )
    # Unversioned
    with pytest.raises(ValueError, match="<project>/toolboxes"):
        validate_toolbox_endpoint(
            "https://example.services.ai.azure.com/api/projects/dev/toolboxes/t/mcp",
            project_endpoint="https://example.services.ai.azure.com/api/projects/dev",
        )
    for endpoint in (
        "https://example.services.ai.azure.com/api/projects/dev2/toolboxes/t/versions/1/mcp",
        "https://example.services.ai.azure.com/api/projects/dev/toolboxes/t/versions/1/mcp/extra",
        "https://example.services.ai.azure.com/api/projects/dev/toolboxes/t/versions//mcp",
    ):
        with pytest.raises(ValueError):
            validate_toolbox_endpoint(
                endpoint,
                project_endpoint="https://example.services.ai.azure.com/api/projects/dev",
            )


def test_create_toolbox_candidate_lifecycle() -> None:
    project = MagicMock()
    project.beta.skills.create_from_files.return_value = SimpleNamespace(version="10")
    project.toolboxes.create_version.return_value = SimpleNamespace(version="20")
    project.send_request.return_value = SimpleNamespace(status_code=200)

    toolbox_spec = ARCHITECTURE_ADVISOR_SPEC.toolbox
    assert toolbox_spec is not None

    candidate = create_toolbox_candidate(
        project,
        toolbox_spec,
        project_endpoint="https://example.services.ai.azure.com/api/projects/dev",
        metadata={"deployment_id": "test-dep"},
    )

    assert candidate.skill_name == "architecture-decision-brief"
    assert candidate.skill_version == "10"
    assert len(candidate.skill_sha256) == 64
    assert candidate.toolbox_name == "architecture-advisor-toolbox"
    assert candidate.toolbox_version == "20"
    assert (
        candidate.toolbox_endpoint
        == "https://example.services.ai.azure.com/api/projects/dev/toolboxes/architecture-advisor-toolbox/versions/20/mcp?api-version=v1"
    )

    # Verify skill zip packaging
    skill_call = project.beta.skills.create_from_files.call_args
    assert skill_call.args[0] == "architecture-decision-brief"
    upload_body = skill_call.kwargs["content"]
    uploaded_files = upload_body["files"]
    assert len(uploaded_files) == 1
    filename, zip_bytes = uploaded_files[0]
    assert filename == "architecture-decision-brief.zip"

    with ZipFile(BytesIO(zip_bytes)) as zf:
        namelist = zf.namelist()
        assert "SKILL.md" in namelist
        content = zf.read("SKILL.md").decode("utf-8")
        assert "architecture-decision-brief" in content

    # Verify toolbox version call
    toolbox_call = project.toolboxes.create_version.call_args
    assert toolbox_call.kwargs["name"] == "architecture-advisor-toolbox"
    assert len(toolbox_call.kwargs["tools"]) == 1
    mcp_tool = toolbox_call.kwargs["tools"][0]
    assert mcp_tool.server_url == "https://learn.microsoft.com/api/mcp"
    assert mcp_tool.allowed_tools == ["microsoft_docs_search", "microsoft_docs_fetch"]
    assert mcp_tool.require_approval == "never"

    skill_ref = toolbox_call.kwargs["skills"][0]
    assert skill_ref.name == "architecture-decision-brief"
    assert skill_ref.version == "10"
    readiness_request = project.send_request.call_args.args[0]
    assert readiness_request.method == "POST"
    assert readiness_request.url == candidate.toolbox_endpoint
    assert b'"method": "initialize"' in readiness_request.content.encode()


def test_create_toolbox_candidate_empty_version_fails() -> None:
    project = MagicMock()
    project.beta.skills.create_from_files.return_value = SimpleNamespace(version="")
    toolbox_spec = ARCHITECTURE_ADVISOR_SPEC.toolbox
    assert toolbox_spec is not None

    with pytest.raises(CandidateVersionError, match="version identifier for skill"):
        create_toolbox_candidate(
            project,
            toolbox_spec,
            project_endpoint="https://example.services.ai.azure.com/api/projects/dev",
            metadata={},
        )


def test_skill_upload_body_survives_sdk_multipart_serialization() -> None:
    content = build_skill_upload_body("architecture-decision-brief", b"name: test")
    serialized_files = prepare_multipart_form_data(
        content.as_dict(),
        ["files"],
        ["default"],
    )
    request = build_beta_skills_create_from_files_request(
        "architecture-decision-brief",
        files=serialized_files,
    )

    assert request.files is not None
    assert len(request.files) == 1
    field_name, (filename, body) = request.files[0]
    assert field_name == "files"
    assert filename == "architecture-decision-brief.zip"
    with ZipFile(BytesIO(body)) as archive:
        assert archive.namelist() == ["SKILL.md"]


def test_toolbox_probe_failure_stops_candidate_creation() -> None:
    project = MagicMock()
    project.beta.skills.create_from_files.return_value = SimpleNamespace(version="10")
    project.toolboxes.create_version.return_value = SimpleNamespace(version="20")
    project.send_request.return_value = SimpleNamespace(status_code=404)
    toolbox_spec = ARCHITECTURE_ADVISOR_SPEC.toolbox
    assert toolbox_spec is not None

    with pytest.raises(ToolboxEndpointError, match="HTTP 404"):
        create_toolbox_candidate(
            project,
            toolbox_spec,
            project_endpoint="https://example.services.ai.azure.com/api/projects/dev",
            metadata={},
        )
