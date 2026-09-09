"""Research assistant agent specification with Web Search capabilities."""

from foundry_demo.agents.common.base import PromptAgentSpec
from foundry_demo.agents.research_assistant.instructions import (
    RESEARCH_ASSISTANT_INSTRUCTIONS,
    RESEARCH_ASSISTANT_SMOKE_PROMPT,
)
from foundry_demo.agents.research_assistant.tools import (
    build_tools,
    validate_smoke,
)

RESEARCH_ASSISTANT_SPEC = PromptAgentSpec(
    name="research-assistant",
    description="Research assistant with required public Web Search.",
    instructions=RESEARCH_ASSISTANT_INSTRUCTIONS,
    tools_factory=build_tools,
    smoke_prompt=RESEARCH_ASSISTANT_SMOKE_PROMPT,
    validate_smoke=validate_smoke,
    smoke_tool_choice="required",
)
