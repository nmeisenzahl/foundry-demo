"""Tools and smoke validation for the research assistant."""

from typing import TYPE_CHECKING, Any

from azure.ai.projects.models import WebSearchTool

from foundry_demo.agents.common.validators import (
    validate_annotations,
    validate_tool_calls,
)

if TYPE_CHECKING:
    from foundry_demo.delivery.smoke import SmokeEvidence


def build_tools() -> list[Any]:
    """Build tool dependencies for research assistant."""
    return [
        WebSearchTool(
            external_web_access=True,
            search_context_size="low",
        )
    ]


def validate_smoke(evidence: "SmokeEvidence") -> None:
    """Validate research assistant smoke execution."""
    failures: list[str] = []
    tool_err = validate_tool_calls(evidence, "web_search_call", min_count=1)
    if tool_err:
        failures.append(tool_err)
    anno_err = validate_annotations(evidence, "url_citation", min_count=1)
    if anno_err:
        failures.append(anno_err)

    if failures:
        from foundry_demo.delivery.smoke import SmokeTestError

        raise SmokeTestError("; ".join(failures), evidence=evidence)
