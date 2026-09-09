"""Strict smoke test validation for candidate agent versions."""

import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from azure.core.exceptions import AzureError
from openai import APIError, OpenAI

from foundry_demo.agents.common.base import AgentSpec, HostedAgentSpec, PromptAgentSpec


@dataclass(frozen=True)
class SmokeEvidence:
    """Summary of smoke test execution evidence."""

    response_id: str | None
    response_status: str
    output_text_present: bool
    output_item_counts: dict[str, int]
    completed_output_item_counts: dict[str, int]
    annotation_counts: dict[str, int]
    completed_tool_name_counts: dict[str, int] = field(default_factory=dict)
    source_domain_counts: dict[str, int] = field(default_factory=dict)


class SmokeTestError(RuntimeError):
    """Raised when candidate version smoke test fails acceptance criteria."""

    def __init__(self, message: str, evidence: SmokeEvidence) -> None:
        super().__init__(message)
        self.evidence = evidence


def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _count_evidence(
    outputs: Iterable[Any],
    output_text: str = "",
) -> tuple[
    dict[str, int],
    dict[str, int],
    dict[str, int],
    dict[str, int],
    dict[str, int],
]:
    output_item_counts: dict[str, int] = {}
    completed_output_item_counts: dict[str, int] = {}
    annotation_counts: dict[str, int] = {}
    completed_tool_name_counts: dict[str, int] = {}
    source_domain_counts: dict[str, int] = {}

    for item in outputs:
        item_type = _get_val(item, "type")
        if isinstance(item_type, str) and item_type:
            output_item_counts[item_type] = output_item_counts.get(item_type, 0) + 1
            if _get_val(item, "status") == "completed":
                completed_output_item_counts[item_type] = (
                    completed_output_item_counts.get(item_type, 0) + 1
                )
                if item_type in ("function_call", "mcp_call"):
                    name = _get_val(item, "name")
                    if isinstance(name, str) and name:
                        completed_tool_name_counts[name] = (
                            completed_tool_name_counts.get(name, 0) + 1
                        )

        if item_type == "message":
            content_list = _get_val(item, "content", []) or []
            for content in content_list:
                if _get_val(content, "type") == "output_text":
                    annotations = _get_val(content, "annotations", []) or []
                    for ann in annotations:
                        annotation_type = _get_val(ann, "type")
                        if isinstance(annotation_type, str) and annotation_type:
                            annotation_counts[annotation_type] = (
                                annotation_counts.get(annotation_type, 0) + 1
                            )
                        if annotation_type == "url_citation":
                            url = _get_val(ann, "url")
                            if isinstance(url, str):
                                domain = (urlparse(url).hostname or "").lower()
                                if domain:
                                    source_domain_counts[domain] = (
                                        source_domain_counts.get(domain, 0) + 1
                                    )

    if output_text:
        for match in re.finditer(r"https?://([^/\s:]+)", output_text):
            domain = match.group(1).lower()
            source_domain_counts[domain] = source_domain_counts.get(domain, 0) + 1

    return (
        output_item_counts,
        completed_output_item_counts,
        annotation_counts,
        completed_tool_name_counts,
        source_domain_counts,
    )


def _merge_evidence(first: SmokeEvidence, second: SmokeEvidence) -> SmokeEvidence:
    def merge_counts(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
        result = dict(left)
        for key, value in right.items():
            result[key] = result.get(key, 0) + value
        return result

    return SmokeEvidence(
        response_id=second.response_id,
        response_status=second.response_status,
        output_text_present=first.output_text_present and second.output_text_present,
        output_item_counts=merge_counts(first.output_item_counts, second.output_item_counts),
        completed_output_item_counts=merge_counts(
            first.completed_output_item_counts,
            second.completed_output_item_counts,
        ),
        annotation_counts=merge_counts(first.annotation_counts, second.annotation_counts),
        completed_tool_name_counts=merge_counts(
            first.completed_tool_name_counts,
            second.completed_tool_name_counts,
        ),
        source_domain_counts=merge_counts(
            first.source_domain_counts,
            second.source_domain_counts,
        ),
    )


def _validate_response(
    response: Any,
    *,
    agent_spec: AgentSpec,
    candidate_version: str,
    apply_agent_validator: bool = True,
) -> SmokeEvidence:
    response_id = _get_val(response, "id")
    response_status = str(_get_val(response, "status", "") or "")
    output_text = str(_get_val(response, "output_text", "") or "")
    outputs = _get_val(response, "output", []) or []

    (
        output_item_counts,
        completed_output_item_counts,
        annotation_counts,
        completed_tool_name_counts,
        source_domain_counts,
    ) = _count_evidence(outputs, output_text=output_text)

    evidence = SmokeEvidence(
        response_id=response_id,
        response_status=response_status,
        output_text_present=bool(output_text.strip()),
        output_item_counts=output_item_counts,
        completed_output_item_counts=completed_output_item_counts,
        annotation_counts=annotation_counts,
        completed_tool_name_counts=completed_tool_name_counts,
        source_domain_counts=source_domain_counts,
    )

    failures: list[str] = []
    if response_status != "completed":
        failures.append(f"Response status is {response_status!r}, expected 'completed'.")

    if not evidence.output_text_present:
        failures.append("Response output text is empty or blank.")

    if failures:
        msg = f"Smoke test failed for {agent_spec.name}:{candidate_version}: " + "; ".join(failures)
        raise SmokeTestError(msg, evidence=evidence)

    if apply_agent_validator and agent_spec.validate_smoke is not None:
        agent_spec.validate_smoke(evidence)

    return evidence


def _response_evidence(
    response: Any,
    *,
    agent_spec: AgentSpec,
    candidate_version: str,
) -> SmokeEvidence:
    return _validate_response(
        response,
        agent_spec=agent_spec,
        candidate_version=candidate_version,
        apply_agent_validator=False,
    )


def _status_code(exc: Exception) -> int | None:
    raw = getattr(exc, "status_code", None)
    if raw is None:
        response = getattr(exc, "response", None)
        raw = getattr(response, "status_code", None)
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _hosted_transport_error(
    exc: Exception,
    *,
    agent_spec: HostedAgentSpec,
    candidate_version: str,
) -> SmokeTestError:
    status = _status_code(exc)
    evidence = SmokeEvidence(
        response_id=None,
        response_status=f"transport_error_{status or 'unknown'}",
        output_text_present=False,
        output_item_counts={},
        completed_output_item_counts={},
        annotation_counts={},
    )
    if status in (401, 403):
        message = (
            f"Smoke test failed for {agent_spec.name}:{candidate_version}: HTTP {status}. "
            f"Grant the hosted identity for agent {agent_spec.name!r} the 'Azure AI User' "
            "role on this Foundry project, wait for RBAC propagation, and redeploy."
        )
    else:
        message = (
            f"Smoke test transport failed for {agent_spec.name}:{candidate_version}: "
            f"{type(exc).__name__}."
        )
    return SmokeTestError(message, evidence)


def run_prompt_smoke(
    openai_client: OpenAI,
    *,
    agent_spec: PromptAgentSpec,
    candidate_version: str,
) -> SmokeEvidence:
    """Invoke a prompt-agent candidate directly and validate strict evidence."""
    request: dict[str, Any] = {
        "input": agent_spec.smoke_prompt,
        "extra_body": {
            "agent_reference": {
                "type": "agent_reference",
                "name": agent_spec.name,
                "version": candidate_version,
            }
        },
    }
    if agent_spec.smoke_tool_choice is not None:
        request["tool_choice"] = agent_spec.smoke_tool_choice

    response = openai_client.responses.create(**request)
    return _validate_response(
        response,
        agent_spec=agent_spec,
        candidate_version=candidate_version,
    )


def run_hosted_smoke(
    project: Any,
    openai_client: OpenAI,
    *,
    agent_spec: HostedAgentSpec,
    candidate_version: str,
) -> SmokeEvidence:
    """Pin a hosted-agent session to the candidate and validate its response."""
    from azure.ai.projects.models import VersionRefIndicator

    last_error: SmokeTestError | None = None
    for attempt in range(1, agent_spec.smoke_max_attempts + 1):
        try:
            session = project.agents.create_session(
                agent_name=agent_spec.name,
                version_indicator=VersionRefIndicator(agent_version=candidate_version),
            )
            session_id = str(getattr(session, "agent_session_id", "") or "").strip()
            if not session_id:
                evidence = SmokeEvidence(None, "not_started", False, {}, {}, {})
                raise SmokeTestError(
                    f"Smoke test failed for {agent_spec.name}:{candidate_version}: "
                    "Foundry did not return an agent session identifier.",
                    evidence=evidence,
                )

            combined: SmokeEvidence | None = None
            if agent_spec.smoke_setup_prompt:
                setup_request: dict[str, Any] = {
                    "input": agent_spec.smoke_setup_prompt,
                    "extra_body": {"agent_session_id": session_id},
                }
                if agent_spec.smoke_setup_tool_name is not None:
                    setup_request["tool_choice"] = {
                        "type": "function",
                        "name": agent_spec.smoke_setup_tool_name,
                    }
                setup_response = openai_client.responses.create(**setup_request)
                combined = _response_evidence(
                    setup_response,
                    agent_spec=agent_spec,
                    candidate_version=candidate_version,
                )

            request: dict[str, Any] = {
                "input": agent_spec.smoke_prompt,
                "extra_body": {"agent_session_id": session_id},
            }
            if agent_spec.smoke_tool_choice is not None:
                request["tool_choice"] = agent_spec.smoke_tool_choice
            response = openai_client.responses.create(**request)
            evidence = _response_evidence(
                response,
                agent_spec=agent_spec,
                candidate_version=candidate_version,
            )
            if combined is not None:
                evidence = _merge_evidence(combined, evidence)
            if agent_spec.validate_smoke is not None:
                agent_spec.validate_smoke(evidence)
            return evidence
        except SmokeTestError:
            raise
        except (APIError, AzureError) as exc:
            last_error = _hosted_transport_error(
                exc,
                agent_spec=agent_spec,
                candidate_version=candidate_version,
            )
            if _status_code(exc) not in (401, 403) or attempt >= agent_spec.smoke_max_attempts:
                raise last_error from exc
            time.sleep(agent_spec.smoke_retry_seconds)

    assert last_error is not None
    raise last_error
