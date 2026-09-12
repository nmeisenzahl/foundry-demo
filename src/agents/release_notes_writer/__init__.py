"""Release notes writer package."""

from foundry_demo.agents.release_notes_writer.instructions import (
    RELEASE_NOTES_WRITER_INSTRUCTIONS,
    RELEASE_NOTES_WRITER_SMOKE_PROMPT,
)
from foundry_demo.agents.release_notes_writer.spec import RELEASE_NOTES_WRITER_SPEC
from foundry_demo.agents.release_notes_writer.validation import validate_smoke

AGENT_SPEC = RELEASE_NOTES_WRITER_SPEC

__all__ = [
    "AGENT_SPEC",
    "RELEASE_NOTES_WRITER_INSTRUCTIONS",
    "RELEASE_NOTES_WRITER_SMOKE_PROMPT",
    "RELEASE_NOTES_WRITER_SPEC",
    "validate_smoke",
]
