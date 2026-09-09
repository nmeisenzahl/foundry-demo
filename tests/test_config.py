import pytest

from foundry_demo.delivery.config import ConfigurationError, load_config


def test_load_config_uses_environment_model_without_hardcoding() -> None:
    config = load_config(
        {
            "FOUNDRY_PROJECT_ENDPOINT": (
                "https://example.services.ai.azure.com/api/projects/example-dev"
            ),
            "FOUNDRY_MODEL_DEPLOYMENT_NAME": "example-chat-model",
        }
    )

    assert config.project_endpoint == (
        "https://example.services.ai.azure.com/api/projects/example-dev"
    )
    assert config.model_deployment_name == "example-chat-model"
    assert config.temperature is None


def test_load_config_canonicalizes_endpoint_trailing_slash() -> None:
    config = load_config(
        {
            "FOUNDRY_PROJECT_ENDPOINT": (
                "https://example.services.ai.azure.com/api/projects/example-dev/"
            ),
            "FOUNDRY_MODEL_DEPLOYMENT_NAME": "example-chat-model",
        }
    )

    assert config.project_endpoint == (
        "https://example.services.ai.azure.com/api/projects/example-dev"
    )


def test_load_config_missing_endpoint_raises() -> None:
    with pytest.raises(ConfigurationError, match="FOUNDRY_PROJECT_ENDPOINT"):
        load_config({"FOUNDRY_MODEL_DEPLOYMENT_NAME": "example-chat-model"})


@pytest.mark.parametrize(
    ("bad_endpoint", "expected_match"),
    [
        ("http://example.services.ai.azure.com/api/projects/example-dev", "HTTPS"),
        ("https://", "HTTPS"),
        ("not-a-url", "HTTPS"),
        ("https://example.services.ai.azure.com/other/path", "/api/projects/"),
    ],
)
def test_load_config_invalid_endpoint_raises(
    bad_endpoint: str, expected_match: str
) -> None:
    with pytest.raises(ConfigurationError, match=expected_match):
        load_config(
            {
                "FOUNDRY_PROJECT_ENDPOINT": bad_endpoint,
                "FOUNDRY_MODEL_DEPLOYMENT_NAME": "example-chat-model",
            }
        )


@pytest.mark.parametrize("bad_model", ["", "   ", "\t"])
def test_load_config_missing_or_blank_model_raises(bad_model: str) -> None:
    with pytest.raises(ConfigurationError, match="FOUNDRY_MODEL_DEPLOYMENT_NAME"):
        load_config(
            {
                "FOUNDRY_PROJECT_ENDPOINT": (
                    "https://example.services.ai.azure.com/api/projects/example-dev"
                ),
                "FOUNDRY_MODEL_DEPLOYMENT_NAME": bad_model,
            }
        )


def test_load_config_explicit_finite_temperature() -> None:
    config = load_config(
        {
            "FOUNDRY_PROJECT_ENDPOINT": (
                "https://example.services.ai.azure.com/api/projects/example-dev"
            ),
            "FOUNDRY_MODEL_DEPLOYMENT_NAME": "example-chat-model",
            "FOUNDRY_AGENT_TEMPERATURE": "0.4",
        }
    )
    assert config.temperature == pytest.approx(0.4)


@pytest.mark.parametrize("bad_temp", ["abc", "nan", "inf", "-inf"])
def test_load_config_invalid_temperature_raises(bad_temp: str) -> None:
    with pytest.raises(ConfigurationError, match=r"(?i)temperature"):
        load_config(
            {
                "FOUNDRY_PROJECT_ENDPOINT": (
                    "https://example.services.ai.azure.com/api/projects/example-dev"
                ),
                "FOUNDRY_MODEL_DEPLOYMENT_NAME": "example-chat-model",
                "FOUNDRY_AGENT_TEMPERATURE": bad_temp,
            }
        )
