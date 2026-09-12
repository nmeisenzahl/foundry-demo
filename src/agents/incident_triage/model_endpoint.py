"""Resolution of the Azure inference endpoint used by LiteLLM.

Foundry injects ``FOUNDRY_PROJECT_ENDPOINT`` (``https://<host>/api/projects/<project>``)
into the hosted container, but LiteLLM's Azure provider wants the account-level
base URL it appends ``/openai/deployments/...`` to. The project endpoint is the
only injected value that carries the host, so the base URL is derived from it
and validated rather than concatenated at the call site.
"""

import os
from urllib.parse import urlparse

# Overridable because the API version has to cover whichever model the Foundry
# project deploys; the default tracks the version Flock documents for Azure.
DEFAULT_API_VERSION = "2024-12-01-preview"


def resolve_model_endpoint(project_endpoint: str) -> str:
    """Return the scheme+host inference base URL for a Foundry project endpoint."""
    cleaned = project_endpoint.strip()
    if not cleaned:
        raise ValueError("Project endpoint cannot be empty.")

    parsed = urlparse(cleaned)
    if parsed.scheme != "https":
        raise ValueError(f"Project endpoint must use https scheme: {cleaned!r}")
    if not parsed.netloc:
        raise ValueError(f"Project endpoint must include a host: {cleaned!r}")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError(f"Project endpoint contains unsupported URL components: {cleaned!r}")

    return f"https://{parsed.netloc}"


def resolve_api_base(environ: dict[str, str] | None = None) -> str:
    """Return the configured inference base URL, preferring an explicit override."""
    env = os.environ if environ is None else environ
    override = (env.get("AZURE_API_BASE") or "").strip()
    if override:
        return resolve_model_endpoint(override)
    return resolve_model_endpoint(env["FOUNDRY_PROJECT_ENDPOINT"])


def resolve_api_version(environ: dict[str, str] | None = None) -> str:
    """Return the Azure OpenAI API version to call."""
    env = os.environ if environ is None else environ
    return (env.get("AZURE_API_VERSION") or "").strip() or DEFAULT_API_VERSION
