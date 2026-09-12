import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("agent_name", "env_prefix", "context"),
    [
        (
            "architecture-advisor",
            "FOUNDRY_ARCHITECTURE_ADVISOR",
            "src/agents/architecture_advisor",
        ),
        ("incident-triage", "FOUNDRY_INCIDENT_TRIAGE", "src/agents/incident_triage"),
    ],
)
def test_publish_script_writes_image_metadata(
    tmp_path: Path, agent_name: str, env_prefix: str, context: str
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    docker = fake_bin / "docker"
    docker.write_text(
        '#!/bin/sh\necho "docker $*" >> "$COMMAND_LOG"\n'
        'case "$*" in *"imagetools inspect"*) echo "sha256:' + "a" * 64 + '";; esac\n'
    )
    az = fake_bin / "az"
    az.write_text(
        '#!/bin/sh\necho "az $*" >> "$COMMAND_LOG"\n'
        'case "$*" in *"acr show"*) echo "demo.azurecr.io";; esac\n'
    )
    docker.chmod(0o755)
    az.chmod(0o755)
    env_file = tmp_path / "image.env"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "COMMAND_LOG": str(log)}
    result = subprocess.run(
        ["scripts/publish-hosted-image.sh", "demo", agent_name, "v1", str(env_file)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.stdout.strip() == str(env_file)
    content = env_file.read_text()
    assert f"{env_prefix}_IMAGE=demo.azurecr.io/{agent_name}:v1-" in content
    assert f"{env_prefix}_IMAGE_DIGEST=sha256:" in content
    commands = log.read_text()
    assert "buildx build --platform linux/amd64 --push" in commands
    assert context in commands
    assert (Path(context) / "Dockerfile").is_file()


def test_publish_script_rejects_an_unknown_agent(tmp_path: Path):
    result = subprocess.run(
        ["scripts/publish-hosted-image.sh", "demo", "not-an-agent"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "No Dockerfile found" in result.stderr
