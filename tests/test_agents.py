import tomllib
from importlib import import_module
from pathlib import Path

import pytest

from foundry_demo.agents import (
    AGENTS,
    AgentKind,
    AgentSpecError,
    PromptAgentSpec,
    get_agent,
    list_agents,
)
from foundry_demo.agents.architecture_advisor import ARCHITECTURE_ADVISOR_SPEC
from foundry_demo.agents.incident_triage import INCIDENT_TRIAGE_SPEC
from foundry_demo.agents.research_assistant import RESEARCH_ASSISTANT_SPEC
from foundry_demo.delivery.prompt import build_definition
from foundry_demo.delivery.smoke import SmokeEvidence, SmokeTestError


def test_list_and_get_agents() -> None:
    names = list_agents()
    assert "research-assistant" in names
    assert "architecture-advisor" in names
    assert "incident-triage" in names
    assert ARCHITECTURE_ADVISOR_SPEC.kind is AgentKind.HOSTED
    assert INCIDENT_TRIAGE_SPEC.kind is AgentKind.HOSTED
    assert RESEARCH_ASSISTANT_SPEC.kind is AgentKind.PROMPT

    spec = get_agent("research-assistant")
    assert spec.name == "research-assistant"
    assert "Web Search" in spec.description


def test_get_agent_unknown_raises() -> None:
    with pytest.raises(AgentSpecError, match="not found"):
        get_agent("nonexistent-agent")


def test_research_assistant_spec_properties() -> None:
    assert RESEARCH_ASSISTANT_SPEC.name == "research-assistant"
    assert "research assistant that answers questions" in RESEARCH_ASSISTANT_SPEC.instructions
    tools = RESEARCH_ASSISTANT_SPEC.tools_factory()
    assert len(tools) == 1
    assert tools[0].external_web_access is True
    assert tools[0].search_context_size == "low"


def test_research_assistant_smoke_validation() -> None:
    valid_evidence = SmokeEvidence(
        response_id="resp-1",
        response_status="completed",
        output_text_present=True,
        output_item_counts={"web_search_call": 1, "message": 1},
        completed_output_item_counts={"web_search_call": 1},
        annotation_counts={"url_citation": 1},
    )
    # Should not raise
    assert RESEARCH_ASSISTANT_SPEC.validate_smoke is not None
    RESEARCH_ASSISTANT_SPEC.validate_smoke(valid_evidence)

    invalid_evidence = SmokeEvidence(
        response_id="resp-2",
        response_status="completed",
        output_text_present=True,
        output_item_counts={"message": 1},
        completed_output_item_counts={},
        annotation_counts={},
    )
    with pytest.raises(SmokeTestError, match="No completed web_search_call"):
        RESEARCH_ASSISTANT_SPEC.validate_smoke(invalid_evidence)


def test_build_definition_without_temperature() -> None:
    definition = build_definition(
        RESEARCH_ASSISTANT_SPEC,
        default_model="example-model",
        temperature=None,
    )

    assert definition.model == "example-model"
    assert definition.instructions == RESEARCH_ASSISTANT_SPEC.instructions
    assert len(definition.tools) == 1
    assert definition.temperature is None
    if hasattr(definition, "as_dict"):
        assert "temperature" not in definition.as_dict()


def test_build_definition_with_temperature() -> None:
    definition = build_definition(
        RESEARCH_ASSISTANT_SPEC,
        default_model="example-model",
        temperature=0.3,
    )

    assert definition.model == "example-model"
    assert definition.temperature == pytest.approx(0.3)
    if hasattr(definition, "as_dict"):
        assert definition.as_dict().get("temperature") == pytest.approx(0.3)


def test_build_definition_spec_model_override() -> None:
    custom_spec = PromptAgentSpec(
        name="custom-agent",
        description="Custom",
        instructions="Instructions",
        tools_factory=lambda: [],
        smoke_prompt="Hello",
        model_deployment_name="override-model",
    )
    definition = build_definition(custom_spec, default_model="default-model")
    assert definition.model == "override-model"


def test_build_definition_blank_model_raises() -> None:
    custom_spec = PromptAgentSpec(
        name="custom-agent",
        description="Custom",
        instructions="Instructions",
        tools_factory=lambda: [],
        smoke_prompt="Hello",
    )
    with pytest.raises(AgentSpecError, match="Model deployment name"):
        build_definition(custom_spec, default_model="  ")


def test_build_definition_blank_instructions_raises() -> None:
    custom_spec = PromptAgentSpec(
        name="custom-agent",
        description="Custom",
        instructions="   ",
        tools_factory=lambda: [],
        smoke_prompt="Hello",
    )
    with pytest.raises(AgentSpecError, match="instructions cannot be empty"):
        build_definition(custom_spec, default_model="default-model")


def test_base_agent_spec_inheritance() -> None:
    from foundry_demo.agents.common.base import BaseAgentSpec, HostedAgentSpec, PromptAgentSpec

    assert issubclass(PromptAgentSpec, BaseAgentSpec)
    assert issubclass(HostedAgentSpec, BaseAgentSpec)
    assert isinstance(RESEARCH_ASSISTANT_SPEC, BaseAgentSpec)
    assert isinstance(ARCHITECTURE_ADVISOR_SPEC, BaseAgentSpec)


def test_architecture_advisor_declares_pinned_foundry_dependencies() -> None:
    toolbox = ARCHITECTURE_ADVISOR_SPEC.toolbox
    assert toolbox is not None
    assert toolbox.name == "architecture-advisor-toolbox"
    assert toolbox.skill.name == "architecture-decision-brief"
    assert toolbox.skill.resource_path.endswith("/SKILL.md")
    assert toolbox.mcp_tools[0].server_url == "https://learn.microsoft.com/api/mcp"
    assert toolbox.mcp_tools[0].allowed_tools == (
        "microsoft_docs_search",
        "microsoft_docs_fetch",
    )
    assert toolbox.mcp_tools[0].require_approval == "never"


def test_architecture_advisor_smoke_validation() -> None:
    valid_evidence = SmokeEvidence(
        response_id="resp-1",
        response_status="completed",
        output_text_present=True,
        output_item_counts={"function_call": 2, "message": 1},
        completed_output_item_counts={"function_call": 2},
        annotation_counts={},
        completed_tool_name_counts={"load_skill": 1, "microsoft_docs_search": 1},
        source_domain_counts={"learn.microsoft.com": 1},
    )
    assert ARCHITECTURE_ADVISOR_SPEC.validate_smoke is not None
    ARCHITECTURE_ADVISOR_SPEC.validate_smoke(valid_evidence)

    # Validate namespaced MCP tool names from Foundry Toolbox
    namespaced_evidence = SmokeEvidence(
        response_id="resp-1b",
        response_status="completed",
        output_text_present=True,
        output_item_counts={"function_call": 2, "message": 1},
        completed_output_item_counts={"function_call": 2},
        annotation_counts={},
        completed_tool_name_counts={
            "load_skill": 1,
            "microsoft-learn___microsoft_docs_search": 1,
        },
        source_domain_counts={"learn.microsoft.com": 1},
    )
    ARCHITECTURE_ADVISOR_SPEC.validate_smoke(namespaced_evidence)

    invalid_evidence = SmokeEvidence(
        response_id="resp-2",
        response_status="completed",
        output_text_present=True,
        output_item_counts={},
        completed_output_item_counts={},
        annotation_counts={},
        completed_tool_name_counts={},
        source_domain_counts={},
    )
    with pytest.raises(SmokeTestError, match="No completed load_skill"):
        ARCHITECTURE_ADVISOR_SPEC.validate_smoke(invalid_evidence)


def test_incident_triage_needs_no_toolbox_or_forced_tool_call() -> None:
    # The Flock crew calls no tools, so a forced tool choice would fail by
    # construction and there is nothing for a Toolbox to expose.
    assert INCIDENT_TRIAGE_SPEC.toolbox is None
    assert INCIDENT_TRIAGE_SPEC.smoke_tool_choice is None
    assert INCIDENT_TRIAGE_SPEC.smoke_setup_prompt is None
    assert INCIDENT_TRIAGE_SPEC.image_env_var == "FOUNDRY_INCIDENT_TRIAGE_IMAGE"
    assert INCIDENT_TRIAGE_SPEC.image_digest_env_var == "FOUNDRY_INCIDENT_TRIAGE_IMAGE_DIGEST"


def test_incident_triage_smoke_requires_the_full_cascade() -> None:
    def evidence(message_count: int) -> SmokeEvidence:
        return SmokeEvidence(
            response_id="resp-1",
            response_status="completed",
            output_text_present=True,
            output_item_counts={"message": message_count},
            completed_output_item_counts={"message": message_count},
            annotation_counts={},
        )

    assert INCIDENT_TRIAGE_SPEC.validate_smoke is not None
    # Impact assessment, root-cause hypothesis, and the action plan that joins them.
    INCIDENT_TRIAGE_SPEC.validate_smoke(evidence(3))
    INCIDENT_TRIAGE_SPEC.validate_smoke(evidence(4))

    for partial in (0, 1, 2):
        with pytest.raises(SmokeTestError, match="at least 3 message"):
            INCIDENT_TRIAGE_SPEC.validate_smoke(evidence(partial))


def test_subfolder_package_exports() -> None:
    from foundry_demo.agents.architecture_advisor import (
        ARCHITECTURE_ADVISOR_SPEC as SUB_HOSTED_SPEC,
    )
    from foundry_demo.agents.research_assistant import (
        RESEARCH_ASSISTANT_SPEC as SUB_PROMPT_SPEC,
    )

    assert SUB_PROMPT_SPEC is RESEARCH_ASSISTANT_SPEC
    assert SUB_HOSTED_SPEC is ARCHITECTURE_ADVISOR_SPEC


def test_all_agent_packages_are_discovered_and_packaged() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    agent_packages = {
        path.name
        for path in (repository_root / "src/agents").iterdir()
        if path.is_dir() and path.name != "common" and (path / "__init__.py").is_file()
    }
    discovered_specs = {
        import_module(f"foundry_demo.agents.{package_name}").AGENT_SPEC
        for package_name in agent_packages
    }
    assert discovered_specs == set(AGENTS.values())

    pyproject = tomllib.loads((repository_root / "pyproject.toml").read_text(encoding="utf-8"))
    wheel_packages = set(pyproject["tool"]["setuptools"]["packages"])
    assert {
        f"foundry_demo.agents.{package_name}" for package_name in agent_packages
    } <= wheel_packages
