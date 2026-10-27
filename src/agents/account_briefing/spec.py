"""Declarative account briefing agent with native Web Search."""

from foundry_demo.agents.account_briefing.instructions import (
    ACCOUNT_BRIEFING_INSTRUCTIONS,
    ACCOUNT_BRIEFING_SMOKE_PROMPT,
)
from foundry_demo.agents.account_briefing.tools import build_tools, validate_smoke
from foundry_demo.agents.common.base import PromptAgentSpec

ACCOUNT_BRIEFING_SPEC = PromptAgentSpec(
    name="account-briefing",
    description="Sales account briefings grounded in current public Web Search.",
    instructions=ACCOUNT_BRIEFING_INSTRUCTIONS,
    tools_factory=build_tools,
    smoke_prompt=ACCOUNT_BRIEFING_SMOKE_PROMPT,
    validate_smoke=validate_smoke,
    smoke_tool_choice="required",
)
