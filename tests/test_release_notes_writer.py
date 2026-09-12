"""The connected-model release notes writer."""

import pytest

from foundry_demo.agents import get_agent, list_agents
from foundry_demo.agents.release_notes_writer import (
    RELEASE_NOTES_WRITER_SPEC,
    validate_smoke,
)
from foundry_demo.delivery.smoke import SmokeEvidence, SmokeTestError


def _evidence(**overrides: object) -> SmokeEvidence:
    defaults: dict[str, object] = {
        "response_id": "resp_1",
        "response_status": "completed",
        "output_text_present": True,
        "output_item_counts": {"message": 1},
        "completed_output_item_counts": {"message": 1},
        "annotation_counts": {},
        "completed_tool_name_counts": {},
        "source_domain_counts": {},
    }
    defaults.update(overrides)
    return SmokeEvidence(**defaults)  # type: ignore[arg-type]


def test_the_agent_is_registered() -> None:
    assert "release-notes-writer" in list_agents()
    assert get_agent("release-notes-writer") is RELEASE_NOTES_WRITER_SPEC


def test_the_agent_runs_on_a_connected_model_and_declares_no_tools() -> None:
    assert (
        RELEASE_NOTES_WRITER_SPEC.connected_model_env_var
        == "FOUNDRY_CONNECTED_MODEL_DEPLOYMENT_NAME"
    )
    # Connected models cannot serve Web Search, Bing grounding, SharePoint,
    # Memory Search, Browser Automation, or Fabric.
    assert RELEASE_NOTES_WRITER_SPEC.tools_factory() == []
    assert RELEASE_NOTES_WRITER_SPEC.smoke_tool_choice is None


def test_a_plain_message_response_passes() -> None:
    validate_smoke(_evidence())


def test_a_response_without_a_message_fails() -> None:
    with pytest.raises(SmokeTestError, match="message output item"):
        validate_smoke(_evidence(output_item_counts={}))


def test_a_response_that_called_a_tool_fails() -> None:
    with pytest.raises(SmokeTestError, match="no tool calls"):
        validate_smoke(_evidence(completed_tool_name_counts={"web_search": 1}))
