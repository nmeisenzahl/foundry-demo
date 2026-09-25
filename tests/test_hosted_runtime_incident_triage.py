import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip(
    "azure.ai.agentserver.responses",
    reason="hosted runtime deps live in src/agents/incident_triage",
)

os.environ.setdefault("FLOCK_AUTO_TRACE", "false")

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src" / "agents" / "incident_triage"),
)

import flock_app  # noqa: E402
import main  # noqa: E402
from azure.ai.agentserver.responses import InMemoryResponseProvider  # noqa: E402
from flock import WorkflowContext, WorkflowStatus  # noqa: E402
from flock.components.agent import EngineComponent  # noqa: E402
from flock.integrations.foundry import FoundryResponsesAdapter, IdentityPolicy  # noqa: E402
from flock.registry import type_registry  # noqa: E402
from flock.utils.runtime import EvalResult  # noqa: E402
from model_endpoint import (  # noqa: E402
    DEFAULT_API_VERSION,
    resolve_api_base,
    resolve_api_version,
    resolve_model_endpoint,
)
from starlette.testclient import TestClient  # noqa: E402

PROJECT_ENDPOINT = "https://example.services.ai.azure.com/api/projects/dev"
MODEL = "azure/demo-chat-model"
CONTEXT = WorkflowContext("resp-test", attributes={"foundry.call_id": "call-1"})


def _dspy_builder():
    return flock_app.dspy_engine_builder(
        model=MODEL,
        api_base="https://example.services.ai.azure.com",
        api_version=DEFAULT_API_VERSION,
        token_provider=lambda: "token",
    )


def _build():
    return flock_app.build_flock(CONTEXT, model=MODEL, new_engine=_dspy_builder())


# -- crew topology -----------------------------------------------------------


def test_build_flock_registers_the_triage_crew() -> None:
    flock = _build()
    assert [agent.name for agent in flock.agents] == [
        "impact_assessor",
        "root_cause_analyst",
        "incident_commander",
    ]
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


def test_each_agent_gets_its_own_engine_with_a_bounded_completion() -> None:
    flock = _build()
    engines = [agent.engines[0] for agent in flock.agents]
    assert len({id(engine) for engine in engines}) == 3
    for engine in engines:
        assert engine.model == MODEL
        assert engine.lm_kwargs["api_base"] == "https://example.services.ai.azure.com"
        assert engine.lm_kwargs["api_version"] == DEFAULT_API_VERSION
        assert engine.lm_kwargs["azure_ad_token_provider"]() == "token"
        assert engine.lm_kwargs["extra_headers"] == {"x-agent-foundry-call-id": "call-1"}
        # Foundry chat models reject max_tokens: the cap is sent as
        # max_completion_tokens instead of being dropped.
        assert engine.max_completion_tokens == flock_app.MAX_COMPLETION_TOKENS
        assert "additional_drop_params" not in engine.lm_kwargs
        assert engine.stream is False
        assert engine.no_output is True


def test_the_lm_sends_only_max_completion_tokens() -> None:
    import dspy

    engine = _build().agents[0].engines[0]
    lm = engine._create_lm(dspy, engine.model, engine._build_lm_kwargs())

    assert lm.kwargs["max_completion_tokens"] == flock_app.MAX_COMPLETION_TOKENS
    assert "max_tokens" not in lm.kwargs


def test_dspy_input_type_warning_is_disabled() -> None:
    import dspy

    assert dspy.settings.warn_on_type_mismatch is False


# -- model endpoint ----------------------------------------------------------


def test_resolve_model_endpoint_strips_the_project_path() -> None:
    assert resolve_model_endpoint(PROJECT_ENDPOINT) == "https://example.services.ai.azure.com"


@pytest.mark.parametrize(
    ("endpoint", "message"),
    [
        ("", "empty"),
        ("http://example.services.ai.azure.com", "https"),
        ("https:///api/projects/dev", "host"),
        ("https://user:pw@example.services.ai.azure.com", "unsupported"),
    ],
)
def test_resolve_model_endpoint_rejects_bad_input(endpoint: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        resolve_model_endpoint(endpoint)


def test_resolve_api_base_prefers_explicit_override() -> None:
    env = {
        "FOUNDRY_PROJECT_ENDPOINT": PROJECT_ENDPOINT,
        "AZURE_API_BASE": "https://override.openai.azure.com/",
    }
    assert resolve_api_base(env) == "https://override.openai.azure.com"
    assert resolve_api_base({"FOUNDRY_PROJECT_ENDPOINT": PROJECT_ENDPOINT}) == (
        "https://example.services.ai.azure.com"
    )


def test_resolve_api_version_defaults_and_overrides() -> None:
    assert resolve_api_version({}) == DEFAULT_API_VERSION
    assert resolve_api_version({"AZURE_API_VERSION": " 2030-01-01 "}) == "2030-01-01"


def test_format_artifact_labels_the_type() -> None:
    rendered = main.format_artifact(
        flock_app.ImpactAssessment(
            severity="High",
            affected_scope="Checkout API and the mobile client",
            user_impact="Roughly a third of purchases fail",
        )
    )
    assert rendered.startswith("ImpactAssessment\n")
    assert '"severity": "High"' in rendered


# -- hosted runtime (real Flock + real AgentServer host, fake model) ------------


class FakeCrewEngine(EngineComponent):
    """Deterministic stand-in for the LLM: same artifacts, no network."""

    delay: float = 0.0
    fail_agent: str | None = None
    log: Any = None

    async def evaluate(self, agent, ctx, inputs, output_group) -> EvalResult:
        if self.log is not None:
            self.log.append(agent.name)
        await asyncio.sleep(self.delay)
        if agent.name == self.fail_agent:
            raise RuntimeError("secret: Principal does not have access to API/Operation.")
        produced = {
            "impact_assessor": flock_app.ImpactAssessment(
                severity="High",
                affected_scope="Checkout API and the mobile client",
                user_impact="Roughly a third of purchases fail",
            ),
            "root_cause_analyst": flock_app.RootCauseHypothesis(
                hypothesis="The 14:05 deploy shipped a schema change the payment adapter rejects.",
                evidence=["Errors start at 14:05"],
                confidence=0.7,
            ),
            "incident_commander": flock_app.ActionPlan(
                immediate_steps=["Roll back the 14:05 deploy"],
                owner_role="Checkout on-call",
                comms_update="Checkout is degraded; a rollback is in progress.",
            ),
        }[agent.name]
        return EvalResult.from_object(produced, agent=agent)


def _host(**engine_options) -> FoundryResponsesAdapter:
    application = flock_app.build_application(
        model=MODEL, new_engine=lambda _context: FakeCrewEngine(**engine_options)
    )
    return FoundryResponsesAdapter(
        application,
        input_mapper=main.to_report,
        output_mapper=main.format_artifact,
        identity=IdentityPolicy(local_development=True),
        observability="none",
        store=InMemoryResponseProvider(),
    )


def _message_texts(response: dict) -> list[str]:
    return [
        part["text"]
        for item in response.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
    ]


def test_turn_returns_one_message_per_artifact() -> None:
    with TestClient(_host().app) as client:
        body = client.post("/responses", json={"input": "checkout is failing"}).json()

    assert body["status"] == "completed"
    assert [text.split("\n", 1)[0] for text in _message_texts(body)] == [
        "ImpactAssessment",
        "RootCauseHypothesis",
        "ActionPlan",
    ]


def test_streamed_turn_emits_artifacts_as_they_are_published() -> None:
    with TestClient(_host().app) as client:
        stream = client.post(
            "/responses", json={"input": "checkout is failing", "stream": True}
        ).text

    events = [json.loads(line[6:]) for line in stream.splitlines() if line.startswith("data: ")]
    types = [event["type"] for event in events]
    assert types[0] == "response.created"
    assert types[-1] == "response.completed"
    assert types.count("response.output_item.done") == 3


def test_failed_agent_fails_the_turn_without_leaking_the_error() -> None:
    with TestClient(_host(fail_agent="impact_assessor").app) as client:
        body = client.post("/responses", json={"input": "checkout is failing"}).json()

    assert body["status"] == "failed"
    assert "agent_failed" in body["error"]["message"]
    assert "Principal does not have access" not in json.dumps(body)


def test_concurrent_turns_run_in_parallel_on_isolated_blackboards() -> None:
    """The old runtime serialized every turn behind a process-wide lock."""
    application = flock_app.build_application(
        model=MODEL, new_engine=lambda _context: FakeCrewEngine(delay=0.3)
    )

    async def exercise():
        await application.start()
        started = time.monotonic()
        results = await asyncio.gather(
            *(
                application.run(
                    flock_app.IncidentReport(raw_report=f"incident {i}"),
                    context=WorkflowContext(f"resp-{i}", principal_id=f"user-{i}"),
                )
                for i in range(4)
            )
        )
        return results, time.monotonic() - started

    results, elapsed = asyncio.run(exercise())

    assert all(result.status is WorkflowStatus.SUCCEEDED for result in results)
    assert all(len(result.outputs) == 3 for result in results)
    # 4 turns x 2 sequential stages x 0.3 s would take >= 2.4 s if serialized.
    assert elapsed < 1.5


def test_parallel_agents_run_concurrently_within_a_turn() -> None:
    log: list[str] = []
    application = flock_app.build_application(
        model=MODEL, new_engine=lambda _context: FakeCrewEngine(delay=0.2, log=log)
    )

    async def exercise():
        started = time.monotonic()
        result = await application.run(
            flock_app.IncidentReport(raw_report="x"), context=WorkflowContext("resp-par")
        )
        return result, time.monotonic() - started

    result, elapsed = asyncio.run(exercise())

    assert result.ok
    assert log[-1] == "incident_commander"
    # two parallel analysts (0.2 s) + commander (0.2 s), not three sequential calls
    assert elapsed < 0.55
