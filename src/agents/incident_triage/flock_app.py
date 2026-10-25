"""Flock blackboard crew that triages an incident report into an action plan.

The agents are wired by *type subscription only*. ``impact_assessor`` and
``root_cause_analyst`` both consume :class:`IncidentReport`, so Flock runs them
concurrently. ``incident_commander`` consumes both of their output types, which
Flock treats as an AND gate: it fires once, after both have published. No edges
are declared anywhere -- the topology is a consequence of the contracts below.

:func:`build_flock` is the per-workflow factory: the hosting layer calls it
once per request, so every request gets its own blackboard.
"""

from collections.abc import Callable

import dspy
from flock import DSPyEngine, Flock, WorkflowContext, flock_type
from flock.components.agent import EngineComponent
from flock.integrations.foundry import foundry_headers
from pydantic import BaseModel, Field

# Flock hands DSPy the artifact payload as a dict (`_validate_input_payload`
# returns `model_dump()`) while annotating the signature field with the model
# class, so DSPy's `check_type` fails and warns on every single-input agent
# invocation. Still true in flock-core 0.5.700. The check can never be
# meaningful here -- DSPy is driven only by Flock, which always passes dicts --
# so the warning is switched off at import time, on the main thread, because
# DSPy only lets the configuring thread change settings.
dspy.settings.configure(warn_on_type_mismatch=False)

SEVERITY_PATTERN = "^(Critical|High|Medium|Low)$"

# LiteLLM's Azure v1 path: with this version it calls
# `<FOUNDRY_PROJECT_ENDPOINT>/openai/v1`, the URL AIProjectClient uses.
PROJECT_API_VERSION = "v1"

EngineFactory = Callable[[WorkflowContext], EngineComponent]


@flock_type
class IncidentReport(BaseModel):
    """The raw report as it arrives from a caller."""

    raw_report: str = Field(min_length=1, description="Unstructured incident description.")
    reported_by: str | None = Field(default=None, description="Who raised the incident.")


@flock_type
class ImpactAssessment(BaseModel):
    """How bad it is, independent of why it happened."""

    severity: str = Field(pattern=SEVERITY_PATTERN)
    affected_scope: str = Field(min_length=10, description="Services and surfaces affected.")
    user_impact: str = Field(min_length=10, description="What users actually experience.")


@flock_type
class RootCauseHypothesis(BaseModel):
    """Why it is happening, independent of how bad it is."""

    hypothesis: str = Field(min_length=40)
    evidence: list[str] = Field(min_length=1, description="Signals supporting the hypothesis.")
    confidence: float = Field(ge=0.0, le=1.0)


@flock_type
class ActionPlan(BaseModel):
    """The join of impact and root cause."""

    immediate_steps: list[str] = Field(min_length=1)
    owner_role: str = Field(min_length=3, description="Role that owns the response.")
    comms_update: str = Field(min_length=10, description="One-paragraph status update.")


def dspy_engine_factory(
    *,
    model: str,
    api_base: str,
    token_provider: Callable[..., str],
    max_completion_tokens: int,
) -> EngineFactory:
    """Return a factory producing one model-backed engine per agent and workflow.

    Agents must not share an engine instance: ``Agent._resolve_engines`` mutates
    engine attributes on the instances it is handed. ``token_provider`` is shared
    instead: it is created once per process and refreshes tokens itself.
    """

    def build(context: WorkflowContext) -> DSPyEngine:
        return DSPyEngine(
            model=model,
            # Sent as `max_completion_tokens`, never `max_tokens`: current
            # Foundry chat models reject `max_tokens`, and LiteLLM cannot map it
            # for an Azure *deployment* name.
            max_completion_tokens=max_completion_tokens,
            max_retries=3,
            no_output=True,
            # Responses are streamed per artifact, not per token, so token
            # streaming buys nothing -- and LiteLLM's StreamWrapper breaks under
            # the hosting runtime's GenAI tracing instrumentation.
            stream=False,
            lm_kwargs={
                "api_base": api_base,
                "api_version": PROJECT_API_VERSION,
                "azure_ad_token_provider": token_provider,
                # Correlates model calls with the Foundry request (not an auth key).
                "extra_headers": foundry_headers(context),
            },
        )

    return build


def build_flock(context: WorkflowContext, *, engine_factory: EngineFactory) -> Flock:
    """Build a fresh triage blackboard for one workflow."""
    flock = Flock(no_output=True)

    (
        flock.agent("impact_assessor")
        .description("Rates incident severity, affected scope, and user-visible impact.")
        .consumes(IncidentReport)
        .publishes(ImpactAssessment)
        .with_engines(engine_factory(context))
    )
    (
        flock.agent("root_cause_analyst")
        .description("Proposes the most probable root cause and the evidence behind it.")
        .consumes(IncidentReport)
        .publishes(RootCauseHypothesis)
        .with_engines(engine_factory(context))
    )
    # AND gate: two consumed types, so this agent waits for both publishers.
    (
        flock.agent("incident_commander")
        .description("Turns an impact assessment and a root cause into an owned action plan.")
        .consumes(ImpactAssessment, RootCauseHypothesis)
        .publishes(ActionPlan)
        .with_engines(engine_factory(context))
    )

    return flock
