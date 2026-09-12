"""Reusable smoke validation helpers shared across agents."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from foundry_demo.delivery.smoke import SmokeEvidence


def validate_non_empty_response(evidence: "SmokeEvidence") -> None:
    """Validate that the smoke test produced non-empty output text."""
    if not evidence.output_text_present:
        from foundry_demo.delivery.smoke import SmokeTestError

        raise SmokeTestError(
            "Expected output text in smoke response, but none was present.",
            evidence=evidence,
        )


def validate_tool_calls(
    evidence: "SmokeEvidence",
    tool_type: str,
    min_count: int = 1,
) -> str | None:
    """Check that the smoke response completed the minimum number of tool calls."""
    count = evidence.completed_output_item_counts.get(tool_type, 0)
    if count < min_count:
        return f"No completed {tool_type} found in response output."
    return None


def validate_annotations(
    evidence: "SmokeEvidence",
    annotation_type: str,
    min_count: int = 1,
) -> str | None:
    """Check that the smoke response contains the minimum number of annotations."""
    count = evidence.annotation_counts.get(annotation_type, 0)
    if count < min_count:
        return f"No {annotation_type} annotations found in response output."
    return None


def validate_output_items(
    evidence: "SmokeEvidence",
    item_type: str,
    min_count: int = 1,
) -> str | None:
    """Check that the smoke response emitted the minimum number of output items.

    Unlike :func:`validate_tool_calls` this counts every item of the type, not
    only completed ones, so it fits agents whose evidence is the shape of the
    response rather than tool execution.
    """
    count = evidence.output_item_counts.get(item_type, 0)
    if count < min_count:
        return (
            f"Expected at least {min_count} {item_type} output item(s), found {count}."
        )
    return None


def _tool_count(completed_tool_name_counts: dict[str, int], target_name: str) -> int:
    direct = completed_tool_name_counts.get(target_name, 0)
    if direct:
        return direct
    return sum(
        count
        for name, count in completed_tool_name_counts.items()
        if name.endswith(f"___{target_name}")
    )


def validate_tool_name(
    evidence: "SmokeEvidence",
    tool_name: str,
    min_count: int = 1,
) -> str | None:
    """Check that the smoke response completed the minimum number of named tool calls."""
    count = _tool_count(evidence.completed_tool_name_counts, tool_name)
    if count < min_count:
        return f"No completed {tool_name} tool call found in response output."
    return None


def validate_any_tool_name(
    evidence: "SmokeEvidence",
    tool_names: tuple[str, ...],
) -> str | None:
    """Check that at least one of the listed tool names completed."""
    if any(_tool_count(evidence.completed_tool_name_counts, name) for name in tool_names):
        return None
    return "No completed tool call found for: " + ", ".join(tool_names) + "."


def validate_source_domains(
    evidence: "SmokeEvidence",
    domain: str,
    min_count: int = 1,
) -> str | None:
    """Check that the output references at least one URL from the specified domain."""
    count = evidence.source_domain_counts.get(domain, 0)
    if count < min_count:
        return f"No sources from {domain} found in response output."
    return None


def validate_no_tool_calls(evidence: "SmokeEvidence") -> str | None:
    """Check that the smoke response completed without calling any tool.

    A connected model cannot serve Web Search, Bing grounding, SharePoint,
    Memory Search, Browser Automation, or Fabric, so an agent on one is
    expected to answer from the model alone. This is a guard against a tool
    being attached later, not evidence about the current response.
    """
    if evidence.completed_tool_name_counts:
        names = ", ".join(sorted(evidence.completed_tool_name_counts))
        return f"Expected no tool calls, found completed calls to: {names}."
    return None
