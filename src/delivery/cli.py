"""Safe delivery entry point for Microsoft Foundry agents."""

import argparse
import os
import sys
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from foundry_demo.agents import AgentKind, get_agent, list_agents
from foundry_demo.delivery.config import load_config
from foundry_demo.delivery.hosted import HostedAgentOperations
from foundry_demo.delivery.locking import deployment_lock
from foundry_demo.delivery.prompt import PromptAgentOperations
from foundry_demo.delivery.records import DeploymentRecorder, collect_audit_metadata
from foundry_demo.delivery.release import deploy_release

AGENT_NAME = "research-assistant"


def deploy(agent_spec, config, recorder) -> None:
    operations = (
        PromptAgentOperations() if agent_spec.kind is AgentKind.PROMPT else HostedAgentOperations()
    )
    with (
        DefaultAzureCredential() as credential,
        AIProjectClient(
            endpoint=config.project_endpoint,
            credential=credential,
            allow_preview=True,
        ) as project,
    ):
        deploy_release(project, agent_spec, config, recorder, operations)


def _write_record_path_file(
    output: Path, record_path: Path, repository_root: Path
) -> None:
    """Write the record location for downstream automation.

    Repository-relative when the record lives inside the checkout, absolute
    otherwise, so callers never have to guess the record directory layout.
    """
    try:
        reported = record_path.resolve().relative_to(repository_root).as_posix()
    except ValueError:
        reported = record_path.resolve().as_posix()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(f"{reported}\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="deploy-agent",
        description="Safely create, validate, and route a Foundry agent version.",
        allow_abbrev=False,
    )
    parser.add_argument("agent_name", nargs="?", help="Registered agent name to deploy.")
    parser.add_argument(
        "--list", action="store_true", help="List registered agents without deploying."
    )
    parser.add_argument(
        "--record-path-file",
        type=Path,
        help=(
            "Optional file receiving the deployment record path. It is written before "
            "deployment starts, so failed runs still point at their record."
        ),
    )
    parsed = parser.parse_args(args)
    if parsed.list:
        if parsed.agent_name is not None:
            parser.error("--list cannot be combined with an agent name")
        if parsed.record_path_file is not None:
            parser.error("--list cannot be combined with --record-path-file")
        print("Available agents:")
        for name in list_agents():
            print(f"  - {name}")
        return
    agent_name = parsed.agent_name or AGENT_NAME
    environ = os.environ
    repository_root = Path.cwd().resolve()
    raw_record_dir = environ.get("FOUNDRY_DEPLOYMENT_RECORD_DIR", "artifacts/deployments")
    path = Path(raw_record_dir)
    record_dir = repository_root / path if not path.is_absolute() else path
    with DeploymentRecorder(
        agent_name=agent_name,
        record_dir=record_dir,
        audit_metadata=collect_audit_metadata(environ, repository_root),
    ) as recorder:
        if parsed.record_path_file is not None:
            _write_record_path_file(
                parsed.record_path_file, recorder.record_path, repository_root
            )
        spec = get_agent(agent_name)
        config = load_config(environ)
        with deployment_lock(project_endpoint=config.project_endpoint, agent_name=spec.name):
            deploy(spec, config, recorder)
            print(f"Successfully deployed {spec.name} (record: {recorder.record_path})")


__all__ = ["main"]
