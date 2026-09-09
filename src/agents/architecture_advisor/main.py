"""Architecture advisor hosted with the Microsoft Agent Framework Responses protocol."""

import logging
import os

os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient, FoundryToolbox
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.core.tasks import set_resilient_tasks_enabled
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from toolbox_endpoint import validate_toolbox_endpoint

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("architecture-advisor")

set_resilient_tasks_enabled(True)

BASE_INSTRUCTIONS = (
    "You are an Azure architecture advisor. Provide grounded architectural guidance "
    "and decision briefs comparing Azure services or patterns. Use Microsoft Learn tools "
    "and load the architecture-decision-brief skill when comparing options. Cite official "
    "Microsoft Learn documentation."
)


def build_agent() -> Agent:
    load_dotenv(override=False)
    project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    model = os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"]
    raw_toolbox_endpoint = os.environ["TOOLBOX_ENDPOINT"]
    toolbox_endpoint = validate_toolbox_endpoint(
        raw_toolbox_endpoint,
        project_endpoint=project_endpoint,
    )

    credential = DefaultAzureCredential()
    toolbox = FoundryToolbox(credential, url=toolbox_endpoint)
    skills_provider = toolbox.as_skills_provider(
        disable_load_skill_approval=True,
        disable_read_skill_resource_approval=True,
    )
    client = FoundryChatClient(
        project_endpoint=project_endpoint,
        model=model,
        credential=credential,
    )
    return Agent(
        client=client,
        name="architecture-advisor",
        description="Provides grounded Azure architecture guidance and decision briefs.",
        instructions=BASE_INSTRUCTIONS,
        tools=toolbox,
        context_providers=[skills_provider],
        default_options={"store": False},
    )


def main() -> None:
    LOGGER.info("Starting architecture-advisor Responses server on port 8088")
    ResponsesHostServer(build_agent()).run()


if __name__ == "__main__":
    main()
