import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip(
    "flock",
    reason="hosted runtime deps live in src/agents/incident_triage",
)

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src" / "agents" / "incident_triage"),
)

import flock_app  # noqa: E402
import main  # noqa: E402
from flock.registry import type_registry  # noqa: E402
from model_endpoint import (  # noqa: E402
    DEFAULT_API_VERSION,
    resolve_api_base,
    resolve_api_version,
    resolve_model_endpoint,
)

PROJECT_ENDPOINT = "https://example.services.ai.azure.com/api/projects/dev"


def _build() -> "flock_app.Flock":
    return flock_app.build_flock(
        model_deployment="demo-chat-model",
        api_base="https://example.services.ai.azure.com",
        api_version=DEFAULT_API_VERSION,
        token_provider=lambda: "token",
    )


def test_build_flock_registers_the_triage_crew() -> None:
    flock = _build()
    assert [agent.name for agent in flock.agents] == [
        "impact_assessor",
        "root_cause_analyst",
        "incident_commander",
    ]
    assert flock.model == "demo-chat-model" or flock.model.endswith("demo-chat-model")


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


def test_each_agent_gets_its_own_engine_carrying_the_token_provider() -> None:
    flock = _build()
    engines = [agent.engines[0] for agent in flock.agents]
    assert len({id(engine) for engine in engines}) == 3
    for engine in engines:
        assert engine.model == "azure/demo-chat-model"
        assert engine.lm_kwargs["api_base"] == "https://example.services.ai.azure.com"
        assert engine.lm_kwargs["api_version"] == DEFAULT_API_VERSION
        assert engine.lm_kwargs["azure_ad_token_provider"]() == "token"
        # Foundry chat models reject max_tokens, and LiteLLM cannot infer the
        # model family behind a deployment name, so it has to be dropped.
        assert engine.lm_kwargs["additional_drop_params"] == ["max_tokens"]
        # Whole artifacts are emitted, not tokens.
        assert engine.stream is False
        assert engine.no_output is True


def test_dspy_input_type_warning_is_disabled() -> None:
    # Flock passes DSPy the artifact payload as a dict while annotating the
    # signature with the model class, so the check warns on every invocation and
    # can never be meaningful here.
    import dspy

    assert dspy.settings.warn_on_type_mismatch is False


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


def test_require_one_rejects_an_incomplete_cascade() -> None:
    with pytest.raises(RuntimeError, match="ActionPlan"):
        main._require_one([], artifact_type=flock_app.ActionPlan)


class _StubStore:
    def __init__(self, artifacts: dict[type, list[object]]) -> None:
        self._artifacts = artifacts
        self.correlation_ids: list[str] = []

    async def get_by_type(self, artifact_type, *, correlation_id=None):
        self.correlation_ids.append(correlation_id)
        return self._artifacts[artifact_type]


class _StubFlock:
    def __init__(self, store: _StubStore) -> None:
        self.store = store
        self.published: list[tuple[object, str]] = []
        self.idle_calls = 0

    async def publish(self, obj, *, correlation_id=None):
        self.published.append((obj, correlation_id))

    async def run_until_idle(self, *, timeout=None):
        self.idle_calls += 1


def _stub_flock() -> _StubFlock:
    return _StubFlock(
        _StubStore(
            {
                flock_app.ImpactAssessment: [
                    flock_app.ImpactAssessment(
                        severity="High",
                        affected_scope="Checkout API and the mobile client",
                        user_impact="Roughly a third of purchases fail",
                    )
                ],
                flock_app.RootCauseHypothesis: [
                    flock_app.RootCauseHypothesis(
                        hypothesis=(
                            "The 14:05 deploy shipped a schema change the payment "
                            "adapter cannot deserialize."
                        ),
                        evidence=["Errors start at 14:05"],
                        confidence=0.7,
                    )
                ],
                flock_app.ActionPlan: [
                    flock_app.ActionPlan(
                        immediate_steps=["Roll back the 14:05 deploy"],
                        owner_role="Checkout on-call",
                        comms_update="Checkout is degraded; a rollback is in progress.",
                    )
                ],
            }
        )
    )


def test_run_triage_publishes_and_collects_under_one_correlation_id() -> None:
    stub = _stub_flock()

    async def exercise():
        with patch.object(main, "get_flock", return_value=stub):
            return await main.run_triage("checkout is failing", correlation_id="resp-1")

    artifacts = asyncio.run(exercise())

    assert [type(artifact).__name__ for artifact in artifacts] == [
        "ImpactAssessment",
        "RootCauseHypothesis",
        "ActionPlan",
    ]
    report, correlation_id = stub.published[0]
    assert isinstance(report, flock_app.IncidentReport)
    assert report.raw_report == "checkout is failing"
    assert correlation_id == "resp-1"
    assert stub.idle_calls == 1
    assert stub.store.correlation_ids == ["resp-1", "resp-1", "resp-1"]


def test_handler_emits_one_output_item_per_artifact() -> None:
    stub = _stub_flock()

    class _Context:
        response_id = "resp-2"

        async def get_input_text(self):
            return "checkout is failing"

    async def exercise():
        with patch.object(main, "get_flock", return_value=stub):
            return [
                event
                async for event in main.handler(None, _Context(), asyncio.Event())
            ]

    events = asyncio.run(exercise())
    types = [event["type"] for event in events]
    assert types[0] == "response.created"
    assert types[-1] == "response.completed"
    assert types.count("response.output_item.done") == 3
