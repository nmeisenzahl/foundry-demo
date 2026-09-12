"""Smoke validation for the release notes writer."""

from typing import TYPE_CHECKING

from foundry_demo.agents.common.validators import (
    validate_no_tool_calls,
    validate_output_items,
)

if TYPE_CHECKING:
    from foundry_demo.delivery.smoke import SmokeEvidence


def validate_smoke(evidence: "SmokeEvidence") -> None:
    """Validate release notes writer smoke execution.

    The delivery package already requires a completed response with non-empty
    output text. What this adds is thin on purpose: the load-bearing check for
    a connected-model agent is that the invocation reached the gateway at all,
    which a rejected key, a malformed model reference, or an exhausted Token
    Control budget each turn into a failed response before this runs.
    """
    failures: list[str] = []
    message_err = validate_output_items(evidence, "message", min_count=1)
    if message_err:
        failures.append(message_err)
    tool_err = validate_no_tool_calls(evidence)
    if tool_err:
        failures.append(tool_err)

    if failures:
        from foundry_demo.delivery.smoke import SmokeTestError

        raise SmokeTestError("; ".join(failures), evidence=evidence)
