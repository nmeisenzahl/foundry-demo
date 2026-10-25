"""Incident triage hosted behind the Foundry Responses protocol, powered by Flock.

Every Responses request runs as its own isolated Flock workflow: the caller's
free text becomes an :class:`~flock_app.IncidentReport`, a fresh blackboard
cascades it through the triage crew, and each artifact the crew produced is
streamed as its own Responses output item, so the multi-agent flow is visible to
the caller rather than collapsed into prose.

HTTP, SSE, background mode, cancellation, readiness and shutdown are owned by
Flock's ``FoundryResponsesAdapter`` on top of the official agentserver SDK.
"""

import os

# Let the Foundry host own OpenTelemetry: must be set before importing flock.
os.environ.setdefault("FLOCK_AUTO_TRACE", "false")

from collections.abc import Mapping  # noqa: E402
from functools import cache  # noqa: E402
from typing import Any  # noqa: E402

from flock import FlockApplication, WorkflowContext  # noqa: E402
from flock.engines.auth.azure import get_default_azure_token_provider  # noqa: E402
from flock.integrations.foundry import FoundryResponsesAdapter, IdentityPolicy  # noqa: E402
from flock.logging.logging import configure_logging  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from flock_app import (  # noqa: E402
    ActionPlan,
    EngineFactory,
    ImpactAssessment,
    IncidentReport,
    RootCauseHypothesis,
    build_flock,
    dspy_engine_factory,
)

# With auto-tracing off, Flock's loggers default to ERROR, which would hide the
# adapter's warnings, such as why /readiness answers 503. Agent failures are
# logged at ERROR, with the exception, either way.
configure_logging(flock_level="WARNING", external_level="WARNING")

# Project-endpoint inference (`<FOUNDRY_PROJECT_ENDPOINT>/openai/v1`) takes a
# token for the Foundry audience, not the Cognitive Services one.
FOUNDRY_SCOPE = "https://ai.azure.com/.default"

# The crew makes three LLM calls (two in parallel, then the join), so the
# deadline is generous relative to a single completion.
TRIAGE_TIMEOUT_SECONDS = 300


def max_active_workflows(environ: Mapping[str, str] = os.environ) -> int:
    """Concurrent workflows admitted per replica.

    Every request builds its own blackboard and runs three model calls. Requests
    beyond this bound are answered with `rate_limit_exceeded` instead of queueing.
    """
    return int(environ.get("MAX_ACTIVE_WORKFLOWS", "4"))


MAX_ACTIVE_WORKFLOWS = max_active_workflows()


@cache
def model_engine_factory() -> EngineFactory:
    """Return the engine factory backed by the Foundry project's model.

    Resolved on first use, not at import, so the module imports without Azure
    configuration. Cached, so the token provider (and its credential) is created
    once per process and shared by every workflow. A missing variable fails the
    contract check behind ``GET /readiness`` and is logged there.
    """
    return dspy_engine_factory(
        model=f"azure/{os.environ['AZURE_AI_MODEL_DEPLOYMENT_NAME']}",
        api_base=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        token_provider=get_default_azure_token_provider(scopes=(FOUNDRY_SCOPE,)),
        max_completion_tokens=int(os.environ.get("MAX_COMPLETION_TOKENS", "4000")),
    )


def build_application(
    *,
    engine_factory: EngineFactory | None = None,
    max_active_workflows: int = MAX_ACTIVE_WORKFLOWS,
) -> FlockApplication:
    """Build the application; ``engine_factory`` defaults to the Foundry model."""

    def factory(context: WorkflowContext):
        return build_flock(
            context,
            engine_factory=engine_factory or model_engine_factory(),
        )

    return FlockApplication(
        factory=factory,
        input_type=IncidentReport,
        # All three artifacts are public outputs, streamed one item each.
        output_types=(ImpactAssessment, RootCauseHypothesis, ActionPlan),
        # The AND gate means an ActionPlan implies both analyses; without one
        # the workflow fails even if no agent raised.
        required_output_types=(ActionPlan,),
        default_timeout=TRIAGE_TIMEOUT_SECONDS,
        max_active_workflows=max_active_workflows,
    )


def format_artifact(artifact: BaseModel) -> str:
    """Render one blackboard artifact as a labelled JSON block."""
    return f"{type(artifact).__name__}\n{artifact.model_dump_json(indent=2)}"


def build_host(
    application: FlockApplication,
    *,
    identity: IdentityPolicy | None = None,
    **adapter_kwargs: Any,
) -> FoundryResponsesAdapter:
    """Expose ``application`` over the Foundry Responses protocol."""
    if identity is None:
        identity = IdentityPolicy(
            # Nothing is partitioned by principal: turns are stateless and every
            # output is public. A request without `x-agent-user-id` therefore
            # runs with principal_id=None instead of being rejected.
            required=False,
            # Trust the header outside Foundry only for explicit local runs.
            local_development=os.environ.get("FLOCK_FOUNDRY_LOCAL_DEV") == "1",
        )
    return FoundryResponsesAdapter(
        application,
        history_mode="stateless",
        input_mapper=lambda turn: IncidentReport(raw_report=turn.text),
        output_mapper=format_artifact,
        identity=identity,
        **adapter_kwargs,
    )


application = build_application()
host = build_host(application)
app = host.app  # ASGI entry point; serve it as the root app.


if __name__ == "__main__":
    host.run()
