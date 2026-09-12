"""Incident triage hosted behind the Foundry Responses protocol, powered by Flock.

One HTTP turn drives one blackboard run: the caller's free text is published as
an :class:`~flock_app.IncidentReport`, Flock cascades it through the triage crew,
and each artifact the crew produced is returned as its own Responses output item
so the multi-agent flow is visible to the caller rather than collapsed into prose.
"""

import asyncio
import logging
import os

os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")

from azure.ai.agentserver.core.tasks import set_resilient_tasks_enabled
from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponseEventStream,
    ResponsesAgentServerHost,
)
from dotenv import load_dotenv
from flock import Flock
from flock.models.system_artifacts import WorkflowError
from pydantic import BaseModel

from flock_app import (
    ActionPlan,
    ImpactAssessment,
    IncidentReport,
    RootCauseHypothesis,
    build_flock,
)
from model_endpoint import resolve_api_base, resolve_api_version

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("incident-triage")

set_resilient_tasks_enabled(True)

# The crew makes three sequential-ish LLM calls (two in parallel, then the join),
# so the cap is generous relative to a single completion.
TRIAGE_TIMEOUT_SECONDS = 300

app = ResponsesAgentServerHost()

_flock: Flock | None = None
_BUILD_LOCK = asyncio.Lock()
# run_until_idle() waits on every scheduled task in the process, not just this
# turn's cascade, so turns are serialized. CorrelatedContextProvider already
# isolates what each agent *sees*; this lock is what makes the *wait*
# deterministic. Throughput is traded for correctness, which is the right call
# for a demo agent.
_TURN_LOCK = asyncio.Lock()


async def get_flock() -> Flock:
    """Return the process-wide blackboard, building it on first use."""
    global _flock
    async with _BUILD_LOCK:
        if _flock is None:
            load_dotenv(override=False)
            _flock = build_flock(
                model_deployment=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
                api_base=resolve_api_base(),
                api_version=resolve_api_version(),
            )
            LOGGER.info("Flock blackboard initialized with %d agents", len(_flock.agents))
    return _flock


class TriageError(RuntimeError):
    """The cascade ended without producing the full artifact set."""


def describe_workflow_errors(errors: list[WorkflowError]) -> str:
    """Summarise why agents dropped out of a cascade.

    Flock does not propagate an agent failure to the caller: it publishes a
    WorkflowError artifact and lets the orchestrator go idle. Without this the
    only symptom is a missing artifact, which says nothing about the cause.
    """
    if not errors:
        return "no agent reported an error"
    return "; ".join(
        f"{error.failed_agent}: {error.error_type}: {error.error_message}"
        for error in errors
    )


def _require_one(
    artifacts: list[BaseModel],
    *,
    artifact_type: type[BaseModel],
    errors: list[WorkflowError],
) -> BaseModel:
    if not artifacts:
        raise TriageError(
            f"Blackboard produced no {artifact_type.__name__}; the triage cascade "
            f"did not complete ({describe_workflow_errors(errors)})."
        )
    return artifacts[-1]


async def run_triage(raw_report: str, *, correlation_id: str) -> list[BaseModel]:
    """Publish one report and collect the three artifacts its cascade produced."""
    flock = await get_flock()
    async with _TURN_LOCK:
        await flock.publish(
            IncidentReport(raw_report=raw_report),
            correlation_id=correlation_id,
        )
        await flock.run_until_idle(timeout=TRIAGE_TIMEOUT_SECONDS)
        collected = [
            await flock.store.get_by_type(artifact_type, correlation_id=correlation_id)
            for artifact_type in (ImpactAssessment, RootCauseHypothesis, ActionPlan)
        ]
        errors = await flock.store.get_by_type(WorkflowError, correlation_id=correlation_id)
    return [
        _require_one(artifacts, artifact_type=artifact_type, errors=errors)
        for artifacts, artifact_type in zip(
            collected,
            (ImpactAssessment, RootCauseHypothesis, ActionPlan),
            strict=True,
        )
    ]


def format_artifact(artifact: BaseModel) -> str:
    """Render one blackboard artifact as a labelled JSON block."""
    return f"{type(artifact).__name__}\n{artifact.model_dump_json(indent=2)}"


@app.response_handler
async def handler(
    request: CreateResponse,
    context: ResponseContext,
    cancellation_signal: asyncio.Event,
):
    """Run the blackboard once and emit one output item per artifact."""
    # Everything that can fail runs *after* response.created. A handler that
    # raises before the first event leaves the host with no response to fail,
    # so it answers HTTP 500 with a generic body and the real cause survives
    # only in the container log. Emitting first turns the same failure into a
    # response.failed the caller can read.
    stream = ResponseEventStream(response_id=context.response_id, request=request)
    yield stream.emit_created()
    yield stream.emit_in_progress()
    try:
        raw_report = await context.get_input_text()
        artifacts = await run_triage(raw_report, correlation_id=context.response_id)
    except Exception as exc:
        LOGGER.exception("Triage cascade failed")
        yield stream.emit_failed(message=str(exc))
        return
    for artifact in artifacts:
        if cancellation_signal.is_set():
            break
        for event in stream.output_item_message(format_artifact(artifact)):
            yield event
    yield stream.emit_completed()


def main() -> None:
    LOGGER.info("Starting incident-triage Responses server on port 8088")
    app.run()


if __name__ == "__main__":
    main()
