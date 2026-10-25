import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# The Foundry host owns OpenTelemetry; flock installs its own tracer at import
# unless told not to, and importorskip below already imports it.
os.environ.setdefault("FLOCK_AUTO_TRACE", "false")

pytest.importorskip(
    "flock",
    reason="hosted runtime deps live in src/agents/incident_triage",
)

# Without an Application Insights connection string the host falls back to
# console exporters, which write to pytest's closed capture streams at exit.
# Set after the skip so the root test session is unaffected.
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src" / "agents" / "incident_triage"),
)

import dspy  # noqa: E402
import flock_app  # noqa: E402
import main  # noqa: E402
from azure.ai.agentserver.responses import InMemoryResponseProvider  # noqa: E402
from flock import FlockApplication, WorkflowContext  # noqa: E402
from flock.components.agent import EngineComponent  # noqa: E402
from flock.integrations.foundry import IdentityPolicy  # noqa: E402
from flock.registry import type_registry  # noqa: E402
from flock.utils.runtime import EvalResult  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

PROJECT_ENDPOINT = "https://example.services.ai.azure.com/api/projects/dev"
REPORT = "Checkout returns HTTP 500 for 30% of requests since the 14:05 deploy."

CANNED: dict[str, Any] = {
    "impact_assessor": flock_app.ImpactAssessment(
        severity="High",
        affected_scope="Checkout API and the mobile client",
        user_impact="Roughly a third of purchases fail",
    ),
    "root_cause_analyst": flock_app.RootCauseHypothesis(
        hypothesis=(
            "The 14:05 deploy shipped a schema change the payment adapter cannot deserialize."
        ),
        evidence=["Errors start at 14:05"],
        confidence=0.7,
    ),
    "incident_commander": flock_app.ActionPlan(
        immediate_steps=["Roll back the 14:05 deploy"],
        owner_role="Checkout on-call",
        comms_update="Checkout is degraded; a rollback is in progress.",
    ),
}


class OfflineEngine(EngineComponent):
    """Deterministic stand-in for the model: one canned artifact per agent."""

    fail_agent: str | None = None
    # Any, not a dict annotation: pydantic would copy a dict and lose the record.
    seen: Any = None

    async def evaluate(self, agent, ctx, inputs, output_group) -> EvalResult:
        if self.seen is not None:
            self.seen[agent.name] = sorted(artifact.type for artifact in inputs.artifacts)
        if agent.name == self.fail_agent:
            raise RuntimeError("model deployment unavailable")
        return EvalResult.from_object(CANNED[agent.name], agent=agent)


def _offline(**engine_fields: Any) -> flock_app.EngineFactory:
    return lambda context: OfflineEngine(**engine_fields)


def _context(workflow_id: str = "wf-1", **attributes: str) -> WorkflowContext:
    return WorkflowContext(workflow_id=workflow_id, attributes=attributes)


def _build() -> "flock_app.Flock":
    return flock_app.build_flock(_context(), engine_factory=_offline())


def _run(application: FlockApplication, workflow_id: str = "wf-1"):
    return asyncio.run(
        application.run(
            flock_app.IncidentReport(raw_report=REPORT),
            context=_context(workflow_id),
        )
    )


def _offline_host(application: FlockApplication, **kwargs: Any):
    kwargs.setdefault("identity", IdentityPolicy(local_development=True))
    return main.build_host(
        application,
        observability="none",
        store=InMemoryResponseProvider(),
        **kwargs,
    )


def _message_texts(response: dict) -> list[str]:
    return [
        part["text"]
        for item in response.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    ]


# -- crew topology ----------------------------------------------------------


def test_build_flock_registers_the_triage_crew() -> None:
    flock = _build()
    assert [agent.name for agent in flock.agents] == [
        "impact_assessor",
        "root_cause_analyst",
        "incident_commander",
    ]
    # FlockApplication refuses instances that would print result panels.
    assert flock.no_output is True


def test_parallel_agents_share_the_incident_report_subscription() -> None:
    flock = _build()
    consumed = {
        agent.name: {
            type_name
            for subscription in agent.subscriptions
            for type_name in subscription.type_names
        }
        for agent in flock.agents
    }
    report = type_registry.name_for(flock_app.IncidentReport)
    assert consumed["impact_assessor"] == {report}
    assert consumed["root_cause_analyst"] == {report}


def test_commander_declares_an_and_gate_over_both_analyses() -> None:
    flock = _build()
    commander = flock.get_agent("incident_commander")
    # One subscription naming both types is what makes Flock wait for both.
    assert len(commander.subscriptions) == 1
    assert set(commander.subscriptions[0].type_names) == {
        type_registry.name_for(flock_app.ImpactAssessment),
        type_registry.name_for(flock_app.RootCauseHypothesis),
    }
    published = {output.spec.type_name for output in commander.outputs}
    assert published == {type_registry.name_for(flock_app.ActionPlan)}


def test_each_workflow_gets_a_fresh_blackboard() -> None:
    """FlockApplication rejects a reused instance; the factory must build anew."""
    assert _build() is not _build()


# -- model engines ----------------------------------------------------------


def _model_flock(token_provider=lambda: "token"):
    factory = flock_app.dspy_engine_factory(
        model="azure/demo-chat-model",
        api_base=PROJECT_ENDPOINT,
        token_provider=token_provider,
        max_completion_tokens=1234,
    )
    return flock_app.build_flock(
        _context(**{"foundry.call_id": "call-1"}), engine_factory=factory
    )


def test_each_agent_gets_its_own_engine_on_the_project_endpoint() -> None:
    def token_provider() -> str:
        return "token"

    engines = [agent.engines[0] for agent in _model_flock(token_provider).agents]

    # Agent._resolve_engines mutates the engines it is handed.
    assert len({id(engine) for engine in engines}) == 3
    for engine in engines:
        assert engine.model == "azure/demo-chat-model"
        # LiteLLM's v1 path calls <project endpoint>/openai/v1.
        assert engine.lm_kwargs["api_base"] == PROJECT_ENDPOINT
        assert engine.lm_kwargs["api_version"] == "v1"
        # One provider per process, not per agent or workflow.
        assert engine.lm_kwargs["azure_ad_token_provider"] is token_provider
        assert engine.lm_kwargs["extra_headers"] == {"x-agent-foundry-call-id": "call-1"}
        assert engine.max_retries == 3
        # Whole artifacts are emitted, not tokens.
        assert engine.stream is False
        assert engine.no_output is True


def test_engines_send_max_completion_tokens_and_never_max_tokens() -> None:
    """Foundry chat models reject max_tokens.

    LiteLLM cannot infer the model family behind an Azure deployment name, so
    its own mapping never fires; the engine has to send the right parameter.
    """
    engine = _model_flock().agents[0].engines[0]

    lm = engine._create_lm(dspy, engine.model, engine._build_lm_kwargs())

    assert lm.kwargs["max_completion_tokens"] == 1234
    assert "max_tokens" not in lm.kwargs
    assert lm.num_retries == 3


def test_model_engine_factory_reads_the_injected_environment(monkeypatch) -> None:
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", PROJECT_ENDPOINT)
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "demo-chat-model")
    monkeypatch.delenv("MAX_COMPLETION_TOKENS", raising=False)
    main.model_engine_factory.cache_clear()
    try:
        with patch.object(
            main, "get_default_azure_token_provider", return_value=lambda: "token"
        ) as provider:
            first = main.model_engine_factory()(_context("wf-a"))
            second = main.model_engine_factory()(_context("wf-b"))
    finally:
        main.model_engine_factory.cache_clear()

    # Created once per process, for the Foundry audience.
    provider.assert_called_once_with(scopes=("https://ai.azure.com/.default",))
    assert first is not second
    assert first.lm_kwargs["azure_ad_token_provider"] is second.lm_kwargs[
        "azure_ad_token_provider"
    ]
    assert first.model == "azure/demo-chat-model"
    assert first.lm_kwargs["api_base"] == PROJECT_ENDPOINT
    assert first.max_completion_tokens == 4000


def test_dspy_input_type_warning_is_disabled() -> None:
    # Flock passes DSPy the artifact payload as a dict while annotating the
    # signature with the model class, so the check warns on every invocation and
    # can never be meaningful here.
    assert dspy.settings.warn_on_type_mismatch is False


# -- application contract ---------------------------------------------------


def test_application_exposes_all_three_artifacts_and_requires_the_plan() -> None:
    contract = main.application.contract
    assert contract.input_type is flock_app.IncidentReport
    assert contract.output_types == (
        flock_app.ImpactAssessment,
        flock_app.RootCauseHypothesis,
        flock_app.ActionPlan,
    )
    assert contract.required_output_types == (flock_app.ActionPlan,)
    assert main.application.admission.limit == main.MAX_ACTIVE_WORKFLOWS == 4
    assert main.application.default_timeout == 300


def test_module_exposes_the_adapter_as_the_asgi_app() -> None:
    assert main.app is main.host.app
    assert main.host.application is main.application


def test_format_artifact_labels_the_type() -> None:
    rendered = main.format_artifact(CANNED["impact_assessor"])
    assert rendered.startswith("ImpactAssessment\n")
    assert '"severity": "High"' in rendered


# -- offline end-to-end -------------------------------------------------------


def test_offline_cascade_publishes_all_three_artifacts() -> None:
    seen: dict[str, list[str]] = {}
    application = main.build_application(engine_factory=_offline(seen=seen))

    result = _run(application)

    assert result.status == "succeeded", result
    assert len(result.outputs) == 3
    values = [event.value for event in result.outputs]
    assert {type(value) for value in values} == {
        flock_app.ImpactAssessment,
        flock_app.RootCauseHypothesis,
        flock_app.ActionPlan,
    }
    # The join can only publish after both analyses.
    assert isinstance(values[-1], flock_app.ActionPlan)
    assert seen["incident_commander"] == sorted(
        [
            type_registry.name_for(flock_app.ImpactAssessment),
            type_registry.name_for(flock_app.RootCauseHypothesis),
        ]
    )


def test_a_failing_agent_fails_the_workflow_and_logs_the_cause(capfd) -> None:
    """Callers get a safe failure code; operators find the cause in the log.

    Without the analysis the AND gate never fires, so fewer than three items
    exist and the smoke gate rejects the candidate.
    """
    application = main.build_application(
        engine_factory=_offline(fail_agent="impact_assessor")
    )

    result = _run(application)

    assert result.status == "failed"
    assert result.failure.code == "agent_failed"
    assert result.failure.agent == "impact_assessor"
    assert "model deployment unavailable" not in result.failure.message
    assert flock_app.ActionPlan not in {type(event.value) for event in result.outputs}
    # Flock logs agent failures (with traceback) to stdout.
    captured = capfd.readouterr()
    assert "Agent 'impact_assessor' failed" in captured.out + captured.err
    assert "model deployment unavailable" in captured.out + captured.err


# -- HTTP (in-process ASGI) -----------------------------------------------------


def test_http_readiness_and_one_message_item_per_artifact() -> None:
    host = _offline_host(main.build_application(engine_factory=_offline()))

    with TestClient(host.app) as client:
        readiness = client.get("/readiness")
        body = client.post("/responses", json={"input": REPORT}).json()

    assert readiness.status_code == 200
    assert body["status"] == "completed", body
    texts = _message_texts(body)
    assert len(texts) == 3
    assert {text.split("\n", 1)[0] for text in texts} == {
        "ImpactAssessment",
        "RootCauseHypothesis",
        "ActionPlan",
    }
    assert texts[-1].startswith("ActionPlan\n")


def test_http_stream_emits_one_message_item_per_artifact() -> None:
    host = _offline_host(main.build_application(engine_factory=_offline()))

    with TestClient(host.app) as client:
        streamed = client.post("/responses", json={"input": REPORT, "stream": True}).text

    events = [
        json.loads(line[len("data: ") :])
        for line in streamed.splitlines()
        if line.startswith("data: ")
    ]
    done = [
        event
        for event in events
        if event["type"] == "response.output_item.done" and event["item"]["type"] == "message"
    ]
    assert len(done) == 3
    assert events[-1]["type"] == "response.completed"


def test_http_agent_failure_is_a_safe_failure_code() -> None:
    host = _offline_host(
        main.build_application(engine_factory=_offline(fail_agent="root_cause_analyst"))
    )

    with TestClient(host.app) as client:
        body = client.post("/responses", json={"input": REPORT}).json()

    assert body["status"] == "failed"
    assert body["error"]["code"] == "server_error"
    assert body["error"]["message"].startswith("agent_failed:")
    assert "model deployment unavailable" not in json.dumps(body)


def test_http_requires_the_gateway_identity_outside_local_development() -> None:
    """Without FLOCK_FOUNDRY_LOCAL_DEV=1 a direct call has no trusted principal."""
    host = _offline_host(
        main.build_application(engine_factory=_offline()), identity=IdentityPolicy()
    )

    with TestClient(host.app) as client:
        body = client.post("/responses", json={"input": REPORT}).json()

    assert body["status"] == "failed"
    assert body["error"]["code"] == "invalid_prompt"
