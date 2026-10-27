"""Account briefing package."""

from foundry_demo.agents.account_briefing.instructions import (
    ACCOUNT_BRIEFING_INSTRUCTIONS,
    ACCOUNT_BRIEFING_SMOKE_PROMPT,
)
from foundry_demo.agents.account_briefing.spec import ACCOUNT_BRIEFING_SPEC
from foundry_demo.agents.account_briefing.tools import build_tools, validate_smoke

AGENT_SPEC = ACCOUNT_BRIEFING_SPEC

__all__ = [
    "AGENT_SPEC",
    "ACCOUNT_BRIEFING_INSTRUCTIONS",
    "ACCOUNT_BRIEFING_SMOKE_PROMPT",
    "ACCOUNT_BRIEFING_SPEC",
    "build_tools",
    "validate_smoke",
]
