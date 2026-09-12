"""Configuration loading and validation for Foundry agent delivery."""

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from foundry_demo.agents.common.base import AgentSpec, HostedAgentSpec, PromptAgentSpec


class ConfigurationError(ValueError):
    """Raised when local agent deployment configuration is invalid."""


@dataclass(frozen=True)
class HostedImage:
    reference: str
    digest: str


@dataclass(frozen=True)
class DeploymentConfig:
    project_endpoint: str
    model_deployment_name: str
    temperature: float | None
    environ: Mapping[str, str] | None = None

    def get_hosted_image(self, spec: HostedAgentSpec) -> HostedImage:
        env = os.environ if self.environ is None else self.environ
        reference = (env.get(spec.image_env_var) or "").strip()
        digest = (env.get(spec.image_digest_env_var) or "").strip()
        if not reference or "/" not in reference or ":" not in reference.rsplit("/", 1)[-1]:
            raise ConfigurationError(
                f"{spec.image_env_var} must be a fully qualified tagged image."
            )
        if reference.endswith(":latest"):
            raise ConfigurationError(f"{spec.image_env_var} must not use the mutable 'latest' tag.")
        if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            raise ConfigurationError(f"{spec.image_digest_env_var} must be a sha256 image digest.")
        return HostedImage(reference=reference, digest=digest.lower())

    def get_connected_model(self, spec: PromptAgentSpec) -> str:
        """Resolve and validate an admin-connected model reference.

        Foundry accepts only ``<connection-name>/<model-name>`` for a model
        behind a gateway connection, and reports anything else as
        ``model not found`` at invocation time -- after a candidate version
        already exists. Validating the shape here turns that into a
        configuration error before the first Foundry call.
        """
        env = os.environ if self.environ is None else self.environ
        variable = spec.connected_model_env_var or ""
        raw = (env.get(variable) or "").strip()
        if not raw:
            raise ConfigurationError(
                f"{variable} is required for agent {spec.name!r} and cannot be blank."
            )
        parts = raw.split("/")
        if len(parts) != 2 or not all(part.strip() for part in parts):
            raise ConfigurationError(
                f"{variable} must use '<connection-name>/<model-name>' form: {raw!r}"
            )
        return raw

    def resolve_model_deployment_name(self, spec: AgentSpec) -> str:
        """Resolve the model an agent runs on.

        Precedence: an admin-connected model, then a spec-level override, then
        the Terraform-deployed default.
        """
        if isinstance(spec, PromptAgentSpec) and spec.connected_model_env_var:
            return self.get_connected_model(spec)
        return (spec.model_deployment_name or self.model_deployment_name).strip()


def _validate_project_endpoint(endpoint: str) -> str:
    cleaned = endpoint.strip().rstrip("/")
    if not cleaned:
        raise ConfigurationError("FOUNDRY_PROJECT_ENDPOINT cannot be empty.")
    parsed = urlparse(cleaned)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConfigurationError(
            f"FOUNDRY_PROJECT_ENDPOINT must be an absolute HTTPS URL: {endpoint!r}"
        )
    if "/api/projects/" not in parsed.path:
        raise ConfigurationError(
            f"FOUNDRY_PROJECT_ENDPOINT must include '/api/projects/': {endpoint!r}"
        )
    return cleaned


def _validate_model_name(model_name: str | None) -> str:
    if not model_name or not model_name.strip():
        raise ConfigurationError("FOUNDRY_MODEL_DEPLOYMENT_NAME is required and cannot be blank.")
    return model_name.strip()


def _parse_temperature(raw_temp: str | None) -> float | None:
    if raw_temp is None or not raw_temp.strip():
        return None
    try:
        parsed = float(raw_temp.strip())
    except ValueError as exc:
        raise ConfigurationError(
            f"FOUNDRY_AGENT_TEMPERATURE must be a valid float: {raw_temp!r}"
        ) from exc
    if not math.isfinite(parsed):
        raise ConfigurationError(f"FOUNDRY_AGENT_TEMPERATURE must be a finite float: {raw_temp!r}")
    return parsed


def load_config(
    environ: Mapping[str, str] | None = None,
) -> DeploymentConfig:
    env = os.environ if environ is None else environ
    raw_endpoint = env.get("FOUNDRY_PROJECT_ENDPOINT")
    if not raw_endpoint:
        raise ConfigurationError("FOUNDRY_PROJECT_ENDPOINT environment variable is required.")
    return DeploymentConfig(
        project_endpoint=_validate_project_endpoint(raw_endpoint),
        model_deployment_name=_validate_model_name(env.get("FOUNDRY_MODEL_DEPLOYMENT_NAME")),
        temperature=_parse_temperature(env.get("FOUNDRY_AGENT_TEMPERATURE")),
        environ=env,
    )
