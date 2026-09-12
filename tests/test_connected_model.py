"""Resolution of admin-connected Foundry model references."""

from dataclasses import replace

import pytest

from foundry_demo.agents.common.base import HostedAgentSpec, PromptAgentSpec
from foundry_demo.delivery.config import ConfigurationError, DeploymentConfig
from foundry_demo.delivery.prompt import build_definition

CONNECTED_ENV = "FOUNDRY_CONNECTED_MODEL_DEPLOYMENT_NAME"

PROMPT_SPEC = PromptAgentSpec(
    name="connected-demo",
    description="Prompt agent on an admin-connected model.",
    instructions="Answer briefly.",
    smoke_prompt="Say hello.",
    connected_model_env_var=CONNECTED_ENV,
)

HOSTED_SPEC = HostedAgentSpec(
    name="hosted-demo",
    description="Hosted agent.",
    smoke_prompt="Say hello.",
    image_env_var="DEMO_IMAGE",
    image_digest_env_var="DEMO_IMAGE_DIGEST",
)


def _config(**env: str) -> DeploymentConfig:
    return DeploymentConfig(
        project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
        model_deployment_name="terraform-chat-model",
        temperature=None,
        environ=env,
    )


def test_connected_model_is_resolved_from_the_environment() -> None:
    config = _config(**{CONNECTED_ENV: "tc-connection/gpt-4o"})

    assert config.get_connected_model(PROMPT_SPEC) == "tc-connection/gpt-4o"


def test_surrounding_whitespace_is_stripped() -> None:
    config = _config(**{CONNECTED_ENV: "  tc-connection/gpt-4o  "})

    assert config.get_connected_model(PROMPT_SPEC) == "tc-connection/gpt-4o"


def test_missing_connected_model_variable_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match=CONNECTED_ENV):
        _config().get_connected_model(PROMPT_SPEC)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "gpt-4o",
        "conn/model/extra",
        "/gpt-4o",
        "tc-connection/",
        " / ",
    ],
)
def test_malformed_connected_model_references_are_rejected(value: str) -> None:
    config = _config(**{CONNECTED_ENV: value})

    with pytest.raises(ConfigurationError, match=CONNECTED_ENV):
        config.get_connected_model(PROMPT_SPEC)


def test_connected_model_wins_over_every_other_source() -> None:
    spec = replace(PROMPT_SPEC, model_deployment_name="spec-override")
    config = _config(**{CONNECTED_ENV: "tc-connection/gpt-4o"})

    assert config.resolve_model_deployment_name(spec) == "tc-connection/gpt-4o"


def test_spec_override_wins_when_no_connected_model_is_declared() -> None:
    spec = replace(
        PROMPT_SPEC,
        connected_model_env_var=None,
        model_deployment_name="spec-override",
    )

    assert _config().resolve_model_deployment_name(spec) == "spec-override"


def test_terraform_default_is_used_when_the_spec_declares_nothing() -> None:
    spec = replace(PROMPT_SPEC, connected_model_env_var=None)

    assert _config().resolve_model_deployment_name(spec) == "terraform-chat-model"


def test_hosted_specs_ignore_connected_model_resolution() -> None:
    config = _config(**{CONNECTED_ENV: "tc-connection/gpt-4o"})

    assert config.resolve_model_deployment_name(HOSTED_SPEC) == "terraform-chat-model"


def test_prompt_definition_carries_the_connected_model_reference() -> None:
    definition = build_definition(PROMPT_SPEC, model="tc-connection/gpt-4o")

    assert definition.model == "tc-connection/gpt-4o"


def test_two_connected_models_produce_different_definitions() -> None:
    # The candidate digest is a sha256 over the serialized definition, so
    # differing definitions are what makes a re-point an immutable new version.
    first = build_definition(PROMPT_SPEC, model="tc-connection/gpt-4o")
    second = build_definition(PROMPT_SPEC, model="tc-connection/gpt-4o-mini")

    assert first.as_dict() != second.as_dict()
