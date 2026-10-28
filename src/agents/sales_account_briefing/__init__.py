"""Sales account briefing agent package."""

from foundry_demo.agents.sales_account_briefing.instructions import (
    SALES_ACCOUNT_BRIEFING_INSTRUCTIONS,
    SALES_ACCOUNT_BRIEFING_SMOKE_PROMPT,
)
from foundry_demo.agents.sales_account_briefing.spec import SALES_ACCOUNT_BRIEFING_SPEC
from foundry_demo.agents.sales_account_briefing.tools import (
    build_tools,
    validate_smoke,
)

AGENT_SPEC = SALES_ACCOUNT_BRIEFING_SPEC

__all__ = [
    "AGENT_SPEC",
    "SALES_ACCOUNT_BRIEFING_INSTRUCTIONS",
    "SALES_ACCOUNT_BRIEFING_SMOKE_PROMPT",
    "SALES_ACCOUNT_BRIEFING_SPEC",
    "build_tools",
    "validate_smoke",
]
