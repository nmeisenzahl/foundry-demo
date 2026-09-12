"""Release notes writer agent specification on an admin-connected model."""

from foundry_demo.agents.common.base import PromptAgentSpec
from foundry_demo.agents.release_notes_writer.instructions import (
    RELEASE_NOTES_WRITER_INSTRUCTIONS,
    RELEASE_NOTES_WRITER_SMOKE_PROMPT,
)
from foundry_demo.agents.release_notes_writer.validation import validate_smoke

RELEASE_NOTES_WRITER_SPEC = PromptAgentSpec(
    name="release-notes-writer",
    description=(
        "Turns a raw change list into structured release notes, running on an "
        "admin-connected model governed by Token Control."
    ),
    instructions=RELEASE_NOTES_WRITER_INSTRUCTIONS,
    smoke_prompt=RELEASE_NOTES_WRITER_SMOKE_PROMPT,
    validate_smoke=validate_smoke,
    # The model lives behind a Foundry ModelGateway connection, so the
    # reference is "<connection>/<model>" rather than a deployment name.
    connected_model_env_var="FOUNDRY_CONNECTED_MODEL_DEPLOYMENT_NAME",
    # Connected models cannot serve the hosted tools, so forcing a tool call
    # would fail by construction.
    smoke_tool_choice=None,
)

AGENT_SPEC = RELEASE_NOTES_WRITER_SPEC
