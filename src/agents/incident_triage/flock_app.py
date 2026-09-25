"""Flock blackboard crew that triages an incident report into an action plan.

The agents are wired by *type subscription only*. ``impact_assessor`` and
``root_cause_analyst`` both consume :class:`IncidentReport`, so Flock runs them
concurrently. ``incident_commander`` consumes both of their output types, which
Flock treats as an AND gate: it fires once, after both have published. No edges
are declared anywhere -- the topology is a consequence of the contracts below.

:func:`build_application` wraps the crew in a :class:`flock.FlockApplication`:
every Responses turn runs as its own isolated workflow on a fresh blackboard,
so concurrent turns neither wait for nor see each other.
"""

from collections.abc import Callable
from typing import Any

import dspy
from flock import Flock, FlockApplication, WorkflowContext, flock_type
from flock.components.agent import EngineComponent
from flock.engines import DSPyEngine
from flock.integrations.foundry import foundry_headers
from pydantic import BaseModel, Field

# Flock hands DSPy the artifact payload as a dict (`_validate_input_payload`
# returns `model_dump()`) while annotating the signature field with the model
# class, so DSPy's `check_type` fails and warns on every single-input agent
# invocation. The check can never be meaningful here -- DSPy is driven only by
# Flock, which always passes dicts -- so the warning is switched off at import
# time, on the main thread, because DSPy only lets the configuring thread change
# settings.
dspy.settings.configure(warn_on_type_mismatch=False)

SEVERITY_PATTERN = "^(Critical|High|Medium|Low)$"

# Current Foundry chat models reject `max_tokens`, and LiteLLM cannot infer the
# model family behind an Azure *deployment* name. DSPyEngine sends this bound as
# `max_completion_tokens` instead (never both), so the output stays capped.
MAX_COMPLETION_TOKENS = 4000

# The crew makes three LLM calls (two in parallel, then the join).
TRIAGE_TIMEOUT_SECONDS = 300

# Concurrent turns per replica (1 CPU / 2 GiB). Further turns are answered with
# rate_limit_exceeded instead of queueing or exhausting the container.
MAX_ACTIVE_WORKFLOWS = 8


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


# Every artifact the crew publishes is part of the public answer, in this order.
PUBLIC_OUTPUTS = (ImpactAssessment, RootCauseHypothesis, ActionPlan)

EngineBuilder = Callable[[WorkflowContext], EngineComponent]


def dspy_engine_builder(
    *,
    model: str,
    api_base: str,
    api_version: str,
    token_provider: Callable[..., str],
) -> EngineBuilder:
    """Return a builder producing one engine per agent and workflow.

    Agents must not share an engine instance: ``Agent._resolve_engines`` mutates
    engine attributes on the instances it is handed. The token provider, by
    contrast, is shared process-wide: LiteLLM caches its Azure client per
    provider and the provider refreshes tokens itself.
    """

    def build(context: WorkflowContext) -> DSPyEngine:
        return DSPyEngine(
            model=model,
            no_output=True,
            # The adapter emits whole artifacts rather than tokens, so per-token
            # streaming buys nothing -- and LiteLLM's StreamWrapper breaks under
            # the hosting runtime's GenAI tracing instrumentation.
            stream=False,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
            lm_kwargs={
                "api_base": api_base,
                "api_version": api_version,
                "azure_ad_token_provider": token_provider,
                # Correlates model calls with the Foundry request (not an auth key).
                "extra_headers": foundry_headers(context),
            },
        )

    return build


def build_flock(context: WorkflowContext, *, model: str, new_engine: EngineBuilder) -> Flock:
    """Build the triage blackboard for one workflow."""
    flock = Flock(model, no_output=True)
    (
        flock.agent("impact_assessor")
        .description("Rates incident severity, affected scope, and user-visible impact.")
        .consumes(IncidentReport)
        .publishes(ImpactAssessment)
        .with_engines(new_engine(context))
    )
    (
        flock.agent("root_cause_analyst")
        .description("Proposes the most probable root cause and the evidence behind it.")
        .consumes(IncidentReport)
        .publishes(RootCauseHypothesis)
        .with_engines(new_engine(context))
    )
    # AND gate: two consumed types, so this agent waits for both publishers.
    (
        flock.agent("incident_commander")
        .description("Turns an impact assessment and a root cause into an owned action plan.")
        .consumes(ImpactAssessment, RootCauseHypothesis)
        .publishes(ActionPlan)
        .with_engines(new_engine(context))
    )
    return flock


def build_application(*, model: str, new_engine: EngineBuilder, **options: Any) -> FlockApplication:
    """The triage crew as a transport-independent Flock application.

    All three artifacts are public and required: a turn succeeds only if both
    parallel agents *and* the joining commander published.
    """
    options.setdefault("default_timeout", TRIAGE_TIMEOUT_SECONDS)
    options.setdefault("max_active_workflows", MAX_ACTIVE_WORKFLOWS)
    return FlockApplication(
        factory=lambda context: build_flock(context, model=model, new_engine=new_engine),
        input_type=IncidentReport,
        output_types=PUBLIC_OUTPUTS,
        required_output_types=PUBLIC_OUTPUTS,
        **options,
    )
