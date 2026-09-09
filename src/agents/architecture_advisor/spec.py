"""Microsoft Agent Framework architecture-advisor deployment specification."""

from foundry_demo.agents.architecture_advisor.tools import validate_smoke
from foundry_demo.agents.common.base import (
    HostedAgentSpec,
    MCPToolSpec,
    SkillSpec,
    ToolboxSpec,
)

ARCHITECTURE_ADVISOR_SPEC = HostedAgentSpec(
    name="architecture-advisor",
    description="Provides grounded Azure architecture guidance and decision briefs.",
    smoke_prompt=(
        "Compare Azure Container Apps and Azure Kubernetes Service for hosting a small "
        "team's internal HTTP API with low operational overhead. Cite Microsoft Learn."
    ),
    image_env_var="FOUNDRY_ARCHITECTURE_ADVISOR_IMAGE",
    image_digest_env_var="FOUNDRY_ARCHITECTURE_ADVISOR_IMAGE_DIGEST",
    validate_smoke=validate_smoke,
    smoke_tool_choice="required",
    smoke_setup_prompt=(
        "Load the architecture-decision-brief skill before answering. "
        "Reply with a short confirmation after the skill is loaded."
    ),
    smoke_setup_tool_name="load_skill",
    smoke_max_attempts=3,
    smoke_retry_seconds=10,
    toolbox=ToolboxSpec(
        name="architecture-advisor-toolbox",
        skill=SkillSpec(
            name="architecture-decision-brief",
            package="foundry_demo.agents.architecture_advisor",
            resource_path="skills/architecture-decision-brief/SKILL.md",
        ),
        mcp_tools=(
            MCPToolSpec(
                server_label="microsoft-learn",
                server_url="https://learn.microsoft.com/api/mcp",
                allowed_tools=(
                    "microsoft_docs_search",
                    "microsoft_docs_fetch",
                ),
                require_approval="never",
            ),
        ),
    ),
)
