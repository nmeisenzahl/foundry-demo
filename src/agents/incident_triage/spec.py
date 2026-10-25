"""Flock incident-triage deployment specification."""

from foundry_demo.agents.common.base import HostedAgentSpec
from foundry_demo.agents.incident_triage.tools import validate_smoke

INCIDENT_TRIAGE_SPEC = HostedAgentSpec(
    name="incident-triage",
    description=(
        "Triages an incident report through a Flock blackboard crew and returns "
        "a typed impact assessment, root-cause hypothesis, and action plan."
    ),
    smoke_prompt=(
        "The checkout service has returned HTTP 500 for roughly 30% of requests "
        "since the 14:05 deploy. Latency is normal and the error rate is still "
        "climbing."
    ),
    image_env_var="FOUNDRY_INCIDENT_TRIAGE_IMAGE",
    image_digest_env_var="FOUNDRY_INCIDENT_TRIAGE_IMAGE_DIGEST",
    validate_smoke=validate_smoke,
    # The agent has no tools, so forcing a tool call would fail by construction.
    smoke_tool_choice=None,
    # Retries cover transport-level 401/403 from the Foundry endpoint while
    # RBAC propagates. Model auth failures inside the container come back as a
    # failed response (agent_failed) and are not retried.
    smoke_max_attempts=3,
    smoke_retry_seconds=10,
)

AGENT_SPEC = INCIDENT_TRIAGE_SPEC
