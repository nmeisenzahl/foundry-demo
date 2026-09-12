"""Flock blackboard crew that triages an incident report into an action plan.

The agents are wired by *type subscription only*. ``impact_assessor`` and
``root_cause_analyst`` both consume :class:`IncidentReport`, so Flock runs them
concurrently. ``incident_commander`` consumes both of their output types, which
Flock treats as an AND gate: it fires once, after both have published. No edges
are declared anywhere -- the topology is a consequence of the contracts below.
"""

from collections.abc import Callable
from typing import Any

import dspy
from flock import Flock, flock_type
from flock.core.context_provider import CorrelatedContextProvider
from flock.engines import DSPyEngine
from flock.engines.auth.azure import get_default_azure_token_provider
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

# DSPyEngine always sends `max_tokens`, which current Foundry chat models reject
# in favour of `max_completion_tokens`. LiteLLM cannot infer the model family
# behind an Azure *deployment* name, so its own parameter mapping does not fire
# and the parameter has to be dropped explicitly. DSPyEngine reserves
# `max_completion_tokens` without ever setting it, so no replacement cap can be
# supplied and the model's default output limit applies.
DROPPED_LM_PARAMS = ["max_tokens"]


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


def _engine_factory(
    *,
    model: str,
    api_base: str,
    api_version: str,
    token_provider: Callable[..., str],
) -> Callable[[], DSPyEngine]:
    """Return a factory producing one engine per agent.

    Agents must not share an engine instance: ``Agent._resolve_engines`` mutates
    engine attributes on the instances it is handed.
    """

    def build() -> DSPyEngine:
        return DSPyEngine(
            model=model,
            # Engines only inherit the orchestrator's no_output at execution
            # time; setting it here keeps result panels out of container logs
            # from the first run.
            no_output=True,
            # The handler emits whole artifacts rather than tokens, so per-token
            # streaming buys nothing -- and LiteLLM's StreamWrapper breaks under
            # the hosting runtime's GenAI tracing instrumentation.
            stream=False,
            lm_kwargs={
                "api_base": api_base,
                "api_version": api_version,
                "azure_ad_token_provider": token_provider,
                "additional_drop_params": DROPPED_LM_PARAMS,
            },
        )

    return build


def build_flock(
    *,
    model_deployment: str,
    api_base: str,
    api_version: str,
    token_provider: Callable[..., str] | None = None,
    **flock_kwargs: Any,
) -> Flock:
    """Build the triage blackboard.

    The Foundry account has local authentication disabled, so inference is
    reached with the container's managed identity through LiteLLM's Azure AD
    token provider hook rather than an API key.
    """
    model = f"azure/{model_deployment}"
    provider = token_provider if token_provider is not None else get_default_azure_token_provider()
    new_engine = _engine_factory(
        model=model,
        api_base=api_base,
        api_version=api_version,
        token_provider=provider,
    )

    flock = Flock(
        model,
        no_output=True,
        context_provider=CorrelatedContextProvider(),
        **flock_kwargs,
    )

    (
        flock.agent("impact_assessor")
        .description("Rates incident severity, affected scope, and user-visible impact.")
        .consumes(IncidentReport)
        .publishes(ImpactAssessment)
        .with_engines(new_engine())
    )
    (
        flock.agent("root_cause_analyst")
        .description("Proposes the most probable root cause and the evidence behind it.")
        .consumes(IncidentReport)
        .publishes(RootCauseHypothesis)
        .with_engines(new_engine())
    )
    # AND gate: two consumed types, so this agent waits for both publishers.
    (
        flock.agent("incident_commander")
        .description("Turns an impact assessment and a root cause into an owned action plan.")
        .consumes(ImpactAssessment, RootCauseHypothesis)
        .publishes(ActionPlan)
        .with_engines(new_engine())
    )

    return flock
