import os
import subprocess
from pathlib import Path


def test_publish_script_writes_image_metadata(tmp_path: Path):
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
        ["scripts/publish-hosted-image.sh", "demo", "v1", str(env_file)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.stdout.strip() == str(env_file)
    content = env_file.read_text()
    assert "FOUNDRY_ARCHITECTURE_ADVISOR_IMAGE=demo.azurecr.io/architecture-advisor:v1-" in content
    assert "FOUNDRY_ARCHITECTURE_ADVISOR_IMAGE_DIGEST=sha256:" in content
    commands = log.read_text()
    assert "buildx build --platform linux/amd64 --push" in commands
    assert "src/agents/architecture_advisor" in commands
    assert (Path("src/agents/architecture_advisor") / "Dockerfile").is_file()
