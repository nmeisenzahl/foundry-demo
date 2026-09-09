"""Foundry Skill packaging and Toolbox delivery lifecycle."""

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from io import BytesIO
from typing import Any
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile

from azure.ai.projects.models import (
    CreateSkillVersionFromFilesBody,
    MCPToolboxTool,
    ToolboxSkillReference,
)
from azure.core.rest import HttpRequest

from foundry_demo.agents.architecture_advisor.toolbox_endpoint import (
    validate_toolbox_endpoint,
)
from foundry_demo.agents.common.base import ToolboxSpec
from foundry_demo.delivery.prompt import CandidateVersionError


@dataclass(frozen=True)
class ToolboxCandidate:
    skill_name: str
    skill_version: str
    skill_sha256: str
    toolbox_name: str
    toolbox_version: str
    toolbox_endpoint: str
    toolbox_sha256: str


class ToolboxEndpointError(RuntimeError):
    """Raised when the immutable Toolbox MCP route is not ready."""


def probe_toolbox_endpoint(project: Any, endpoint: str) -> None:
    """Initialize the MCP route using the authenticated Foundry project pipeline."""
    request = HttpRequest(
        "POST",
        endpoint,
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        content=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "delivery-readiness",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "foundry-demo-delivery",
                        "version": "1.0",
                    },
                },
            }
        ),
    )
    response = project.send_request(request)
    status_code = int(response.status_code)
    if status_code >= 400:
        raise ToolboxEndpointError(
            f"Toolbox MCP initialize failed with HTTP {status_code} for the pinned endpoint."
        )


def _read_skill_bytes(spec: ToolboxSpec) -> bytes:
    subpath = spec.skill.resource_path
    resource = files(spec.skill.package).joinpath(subpath)
    if not resource.is_file():
        raise FileNotFoundError(f"Skill resource not found: {spec.skill.package}/{subpath}")
    content = resource.read_bytes()
    text = content.decode("utf-8")
    expected_header = f"name: {spec.skill.name}"
    if expected_header not in text:
        raise ValueError(
            f"Skill frontmatter in {subpath} does not declare expected name {spec.skill.name!r}."
        )
    return content


def build_skill_upload_body(skill_name: str, skill_bytes: bytes) -> CreateSkillVersionFromFilesBody:
    """Build the SDK multipart body for one root-level SKILL.md archive."""
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", skill_bytes)
    return CreateSkillVersionFromFilesBody(files=[(f"{skill_name}.zip", buffer.getvalue())])


def create_toolbox_candidate(
    project: Any,
    toolbox_spec: ToolboxSpec,
    project_endpoint: str,
    metadata: dict[str, str],
) -> ToolboxCandidate:
    """Package Skill, create Skill version, create Toolbox version, and build immutable endpoint."""
    skill_bytes = _read_skill_bytes(toolbox_spec)
    skill_sha256 = hashlib.sha256(skill_bytes).hexdigest()

    created_skill = project.beta.skills.create_from_files(
        toolbox_spec.skill.name,
        content=build_skill_upload_body(toolbox_spec.skill.name, skill_bytes),
    )
    raw_skill_version = getattr(created_skill, "version", None) or getattr(
        created_skill, "id", None
    )
    if not raw_skill_version or not str(raw_skill_version).strip():
        raise CandidateVersionError(
            f"Foundry did not return a version identifier for skill {toolbox_spec.skill.name!r}."
        )
    skill_version = str(raw_skill_version).strip()

    mcp_tools = [
        MCPToolboxTool(
            server_label=tool.server_label,
            server_url=tool.server_url,
            allowed_tools=list(tool.allowed_tools),
            require_approval=tool.require_approval,
        )
        for tool in toolbox_spec.mcp_tools
    ]

    skills_refs = [
        ToolboxSkillReference(
            name=toolbox_spec.skill.name,
            version=skill_version,
        )
    ]

    created_toolbox = project.toolboxes.create_version(
        name=toolbox_spec.name,
        description="Microsoft Learn tools and architecture decision skill.",
        tools=mcp_tools,
        skills=skills_refs,
        metadata=metadata,
    )
    raw_toolbox_version = getattr(created_toolbox, "version", None) or getattr(
        created_toolbox, "id", None
    )
    if not raw_toolbox_version or not str(raw_toolbox_version).strip():
        raise CandidateVersionError(
            f"Foundry did not return a version identifier for toolbox {toolbox_spec.name!r}."
        )
    toolbox_version = str(raw_toolbox_version).strip()

    raw_endpoint = (
        f"{project_endpoint.rstrip('/')}/toolboxes/{quote(toolbox_spec.name, safe='')}"
        f"/versions/{quote(toolbox_version, safe='')}/mcp?api-version=v1"
    )
    toolbox_endpoint = validate_toolbox_endpoint(raw_endpoint, project_endpoint=project_endpoint)
    probe_toolbox_endpoint(project, toolbox_endpoint)

    toolbox_sha256 = hashlib.sha256(
        f"{toolbox_spec.name}:{toolbox_version}:{skill_sha256}:{toolbox_endpoint}".encode()
    ).hexdigest()

    return ToolboxCandidate(
        skill_name=toolbox_spec.skill.name,
        skill_version=skill_version,
        skill_sha256=skill_sha256,
        toolbox_name=toolbox_spec.name,
        toolbox_version=toolbox_version,
        toolbox_endpoint=toolbox_endpoint,
        toolbox_sha256=toolbox_sha256,
    )
