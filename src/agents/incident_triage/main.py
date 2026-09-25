"""Incident triage hosted behind the Foundry Responses protocol, powered by Flock.

Each Responses turn becomes one isolated Flock workflow: the caller's text is
published as an :class:`~flock_app.IncidentReport`, the triage crew cascades it,
and every artifact the crew publishes is streamed as its own Responses output
item as soon as it exists, so the multi-agent flow is visible to the caller.

HTTP, SSE, background mode, polling, cancellation, readiness and shutdown are
handled by Flock's Foundry integration (``flock.integrations.foundry``) on top of
the official AgentServer SDK.
"""

import logging
import os

# Must be set before flock is imported: the Foundry host owns OpenTelemetry.
os.environ.setdefault("FLOCK_AUTO_TRACE", "false")
os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")

from dotenv import load_dotenv  # noqa: E402
from flock.engines.auth.azure import get_default_azure_token_provider  # noqa: E402
from flock.integrations.foundry import (  # noqa: E402
    FoundryResponsesAdapter,
    IdentityPolicy,
    TextTurn,
)
from pydantic import BaseModel  # noqa: E402

from flock_app import (  # noqa: E402
    IncidentReport,
    build_application,
    dspy_engine_builder,
)
from model_endpoint import resolve_api_base, resolve_api_version  # noqa: E402

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("incident-triage")


def format_artifact(artifact: BaseModel) -> str:
    """Render one blackboard artifact as a labelled JSON block."""
    return f"{type(artifact).__name__}\n{artifact.model_dump_json(indent=2)}"


def to_report(turn: TextTurn) -> IncidentReport:
    """Map the Responses input text onto the crew's typed input."""
    return IncidentReport(raw_report=turn.text)


def create_host(**adapter_options) -> FoundryResponsesAdapter:
    """Build the Foundry host from the container environment."""
    load_dotenv(override=False)
    model = f"azure/{os.environ['AZURE_AI_MODEL_DEPLOYMENT_NAME']}"
    application = build_application(
        model=model,
        new_engine=dspy_engine_builder(
            model=model,
            api_base=resolve_api_base(),
            api_version=resolve_api_version(),
            # One renewable provider for the whole process: the hosted agent's
            # managed identity (local auth is disabled on the Foundry account).
            token_provider=get_default_azure_token_provider(),
        ),
    )
    return FoundryResponsesAdapter(
        application,
        input_mapper=to_report,
        output_mapper=format_artifact,
        # The crew publishes only public artifacts and keeps no per-user state,
        # so a missing gateway user id is not a reason to reject a turn.
        identity=IdentityPolicy(
            required=False,
            local_development=os.environ.get("FLOCK_FOUNDRY_LOCAL_DEV") == "1",
        ),
        **adapter_options,
    )


def main() -> None:
    LOGGER.info("Starting incident-triage Responses server on port 8088")
    create_host().run()


if __name__ == "__main__":
    main()
