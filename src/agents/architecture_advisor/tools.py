"""Smoke acceptance validation for architecture-advisor agent."""

from foundry_demo.agents.common.validators import (
    validate_any_tool_name,
    validate_source_domains,
    validate_tool_name,
)
from foundry_demo.delivery.smoke import SmokeEvidence, SmokeTestError


def validate_smoke(evidence: SmokeEvidence) -> None:
    """Validate that the agent loaded the skill, called Microsoft Learn, and cited docs."""
    failures: list[str] = []

    err_skill = validate_tool_name(evidence, "load_skill")
    if err_skill:
        failures.append(err_skill)

    err_mcp = validate_any_tool_name(
        evidence, ("microsoft_docs_search", "microsoft_docs_fetch")
    )
    if err_mcp:
        failures.append(err_mcp)

    err_domain = validate_source_domains(evidence, "learn.microsoft.com")
    if err_domain:
        failures.append(err_domain)

    if failures:
        raise SmokeTestError("; ".join(failures), evidence=evidence)
