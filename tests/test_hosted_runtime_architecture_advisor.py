import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip(
    "agent_framework",
    reason="hosted runtime deps live in src/agents/architecture_advisor",
)

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[1]
        / "src"
        / "agents"
        / "architecture_advisor"
    ),
)

import main


@patch("main.Agent")
@patch("main.FoundryToolbox")
@patch("main.DefaultAzureCredential")
@patch("main.FoundryChatClient")
def test_build_agent(mock_client, mock_credential, mock_toolbox, mock_agent, monkeypatch):
    endpoint = (
        "https://example.services.ai.azure.com/api/projects/dev/"
        "toolboxes/architecture-advisor-toolbox/versions/7/mcp?api-version=v1"
    )
    monkeypatch.setenv(
        "FOUNDRY_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/dev"
    )
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "model")
    monkeypatch.setenv("TOOLBOX_ENDPOINT", endpoint)

    result = main.build_agent()
    mock_client.assert_called_once_with(
        project_endpoint="https://example.services.ai.azure.com/api/projects/dev",
        model="model",
        credential=mock_credential.return_value,
    )
    mock_toolbox.assert_called_once_with(
        mock_credential.return_value,
        url=endpoint,
    )
    mock_toolbox.return_value.as_skills_provider.assert_called_once_with(
        disable_load_skill_approval=True,
        disable_read_skill_resource_approval=True,
    )
    assert mock_agent.call_args.kwargs["tools"] is mock_toolbox.return_value
    assert mock_agent.call_args.kwargs["context_providers"] == [
        mock_toolbox.return_value.as_skills_provider.return_value
    ]
    assert mock_agent.call_args.kwargs["default_options"] == {"store": False}
    assert result is mock_agent.return_value


def test_validate_toolbox_endpoint_rejects_mismatch():
    with pytest.raises(ValueError, match="host"):
        main.validate_toolbox_endpoint(
            "https://other.services.ai.azure.com/api/projects/dev/toolboxes/t/versions/1/mcp",
            project_endpoint="https://example.services.ai.azure.com/api/projects/dev",
        )


@patch("main.ResponsesHostServer")
@patch("main.build_agent")
def test_main_runs_server(mock_build_agent, mock_server):
    main.main()
    mock_server.assert_called_once_with(mock_build_agent.return_value)
    mock_server.return_value.run.assert_called_once_with()


def test_runtime_configuration():
    from azure.ai.agentserver.core.tasks import resilient_tasks_enabled

    assert resilient_tasks_enabled() is True
    assert os.environ.get("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING") == "true"

