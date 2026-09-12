import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from azure.core.exceptions import (
    ClientAuthenticationError,
    ResourceNotFoundError,
    ServiceRequestError,
)

from foundry_demo.agents import AgentSpecError
from foundry_demo.agents.architecture_advisor import ARCHITECTURE_ADVISOR_SPEC
from foundry_demo.agents.research_assistant import RESEARCH_ASSISTANT_SPEC
from foundry_demo.delivery.cli import (
    AGENT_NAME,
    deploy,
    main,
)
from foundry_demo.delivery.config import ConfigurationError, DeploymentConfig
from foundry_demo.delivery.prompt import CandidateVersionError
from foundry_demo.delivery.records import AuditMetadata, DeploymentRecorder
from foundry_demo.delivery.release import (
    RoutingError,
    RoutingState,
    fixed_selector,
    pin_version,
    resolve_current_route,
)
from foundry_demo.delivery.smoke import SmokeTestError


class DummyRule:
    def __init__(self, version: str = "1", percentage: int = 100, rule_type: str = "FixedRatio"):
        self.agent_version = version
        self.traffic_percentage = percentage
        self.type = rule_type


class DummySelector:
    def __init__(self, rules=None):
        self.version_selection_rules = rules


class DummyEndpoint:
    def __init__(self, selector=None):
        self.version_selector = selector


class DummyAgent:
    def __init__(self, endpoint=None):
        self.agent_endpoint = endpoint


class DummyVersionItem:
    def __init__(self, version: str, status: str = "active"):
        self.version = version
        self.status = status


def test_resolve_current_route_first_deployment() -> None:
    agents = MagicMock()
    agents.get.side_effect = ResourceNotFoundError("Agent does not exist")

    state = resolve_current_route(agents, agent_name=AGENT_NAME)

    assert isinstance(state, RoutingState)
    assert state.agent_exists is False
    assert state.previous_active_version is None
    assert state.pinned_during_resolution is False
    agents.get.assert_called_once_with(agent_name=AGENT_NAME)
    agents.list_versions.assert_not_called()
    agents.update_details.assert_not_called()


def test_resolve_current_route_default_latest_pins_and_returns() -> None:
    agents = MagicMock()
    # Agent exists but has no selector (default-latest)
    agents.get.return_value = DummyAgent(endpoint=DummyEndpoint(selector=None))
    agents.list_versions.return_value = [DummyVersionItem("3")]
    agents.get_version.return_value = DummyVersionItem("3")

    state = resolve_current_route(agents, agent_name=AGENT_NAME)

    assert state.agent_exists is True
    assert state.previous_active_version == "3"
    assert state.pinned_during_resolution is True

    agents.list_versions.assert_called_once_with(agent_name=AGENT_NAME, limit=1, order="desc")
    agents.update_details.assert_called_once()
    call_kwargs = agents.update_details.call_args.kwargs
    assert call_kwargs["agent_name"] == AGENT_NAME
    rule = call_kwargs["agent_endpoint"].version_selector.version_selection_rules[0]
    assert rule.agent_version == "3"
    assert rule.traffic_percentage == 100


def test_resolve_current_route_default_latest_rule_pins_and_returns() -> None:
    agents = MagicMock()
    # Agent exists with @latest rule
    agents.get.return_value = DummyAgent(
        endpoint=DummyEndpoint(
            selector=DummySelector(rules=[DummyRule(version="@latest", percentage=100)])
        )
    )
    agents.list_versions.return_value = [DummyVersionItem("3")]
    agents.get_version.return_value = DummyVersionItem("3")

    state = resolve_current_route(agents, agent_name=AGENT_NAME)

    assert state.agent_exists is True
    assert state.previous_active_version == "3"
    assert state.pinned_during_resolution is True

    agents.list_versions.assert_called_once_with(agent_name=AGENT_NAME, limit=1, order="desc")
    agents.update_details.assert_called_once()
    call_kwargs = agents.update_details.call_args.kwargs
    assert call_kwargs["agent_name"] == AGENT_NAME
    rule = call_kwargs["agent_endpoint"].version_selector.version_selection_rules[0]
    assert rule.agent_version == "3"
    assert rule.traffic_percentage == 100


def test_resolve_current_route_fixed_100_no_update() -> None:
    agents = MagicMock()
    agents.get.return_value = DummyAgent(
        endpoint=DummyEndpoint(
            selector=DummySelector(rules=[DummyRule(version="4", percentage=100)])
        )
    )
    agents.get_version.return_value = DummyVersionItem("4")

    state = resolve_current_route(agents, agent_name=AGENT_NAME)

    assert state.agent_exists is True
    assert state.previous_active_version == "4"
    assert state.pinned_during_resolution is False
    agents.list_versions.assert_not_called()
    agents.update_details.assert_not_called()


def test_resolve_current_route_rejects_missing_fixed_version() -> None:
    agents = MagicMock()
    agents.get.return_value = DummyAgent(
        endpoint=DummyEndpoint(
            selector=DummySelector(rules=[DummyRule(version="4", percentage=100)])
        )
    )
    agents.get_version.side_effect = ResourceNotFoundError("Version does not exist")

    with pytest.raises(RoutingError, match="does not exist"):
        resolve_current_route(agents, agent_name=AGENT_NAME)

    agents.update_details.assert_not_called()


def test_resolve_current_route_rejects_non_active_version() -> None:
    agents = MagicMock()
    agents.get.return_value = DummyAgent(
        endpoint=DummyEndpoint(
            selector=DummySelector(rules=[DummyRule(version="4", percentage=100)])
        )
    )
    agents.get_version.return_value = DummyVersionItem("4", status="creating")

    with pytest.raises(RoutingError, match="creating"):
        resolve_current_route(agents, agent_name=AGENT_NAME)

    agents.update_details.assert_not_called()


@pytest.mark.parametrize(
    "bad_rules",
    [
        [],  # empty rule list when selector present but has no rules and no versions
        [DummyRule(version="1", percentage=50), DummyRule(version="2", percentage=50)],  # split
        [DummyRule(version="1", percentage=80)],  # not 100
        [DummyRule(version="", percentage=100)],  # empty version
        [DummyRule(version="   ", percentage=100)],  # blank version
        [DummyRule(version="1", percentage=100, rule_type="UnknownRule")],  # wrong rule type
    ],
)
def test_resolve_current_route_unsupported_selector_raises(bad_rules: list) -> None:
    agents = MagicMock()
    agents.get.return_value = DummyAgent(
        endpoint=DummyEndpoint(selector=DummySelector(rules=bad_rules))
    )

    with pytest.raises(RoutingError):
        resolve_current_route(agents, agent_name=AGENT_NAME)

    agents.update_details.assert_not_called()


def test_fixed_selector_contract() -> None:
    endpoint_config = fixed_selector("5")
    rules = endpoint_config.version_selector.version_selection_rules
    assert len(rules) == 1
    assert rules[0].agent_version == "5"
    assert rules[0].traffic_percentage == 100


def test_pin_version_contract() -> None:
    agents = MagicMock()
    pin_version(agents, agent_name="test-agent", version="6")

    agents.update_details.assert_called_once()
    assert agents.update_details.call_args.kwargs["agent_name"] == "test-agent"


def _make_config(_tmp_path: Path) -> DeploymentConfig:
    return DeploymentConfig(
        project_endpoint="https://example.services.ai.azure.com/api/projects/example-dev",
        model_deployment_name="example-model",
        temperature=None,
    )


def _make_hosted_config(_tmp_path: Path) -> DeploymentConfig:
    return DeploymentConfig(
        project_endpoint="https://example.services.ai.azure.com/api/projects/example-dev",
        model_deployment_name="example-model",
        temperature=None,
        environ={
            "FOUNDRY_ARCHITECTURE_ADVISOR_IMAGE": "example.azurecr.io/architecture-advisor:v1",
            "FOUNDRY_ARCHITECTURE_ADVISOR_IMAGE_DIGEST": "sha256:" + "b" * 64,
        },
    )


class _DummyAnnotation:
    type = "url_citation"
    url = "https://example.com"


class _DummyContent:
    type = "output_text"
    text = "Current date is 2026-09-08."
    annotations = [_DummyAnnotation()]


class _DummyMessage:
    type = "message"
    content = [_DummyContent()]


class _DummyWebSearchCall:
    type = "web_search_call"
    status = "completed"


class _DummySmokeResponse:
    id = "resp-123"
    status = "completed"
    output_text = "Current date is 2026-09-08."
    output = [_DummyWebSearchCall(), _DummyMessage()]


@patch("foundry_demo.delivery.cli.AIProjectClient")
@patch("foundry_demo.delivery.cli.DefaultAzureCredential")
def test_deploy_first_deployment_order(
    mock_cred_cls: MagicMock,
    mock_client_cls: MagicMock,
    tmp_path: Path,
) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    agents = mock_client.agents
    agents.get.side_effect = ResourceNotFoundError("Agent does not exist")
    agents.create_version.return_value = DummyVersionItem("1")

    openai_client = MagicMock()
    openai_client.responses.create.return_value = _DummySmokeResponse()
    mock_client.get_openai_client.return_value = openai_client

    config = _make_config(tmp_path)
    with DeploymentRecorder(
        agent_name=AGENT_NAME,
        record_dir=tmp_path / "records",
        audit_metadata=AuditMetadata(
            git_sha="abc123",
            repository="org/repo",
            workflow_url="https://github.com/org/repo/actions/runs/42",
            run_id="42",
            run_attempt="3",
            git_dirty=False,
        ),
    ) as recorder:
        deploy(RESEARCH_ASSISTANT_SPEC, config, recorder)

    assert recorder.record_path.is_file()
    record = json.loads(recorder.record_path.read_text(encoding="utf-8"))
    assert record["schema_version"] == "4"
    assert record["project_endpoint"] == config.project_endpoint
    assert record["agent_kind"] == "prompt"
    assert record["artifact_type"] == "prompt_definition"
    assert record["status"] == "succeeded"
    assert record["phase"] == "cutover_complete"
    assert record["candidate_version"] == "1"
    assert record["previous_active_version"] is None
    assert len(record["definition_sha256"]) == 64
    assert record["smoke"]["completed_output_item_counts"]["web_search_call"] == 1
    assert record["smoke"]["annotation_counts"]["url_citation"] == 1

    metadata = agents.create_version.call_args.kwargs["metadata"]
    assert metadata["deployment_id"] == recorder.deployment_id
    assert len(metadata["definition_sha256"]) == 64
    assert metadata["git_sha"] == "abc123"
    assert metadata["repository"] == "org/repo"
    assert metadata["workflow_url"] == "https://github.com/org/repo/actions/runs/42"
    assert metadata["run_id"] == "42"
    assert metadata["run_attempt"] == "3"
    assert metadata["git_dirty"] == "false"

    # In first deployment, update_details is called once (for cutover)
    agents.update_details.assert_called_once()
    kwargs = agents.update_details.call_args.kwargs
    rule = kwargs["agent_endpoint"].version_selector.version_selection_rules[0]
    assert rule.agent_version == "1"
    assert rule.traffic_percentage == 100


@patch("foundry_demo.delivery.cli.AIProjectClient")
@patch("foundry_demo.delivery.cli.DefaultAzureCredential")
def test_deploy_later_deployment_with_default_latest_pins_and_cuts_over(
    mock_cred_cls: MagicMock,
    mock_client_cls: MagicMock,
    tmp_path: Path,
) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    agents = mock_client.agents
    # Agent exists with default-latest (selector=None)
    agents.get.return_value = DummyAgent(endpoint=DummyEndpoint(selector=None))
    agents.list_versions.return_value = [DummyVersionItem("1")]
    agents.get_version.return_value = DummyVersionItem("1")
    agents.create_version.return_value = DummyVersionItem("2")

    openai_client = MagicMock()
    openai_client.responses.create.return_value = _DummySmokeResponse()
    mock_client.get_openai_client.return_value = openai_client

    config = _make_config(tmp_path)
    with DeploymentRecorder(
        agent_name=AGENT_NAME,
        record_dir=tmp_path / "records",
    ) as recorder:
        deploy(RESEARCH_ASSISTANT_SPEC, config, recorder)

    assert recorder.record_path.is_file()
    record = json.loads(recorder.record_path.read_text(encoding="utf-8"))
    assert record["status"] == "succeeded"
    assert record["candidate_version"] == "2"
    assert record["previous_active_version"] == "1"

    # update_details called twice: first to pin "1", second to cutover to "2"
    assert agents.update_details.call_count == 2
    first_ep = agents.update_details.call_args_list[0].kwargs["agent_endpoint"]
    first_rule = first_ep.version_selector.version_selection_rules[0]
    second_ep = agents.update_details.call_args_list[1].kwargs["agent_endpoint"]
    second_rule = second_ep.version_selector.version_selection_rules[0]
    assert first_rule.agent_version == "1"
    assert second_rule.agent_version == "2"


@patch("foundry_demo.delivery.cli.AIProjectClient")
@patch("foundry_demo.delivery.cli.DefaultAzureCredential")
def test_deploy_auth_failure_writes_failed_record(
    mock_cred_cls: MagicMock,
    mock_client_cls: MagicMock,
    tmp_path: Path,
) -> None:
    mock_client_cls.side_effect = ClientAuthenticationError("Authentication failed")

    config = _make_config(tmp_path)
    with (
        pytest.raises(ClientAuthenticationError),
        DeploymentRecorder(
            agent_name=AGENT_NAME,
            record_dir=tmp_path / "records",
        ) as recorder,
    ):
        deploy(RESEARCH_ASSISTANT_SPEC, config, recorder)

    assert recorder.record_path.is_file()
    record = json.loads(recorder.record_path.read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert "Authentication failed" in record["failure_message"]
    assert record["candidate_version"] is None


@patch("foundry_demo.delivery.cli.AIProjectClient")
@patch("foundry_demo.delivery.cli.DefaultAzureCredential")
def test_deploy_unsupported_selector_writes_failed_record_and_no_candidate(
    mock_cred_cls: MagicMock,
    mock_client_cls: MagicMock,
    tmp_path: Path,
) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    agents = mock_client.agents
    # 50/50 split routing -> unsupported
    agents.get.return_value = DummyAgent(
        endpoint=DummyEndpoint(
            selector=DummySelector(rules=[DummyRule(version="1", percentage=50)])
        )
    )

    config = _make_config(tmp_path)
    with (
        pytest.raises(RoutingError),
        DeploymentRecorder(
            agent_name=AGENT_NAME,
            record_dir=tmp_path / "records",
        ) as recorder,
    ):
        deploy(RESEARCH_ASSISTANT_SPEC, config, recorder)

    agents.create_version.assert_not_called()
    assert recorder.record_path.is_file()
    record = json.loads(recorder.record_path.read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["candidate_version"] is None


@patch("foundry_demo.delivery.cli.AIProjectClient")
@patch("foundry_demo.delivery.cli.DefaultAzureCredential")
def test_deploy_first_smoke_failure_never_cuts_over(
    mock_cred_cls: MagicMock,
    mock_client_cls: MagicMock,
    tmp_path: Path,
) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    agents = mock_client.agents
    agents.get.side_effect = ResourceNotFoundError("Agent does not exist")
    agents.create_version.return_value = DummyVersionItem("1")

    openai_client = MagicMock()
    # Smoke fails (no web search call)
    failed_smoke_resp = MagicMock(
        id="resp-err",
        status="completed",
        output_text="no search call",
        output=[],
    )
    openai_client.responses.create.return_value = failed_smoke_resp
    mock_client.get_openai_client.return_value = openai_client

    config = _make_config(tmp_path)
    with (
        pytest.raises(SmokeTestError),
        DeploymentRecorder(
            agent_name=AGENT_NAME,
            record_dir=tmp_path / "records",
        ) as recorder,
    ):
        deploy(RESEARCH_ASSISTANT_SPEC, config, recorder)

    # In first deployment smoke failure, update_details MUST NOT be called!
    agents.update_details.assert_not_called()
    assert recorder.record_path.is_file()
    record = json.loads(recorder.record_path.read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["candidate_version"] == "1"
    assert record["smoke"]["output_text_present"] is True
    assert record["smoke"]["completed_output_item_counts"] == {}
    assert record["smoke"]["annotation_counts"] == {}


@patch("foundry_demo.delivery.cli.AIProjectClient")
@patch("foundry_demo.delivery.cli.DefaultAzureCredential")
def test_deploy_later_smoke_failure_preserves_previous_pin(
    mock_cred_cls: MagicMock,
    mock_client_cls: MagicMock,
    tmp_path: Path,
) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    agents = mock_client.agents
    # Agent exists with fixed rule version "1"
    agents.get.return_value = DummyAgent(
        endpoint=DummyEndpoint(
            selector=DummySelector(rules=[DummyRule(version="1", percentage=100)])
        )
    )
    agents.get_version.return_value = DummyVersionItem("1")
    agents.create_version.return_value = DummyVersionItem("2")

    openai_client = MagicMock()
    # Smoke fails
    failed_smoke_resp = MagicMock(
        id="resp-err",
        status="completed",
        output_text="no search call",
        output=[],
    )
    openai_client.responses.create.return_value = failed_smoke_resp
    mock_client.get_openai_client.return_value = openai_client

    config = _make_config(tmp_path)
    with (
        pytest.raises(SmokeTestError),
        DeploymentRecorder(
            agent_name=AGENT_NAME,
            record_dir=tmp_path / "records",
        ) as recorder,
    ):
        deploy(RESEARCH_ASSISTANT_SPEC, config, recorder)

    # Because version "1" was already fixed, and candidate "2" failed smoke,
    # update_details should NEVER be called!
    agents.update_details.assert_not_called()
    assert recorder.record_path.is_file()
    record = json.loads(recorder.record_path.read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["candidate_version"] == "2"
    assert record["previous_active_version"] == "1"


@patch("foundry_demo.delivery.cli.AIProjectClient")
@patch("foundry_demo.delivery.cli.DefaultAzureCredential")
def test_deploy_cutover_failure_writes_failed_record(
    mock_cred_cls: MagicMock,
    mock_client_cls: MagicMock,
    tmp_path: Path,
) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    agents = mock_client.agents
    agents.get.side_effect = ResourceNotFoundError("Agent does not exist")
    agents.create_version.return_value = DummyVersionItem("1")

    openai_client = MagicMock()
    openai_client.responses.create.return_value = _DummySmokeResponse()
    mock_client.get_openai_client.return_value = openai_client

    agents.update_details.side_effect = RuntimeError("Azure update_details failed")

    config = _make_config(tmp_path)
    with (
        pytest.raises(RuntimeError, match="Azure update_details failed"),
        DeploymentRecorder(
            agent_name=AGENT_NAME,
            record_dir=tmp_path / "records",
        ) as recorder,
    ):
        deploy(RESEARCH_ASSISTANT_SPEC, config, recorder)

    assert recorder.record_path.is_file()
    record = json.loads(recorder.record_path.read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert "Azure update_details failed" in record["failure_message"]


@patch("foundry_demo.delivery.cli.AIProjectClient")
@patch("foundry_demo.delivery.cli.DefaultAzureCredential")
def test_deploy_transport_failure_confirms_candidate_by_readback(
    mock_cred_cls: MagicMock,
    mock_client_cls: MagicMock,
    tmp_path: Path,
) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    agents = mock_client.agents
    agents.get.side_effect = [
        ResourceNotFoundError("Agent does not exist"),
        DummyAgent(
            endpoint=DummyEndpoint(
                selector=DummySelector(rules=[DummyRule(version="1", percentage=100)])
            )
        ),
    ]
    agents.get_version.return_value = DummyVersionItem("1")
    agents.create_version.return_value = DummyVersionItem("1")
    agents.update_details.side_effect = ServiceRequestError("request timed out")
    openai_client = MagicMock()
    openai_client.responses.create.return_value = _DummySmokeResponse()
    mock_client.get_openai_client.return_value = openai_client

    config = _make_config(tmp_path)
    with DeploymentRecorder(
        agent_name=AGENT_NAME,
        record_dir=tmp_path / "records",
    ) as recorder:
        deploy(RESEARCH_ASSISTANT_SPEC, config, recorder)

    record = json.loads(recorder.record_path.read_text(encoding="utf-8"))
    assert record["status"] == "succeeded"
    assert record["cutover_outcome"] == "confirmed_after_transport_error"
    assert record["observed_active_version"] == "1"


@patch("foundry_demo.delivery.cli.AIProjectClient")
@patch("foundry_demo.delivery.cli.DefaultAzureCredential")
def test_deploy_transport_failure_records_uncertain_route(
    mock_cred_cls: MagicMock,
    mock_client_cls: MagicMock,
    tmp_path: Path,
) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    agents = mock_client.agents
    agents.get.side_effect = [
        ResourceNotFoundError("Agent does not exist"),
        DummyAgent(
            endpoint=DummyEndpoint(
                selector=DummySelector(rules=[DummyRule(version="0", percentage=100)])
            )
        ),
    ]
    agents.get_version.return_value = DummyVersionItem("0")
    agents.create_version.return_value = DummyVersionItem("1")
    agents.update_details.side_effect = ServiceRequestError("request timed out")
    openai_client = MagicMock()
    openai_client.responses.create.return_value = _DummySmokeResponse()
    mock_client.get_openai_client.return_value = openai_client

    config = _make_config(tmp_path)
    with (
        pytest.raises(ServiceRequestError, match="request timed out"),
        DeploymentRecorder(
            agent_name=AGENT_NAME,
            record_dir=tmp_path / "records",
        ) as recorder,
    ):
        deploy(RESEARCH_ASSISTANT_SPEC, config, recorder)

    record = json.loads(recorder.record_path.read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["cutover_outcome"] == "uncertain"
    assert record["observed_active_version"] == "0"


@patch("foundry_demo.delivery.cli.AIProjectClient")
@patch("foundry_demo.delivery.cli.DefaultAzureCredential")
def test_deploy_transport_failure_does_not_accept_default_latest(
    mock_cred_cls: MagicMock,
    mock_client_cls: MagicMock,
    tmp_path: Path,
) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    agents = mock_client.agents
    agents.get.side_effect = [
        ResourceNotFoundError("Agent does not exist"),
        DummyAgent(endpoint=DummyEndpoint(selector=None)),
    ]
    agents.list_versions.return_value = [DummyVersionItem("1")]
    agents.get_version.return_value = DummyVersionItem("1")
    agents.create_version.return_value = DummyVersionItem("1")
    agents.update_details.side_effect = ServiceRequestError("request timed out")
    openai_client = MagicMock()
    openai_client.responses.create.return_value = _DummySmokeResponse()
    mock_client.get_openai_client.return_value = openai_client

    config = _make_config(tmp_path)
    with (
        pytest.raises(ServiceRequestError, match="request timed out"),
        DeploymentRecorder(
            agent_name=AGENT_NAME,
            record_dir=tmp_path / "records",
        ) as recorder,
    ):
        deploy(RESEARCH_ASSISTANT_SPEC, config, recorder)

    record = json.loads(recorder.record_path.read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["cutover_outcome"] == "uncertain"
    assert record["observed_active_version"] == "1"


def test_main_missing_config_writes_failed_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records_dir = tmp_path / "deployments"
    monkeypatch.setenv("FOUNDRY_DEPLOYMENT_RECORD_DIR", str(records_dir))
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("FOUNDRY_MODEL_DEPLOYMENT_NAME", raising=False)

    with pytest.raises(ConfigurationError):
        main([])

    records = list(records_dir.glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert "FOUNDRY_PROJECT_ENDPOINT" in record["failure_message"]


def test_main_list_agents(capsys: pytest.CaptureFixture) -> None:
    main(["--list"])
    captured = capsys.readouterr()
    assert "Available agents:" in captured.out
    assert "architecture-advisor" in captured.out
    assert "research-assistant" in captured.out


def test_main_unknown_agent_writes_failed_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records_dir = tmp_path / "deployments"
    monkeypatch.setenv("FOUNDRY_DEPLOYMENT_RECORD_DIR", str(records_dir))

    with pytest.raises(AgentSpecError, match="not found"):
        main(["unknown-agent-name"])

    records = list(records_dir.glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["agent_name"] == "unknown-agent-name"
    assert record["status"] == "failed"


@pytest.mark.parametrize("args", [["--lsit"], ["research-assistant", "extra"]])
def test_main_rejects_unknown_or_extra_arguments(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(args)

    assert exc_info.value.code == 2


@patch("foundry_demo.delivery.cli.AIProjectClient")
@patch("foundry_demo.delivery.cli.DefaultAzureCredential")
def test_deploy_missing_candidate_version_fails_closed(
    mock_cred_cls: MagicMock,
    mock_client_cls: MagicMock,
    tmp_path: Path,
) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    agents = mock_client.agents
    agents.get.side_effect = ResourceNotFoundError("Agent does not exist")
    agents.create_version.return_value = object()

    config = _make_config(tmp_path)
    with (
        pytest.raises(CandidateVersionError),
        DeploymentRecorder(
            agent_name=AGENT_NAME,
            record_dir=tmp_path / "records",
        ) as recorder,
    ):
        deploy(RESEARCH_ASSISTANT_SPEC, config, recorder)

    agents.update_details.assert_not_called()
    mock_client.get_openai_client.assert_not_called()
    record = json.loads(recorder.record_path.read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["candidate_version"] is None


@patch("foundry_demo.delivery.cli.AIProjectClient")
@patch("foundry_demo.delivery.cli.DefaultAzureCredential")
def test_deploy_hosted_agent_success(
    mock_cred_cls: MagicMock,
    mock_client_cls: MagicMock,
    tmp_path: Path,
) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    agents = mock_client.agents
    agents.get.side_effect = ResourceNotFoundError("Agent does not exist")
    agents.create_version.return_value = DummyVersionItem("1")
    agents.get_version.return_value = SimpleNamespace(status="active")
    agents.create_session.return_value = SimpleNamespace(agent_session_id="session-hosted")
    mock_client.beta.skills.create_from_files.return_value = SimpleNamespace(version="1")
    mock_client.toolboxes.create_version.return_value = SimpleNamespace(version="1")
    mock_client.send_request.return_value = SimpleNamespace(status_code=200)

    openai_client = MagicMock()
    openai_client.responses.create.side_effect = [
        MagicMock(
            id="resp-setup",
            status="completed",
            output_text="Skill loaded.",
            output=[
                _DummyMessage(),
                SimpleNamespace(type="function_call", name="load_skill", status="completed"),
            ],
        ),
        MagicMock(
            id="resp-hosted",
            status="completed",
            output_text="Ready for review. https://learn.microsoft.com/azure/container-apps",
            output=[
                _DummyMessage(),
                SimpleNamespace(
                    type="function_call", name="microsoft_docs_search", status="completed"
                ),
            ],
        ),
    ]
    mock_client.get_openai_client.return_value = openai_client

    config = _make_hosted_config(tmp_path)
    with DeploymentRecorder(
        agent_name="architecture-advisor",
        record_dir=tmp_path / "records",
    ) as recorder:
        deploy(ARCHITECTURE_ADVISOR_SPEC, config, recorder)

    mock_client.get_openai_client.assert_called_once_with(agent_name="architecture-advisor")
    assert recorder.record_path.is_file()
    record = json.loads(recorder.record_path.read_text(encoding="utf-8"))
    assert record["status"] == "succeeded"
    assert record["candidate_version"] == "1"
    assert record["artifact_type"] == "hosted_definition"
    assert len(record["definition_sha256"]) == 64
    assert record["image_reference"].endswith("@sha256:" + "b" * 64)
    assert record["image_digest"] == "sha256:" + "b" * 64
    agents.create_session.assert_called_once()
    session_call = agents.create_session.call_args.kwargs
    assert session_call["version_indicator"].agent_version == "1"
    assert openai_client.responses.create.call_count == 2
    assert openai_client.responses.create.call_args_list[1].kwargs == {
        "input": ARCHITECTURE_ADVISOR_SPEC.smoke_prompt,
        "extra_body": {"agent_session_id": "session-hosted"},
        "tool_choice": "required",
    }


def test_main_writes_repository_relative_record_path_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FOUNDRY_DEPLOYMENT_RECORD_DIR", "artifacts/deployments")
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("FOUNDRY_MODEL_DEPLOYMENT_NAME", raising=False)
    record_path_file = tmp_path / "record-path.txt"

    with pytest.raises(ConfigurationError):
        main(["--record-path-file", str(record_path_file)])

    written = record_path_file.read_text(encoding="utf-8").strip()
    records = list((tmp_path / "artifacts/deployments").glob("*.json"))
    assert len(records) == 1
    assert written == records[0].relative_to(tmp_path).as_posix()
    assert not Path(written).is_absolute()


def test_main_record_path_file_uses_absolute_path_outside_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    records_dir = tmp_path / "external" / "deployments"
    monkeypatch.chdir(repository_root)
    monkeypatch.setenv("FOUNDRY_DEPLOYMENT_RECORD_DIR", str(records_dir))
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    record_path_file = tmp_path / "record-path.txt"

    with pytest.raises(ConfigurationError):
        main(["--record-path-file", str(record_path_file)])

    written = Path(record_path_file.read_text(encoding="utf-8").strip())
    records = list(records_dir.glob("*.json"))
    assert len(records) == 1
    assert written == records[0]
