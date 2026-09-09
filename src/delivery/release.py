"""Shared immutable release policy for all Foundry agent kinds."""

from dataclasses import dataclass
from typing import Any

from azure.ai.projects.models import (
    AgentEndpointConfig,
    FixedRatioVersionSelectionRule,
    VersionSelector,
)
from azure.core.exceptions import (
    AzureError,
    ResourceNotFoundError,
    ServiceRequestError,
    ServiceResponseError,
)

from foundry_demo.agents import AgentKind, RegisteredAgentSpec
from foundry_demo.delivery.config import DeploymentConfig
from foundry_demo.delivery.contracts import AgentOperations
from foundry_demo.delivery.records import DeploymentPhase, DeploymentRecorder
from foundry_demo.delivery.smoke import (
    SmokeTestError,
    run_hosted_smoke,
    run_prompt_smoke,
)


class RoutingError(RuntimeError):
    pass


@dataclass(frozen=True)
class RoutingState:
    agent_exists: bool
    previous_active_version: str | None
    pinned_during_resolution: bool
    uses_default_latest: bool = False


def fixed_selector(version: str) -> AgentEndpointConfig:
    return AgentEndpointConfig(
        version_selector=VersionSelector(
            version_selection_rules=[
                FixedRatioVersionSelectionRule(agent_version=version, traffic_percentage=100)
            ]
        )
    )


def pin_version(agents: Any, *, agent_name: str, version: str) -> None:
    agents.update_details(agent_name=agent_name, agent_endpoint=fixed_selector(version))


def _active_version(agents: Any, *, agent_name: str, version: str) -> str:
    try:
        info = agents.get_version(agent_name=agent_name, agent_version=version)
    except ResourceNotFoundError as exc:
        raise RoutingError(
            f"Agent {agent_name!r} routes to version {version!r}, but that version does not exist."
        ) from exc
    raw_status = getattr(info, "status", "") or ""
    status = str(getattr(raw_status, "value", raw_status)).strip().lower()
    if status != "active":
        raise RoutingError(
            f"Agent {agent_name!r} routes to version {version!r} with status "
            f"{status or 'unknown'!r}; expected 'active'."
        )
    return version


def _resolve_default_latest(agents: Any, *, agent_name: str) -> RoutingState:
    latest = next(
        iter(agents.list_versions(agent_name=agent_name, limit=1, order="desc")), None
    )
    version = getattr(latest, "version", None) or getattr(latest, "id", None)
    if not version or not str(version).strip():
        raise RoutingError(
            f"Agent {agent_name!r} exists with default-latest routing, "
            "but no versions were found."
        )
    active_version = _active_version(
        agents,
        agent_name=agent_name,
        version=str(version).strip(),
    )
    return RoutingState(True, active_version, False, True)


def inspect_current_route(agents: Any, *, agent_name: str) -> RoutingState:
    try:
        agent = agents.get(agent_name=agent_name)
    except ResourceNotFoundError:
        return RoutingState(False, None, False)
    endpoint = getattr(agent, "agent_endpoint", None)
    selector = getattr(endpoint, "version_selector", None) if endpoint is not None else None
    if selector is None:
        return _resolve_default_latest(agents, agent_name=agent_name)
    rules = getattr(selector, "version_selection_rules", None)
    if rules is None or len(rules) != 1:
        count = len(rules) if rules is not None else 0
        raise RoutingError(
            f"Unsupported version selection rules count: {count}. "
            "Expected exactly 1 fixed 100% rule."
        )
    rule = rules[0]
    if getattr(rule, "type", None) not in (None, "FixedRatio", "fixed_ratio"):
        raise RoutingError(
            f"Unsupported rule type: {getattr(rule, 'type', None)!r}. Expected 'FixedRatio'."
        )
    if getattr(rule, "traffic_percentage", None) != 100:
        percentage = getattr(rule, "traffic_percentage", None)
        raise RoutingError(f"Unsupported traffic percentage: {percentage}. Expected 100.")
    version = getattr(rule, "agent_version", None)
    if not version or not str(version).strip():
        raise RoutingError("Fixed ratio rule has empty or blank agent_version.")
    if str(version).strip() == "@latest":
        return _resolve_default_latest(agents, agent_name=agent_name)
    active_version = _active_version(
        agents,
        agent_name=agent_name,
        version=str(version).strip(),
    )
    return RoutingState(True, active_version, False)


def resolve_current_route(agents: Any, *, agent_name: str) -> RoutingState:
    route = inspect_current_route(agents, agent_name=agent_name)
    if not route.uses_default_latest:
        return route
    if route.previous_active_version is None:
        raise RoutingError(f"Agent {agent_name!r} has no active version to pin.")
    pin_version(agents, agent_name=agent_name, version=route.previous_active_version)
    return RoutingState(True, route.previous_active_version, True, True)


def deploy_release(
    project: Any,
    spec: RegisteredAgentSpec,
    config: DeploymentConfig,
    recorder: DeploymentRecorder,
    operations: AgentOperations,
) -> None:
    agents = project.agents
    recorder.set_project_endpoint(config.project_endpoint)
    recorder.set_agent_kind(spec.kind.value)
    recorder.set_phase(DeploymentPhase.resolving_current)
    route = resolve_current_route(agents, agent_name=spec.name)
    recorder.set_previous_active_version(route.previous_active_version)
    if route.pinned_during_resolution:
        recorder.set_phase(DeploymentPhase.current_pinned)
    metadata = recorder.version_metadata()
    candidate = operations.create_candidate(project, spec, config, metadata)
    recorder.set_candidate_version(candidate.version)
    recorder.set_artifact(
        candidate.artifact_type,
        candidate.artifact_reference,
        candidate.artifact_sha256,
        image_reference=candidate.image_reference,
        image_digest=candidate.image_digest,
    )
    recorder.set_foundry_dependencies(candidate)
    recorder.set_phase(DeploymentPhase.candidate_created)
    candidate = operations.wait_ready(project, spec, candidate)
    recorder.set_phase(DeploymentPhase.candidate_ready)
    try:
        if spec.kind is AgentKind.HOSTED:
            openai_client = project.get_openai_client(agent_name=spec.name)
            evidence = run_hosted_smoke(
                project,
                openai_client,
                agent_spec=spec,
                candidate_version=candidate.version,
            )
        else:
            openai_client = project.get_openai_client()
            evidence = run_prompt_smoke(
                openai_client,
                agent_spec=spec,
                candidate_version=candidate.version,
            )
    except SmokeTestError as exc:
        recorder.set_smoke_evidence(exc.evidence)
        raise
    recorder.set_smoke_evidence(evidence)
    recorder.set_phase(DeploymentPhase.smoke_passed)
    try:
        pin_version(agents, agent_name=spec.name, version=candidate.version)
    except (ServiceRequestError, ServiceResponseError) as exc:
        recorder.set_cutover_outcome("uncertain")
        try:
            observed = inspect_current_route(agents, agent_name=spec.name)
        except (AzureError, RoutingError) as reconciliation_error:
            raise exc from reconciliation_error
        recorder.set_observed_active_version(observed.previous_active_version)
        if (
            observed.uses_default_latest
            or observed.previous_active_version != candidate.version
        ):
            raise exc
        recorder.set_cutover_outcome("confirmed_after_transport_error")
    else:
        recorder.set_observed_active_version(candidate.version)
        recorder.set_cutover_outcome("confirmed")
    recorder.set_phase(DeploymentPhase.cutover_complete)
    recorder.succeed()
