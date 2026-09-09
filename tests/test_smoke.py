from unittest.mock import MagicMock

import pytest
from azure.core.exceptions import HttpResponseError

from foundry_demo.agents import PromptAgentSpec
from foundry_demo.agents.architecture_advisor import ARCHITECTURE_ADVISOR_SPEC
from foundry_demo.agents.research_assistant import RESEARCH_ASSISTANT_SPEC
from foundry_demo.delivery.smoke import (
    SmokeEvidence,
    SmokeTestError,
    run_hosted_smoke,
    run_prompt_smoke,
)


class DummyAnnotation:
    def __init__(
        self,
        annotation_type: str = "url_citation",
        url: str = "https://example.com",
    ):
        self.type = annotation_type
        self.url = url


class DummyContent:
    def __init__(self, text: str = "Today is 2026-09-08.", annotations=None):
        self.type = "output_text"
        self.text = text
        self.annotations = [DummyAnnotation()] if annotations is None else annotations


class DummyMessage:
    def __init__(self, content=None):
        self.type = "message"
        self.content = content or [DummyContent()]


class DummyWebSearchCall:
    def __init__(self, status: str = "completed"):
        self.type = "web_search_call"
        self.status = status


class DummyResponse:
    def __init__(
        self,
        *,
        response_id: str = "resp-123",
        status: str = "completed",
        output_text: str = "Today is 2026-09-08 [1].",
        output=None,
    ):
        self.id = response_id
        self.status = status
        self.output_text = output_text
        self.output = (
            output
            if output is not None
            else [
                DummyWebSearchCall(status="completed"),
                DummyMessage(),
            ]
        )


def test_run_prompt_smoke_success_contract() -> None:
    openai_client = MagicMock()
    mock_response = DummyResponse()
    openai_client.responses.create.return_value = mock_response

    evidence = run_prompt_smoke(
        openai_client,
        agent_spec=RESEARCH_ASSISTANT_SPEC,
        candidate_version="7",
    )

    openai_client.responses.create.assert_called_once_with(
        input=RESEARCH_ASSISTANT_SPEC.smoke_prompt,
        tool_choice="required",
        extra_body={
            "agent_reference": {
                "type": "agent_reference",
                "name": "research-assistant",
                "version": "7",
            }
        },
    )

    assert isinstance(evidence, SmokeEvidence)
    assert evidence.response_id == "resp-123"
    assert evidence.response_status == "completed"
    assert evidence.output_text_present is True
    assert evidence.output_item_counts == {"web_search_call": 1, "message": 1}
    assert evidence.completed_output_item_counts == {"web_search_call": 1}
    assert evidence.annotation_counts == {"url_citation": 1}


def test_run_prompt_smoke_omits_tool_choice_when_not_required() -> None:
    agent_spec = PromptAgentSpec(
        name="plain-agent",
        description="Agent without tools.",
        instructions="Answer briefly.",
        tools_factory=list,
        smoke_prompt="Say hello.",
    )
    openai_client = MagicMock()
    openai_client.responses.create.return_value = DummyResponse(
        output=[DummyMessage(content=[DummyContent(annotations=[])])]
    )

    run_prompt_smoke(openai_client, agent_spec=agent_spec, candidate_version="1")

    openai_client.responses.create.assert_called_once_with(
        input="Say hello.",
        extra_body={
            "agent_reference": {
                "type": "agent_reference",
                "name": "plain-agent",
                "version": "1",
            }
        },
    )


class DummyFunctionCall:
    def __init__(self, name: str, status: str = "completed"):
        self.type = "function_call"
        self.name = name
        self.status = status


def test_run_hosted_smoke_pins_candidate_session() -> None:
    project = MagicMock()
    project.agents.create_session.return_value.agent_session_id = "session-123"
    openai_client = MagicMock()
    openai_client.responses.create.side_effect = [
        DummyResponse(
            response_id="resp-setup",
            output_text="Skill loaded.",
            output=[
                DummyMessage(content=[DummyContent(annotations=[])]),
                DummyFunctionCall("load_skill"),
            ],
        ),
        DummyResponse(
            output_text="Grounded comparison.",
            output=[
                DummyMessage(
                    content=[
                        DummyContent(
                            annotations=[
                                DummyAnnotation(
                                    url="https://learn.microsoft.com/azure/container-apps"
                                )
                            ]
                        )
                    ]
                ),
                DummyFunctionCall("microsoft_docs_search"),
            ],
        ),
    ]

    run_hosted_smoke(
        project,
        openai_client,
        agent_spec=ARCHITECTURE_ADVISOR_SPEC,
        candidate_version="2",
    )

    create_call = project.agents.create_session.call_args
    assert create_call.kwargs["agent_name"] == "architecture-advisor"
    assert create_call.kwargs["version_indicator"].agent_version == "2"
    assert openai_client.responses.create.call_count == 2
    assert openai_client.responses.create.call_args_list[0].kwargs == {
        "input": ARCHITECTURE_ADVISOR_SPEC.smoke_setup_prompt,
        "extra_body": {"agent_session_id": "session-123"},
        "tool_choice": {"type": "function", "name": "load_skill"},
    }
    assert openai_client.responses.create.call_args_list[1].kwargs == {
        "input": ARCHITECTURE_ADVISOR_SPEC.smoke_prompt,
        "extra_body": {"agent_session_id": "session-123"},
        "tool_choice": "required",
    }


def test_run_hosted_smoke_fails_without_session_id() -> None:
    project = MagicMock()
    project.agents.create_session.return_value.agent_session_id = ""
    openai_client = MagicMock()

    with pytest.raises(SmokeTestError, match="session identifier") as exc_info:
        run_hosted_smoke(
            project,
            openai_client,
            agent_spec=ARCHITECTURE_ADVISOR_SPEC,
            candidate_version="2",
        )

    assert exc_info.value.evidence.response_status == "not_started"
    openai_client.responses.create.assert_not_called()


def test_run_hosted_smoke_retries_rbac_propagation(monkeypatch) -> None:
    project = MagicMock()
    project.agents.create_session.return_value.agent_session_id = "session-123"
    openai_client = MagicMock()
    forbidden_response = MagicMock(status_code=403, reason="Forbidden", headers={})
    openai_client.responses.create.side_effect = [
        HttpResponseError(response=forbidden_response),
        HttpResponseError(response=forbidden_response),
        DummyResponse(
            output_text="Skill loaded.",
            output=[DummyMessage(), DummyFunctionCall("load_skill")],
        ),
        DummyResponse(
            output_text="Grounded comparison.",
            output=[
                DummyMessage(
                    content=[
                        DummyContent(
                            annotations=[
                                DummyAnnotation(
                                    url="https://learn.microsoft.com/azure/container-apps"
                                )
                            ]
                        )
                    ]
                ),
                DummyFunctionCall("microsoft_docs_fetch"),
            ],
        ),
    ]
    sleeps: list[float] = []
    monkeypatch.setattr("foundry_demo.delivery.smoke.time.sleep", sleeps.append)

    evidence = run_hosted_smoke(
        project,
        openai_client,
        agent_spec=ARCHITECTURE_ADVISOR_SPEC,
        candidate_version="2",
    )

    assert evidence.completed_tool_name_counts == {
        "load_skill": 1,
        "microsoft_docs_fetch": 1,
    }
    assert project.agents.create_session.call_count == 3
    assert sleeps == [10, 10]


@pytest.mark.parametrize(
    ("bad_status", "output_text", "output", "expected_msg"),
    [
        (
            "failed",
            "Some text",
            [DummyWebSearchCall(), DummyMessage()],
            "status is 'failed'",
        ),
        (
            "completed",
            "   \n\t  ",
            [DummyWebSearchCall(), DummyMessage()],
            "empty or blank",
        ),
        (
            "completed",
            "Some text",
            [DummyMessage()],
            "No completed web_search_call",
        ),
        (
            "completed",
            "Some text",
            [DummyWebSearchCall(status="in_progress"), DummyMessage()],
            "No completed web_search_call",
        ),
        (
            "completed",
            "Some text citing https://example.com without structured annotation",
            [DummyWebSearchCall(), DummyMessage(content=[DummyContent(annotations=[])])],
            "No url_citation annotations",
        ),
    ],
)
def test_run_prompt_smoke_failure_conditions(
    bad_status: str, output_text: str, output: list, expected_msg: str
) -> None:
    openai_client = MagicMock()
    openai_client.responses.create.return_value = DummyResponse(
        status=bad_status,
        output_text=output_text,
        output=output,
    )

    with pytest.raises(SmokeTestError) as exc_info:
        run_prompt_smoke(
            openai_client,
            agent_spec=RESEARCH_ASSISTANT_SPEC,
            candidate_version="7",
        )

    assert expected_msg in str(exc_info.value)
    assert isinstance(exc_info.value.evidence, SmokeEvidence)
    # Ensure raw output_text or prompt is not exposed in the error message
    assert "https://example.com without structured annotation" not in str(exc_info.value)
