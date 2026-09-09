"""Research assistant package."""

from foundry_demo.agents.research_assistant.instructions import (
    RESEARCH_ASSISTANT_INSTRUCTIONS,
    RESEARCH_ASSISTANT_SMOKE_PROMPT,
)
from foundry_demo.agents.research_assistant.spec import RESEARCH_ASSISTANT_SPEC
from foundry_demo.agents.research_assistant.tools import (
    build_tools,
    validate_smoke,
)

AGENT_SPEC = RESEARCH_ASSISTANT_SPEC

__all__ = [
    "AGENT_SPEC",
    "RESEARCH_ASSISTANT_INSTRUCTIONS",
    "RESEARCH_ASSISTANT_SMOKE_PROMPT",
    "RESEARCH_ASSISTANT_SPEC",
    "build_tools",
    "validate_smoke",
]
