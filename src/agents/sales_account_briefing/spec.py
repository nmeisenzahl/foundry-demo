"""Sales account briefing prompt agent specification with Web Search."""

from foundry_demo.agents.common.base import PromptAgentSpec
from foundry_demo.agents.sales_account_briefing.instructions import (
    SALES_ACCOUNT_BRIEFING_INSTRUCTIONS,
    SALES_ACCOUNT_BRIEFING_SMOKE_PROMPT,
)
from foundry_demo.agents.sales_account_briefing.tools import (
    build_tools,
    validate_smoke,
)

SALES_ACCOUNT_BRIEFING_SPEC = PromptAgentSpec(
    name="sales-account-briefing",
    description="Builds cited pre-meeting sales account briefings from public Web Search.",
    instructions=SALES_ACCOUNT_BRIEFING_INSTRUCTIONS,
    tools_factory=build_tools,
    smoke_prompt=SALES_ACCOUNT_BRIEFING_SMOKE_PROMPT,
    validate_smoke=validate_smoke,
    smoke_tool_choice="required",
)
